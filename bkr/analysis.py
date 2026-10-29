"""分析计算：批次概览、分布、店铺、关键词、参数画像、卖点组合、Top 榜。

纯函数，输入为行字典列表，便于单元测试：
row = {
  "item_id", "title", "price_final", "price_source"("detail"/"list"/None),
  "sold_lb", "sold_text", "cart_lb", "shop_name", "image_url",
  "product_url", "params": dict, "skus": list, "is_detail": 0/1,
}
profile 可选：{"features": [...], "materials": [...], "capacity_enabled": bool}
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

CAPACITY_ML_RE = re.compile(r"(\d{3,4})\s*(?:ml|ML|毫升)")
CAPACITY_L_RE = re.compile(r"(\d(?:\.\d)?)\s*(?:L|升)(?![A-Za-z])")

DEFAULT_FEATURES = [
    "保温", "保冷", "显温", "吸管", "直饮", "大容量", "便携", "刻字", "儿童", "学生",
    "上学", "户外", "运动", "防风", "防水", "三合一", "加绒", "加厚", "羽绒", "棉服",
    "冲锋衣", "夹克", "手表", "钢带", "石英", "机械", "智能", "手环", "水杯", "保温杯",
    "礼盒", "密码锁", "夜光",
]
DEFAULT_FUNC_WORDS = [
    "保温", "保冷", "显温", "吸管", "直饮", "刻字", "大容量", "便携",
    "防水", "防风", "加绒", "智能",
]

# 固定价格带：跨批次可比，符合用户直觉
PRICE_BANDS = [
    ("<¥50", 0, 50),
    ("¥50–100", 50, 100),
    ("¥100–300", 100, 300),
    ("¥300–500", 300, 500),
    ("¥500–1000", 500, 1000),
    ("≥¥1000", 1000, None),
]


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    val = s[f] + (s[c] - s[f]) * (k - f)
    return round(val, 2)


def price_stats(prices: list[float]) -> dict:
    if not prices:
        return {"count": 0}
    return {
        "count": len(prices),
        "min": round(min(prices), 2),
        "p25": _pct(prices, 0.25),
        "median": _pct(prices, 0.5),
        "p75": _pct(prices, 0.75),
        "max": round(max(prices), 2),
        "avg": round(sum(prices) / len(prices), 2),
    }


def price_bands(rows: list[dict]) -> list[dict]:
    """固定价格带统计商品数与销量合计（跨批次可比）。"""
    out = []
    for label, lo, hi in PRICE_BANDS:
        count = 0
        sold = 0
        for r in rows:
            price = r.get("price_final")
            if price is None:
                continue
            p = float(price)
            if p >= lo and (hi is None or p < hi):
                count += 1
                sold += int(r.get("sold_lb") or 0)
        out.append({"label": label, "min": lo, "max": hi, "count": count, "sold": sold})
    return out


_SOLD_BANDS = [
    ("<100", 0, 100),
    ("100–500", 100, 500),
    ("500–1000", 500, 1000),
    ("1000–5000", 1000, 5000),
    ("5000–1万", 5000, 10000),
    ("≥1万", 10000, None),
]


def sold_bands(rows: list[dict]) -> list[dict]:
    values = [int(r["sold_lb"]) for r in rows if r.get("sold_lb") is not None]
    out = []
    for label, lo, hi in _SOLD_BANDS:
        count = sum(1 for v in values if v >= lo and (hi is None or v < hi))
        out.append({"label": label, "count": count})
    return out


def shop_concentration(rows: list[dict], top: int = 8) -> dict:
    agg: dict[str, dict] = {}
    total_sold = 0
    for r in rows:
        name = (r.get("shop_name") or "未知店铺").strip() or "未知店铺"
        sold = int(r.get("sold_lb") or 0)
        total_sold += sold
        g = agg.setdefault(name, {"name": name, "count": 0, "sold": 0})
        g["count"] += 1
        g["sold"] += sold
    shops = sorted(agg.values(), key=lambda x: (x["sold"], x["count"]), reverse=True)
    top5_sold = sum(s["sold"] for s in shops[:5])
    return {
        "shops": shops[:top],
        "distinct": len(shops),
        "top5_share": round(top5_sold / total_sold, 3) if total_sold else 0.0,
    }


def keyword_counts(
    titles: list[str], top: int = 15, features: list[str] | None = None
) -> list[dict]:
    text = " ".join(t or "" for t in titles)
    counter = Counter()
    for word in features or DEFAULT_FEATURES:
        c = text.count(word)
        if c:
            counter[word] = c
    return [{"word": w, "count": c} for w, c in counter.most_common(top)]


def param_profile(rows: list[dict], max_keys: int = 6, max_values: int = 5) -> list[dict]:
    total = sum(1 for r in rows if r.get("params"))
    if not total:
        return []
    key_counts: Counter = Counter()
    values: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        params = r.get("params") or {}
        if not isinstance(params, dict):
            continue
        for k, v in params.items():
            if not k or v in (None, ""):
                continue
            key_counts[str(k)] += 1
            values[str(k)][str(v)] += 1
    threshold = max(1, int(total * 0.4))
    out = []
    for key, count in key_counts.most_common():
        if count < threshold:
            continue
        out.append({
            "key": key,
            "coverage": round(count / total, 2),
            "top": [{"value": v, "count": c} for v, c in values[key].most_common(max_values)],
        })
        if len(out) >= max_keys:
            break
    return out


def _capacity_ml(row: dict) -> int | None:
    text = (row.get("title") or "") + " " + " ".join(row.get("skus") or [])
    m = CAPACITY_ML_RE.search(text)
    if m:
        return int(m.group(1))
    m = CAPACITY_L_RE.search(text)
    if m:
        return int(float(m.group(1)) * 1000)
    return None


def _capacity_label(ml: int) -> str:
    if ml < 400:
        return "≤400ml"
    if ml < 600:
        return "400–600ml"
    if ml < 1000:
        return "600–1000ml"
    return "≥1000ml"


def _price_band_label(price: float | None) -> str:
    if price is None:
        return "价格未知"
    for label, lo, hi in PRICE_BANDS:
        if float(price) >= lo and (hi is None or float(price) < hi):
            return label
    return "≥¥1000"


def _material(row: dict, materials: list[str] | None = None) -> str | None:
    text = (row.get("title") or "") + " " + " ".join(row.get("skus") or [])
    if materials:
        for kw in sorted(materials, key=len, reverse=True):
            if kw and kw in text:
                return kw
    m = re.search(r"(316L?|304)\s*不锈钢", text)
    if m:
        return "316不锈钢" if m.group(1).upper().startswith("316") else "304不锈钢"
    return None


def selling_point_clusters(
    rows: list[dict], top: int = 8, profile: dict | None = None
) -> list[dict]:
    profile = profile or {}
    func_words = profile.get("func_words") or DEFAULT_FUNC_WORDS
    materials = profile.get("materials") or []
    capacity_enabled = profile.get("capacity_enabled", True)

    groups: dict[str, dict] = {}
    for r in rows:
        title = r.get("title") or ""
        parts = [_price_band_label(r.get("price_final"))]
        if capacity_enabled:
            cap = _capacity_ml(r)
            if cap:
                parts.append(_capacity_label(cap))
        mat = _material(r, materials)
        if mat:
            parts.append(mat)
        funcs = [w for w in func_words if w in title][:2]
        parts.append("+".join(funcs) if funcs else "基础款")
        key = " · ".join(parts)

        g = groups.setdefault(key, {
            "cluster": key, "count": 0, "prices": [], "solds": [], "example": title,
        })
        g["count"] += 1
        if r.get("price_final"):
            g["prices"].append(float(r["price_final"]))
        if r.get("sold_lb") is not None:
            g["solds"].append(int(r["sold_lb"]))
        if len(title) > len(g["example"]):
            g["example"] = title

    out = []
    for g in groups.values():
        avg_price = round(sum(g["prices"]) / len(g["prices"]), 2) if g["prices"] else None
        avg_sold = round(sum(g["solds"]) / len(g["solds"]), 1) if g["solds"] else 0
        out.append({
            "cluster": g["cluster"],
            "count": g["count"],
            "avg_price": avg_price,
            "avg_sold": avg_sold,
            "example": g["example"][:80],
            "_rank": g["count"] * (avg_sold or 0),
        })
    out.sort(key=lambda x: (x["_rank"], x["count"]), reverse=True)
    for item in out:
        item.pop("_rank", None)
    return out[:top]


def top_products(rows: list[dict], n: int = 10) -> list[dict]:
    ranked = sorted(
        rows,
        key=lambda r: (int(r.get("sold_lb") or -1), int(r.get("cart_lb") or 0)),
        reverse=True,
    )
    out = []
    for r in ranked[:n]:
        out.append({
            "item_id": r.get("item_id"),
            "title": r.get("title"),
            "price_final": r.get("price_final"),
            "price_source": r.get("price_source"),
            "sold_text": r.get("sold_text"),
            "sold_lb": r.get("sold_lb"),
            "cart_lb": r.get("cart_lb"),
            "shop_name": r.get("shop_name"),
            "image_url": r.get("image_url"),
            "product_url": r.get("product_url"),
            "is_detail": r.get("is_detail", 0),
        })
    return out


def build_summary(rows: list[dict], profile: dict | None = None) -> dict:
    prices = [float(r["price_final"]) for r in rows if r.get("price_final")]
    solds = [int(r["sold_lb"]) for r in rows if r.get("sold_lb") is not None]
    profile = profile or {}
    detail_prices = sum(1 for r in rows if r.get("price_source") == "detail")
    list_prices = sum(1 for r in rows if r.get("price_source") == "list")
    return {
        "profile": profile.get("name"),
        "counts": {
            "candidates": len(rows),
            "detailed": sum(1 for r in rows if r.get("is_detail")),
            "with_price": len(prices),
            "with_price_detail": detail_prices,
            "with_price_list": list_prices,
            "with_sold": len(solds),
        },
        "price": {"stats": price_stats(prices), "bands": price_bands(rows)},
        "sold": {"bands": sold_bands(rows), "median": _pct(solds, 0.5)},
        "shops": shop_concentration(rows),
        "keywords": keyword_counts(
            [r.get("title") or "" for r in rows], features=profile.get("features")
        ),
        "params": param_profile(rows),
        "clusters": selling_point_clusters(rows, profile=profile),
        "top_products": top_products(rows),
    }