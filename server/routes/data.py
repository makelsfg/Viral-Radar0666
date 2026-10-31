"""数据只读接口：任务、批次、商品列表、商品详情、CSV 导出。"""
from __future__ import annotations

import csv
import io
import json
import shutil
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from bkr import db
from bkr.config import RAW_DIR
from bkr.parse import parse_price

router = APIRouter()


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
                "image_url": r["image_url"],
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
                "product_url": r["product_url"] or f"https://detail.tmall.com/item.htm?id={r['item_id']}",
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
        "image_url": p["image_url"],
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


@router.get("/export")
def export(task_id: str, batch: str, only_detail: int = 1):
    rows = products(task_id=task_id, batch=batch, only_detail=only_detail)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["平台", "item_id", "标题", "到手价", "已售", "加购", "店铺", "评分", "好评率",
         "SKU", "参数", "主图", "链接", "采集时间"]
    )
    for r in rows:
        writer.writerow(
            [
                r["platform"], r["item_id"], r["title"], r["price_final"],
                r["sold_text"], r["cart_text"],
                r["shop_name"], r["shop_rating"], r["positive_rate"],
                " / ".join(r["skus"]), json.dumps(r["params"], ensure_ascii=False),
                r["image_url"], r["product_url"], r["captured_at"],
            ]
        )
    content = "﻿" + buf.getvalue()
    filename = f"{task_id}_{batch}.csv"
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )