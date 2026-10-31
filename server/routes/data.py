"""数据只读接口：任务、批次、商品列表、商品详情、CSV 导出。"""
from __future__ import annotations

import csv
import io
import json
import re
import shutil
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from bkr import db
from bkr.config import RAW_DIR
from bkr.parse import parse_price
from ..utils import fallback_product_url, image_referer, is_allowed_image_url, proxied_image

router = APIRouter()

PLATFORM_ORDER = {"tmall": 0, "1688": 1, "pdd": 2}
PLATFORM_LABELS = {"tmall": "天猫", "1688": "1688", "pdd": "拼多多"}


def _safe_filename(text: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", text or "").strip("_")
    return cleaned or "report"


def _keyword_for(conn, task_id: str, fallback: str = "") -> str:
    row = conn.execute(
        "SELECT keywords, name FROM dim_task WHERE task_id=?", (task_id,)
    ).fetchone()
    if not row:
        return fallback or task_id
    kws = _loads(row["keywords"], [])
    if kws:
        return str(kws[0])
    return (row["name"] or fallback or task_id).split(" · ")[0]


def _report_tasks(conn, batch: str) -> list[dict]:
    rows = conn.execute(
        """SELECT b.task_id, b.status, t.platform, t.name, t.keywords
           FROM batch_run b
           LEFT JOIN dim_task t ON t.task_id = b.task_id
           WHERE b.batch=?""",
        (batch,),
    ).fetchall()
    tasks = []
    for r in rows:
        d = dict(r)
        if d.get("platform"):
            tasks.append(d)
    tasks.sort(key=lambda r: (PLATFORM_ORDER.get(r["platform"], 9), r["task_id"]))
    return tasks


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _cleanup_orphan_products(conn) -> None:
    """删除不再被任何快照引用的商品行（按 platform+item_id 对齐）。"""
    conn.execute(
        """DELETE FROM product
           WHERE NOT EXISTS (
             SELECT 1 FROM fact_snapshot s
             WHERE s.item_id = product.item_id AND s.platform = product.platform
           )"""
    )


def _rm_raw_if_unused(batch: str) -> None:
    """批次 Raw 文件只在没有任何任务引用该批次时删除。"""
    conn = db.connect()
    try:
        remain = conn.execute(
            "SELECT 1 FROM fact_snapshot WHERE batch=? LIMIT 1", (batch,)
        ).fetchone()
    finally:
        conn.close()
    if remain:
        return
    target = RAW_DIR / batch
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


@router.get("/tasks")
def tasks():
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT task_id, name, keywords, pages, detail_top FROM dim_task ORDER BY created_at DESC"
        ).fetchall()
        out = []
        for r in rows:
            batches = [
                b["batch"]
                for b in conn.execute(
                    "SELECT DISTINCT batch FROM fact_snapshot WHERE task_id=? ORDER BY batch DESC",
                    (r["task_id"],),
                )
            ]
            out.append({**dict(r), "batches": batches})
        return out
    finally:
        conn.close()


@router.get("/products")
def products(
    task_id: str = Query(...),
    batch: str = Query(...),
    only_detail: int = Query(default=1),
    proxy_images: int = Query(default=1),
):
    conn = db.connect()
    try:
        where = "s.task_id=? AND s.batch=?"
        if only_detail:
            where += " AND s.is_detail=1"
        rows = conn.execute(
            f"""SELECT s.item_id, s.platform,
                       COALESCE(p.title, s.title) AS title,
                       COALESCE(p.image_url, s.image_url) AS image_url,
                       s.price_final, s.price_text, s.sold_text, s.sold_lb,
                       s.cart_text, s.cart_lb,
                       COALESCE(p.shop_name, s.shop_name) AS shop_name,
                       p.shop_rating, p.positive_rate,
                       s.rank, s.captured_at,
                       p.product_url, p.params_json, p.sku_names
               FROM fact_snapshot s
               LEFT JOIN product p
                 ON p.item_id = s.item_id AND p.platform = s.platform
               WHERE {where}
               ORDER BY COALESCE(s.sold_lb, -1) DESC, s.rank ASC""",
            (task_id, batch),
        ).fetchall()
    finally:
        conn.close()

    out = []
    for r in rows:
        skus = _loads(r["sku_names"], [])
        price_final = r["price_final"]
        if price_final is None:
            # 未采详情的候选：用列表页展示价兜底（口径：列表展示价）
            price_final = parse_price(r["price_text"] or "")
        out.append(
            {
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
            }
        )
    return out


@router.get("/products/{item_id}")
def product(item_id: str, platform: str = Query(default="tmall")):
    conn = db.connect()
    try:
        p = conn.execute(
            "SELECT * FROM product WHERE platform=? AND item_id=?", (platform, item_id)
        ).fetchone()
        hist = conn.execute(
            """SELECT batch, price_final, sold_lb, cart_lb, captured_at
               FROM fact_snapshot WHERE platform=? AND item_id=? ORDER BY batch""",
            (platform, item_id),
        ).fetchall()
    finally:
        conn.close()
    if not p:
        raise HTTPException(status_code=404, detail="商品不存在")
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
        "history": [dict(h) for h in hist],
    }


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str):
    """删除品类及其全部快照/批次记录（商品行仅清理孤儿）。"""
    conn = db.connect()
    try:
        if not conn.execute("SELECT 1 FROM dim_task WHERE task_id=?", (task_id,)).fetchone():
            raise HTTPException(status_code=404, detail="品类不存在")
        batches = [
            r["batch"]
            for r in conn.execute(
                "SELECT DISTINCT batch FROM fact_snapshot WHERE task_id=?", (task_id,)
            )
        ]
        conn.execute("DELETE FROM fact_snapshot WHERE task_id=?", (task_id,))
        conn.execute("DELETE FROM batch_run WHERE task_id=?", (task_id,))
        conn.execute("DELETE FROM dim_task WHERE task_id=?", (task_id,))
        _cleanup_orphan_products(conn)
        conn.commit()
    finally:
        conn.close()
    for batch in batches:
        _rm_raw_if_unused(batch)
    return {"ok": True}


