"""详情补充通道：Apify 托管的 Taobao/Tmall Scraper（unitbytes）。

仅用于补充详情字段（SKU/参数/DSR/图集）；列表仍由本地浏览器采集。
需要 APIFY token；按量计费（参考：全详情约 $0.004/条，6 条/天约 $0.7/月）。
"""
from __future__ import annotations

import time

ACTOR_DEFAULT = "unitbytes~taobao-tmall-scraper"
BASE_URL = "https://api.apify.com/v2"


def fetch_details(
    item_ids: list[str],
    token: str,
    actor: str = ACTOR_DEFAULT,
    timeout: int = 300,
    poll: float = 5.0,
) -> list[dict]:
    import requests

    urls = [f"https://detail.tmall.com/item.htm?id={i}" for i in item_ids]
    resp = requests.post(
        f"{BASE_URL}/acts/{actor}/runs",
        params={"token": token},
        json={"itemUrls": urls, "scrapeDetails": True, "maxItems": len(urls)},
        timeout=60,
    )
    resp.raise_for_status()
    run = resp.json().get("data") or {}
    run_id = run.get("id")
    dataset_id = run.get("defaultDatasetId")
    if not run_id:
        raise RuntimeError("Apify 未返回任务 ID")

    deadline = time.time() + timeout
    while time.time() < deadline:
        info = requests.get(
            f"{BASE_URL}/actor-runs/{run_id}", params={"token": token}, timeout=30
        ).json().get("data") or {}
        status = info.get("status")
        if status == "SUCCEEDED":
            dataset_id = info.get("defaultDatasetId") or dataset_id
            break
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify 任务状态：{status}")
        time.sleep(poll)
    else:
        raise RuntimeError(f"Apify 任务超时（>{timeout}s）")

    if not dataset_id:
        raise RuntimeError("Apify 未返回数据集 ID")
    items = requests.get(
        f"{BASE_URL}/datasets/{dataset_id}/items",
        params={"token": token, "clean": "true", "format": "json"},
        timeout=60,
    )
    items.raise_for_status()
    return items.json()


def map_to_product(item: dict) -> dict:
    """把 Apify 返回结构映射为本地 product 字段。"""
    price = item.get("promotionPrice") or item.get("price")
    try:
        price_val = float(price) if price not in (None, "") else None
    except (TypeError, ValueError):
        price_val = None

    skus = [
        s.get("name")
        for s in (item.get("skus") or [])
        if isinstance(s, dict) and s.get("name")
    ]
    images = item.get("images") or []
    attrs = item.get("attributes") or {}
    return {
        "item_id": str(item.get("itemId") or ""),
        "title": item.get("title") or "",
        "price_final": price_val,
        "shop_name": item.get("shopName"),
        "shop_rating": item.get("dsrDescription"),
        "positive_rate": None,
        "sku_names": skus[:40],
        "params": attrs if isinstance(attrs, dict) else {},
        "image_url": item.get("mainImage") or (images[0] if images else None),
        "video_url": None,
        "product_url": item.get("taobaoUrl") or item.get("url"),
        "review_count": item.get("reviewCount"),
    }