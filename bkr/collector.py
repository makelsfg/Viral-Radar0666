"""采集编排：搜索页 → 详情（本地点击或 Apify 通道） → 入库，支持断点续采。"""
from __future__ import annotations

import json
import os
import random
import time
from datetime import datetime

from . import db
from .browser import (
    BlockedError,
    EmptyResultError,
    LoginError,
    browser_runtime,
    close_context,
    launch_context,
)
from .config import RAW_DIR
from .pace import get_pace
from .parse import parse_price, same_search_url as _same_search_url


def _platform_adapters(platform: str):
    """返回 (collect_pages, open_search_page, click_and_extract)。"""
    if platform == "1688":
        from .platforms import alibaba as adapter

        return adapter.collect_pages, adapter.open_search_page, adapter.click_and_extract
    if platform == "pdd":
        from .platforms import pinduoduo as adapter

        return adapter.collect_pages, adapter.open_search_page, adapter.click_and_extract
    from .detail_collector import click_and_extract
    from .list_collector import collect_pages, open_search_page

    return collect_pages, open_search_page, click_and_extract


def batch_id(now: datetime | None = None) -> str:
    """批次号 = 日期_时分秒，保证同一天多次运行互不覆盖。"""
    now = now or datetime.now()
    return now.strftime("%Y-%m-%d_%H%M%S")


def _price_guard(detail_price, list_price):
    """详情价缺失或与列表价偏离过大时回退列表价。"""
    if list_price is None:
        return detail_price
    if detail_price is None:
        return list_price
    if detail_price < list_price * 0.3 or detail_price > list_price * 3:
        return list_price
    return detail_price


def _enrich_with_apify(conn, task, batch: str, pending: list[dict], raw_dir) -> None:
    from .providers.apify_tmall import fetch_details, map_to_product

    token = (os.environ.get("BK_APIFY_TOKEN") or "").strip()
    if not token:
        raise RuntimeError(
            "详情通道为 apify，但未配置 token（settings.yaml → apify.token 或 BK_APIFY_TOKEN）"
        )
    actor = (os.environ.get("BK_APIFY_ACTOR") or "unitbytes~taobao-tmall-scraper").strip()

    print(f"  [apify] 提交 {len(pending)} 个商品，等待结果（最长 300 秒）…")
    items = fetch_details([r["item_id"] for r in pending], token, actor=actor)
    by_id = {str(it.get("itemId")): it for it in items}

    found = 0
    for r in pending:
        iid = r["item_id"]
        it = by_id.get(str(iid))
        if not it:
            print(f"  [apify] 未返回详情：{iid}")
            continue
        rec = map_to_product(it)
        price_final = _price_guard(
            rec["price_final"], parse_price(r.get("price_text") or "")
        )
        payload = dict(rec, source="apify")
        (raw_dir / f"{iid}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        db.upsert_product(conn, {
            "platform": task.platform,
            "item_id": iid,
            "title": rec["title"] or r.get("title") or "",
            "shop_name": rec["shop_name"] or r.get("shop_name"),
            "shop_rating": rec["shop_rating"],
            "positive_rate": rec["positive_rate"],
            "sku_names": json.dumps(rec["sku_names"], ensure_ascii=False),
            "params_json": json.dumps(rec["params"], ensure_ascii=False),
            "image_url": rec["image_url"] or r.get("image_url"),
            "video_url": rec["video_url"],
            "product_url": rec["product_url"]
            or f"https://detail.tmall.com/item.htm?id={iid}",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "first_seen_at": datetime.now().isoformat(timespec="seconds"),
        })
        db.set_snapshot_detail(conn, task.task_id, iid, batch, price_final)
        found += 1
        db.set_batch(conn, batch, task.task_id, stage="detail", detail_done=found)

    print(f"  [apify] 补全 {found}/{len(pending)} 个详情")
    if found == 0:
        raise RuntimeError("Apify 未返回任何详情数据（检查 token / 额度 / actor 名称）")


