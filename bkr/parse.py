"""页面文本解析：价格、销量/加购区间、商品 ID。

全部为纯函数，便于用样本文本做单元测试（不依赖网络/浏览器）。
"""
from __future__ import annotations

import re
from typing import Optional

_ITEM_ID_RE = re.compile(r"[?&]id=(\d+)")
_PRICE_RE = re.compile(r"[¥￥]\s*(\d+(?:\.\d+)?)")
_COUPON_RE = re.compile(r"券后(?:价)?\s*[¥￥]?\s*(\d+(?:\.\d+)?)")
_TO_HAND_RE = re.compile(r"到手价\s*[¥￥]?\s*(\d+(?:\.\d+)?)")
_PRICE_TO_HAND_RE = re.compile(r"[¥￥]\s*(\d+(?:\.\d+)?)\s*(?:补贴)?到手价")
_REDUCE_RE = re.compile(r"立减\s*(\d+(?:\.\d+)?)\s*元")
_COUNT = r"(\d+(?:\.\d+)?)\s*([万wW千kK]?)"
_UNITS = {"万": 10000, "w": 10000, "W": 10000, "千": 1000, "k": 1000, "K": 1000}

_SOLD_AFTER = ["人付款", "人已付款", "人收货", "人购买", "人已买"]
_SOLD_BEFORE = ["已售", "月销", "已拼", "销量"]
_CART_AFTER = ["人加购", "人已加购", "人收藏"]
_CART_BEFORE = ["加购"]

_SHOP_RE = re.compile(r"([一-龥A-Za-z0-9·]{2,20}(?:旗舰店|专卖店|专营店|官方店|企业店))")
_RATE_RE = re.compile(r"好评率\s*[:：]?\s*(\d+(?:\.\d+)?)\s*%")
_RATING_RE = re.compile(r"(?:评分|天猫)\D{0,12}?([0-5]\.\d)")


def extract_item_id(url: str) -> Optional[str]:
    """从商品链接中提取 item_id。"""
    if not url:
        return None
    m = _ITEM_ID_RE.search(url)
    return m.group(1) if m else None


def canonical_url(item_id: str) -> str:
    return f"https://detail.tmall.com/item.htm?id={item_id}"


_SEARCH_MATCH_KEYS = ("q", "keywords", "page", "beginPage", "tab", "sort", "sortType")


def same_search_url(a: str, b: str) -> bool:
    """宽松比较两个搜索页 URL：同域名 + 目标 URL 里出现的每个关键参数都一致。"""
    from urllib.parse import parse_qs, urlparse

    try:
        ua, ub = urlparse(a or ""), urlparse(b or "")
    except ValueError:
        return False
    if not ua.netloc or ua.netloc != ub.netloc:
        return False
    qa, qb = parse_qs(ua.query), parse_qs(ub.query)
    return all(qa.get(k) == qb.get(k) for k in _SEARCH_MATCH_KEYS if k in qb)


def parse_count(text) -> Optional[int]:
    """"500+" / "2万+" / "1.2万" / "600" -> 下限整数。"""
    if text is None:
        return None
    s = str(text).replace(",", "").replace("，", "")
    m = re.search(_COUNT, s)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2)
    if unit:
        value *= _UNITS.get(unit, 1)
    return int(round(value))


def parse_price(text) -> Optional[float]:
    """取文本中第一个 ¥/￥ 后的价格。"""
    if not text:
        return None
    m = _PRICE_RE.search(str(text))
    return float(m.group(1)) if m else None


def _match_after(t: str, words: list[str]):
    for w in words:
        m = re.search(_COUNT + r"\+?\s*" + re.escape(w), t)
        if m:
            return m.group(0), parse_count(m.group(0))
    return None, None


def _match_before(t: str, words: list[str]):
    for w in words:
        m = re.search(re.escape(w) + r"\s*" + _COUNT + r"\+?", t)
        if m:
            return m.group(0), parse_count(m.group(0))
    return None, None


def parse_sold_text(text) -> tuple[Optional[str], Optional[int]]:
    """"已售 500+" / "500+人付款" -> (原始片段, 下限)。"""
    if not text:
        return None, None
    t = str(text)
    frag, val = _match_after(t, _SOLD_AFTER)
    if val is not None:
        return frag, val
    return _match_before(t, _SOLD_BEFORE)


def parse_cart_text(text) -> tuple[Optional[str], Optional[int]]:
    """"超600人加购" -> (原始片段, 下限)。"""
    if not text:
        return None, None
    t = str(text)
    frag, val = _match_after(t, _CART_AFTER)
    if val is not None:
        return frag, val
    return _match_before(t, _CART_BEFORE)


def compute_final_price(price_block: str, promo_text: str = "") -> Optional[float]:
    """计算到手价。

    规则（优先级从高到低）：
    1) 页面明示"到手价 ¥x"（含补贴到手价）-> 直接使用；
    2) "券后价 ¥x" -> 使用券后价；
    3) 展示价 − 所有"立减 x 元"明细。
    只返回一个到手价字段；原始价格文本另行留档。
    """
    block = price_block or ""
    promo = promo_text or ""
    text = block + "\n" + promo

    m = _PRICE_TO_HAND_RE.search(text) or _TO_HAND_RE.search(text)
    if m:
        return round(float(m.group(1)), 2)

    m = _COUPON_RE.search(text)
    if m:
        return round(float(m.group(1)), 2)

    price = parse_price(block)
    if price is None:
        return None

    reductions = [float(x) for x in _REDUCE_RE.findall(text)]
    final = price - sum(reductions)
    if final <= 0:
        return round(price, 2)
    return round(final, 2)


def parse_shop_name(text) -> Optional[str]:
    if not text:
        return None
    m = _SHOP_RE.search(str(text))
    return m.group(1) if m else None


def parse_positive_rate(text) -> Optional[str]:
    """"88VIP好评率96%" -> "96%"。"""
    if not text:
        return None
    m = _RATE_RE.search(str(text))
    return f"{m.group(1)}%" if m else None


def parse_shop_rating(text) -> Optional[float]:
    """"天猫 4.8" -> 4.8。失败返回 None。"""
    if not text:
        return None
    m = _RATING_RE.search(str(text))
    return float(m.group(1)) if m else None