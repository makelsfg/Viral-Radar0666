"""拼多多官方开放平台（多多进宝）商品搜索适配。

配置（config/settings.yaml）：
  pdd:
    client_id: "你的 client_id"
    client_secret: "你的 client_secret"
    pid: "推广位ID（可选）"

也可用环境变量 BK_PDD_CLIENT_ID / BK_PDD_CLIENT_SECRET / BK_PDD_PID。
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Any

import requests

from ..parse import parse_count

API_URL = "https://gw-api.pinduoduo.com/api/router"
_TIMEOUT = 20
_SOLD_RE = re.compile(r"([\d.]+[万wW千kK]?\+?)")


def _cred(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def configured() -> bool:
    return bool(_cred("BK_PDD_CLIENT_ID") and _cred("BK_PDD_CLIENT_SECRET"))


def config_hint() -> str:
    return (
        "未配置拼多多官方 API：请在 config/settings.yaml 的 pdd 段填写 "
        "client_id / client_secret（可选 pid），或在启动前设置 "
        "BK_PDD_CLIENT_ID / BK_PDD_CLIENT_SECRET 环境变量。"
    )


def _sign(params: dict[str, Any], secret: str) -> str:
    raw = secret + "".join(f"{k}{params[k]}" for k in sorted(params)) + secret
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()


def _call(api_type: str, **params: Any) -> dict:
    if not configured():
        raise RuntimeError(config_hint())
    cid = _cred("BK_PDD_CLIENT_ID")
    secret = _cred("BK_PDD_CLIENT_SECRET")
    payload: dict[str, Any] = {
        "type": api_type,
        "client_id": cid,
        "timestamp": str(int(time.time())),
        "data_type": "JSON",
        "version": "V1",
    }
    pid = _cred("BK_PDD_PID")
    if pid:
        payload["pid"] = pid
    payload.update({k: v for k, v in params.items() if v not in (None, "")})
    payload["sign"] = _sign(payload, secret)
    try:
        resp = requests.post(API_URL, data=payload, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"拼多多 API 请求失败：{type(exc).__name__}: {exc}") from exc
    if isinstance(data, dict) and data.get("error_response"):
        err = data["error_response"]
        code = err.get("error_code") or err.get("sub_code") or ""
        msg = err.get("error_msg") or err.get("sub_msg") or err
        raise RuntimeError(f"拼多多 API 返回错误 {code}：{msg}")
    return data if isinstance(data, dict) else {}


def _parse_sold(goods: dict) -> tuple[str | None, int | None]:
    tip = str(goods.get("sales_tip") or "").strip()
    qty = goods.get("sold_quantity")
    if isinstance(qty, (int, float)) and qty >= 0:
        n = int(qty)
        return tip or f"已拼{n}件", n
    m = _SOLD_RE.search(tip)
    if m:
        return tip, parse_count(m.group(1))
    return (tip or None), None


def _price_yuan(cents) -> float | None:
    try:
        return round(float(cents) / 100.0, 2)
    except (TypeError, ValueError):
        return None


def map_card(goods: dict, task) -> dict | None:
    iid = str(goods.get("goods_id") or "").strip()
    if not iid:
        return None
    title = str(goods.get("goods_name") or "").strip()
    text = title + "\n" + str(goods.get("mall_name") or "")
    for w in getattr(task, "exclude_words", []) or []:
        if w and w in text:
            return None
    price = _price_yuan(goods.get("min_group_price") or goods.get("min_normal_price"))
    if price is not None:
        if task.price_min is not None and price < task.price_min:
            return None
        if task.price_max is not None and price > task.price_max:
            return None
    sold_text, sold_lb = _parse_sold(goods)
    image = (
        goods.get("goods_image_url")
        or goods.get("goods_thumbnail_url")
        or ""
    )
    return {
        "item_id": iid,
        "title": title,
        "price_text": f"¥{price:.2f}" if price is not None else None,
        "price_final": price,
        "sold_text": sold_text,
        "sold_lb": sold_lb,
        "shop_name": str(goods.get("mall_name") or "") or None,
        "image_url": image,
        "url": f"https://mobile.yangkeduo.com/goods.html?goods_id={iid}",
        "goods_sign": goods.get("goods_sign"),
    }


def search_goods(keyword: str, page: int = 1, page_size: int = 50,
                 sort_type: int = 1) -> list[dict]:
    """多多进宝商品搜索。sort_type=1 为销量排序（接口不保证时调用方仍会本地排序）。"""
    data = _call(
        "pdd.ddk.goods.search",
        keyword=keyword,
        page=max(1, int(page)),
        page_size=max(1, min(100, int(page_size))),
        sort_type=sort_type,
    )
    body = data.get("goods_search_response") or {}
    goods = body.get("goods_list")
    return goods if isinstance(goods, list) else []


def goods_detail(goods_sign: str) -> dict | None:
    if not goods_sign:
        return None
    data = _call("pdd.ddk.goods.detail", goods_sign=goods_sign)
    body = data.get("goods_detail_response") or {}
    details = body.get("goods_details")
    if not isinstance(details, list) or not details:
        return None
    return details[0]


def map_detail(detail: dict, item_id: str | None = None) -> dict:
    """把详情接口返回映射成采集层需要的字段（没有 SKU/参数时留空）。"""
    iid = str(item_id or detail.get("goods_id") or "")
    sold_text, sold_lb = _parse_sold(detail)
    price = _price_yuan(detail.get("min_group_price") or detail.get("min_normal_price"))
    gallery = detail.get("top_gallery_urls") or []
    image = detail.get("goods_image_url") or (gallery[0] if gallery else "")
    params = {}
    if detail.get("cat_id") is not None:
        params["cat_id"] = str(detail.get("cat_id"))
    return {
        "title": str(detail.get("goods_name") or "").strip(),
        "price_final": price,
        "sold_text": sold_text,
        "sold_lb": sold_lb,
        "cart_text": None,
        "cart_lb": None,
        "shop_name": str(detail.get("mall_name") or "") or None,
        "shop_rating": None,
        "positive_rate": None,
        "sku_names": [],
        "params": params,
        "image_url": image,
        "video_url": "",
        "product_url": f"https://mobile.yangkeduo.com/goods.html?goods_id={iid}",
    }