def _collect_details_by_click(
    conn, ctx, task, batch: str, pending: list[dict], done_count: int, raw_dir,
    open_search_page_fn, click_fn, pace: dict,
) -> int:
    """本地通道：在搜索结果页点击进入详情，拟人节奏。返回已处理数量。"""
    by_page: dict[int, list] = {}
    for r in pending:
        by_page.setdefault(int(r.get("page_no") or 1), []).append(r)

    processed = 0
    for pageno in sorted(by_page):
        items = by_page[pageno]
        print(f"  打开搜索结果第 {pageno} 页，准备点击进入 {len(items)} 个详情（当前标签数 {len(ctx.pages)}）")
        list_page = open_search_page_fn(ctx, task, pageno)
        try:
            # 先像真人一样随意浏览一会儿列表，再开始点
            for _ in range(random.randint(2, 4)):
                try:
                    list_page.mouse.wheel(0, random.randint(300, 900))
                    list_page.wait_for_timeout(random.randint(1500, 3000))
                except Exception:
                    pass

            for r in items:
                iid = r["item_id"]
                print(f"  -> 详情 {processed + 1}/{len(pending)}：{iid}")
                detail = click_fn(ctx, list_page, iid, save_html=task.save_html)

                price_final = _price_guard(
                    detail["price_final"], parse_price(r.get("price_text") or "")
                )
                if price_final != detail["price_final"]:
                    print(
                        f"    [价格修正] 详情价 {detail['price_final']} 与列表价偏差过大，回退列表价 {price_final}"
                    )

                html = detail.pop("_html", None)
                if html:
                    (raw_dir / f"{iid}.html").write_text(html, encoding="utf-8")
                (raw_dir / f"{iid}.json").write_text(
                    json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8"
                )

                db.upsert_product(conn, {
                    "platform": task.platform,
                    "item_id": iid,
                    "title": detail["title"] or r.get("title") or "",
                    "shop_name": detail["shop_name"] or r.get("shop_name"),
                    "shop_rating": detail["shop_rating"],
                    "positive_rate": detail["positive_rate"],
                    "sku_names": json.dumps(detail["sku_names"], ensure_ascii=False),
                    "params_json": json.dumps(detail["params"], ensure_ascii=False),
                    "image_url": detail["image_url"] or r.get("image_url"),
                    "video_url": detail["video_url"],
                    "product_url": detail["product_url"],
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                    "first_seen_at": datetime.now().isoformat(timespec="seconds"),
                })
                db.set_snapshot_detail(
                    conn, task.task_id, iid, batch, price_final,
                    sold_text=detail["sold_text"], sold_lb=detail["sold_lb"],
                    cart_text=detail["cart_text"], cart_lb=detail["cart_lb"],
                )
                processed += 1
                db.set_batch(conn, batch, task.task_id, stage="detail",
                             detail_done=done_count + processed)

                # 拟人节奏（按预设）：基础间隔 + 周期性休息
                pause = random.uniform(pace["detail_lo"], pace["detail_hi"])
                if processed % int(pace["rest_every"]) == 0:
                    pause += random.uniform(pace["rest_lo"], pace["rest_hi"])
                    print(f"  （休息 {pause:.0f} 秒，模拟正常浏览节奏）")
                time.sleep(pause)
        finally:
            # 列表页保持常驻，任务结束时随 context 一起关闭，避免浏览器退出
            pass
    return processed


def run_task(task, batch: str | None = None, resume: bool = False) -> None:
    batch = batch or batch_id()
    try:
        task._batch = batch  # 供采集监督 Agent 记录 agent_run
    except Exception:
        pass
    conn = db.connect()
    db.upsert_task(conn, task)

    row = db.get_batch(conn, batch, task.task_id)
    if row and row["status"] == "done" and int(row.get("items_count") or 0) > 0 and not resume:
        print(f"[{task.task_id}] 批次 {batch} 已完成，跳过（如需重跑请换批次号）")
        return

    db.ensure_batch(conn, batch, task.task_id)
    if not resume:
        db.reset_batch_progress(conn, batch, task.task_id)
    db.set_batch(
        conn, batch, task.task_id,
        status="running", error=None,
        config_json=json.dumps({
            "keywords": task.keywords,
            "pages": task.pages,
            "detail_top": task.detail_top,
            "detail_order": task.detail_order,
            "pace": task.pace,
            "price_min": task.price_min,
            "price_max": task.price_max,
            "exclude_words": task.exclude_words,
            "sort": task.sort,
        }, ensure_ascii=False),
    )

    raw_dir = RAW_DIR / batch / task.platform
    raw_dir.mkdir(parents=True, exist_ok=True)

    collect_pages_fn, open_search_page_fn, click_fn = _platform_adapters(task.platform)

    try:
        with browser_runtime() as p:
            ctx = launch_context(p, platform=task.platform)
            try:
                row = db.get_batch(conn, batch, task.task_id) or {}
                page_done = int(row.get("page_done") or 0)

                # 阶段一：搜索页
                if page_done < task.pages:
                    print(f"[{task.task_id}] 采集搜索页（{task.pages} 页，{task.platform}，节奏 {task.pace}）")
                    cards = collect_pages_fn(ctx, task, start_page=1)
                    stamp = datetime.now().isoformat(timespec="seconds")
                    for c in cards:
                        db.upsert_snapshot(
                            conn, task.task_id, c["item_id"], batch, stamp,
                            c["rank"], c["price_text"], c["sold_text"], c["sold_lb"], None, None,
                            title=c["title"], image_url=c["image_url"],
                            shop_name=c["shop_name"], page_no=c.get("page_no"),
                            platform=task.platform,
                        )
                    conn.commit()
                    db.set_batch(
                        conn, batch, task.task_id,
                        stage="detail", page_done=task.pages, items_count=len(cards),
                    )
                    print(f"[{task.task_id}] 候选商品 {len(cards)} 个")
                else:
                    print(f"[{task.task_id}] 搜索页已完成，跳过")

                # 阶段二：详情（本地点击或 Apify 通道）
                candidates = db.list_candidates(
                    conn, task.task_id, batch, task.detail_top, order=task.detail_order
                )
                pending = [r for r in candidates if not r["is_detail"]]
                done_count = len(candidates) - len(pending)
                pdd_truncated = 0
                if task.platform == "pdd":
                    from .config import PDD_DETAIL_CAP
                    from .platforms.pinduoduo import BATCH_COOLDOWN_MIN as _PDD_COOLDOWN

                    if len(pending) > PDD_DETAIL_CAP:
                        pdd_truncated = len(pending) - PDD_DETAIL_CAP
                        pending = pending[:PDD_DETAIL_CAP]
                        print(
                            f"[{task.task_id}] 拼多多单批上限 {PDD_DETAIL_CAP} 个详情，"
                            f"本批先采 {PDD_DETAIL_CAP} 个（剩余 {pdd_truncated} 个候选留待续采）"
                        )
                provider = (os.environ.get("BK_DETAIL_PROVIDER") or "local").strip().lower()
                order_text = "页面顺序" if task.detail_order == "rank" else "已售优先"
                print(
                    f"[{task.task_id}] 详情采集：待采 {len(pending)} / 共 {len(candidates)}"
                    f"（通道：{provider}，顺序：{order_text}）"
                )

                if pending:
                    today = datetime.now().strftime("%Y-%m-%d")
                    try:
                        today_details = conn.execute(
                            "SELECT COUNT(*) FROM fact_snapshot WHERE is_detail=1 AND captured_at LIKE ?",
                            (today + "%",),
                        ).fetchone()[0]
                    except Exception:
                        today_details = 0
                    if today_details >= 10:
                        print(
                            f"[提示] 今日已采集详情 {today_details} 个，建议降低频率；"
                            "同类目连续采详情是触发风控的主要来源。"
                        )

                    if provider == "apify" and task.platform == "tmall":
                        print(f"[{task.task_id}] 详情通道：Apify（本地不访问详情页）")
                        _enrich_with_apify(conn, task, batch, pending, raw_dir)
                    else:
                        _collect_details_by_click(
                            conn, ctx, task, batch, pending, done_count, raw_dir,
                            open_search_page_fn, click_fn, get_pace(task.pace),
                        )

                if pdd_truncated:
                    msg = (
                        f"拼多多单批上限 {PDD_DETAIL_CAP} 个详情，本批已采 {len(pending)} 个，"
                        f"剩余 {pdd_truncated} 个候选；请至少 {_PDD_COOLDOWN} 分钟后"
                        "点「继续采集」续采（或命令行 --resume）"
                    )
                    db.set_batch(
                        conn, batch, task.task_id,
                        status="paused_captcha", stage="detail", error=msg,
                    )
                    print(f"[{task.task_id}] {msg}")
                else:
                    db.set_batch(
                        conn, batch, task.task_id,
                        status="done", stage="done",
                        finished_at=datetime.now().isoformat(timespec="seconds"),
                    )
                    print(f"[{task.task_id}] 批次 {batch} 完成（详情 {len(pending)} 个）")
            finally:
                close_context(ctx)
    except EmptyResultError as e:
        db.set_batch(conn, batch, task.task_id, status="failed", error=str(e))
        print(f"\n[{task.task_id}] 采集失败：{e}")
        print("排查顺序：")
        print("  1) 确认已登录：python -m bkr.cli login")
        print(f"  2) 生成诊断：python -m bkr.cli debug-search --task {task.task_id}")
        print("  3) 把 data/debug/ 下的 html/png 文件发给开发者调选择器")
        raise SystemExit(1)
    except (BlockedError, LoginError) as e:
        msg = str(e)
        if isinstance(e, LoginError):
            try:
                db.set_platform_auth(conn, task.platform, False, msg)
                conn.commit()
            except Exception:
                pass
        db.set_batch(conn, batch, task.task_id, status="paused_captcha", error=msg)
        print(f"\n[{task.task_id}] 已暂停：{msg}")
        print("处理完成后执行：")
        print(f"  python -m bkr.cli collect --task {task.task_id} --batch {batch} --resume")
        raise SystemExit(2)
    except RuntimeError as e:
        db.set_batch(conn, batch, task.task_id, status="failed", error=str(e))
        print(f"\n[{task.task_id}] 采集失败：{e}")
        raise SystemExit(1)
    except Exception as exc:  # noqa: BLE001
        import traceback

        tb = traceback.format_exc()
        print(tb)
        msg = f"{type(exc).__name__}: {exc}"
        try:
            db.set_batch(conn, batch, task.task_id, status="failed", error=msg)
        except Exception:
            pass
        print(f"\n[{task.task_id}] 采集异常：{msg}")
        raise SystemExit(1)