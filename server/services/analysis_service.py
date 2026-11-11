"""分析服务：批次统计汇总 + 单批次报告（自动合并已保存的 AI 解读）。"""
from __future__ import annotations

import json
from datetime import datetime

from bkr import database
from bkr.analysis import build_summary
from bkr.analysis_profile import ensure_profile, profile_for
from bkr.parse import parse_price
from bkr.repositories import ai_insight_repo, batch_repo, snapshot_repo, task_repo
from server.utils import fallback_product_url, proxied_image

PLATFORM_LABELS = {"tmall": "天猫", "1688": "1688", "pdd": "拼多多"}
PLATFORM_ORDER = {"tmall": 0, "1688": 1, "pdd": 2}

PROFILE_SOURCE_LABELS = {
    "manual": "人工配置",
    "llm": "自动生成",
    "discovered": "自动发现",
    "none": "未生成",
}


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def task_rows(task_id: str, batch: str) -> list[dict]:
    """单平台任务某批次的商品行（价格缺失时用列表价兜底）。"""
    with database.connection() as conn:
        rows = snapshot_repo.list_task_products(conn, task_id, batch)
    out = []
    for r in rows:
        price = r["price_final"]
        source = "detail"
        if price is None:
            price = parse_price(r["price_text"] or "")
            source = "list" if price is not None else None
        skus = _loads(r["sku_names"], [])
        out.append({
            "item_id": r["item_id"],
            "platform": r["platform"],
            "title": r["title"],
            "price_final": price,
            "price_source": source,
            "sold_lb": r["sold_lb"],
            "sold_text": r["sold_text"],
            "cart_lb": r["cart_lb"],
            "shop_name": r["shop_name"],
            "image_url": proxied_image(r["platform"], r["image_url"]),
            "product_url": r["product_url"] or fallback_product_url(r["platform"], r["item_id"]),
            "params": _loads(r["params_json"], {}),
            "skus": skus,
            "is_detail": r["is_detail"],
        })
    return out


def summary_for(name: str, rows: list[dict]) -> dict:
    """统一入口：没有词表就触发后台生成，本次仍返回其余统计。"""
    meta = ensure_profile(name, rows)
    summary = build_summary(rows, profile_for(name))
    summary["profile"]["status"] = meta.get("status")
    summary["profile"]["keyword"] = meta.get("keyword")
    summary["profile"]["message"] = meta.get("message")
    summary["profile"]["label"] = PROFILE_SOURCE_LABELS.get(
        summary["profile"].get("source") or "none", "未生成"
    )
    return summary


def task_summary(task_id: str, batch: str) -> dict | None:
    """批次分析汇总（价格带/销量分布/店铺/卖点词/参数/Top）。"""
    rows = task_rows(task_id, batch)
    if not rows:
        return None
    with database.connection() as conn:
        name = task_repo.task_name(conn, task_id)
    return summary_for(name, rows)


def batch_tasks(batch: str) -> list[dict]:
    """批次内所有平台任务（顺序固定：天猫 → 1688 → 拼多多）。"""
    with database.connection() as conn:
        rows = batch_repo.list_batch_tasks(conn, batch)
    tasks = [r for r in rows if r.get("platform")]
    tasks.sort(key=lambda r: (PLATFORM_ORDER.get(r["platform"], 9), r["task_id"]))
    return tasks


def keyword_of(task_id: str) -> str:
    with database.connection() as conn:
        return task_repo.keyword_for(conn, task_id)


def _fmt_money(v) -> str:
    return "—" if v is None else f"¥{float(v):.2f}"


def _fmt_count(v) -> str:
    if v is None:
        return "—"
    return str(int(v)) if float(v).is_integer() else f"{float(v):.1f}"