@router.delete("/batches")
def delete_batch(task_id: str, batch: str):
    """删除某个品类的单个批次。"""
    conn = db.connect()
    try:
        cur = conn.execute(
            "DELETE FROM fact_snapshot WHERE task_id=? AND batch=?", (task_id, batch)
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="批次不存在")
        conn.execute(
            "DELETE FROM batch_run WHERE task_id=? AND batch=?", (task_id, batch)
        )
        _cleanup_orphan_products(conn)
        conn.commit()
    finally:
        conn.close()
    _rm_raw_if_unused(batch)
    return {"ok": True}


@router.get("/image")
def image_proxy(url: str = Query(...)):
    """本地图片代理：1688 / 拼多多 CDN 防盗链。"""
    if not is_allowed_image_url(url):
        raise HTTPException(status_code=400, detail="不允许的图片地址")
    try:
        import requests

        resp = requests.get(
            url,
            headers={
                "Referer": image_referer(url),
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
            },
            timeout=20,
            stream=True,
        )
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "image/jpeg")
        return StreamingResponse(
            resp.iter_content(64 * 1024),
            media_type=ctype,
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"图片代理失败：{exc}") from exc


@router.get("/export")
def export(task_id: str, batch: str, only_detail: int = 1):
    rows = products(task_id=task_id, batch=batch, only_detail=only_detail, proxy_images=0)
    buf = io.StringIO()
    writer = csv.writer(buf)
    _write_header(writer)
    for r in rows:
        _write_row(writer, r)
    content = "﻿" + buf.getvalue()
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT platform FROM dim_task WHERE task_id=?", (task_id,)
        ).fetchone()
        keyword = _keyword_for(conn, task_id)
    finally:
        conn.close()
    if row and row["platform"]:
        platform = row["platform"]
    elif rows:
        platform = rows[0]["platform"]
    else:
        platform = "tmall"
    label = PLATFORM_LABELS.get(platform, platform or "unknown")
    filename = f"{_safe_filename(keyword)}_({label})_{batch}.csv"
    return _csv_response(content, filename)


def _write_header(writer) -> None:
    writer.writerow(
        ["平台", "item_id", "标题", "到手价", "已售", "加购", "店铺", "评分", "好评率",
         "SKU", "参数", "主图", "链接", "采集时间"]
    )


def _write_row(writer, r: dict) -> None:
    writer.writerow(
        [
            r["platform"], r["item_id"], r["title"], r["price_final"],
            r["sold_text"], r["cart_text"],
            r["shop_name"], r["shop_rating"], r["positive_rate"],
            " / ".join(r["skus"]), json.dumps(r["params"], ensure_ascii=False),
            r["image_url"], r["product_url"], r["captured_at"],
        ]
    )


def _csv_response(content: str, filename: str) -> StreamingResponse:
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/report")
def report(batch: str, only_detail: int = 0):
    """一次搜索的跨平台合并报告：同一批次的各平台商品汇总为一个 CSV。"""
    conn = db.connect()
    try:
        tasks = _report_tasks(conn, batch)
        if not tasks:
            raise HTTPException(status_code=404, detail="该批次没有可导出的平台数据")
        keyword = _keyword_for(conn, tasks[0]["task_id"])
    finally:
        conn.close()

    rows: list[dict] = []
    for t in tasks:
        rows.extend(
            products(task_id=t["task_id"], batch=batch, only_detail=only_detail, proxy_images=0)
        )
    if not rows:
        raise HTTPException(status_code=404, detail="该批次没有商品数据（可能仍在采集中）")

    buf = io.StringIO()
    writer = csv.writer(buf)
    _write_header(writer)
    for r in rows:
        _write_row(writer, r)
    content = "﻿" + buf.getvalue()

    labels = [PLATFORM_LABELS.get(t["platform"], t["platform"]) for t in tasks]
    filename = f"{_safe_filename(keyword)}_({'+'.join(labels)})_{batch}.csv"
    return _csv_response(content, filename)


@router.get("/report/meta")
def report_meta(batch: str):
    """给前端展示用：本次报告包含哪些平台、各多少条数据。"""
    conn = db.connect()
    try:
        tasks = _report_tasks(conn, batch)
        if not tasks:
            raise HTTPException(status_code=404, detail="该批次没有可导出的平台数据")
        out = []
        for t in tasks:
            c = conn.execute(
                """SELECT COUNT(*) AS n,
                          SUM(CASE WHEN is_detail=1 THEN 1 ELSE 0 END) AS details
                   FROM fact_snapshot WHERE task_id=? AND batch=?""",
                (t["task_id"], batch),
            ).fetchone()
            out.append({
                "task_id": t["task_id"],
                "platform": t["platform"],
                "label": PLATFORM_LABELS.get(t["platform"], t["platform"]),
                "status": t["status"],
                "items": int(c["n"] or 0),
                "details": int(c["details"] or 0),
            })
        return {
            "batch": batch,
            "keyword": _keyword_for(conn, tasks[0]["task_id"]),
            "platforms": out,
        }
    finally:
        conn.close()