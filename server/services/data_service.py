"""数据服务：批次商品查询、商品详情、CSV 导出、品类/批次删除。"""
from __future__ import annotations

import csv
import io
import json
import re
import shutil

from bkr import database
from bkr.config import RAW_DIR
from bkr.parse import parse_price
from bkr.repositories import (
    ai_insight_repo,
    batch_repo,
    product_repo,
    snapshot_repo,
    task_repo,
)
from server.utils import fallback_product_url, proxied_image

PLATFORM_LABELS = {"tmall": "天猫", "1688": "1688", "pdd": "拼多多"}
PLATFORM_ORDER = {"tmall": 0, "1688": 1, "pdd": 2}


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def safe_filename(text: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", text or "").strip("_")
    return cleaned or "report"


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------

def list_tasks() -> list[dict]:
    """品类 + 批次清单（ai_batches 供「只看有 AI 分析」过滤）。"""
    with database.connection() as conn:
        rows = task_repo.list_tasks(conn)
        ai_map = ai_insight_repo.list_pairs(conn)
        out = []
        for r in rows:
            out.append({
                **r,
                "batches": snapshot_repo.list_batches(conn, r["task_id"]),
                "ai_batches": ai_map.get(r["task_id"], []),
            })
    return out


def list_products(
    batch: str,
    task_id: str | None = None,
    only_detail: bool = True,
    proxy_images: bool = True,
) -> list[dict]:
    """批次商品列表（task_id 省略时为跨平台合并）。"""
    with database.connection() as conn:
        rows = snapshot_repo.list_products(conn, batch, task_id=task_id, only_detail=only_detail)
    out = []
    for r in rows:
        skus = _loads(r["sku_names"], [])
        price_final = r["price_final"]
        if price_final is None:
            # 未采详情的候选：用列表页展示价兜底（口径：列表展示价）
            price_final = parse_price(r["price_text"] or "")
        out.append({
            "item_id": r["item_id"],
            "platform": r["platform"],
            "title": r["title"],
            "image_url": proxied_image(r["platform"], r["image_url"]) if proxy_images else r["image_url"],
            "price_final": price_final,
            "sold_text": r["sold_text"],
            "sold_lb": r["sold_lb"],
            "cart_text": r["cart_text"],
            "cart_lb": r["cart_lb"],
            "shop_name": r["shop_name"],
            "shop_rating": r["shop_rating"],
            "positive_rate": r["positive_rate"],
            "rank": r["rank"],
            "captured_at": r["captured_at"],
            "product_url": r["product_url"] or fallback_product_url(r["platform"], r["item_id"]),
            "params": _loads(r["params_json"], {}),
            "skus": skus,
            "sku_count": len(skus),
        })
    return out


def product_detail(item_id: str, platform: str = "tmall") -> dict | None:
    with database.connection() as conn:
        p = product_repo.get_product(conn, platform, item_id)
        if not p:
            return None
        history = snapshot_repo.product_history(conn, platform, item_id)
    return {
        "item_id": item_id,
        "platform": platform,
        "title": p["title"],
        "image_url": proxied_image(platform, p["image_url"]),
        "video_url": p["video_url"],
        "shop_name": p["shop_name"],
        "shop_rating": p["shop_rating"],
        "positive_rate": p["positive_rate"],
        "params": _loads(p["params_json"], {}),
        "skus": _loads(p["sku_names"], []),
        "product_url": p["product_url"],
        "history": history,
    }


def report_meta(batch: str) -> dict | None:
    """报告元信息：本批次包含哪些平台、各多少条。"""
    tasks = _report_tasks(batch)
    if not tasks:
        return None
    out = []
    with database.connection() as conn:
        for t in tasks:
            items, details = snapshot_repo.count_batch(conn, t["task_id"], batch)
            out.append({
                "task_id": t["task_id"],
                "platform": t["platform"],
                "label": PLATFORM_LABELS.get(t["platform"], t["platform"]),
                "status": t["status"],
                "items": items,
                "details": details,
            })
        keyword = task_repo.keyword_for(conn, tasks[0]["task_id"])
    return {"batch": batch, "keyword": keyword, "platforms": out}


def _report_tasks(batch: str) -> list[dict]:
    with database.connection() as conn:
        rows = batch_repo.list_batch_tasks(conn, batch)
    tasks = [r for r in rows if r.get("platform")]
    tasks.sort(key=lambda r: (PLATFORM_ORDER.get(r["platform"], 9), r["task_id"]))
    return tasks


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

_CSV_HEADER = [
    "平台", "item_id", "标题", "到手价", "已售", "加购", "店铺", "评分", "好评率",
    "SKU", "参数", "主图", "链接", "采集时间",
]


def _csv_text(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_HEADER)
    for r in rows:
        writer.writerow([
            r["platform"], r["item_id"], r["title"], r["price_final"],
            r["sold_text"], r["cart_text"],
            r["shop_name"], r["shop_rating"], r["positive_rate"],
            " / ".join(r["skus"]), json.dumps(r["params"], ensure_ascii=False),
            r["image_url"], r["product_url"], r["captured_at"],
        ])
    return "\ufeff" + buf.getvalue()


def export_single(task_id: str, batch: str, only_detail: bool) -> tuple[str, str]:
    """单平台批次 CSV：返回 (内容, 文件名)。"""
    rows = list_products(batch, task_id=task_id, only_detail=only_detail, proxy_images=False)
    with database.connection() as conn:
        platform_row = conn.execute(
            "SELECT platform FROM dim_task WHERE task_id=?", (task_id,)
        ).fetchone()
        keyword = task_repo.keyword_for(conn, task_id)
    if platform_row and platform_row["platform"]:
        platform = platform_row["platform"]
    elif rows:
        platform = rows[0]["platform"]
    else:
        platform = "tmall"
    label = PLATFORM_LABELS.get(platform, platform or "unknown")
    return _csv_text(rows), f"{safe_filename(keyword)}_({label})_{batch}.csv"


def export_merged(batch: str, only_detail: bool = False) -> tuple[str, str] | None:
    """一次搜索的跨平台合并 CSV。"""
    tasks = _report_tasks(batch)
    if not tasks:
        return None
    rows: list[dict] = []
    for t in tasks:
        rows.extend(list_products(batch, task_id=t["task_id"],
                                  only_detail=only_detail, proxy_images=False))
    if not rows:
        return None
    with database.connection() as conn:
        keyword = task_repo.keyword_for(conn, tasks[0]["task_id"])
    labels = [PLATFORM_LABELS.get(t["platform"], t["platform"]) for t in tasks]
    return _csv_text(rows), f"{safe_filename(keyword)}_({'+'.join(labels)})_{batch}.csv"


# ---------------------------------------------------------------------------
# 删除
# ---------------------------------------------------------------------------

def _rm_raw_if_unused(batch: str) -> None:
    with database.connection() as conn:
        remain = snapshot_repo.batch_has_rows(conn, batch)
    if remain:
        return
    target = RAW_DIR / batch
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


def delete_task(task_id: str) -> bool:
    """删除品类及其全部快照/批次记录（商品行仅清理孤儿）。"""
    with database.connection() as conn:
        if not task_repo.get_task(conn, task_id):
            return False
        batches = snapshot_repo.list_batches(conn, task_id)
        snapshot_repo.delete_by_task(conn, task_id)
        batch_repo.delete_by_task(conn, task_id)
        task_repo.delete_task(conn, task_id)
        product_repo.delete_orphans(conn)
        conn.commit()
    for batch in batches:
        _rm_raw_if_unused(batch)
    return True


def delete_batch(task_id: str, batch: str) -> bool:
    """删除某个品类的单个批次。"""
    with database.connection() as conn:
        removed = snapshot_repo.delete_by_batch(conn, task_id, batch)
        if removed == 0:
            return False
        batch_repo.delete_by_batch(conn, task_id, batch)
        product_repo.delete_orphans(conn)
        conn.commit()
    _rm_raw_if_unused(batch)
    return True
