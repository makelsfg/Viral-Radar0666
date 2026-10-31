"""分析接口：批次概览、卖点组合、LLM 解读。"""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from bkr import db
from bkr.analysis import build_summary
from bkr.analysis_profile import profile_for
from bkr.parse import parse_price
from ..utils import fallback_product_url, proxied_image

router = APIRouter()


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _task_name(task_id: str) -> str:
    conn = db.connect()
    try:
        row = conn.execute("SELECT name FROM dim_task WHERE task_id=?", (task_id,)).fetchone()
    finally:
        conn.close()
    return row["name"] if row else ""


def _rows(task_id: str, batch: str) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT s.item_id, s.platform,
                      COALESCE(p.title, s.title) AS title,
                      s.price_final, s.price_text, s.sold_lb, s.sold_text, s.cart_lb,
                      COALESCE(p.shop_name, s.shop_name) AS shop_name,
                      COALESCE(p.image_url, s.image_url) AS image_url,
                      p.product_url, p.params_json, p.sku_names, s.is_detail
               FROM fact_snapshot s
               LEFT JOIN product p
                 ON p.item_id = s.item_id AND p.platform = s.platform
               WHERE s.task_id=? AND s.batch=?""",
            (task_id, batch),
        ).fetchall()
    finally:
        conn.close()

    out = []
    for r in rows:
        price = r["price_final"]
        if price is not None:
            price_source = "detail"
        else:
            price = parse_price(r["price_text"] or "")
            price_source = "list" if price is not None else None
        out.append(
            {
                "item_id": r["item_id"],
                "platform": r["platform"],
                "title": r["title"],
                "price_final": price,
                "price_source": price_source,
                "sold_lb": r["sold_lb"],
                "sold_text": r["sold_text"],
                "cart_lb": r["cart_lb"],
                "shop_name": r["shop_name"],
                "image_url": proxied_image(r["platform"], r["image_url"]),
                "product_url": r["product_url"]
                or fallback_product_url(r["platform"], r["item_id"]),
                "params": _loads(r["params_json"], {}),
                "skus": _loads(r["sku_names"], []),
                "is_detail": r["is_detail"],
            }
        )
    return out


@router.get("/analysis/summary")
def summary(task_id: str = Query(...), batch: str = Query(...)):
    rows = _rows(task_id, batch)
    if not rows:
        raise HTTPException(status_code=404, detail="该批次没有数据")
    return build_summary(rows, profile_for(_task_name(task_id)))


class InsightsRequest(BaseModel):
    task_id: str
    batch: str


def _llm_config() -> tuple[str, str, str] | None:
    base = os.environ.get("BK_LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    key = os.environ.get("BK_LLM_API_KEY", "").strip()
    model = os.environ.get("BK_LLM_MODEL", "deepseek-chat").strip()
    if not key:
        return None
    return base, key, model


@router.get("/analysis/llm-status")
def llm_status():
    cfg = _llm_config()
    return {"configured": bool(cfg), "model": cfg[2] if cfg else None}


@router.post("/analysis/insights")
def insights(req: InsightsRequest):
    cfg = _llm_config()
    if not cfg:
        raise HTTPException(
            status_code=400,
            detail="未配置 LLM：请设置 BK_LLM_API_KEY（可选 BK_LLM_BASE_URL / BK_LLM_MODEL）后重启服务",
        )
    rows = _rows(req.task_id, req.batch)
    if not rows:
        raise HTTPException(status_code=404, detail="该批次没有数据")

    summary_data = build_summary(rows, profile_for(_task_name(req.task_id)))
    payload = {
        "counts": summary_data["counts"],
        "price": summary_data["price"]["stats"],
        "price_bands": summary_data["price"]["bands"],
        "sold_bands": summary_data["sold"]["bands"],
        "shops": summary_data["shops"],
        "keywords": summary_data["keywords"],
        "params": summary_data["params"],
        "clusters": summary_data["clusters"][:8],
        "top_products": [
            {k: p[k] for k in ("title", "price_final", "sold_lb", "shop_name")}
            for p in summary_data["top_products"]
        ],
    }
    prompt = (
        "你是电商选品分析师。以下是从天猫搜索结果与详情页采集到的某品类一个批次的真实数据（JSON）。\n"
        "数据口径：已售/加购为页面区间文本的下限，不是精确销量；价格是到手价或列表展示价；"
        "详情字段只覆盖部分商品；样本量有限，可能存在个性化排序偏差。\n\n"
        f"数据：{json.dumps(payload, ensure_ascii=False)}\n\n"
        "请输出五节 Markdown：\n"
        "1. 大盘概览（样本、价格结构、销量结构，150 字内）\n"
        "2. 卖点聚类（哪些卖点组合在走量、对应价格带，给出 3–5 条）\n"
        "3. 供给与竞争（店铺集中度、价格带竞争密度）\n"
        "4. 风险与数据边界（样本偏差、区间销量、覆盖范围）\n"
        "5. 选品与内容建议（3 条可行动建议）\n"
        "硬性约束：只允许引用上面 JSON 中的数字与商品；不要编造未提供的销量、GMV、增长率；"
        "不确定时直说样本不足。"
    )

    try:
        import requests

        resp = requests.post(
            f"{cfg[0]}/chat/completions",
            headers={"Authorization": f"Bearer {cfg[1]}", "Content-Type": "application/json"},
            json={
                "model": cfg[2],
                "messages": [
                    {"role": "system", "content": "你是严谨的电商数据分析师，只基于给定数据作答。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.4,
            },
            timeout=90,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"LLM 调用失败：{exc}") from exc

    return {"text": text, "model": cfg[2]}