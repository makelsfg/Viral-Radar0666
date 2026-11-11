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
from collections import Counter

_SPLIT_VALUES = re.compile(r"[,，、/;；|]+")

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
            if p <= 0:  # 列表价缺失会被上游写成 0，不能算作 <¥50 的真实价格
                continue
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
    titles: list[str], features: list[str] | None = None, top: int = 15
) -> list[dict]:
    """按「命中商品数」统计词表命中（不再用字面出现次数，避免长标题偏差）。"""
    rows = [{"title": t} for t in titles]
    stats = selling_point_stats(rows, features=features, param_words=None)
    return [
        {"word": it["word"], "count": it["items"], "items": it["items"],
         "coverage": it["coverage"]}
        for it in stats["title"][:top]
    ]


def param_profile(rows: list[dict], max_keys: int = 6, max_values: int = 5) -> list[dict]:
    total = sum(1 for r in rows if r.get("params"))
    if not total:
        return []
    key_counts: Counter = Counter()
    values: dict[str, Counter] = {}
    for r in rows:
        params = r.get("params") or {}
        if not isinstance(params, dict):
            continue
        for k, v in params.items():
            if not k or v in (None, ""):
                continue
            key_counts[str(k)] += 1
            values.setdefault(str(k), Counter())[str(v)] += 1
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


def _word_stats(rows: list[dict], word: str, total: int) -> dict:
    hits = [r for r in rows if word in (r.get("title") or "")]
    prices, solds = [], []
    for r in hits:
        try:
            price = float(r.get("price_final"))
        except (TypeError, ValueError):
            price = 0.0
        if price > 0:
            prices.append(price)
        if r.get("sold_lb") is not None:
            solds.append(int(r["sold_lb"]))
    return {
        "items": len(hits),
        "coverage": round(len(hits) / total, 3) if total else 0.0,
        "avg_price": round(sum(prices) / len(prices), 2) if prices else None,
        "avg_sold": round(sum(solds) / len(solds), 1) if solds else None,
        "sold_total": sum(solds) if solds else 0,
    }


def _param_stats(rows: list[dict], total: int, max_pairs: int) -> list[dict]:
    agg: dict[tuple[str, str], dict] = {}
    for r in rows:
        params = r.get("params") or {}
        if not isinstance(params, dict):
            continue
        seen: set[tuple[str, str]] = set()
        for k, v in params.items():
            key = str(k or "").strip()
            if not key or v in (None, ""):
                continue
            for part in _SPLIT_VALUES.split(str(v)):
                value = part.strip()
                if len(value) < 2 or len(value) > 20:
                    continue
                if (key, value) in seen:
                    continue
                seen.add((key, value))
                g = agg.setdefault(
                    (key, value),
                    {"key": key, "value": value, "items": 0, "prices": [], "solds": []},
                )
                g["items"] += 1
                try:
                    price = float(r.get("price_final"))
                except (TypeError, ValueError):
                    price = 0.0
                if price > 0:
                    g["prices"].append(price)
                if r.get("sold_lb") is not None:
                    g["solds"].append(int(r["sold_lb"]))

    out = []
    for g in agg.values():
        if g["items"] < 1:
            continue
        out.append({
            "key": g["key"],
            "value": g["value"],
            "items": g["items"],
            "coverage": round(g["items"] / total, 3) if total else 0.0,
            "avg_price": round(sum(g["prices"]) / len(g["prices"]), 2) if g["prices"] else None,
            "avg_sold": round(sum(g["solds"]) / len(g["solds"]), 1) if g["solds"] else None,
            "sold_total": sum(g["solds"]) if g["solds"] else 0,
        })
    out.sort(key=lambda x: (x["items"], x["sold_total"]), reverse=True)
    return out[:max_pairs]


def selling_point_stats(
    rows: list[dict],
    features: list[str] | None = None,
    param_words: list[str] | None = None,
    max_words: int = 30,
    max_params: int = 20,
) -> dict:
    """卖点词统计：标题词（人工/LLM 词表 + 参数值）+ 参数 key=value 明细。

    只统计「至少命中 1 个商品」的词，避免词表里的死词稀释结果。
    """
    total = len(rows)
    profile_words = [str(w).strip() for w in (features or []) if str(w or "").strip()]
    value_words = [str(w).strip() for w in (param_words or []) if str(w or "").strip()]
    value_set = set(value_words)
    profile_set = set(profile_words)

    merged: list[str] = []
    seen: set[str] = set()
    for w in profile_words + value_words:
        if w not in seen:
            seen.add(w)
            merged.append(w)

    title_items = []
    for w in merged:
        st = _word_stats(rows, w, total)
        if st["items"] == 0:
            continue
        title_items.append({
            "word": w,
            "source": "param" if w in value_set and w not in profile_set else "title",
            **st,
        })
    title_items.sort(key=lambda x: (x["items"], x["sold_total"]), reverse=True)

    covered = 0
    if merged:
        covered = sum(
            1 for r in rows
            if any(w in (r.get("title") or "") for w in merged)
        )
    matched = [w for w in merged if any(w in (r.get("title") or "") for r in rows)]
    return {
        "words": len(matched),      # 实际命中标题的词数（未命中的不进结果）
        "candidates": len(merged),  # 词表候选总数
        "coverage": round(covered / total, 3) if total else 0.0,
        "title": title_items[:max_words],
        "params": _param_stats(rows, total, max_params),
    }


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

    from .profile_llm import param_keys as _param_keys
    from .profile_llm import param_values as _param_values

    # 参数 key 与取值（材质/功能/充绒量等）本身也是卖点词，直接并入词表，不依赖 LLM
    points = selling_point_stats(
        rows,
        features=profile.get("features") or [],
        param_words=_param_values(rows) + _param_keys(rows),
    )
    return {
        "profile": {
            "name": profile.get("name"),
            "source": profile.get("source") or "none",
            "words": points["words"],
            "coverage": points["coverage"],
        },
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
        "keywords": [
            {
                "word": it["word"],
                "count": it["items"],
                "items": it["items"],
                "coverage": it["coverage"],
                "avg_price": it["avg_price"],
                "avg_sold": it["avg_sold"],
            }
            for it in points["title"][:15]
        ],
        "params": param_profile(rows),
        "selling_points": points,
        "top_products": top_products(rows),
    }