def _platform_section(label: str, s: dict) -> str:
    stats = s["price"]["stats"]
    counts = s["counts"]
    lines = [
        f"### {label}",
        "",
        f"- 候选商品 {counts['candidates']} 个，采到详情 {counts['detailed']} 个",
        f"- 价格（有效样本 {stats.get('count') or 0}）："
        f"中位数 {_fmt_money(stats.get('median'))}，"
        f"P25 {_fmt_money(stats.get('p25'))}，P75 {_fmt_money(stats.get('p75'))}，"
        f"区间 {_fmt_money(stats.get('min'))} ~ {_fmt_money(stats.get('max'))}",
        f"- 已售中位数 {_fmt_count(s['sold']['median'])}",
        f"- 店铺 {s['shops']['distinct']} 家，TOP5 销量占比 "
        f"{round(s['shops']['top5_share'] * 100)}%",
    ]
    prof = s.get("profile") or {}
    if prof.get("words"):
        lines.append(
            f"- 卖点词表：{prof.get('label') or '未生成'}，{prof.get('words')} 个词，"
            f"覆盖 {round(float(prof.get('coverage') or 0) * 100)}% 商品"
        )
    else:
        lines.append("- 卖点词表：未生成（仅统计参数卖点）")
    lines.append("")
    if s["price"]["bands"]:
        lines += ["**价格带分布**", "", "| 价格带 | 商品数 | 已售合计 |", "| --- | --- | --- |"]
        for b in s["price"]["bands"]:
            lines.append(f"| {b['label']} | {b['count']} | {b.get('sold', 0)} |")
        lines.append("")
    if s["sold"]["bands"]:
        lines += ["**销量区间分布**", "", "| 已售区间 | 商品数 |", "| --- | --- |"]
        for b in s["sold"]["bands"]:
            lines.append(f"| {b['label']} | {b['count']} |")
        lines.append("")
    points = s.get("selling_points") or {}
    if points.get("title"):
        lines += [
            "**卖点词（标题命中）**", "",
            "| 卖点词 | 来源 | 商品数 | 覆盖率 | 均价 | 均销量 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for it in points["title"][:15]:
            source = "参数" if it.get("source") == "param" else "标题"
            lines.append(
                f"| {it['word']} | {source} | {it['items']} | "
                f"{round(float(it.get('coverage') or 0) * 100)}% | "
                f"{_fmt_money(it.get('avg_price'))} | {_fmt_count(it.get('avg_sold'))} |"
            )
        lines.append("")
    if points.get("params"):
        lines += [
            "**参数卖点（key = value）**", "",
            "| 参数 | 取值 | 商品数 | 覆盖率 | 均价 | 均销量 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for it in points["params"][:12]:
            lines.append(
                f"| {it['key']} | {it['value']} | {it['items']} | "
                f"{round(float(it.get('coverage') or 0) * 100)}% | "
                f"{_fmt_money(it.get('avg_price'))} | {_fmt_count(it.get('avg_sold'))} |"
            )
        lines.append("")
    if s["top_products"]:
        lines += [
            "**爆款候选 Top10（按已售下限）**", "",
            "| # | 商品 | 到手价 | 已售 | 店铺 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for i, p in enumerate(s["top_products"], 1):
            title = (p.get("title") or "—").replace("|", "／")
            lines.append(
                f"| {i} | {title} | {_fmt_money(p.get('price_final'))} | "
                f"{p.get('sold_text') or '—'} | {p.get('shop_name') or '—'} |"
            )
        lines.append("")
    return "\n".join(lines)


def build_batch_report(batch: str) -> dict | None:
    """单批次报告：各平台分别统计 + 已保存的 AI 解读（有则合并）。"""
    tasks = batch_tasks(batch)
    if not tasks:
        return None
    keyword = keyword_of(tasks[0]["task_id"]) or "品类"
    sections: list[str] = []
    platforms_meta: list[dict] = []
    with database.connection() as conn:
        for t in tasks:
            label = PLATFORM_LABELS.get(t["platform"], t["platform"])
            rows = task_rows(t["task_id"], batch)
            ai = ai_insight_repo.get(conn, t["task_id"], batch)
            platforms_meta.append({
                "task_id": t["task_id"],
                "platform": t["platform"],
                "label": label,
                "status": t["status"],
                "items": len(rows),
                "details": sum(1 for r in rows if r["is_detail"]),
                "has_ai": ai is not None,
            })
            if not rows:
                sections.append(f"### {label}\n\n该平台本批次没有采到数据（状态：{t['status']}）。\n")
                continue
            sections.append(_platform_section(
                label, summary_for(t["name"] or keyword, rows)
            ))
            if ai:
                sections.append(f"### {label} · AI 解读\n\n{ai['content_md']}\n")

    markdown = "\n".join([
        f"# {keyword} · 单批次采集报告",
        "",
        f"- 批次：`{batch}`",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 平台：{' + '.join(p['label'] for p in platforms_meta)}",
        "",
        "> 数据口径：已售/加购取页面区间文本的下限，不是精确销量；价格取详情到手价，"
        "未采详情时用列表展示价兜底。三个平台销量口径不同，本报告按平台分别统计，"
        "不做跨平台销量排名。",
        "",
        "---",
        "",
        "\n".join(sections),
    ])
    return {"batch": batch, "keyword": keyword, "platforms": platforms_meta, "markdown": markdown}
