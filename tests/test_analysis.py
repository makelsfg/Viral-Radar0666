"""分析计算单测：构造样例行，验证统计口径。"""
from bkr.analysis import (
    build_summary,
    keyword_counts,
    param_profile,
    price_bands,
    price_stats,
    selling_point_stats,
    shop_concentration,
    sold_bands,
)


def _row(iid, title, price, sold, shop, params=None, skus=None, is_detail=1):
    return {
        "item_id": iid,
        "title": title,
        "price_final": price,
        "sold_lb": sold,
        "sold_text": f"{sold}+人付款" if sold is not None else None,
        "cart_lb": None,
        "shop_name": shop,
        "image_url": "",
        "product_url": "",
        "params": params or {},
        "skus": skus or [],
        "is_detail": is_detail,
    }


ROWS = [
    _row("1", "儿童保温杯316不锈钢 吸管 500ML 学生", 59.9, 5000, "A旗舰店",
         {"材质": "316不锈钢", "容量": "500ML", "功能": "保温,吸管"}),
    _row("2", "儿童保温杯304不锈钢 直饮 400ml", 39.9, 1200, "A旗舰店",
         {"材质": "304不锈钢", "容量": "400ML", "功能": "保温"}),
    _row("3", "大容量保温杯 1000ml 户外", 129, 800, "B旗舰店",
         {"材质": "316不锈钢", "容量": "1000ML"}, skus=["1000ML 黑"]),
    _row("4", "显温儿童保温杯 500ml 吸管", 89, 3000, "C专卖店",
         {"材质": "316不锈钢", "容量": "500ML", "功能": "显温,吸管"}),
    _row("5", "儿童水杯 塑料", 19.9, 200, "D小店", {}, is_detail=0),
]


def test_price_stats():
    stats = price_stats([10, 20, 30, 40])
    assert stats["count"] == 4
    assert stats["median"] == 25.0
    assert stats["avg"] == 25.0


def test_price_bands_sum():
    bands = price_bands(ROWS)
    assert bands
    assert sum(b["count"] for b in bands) == 5
    assert sum(b["sold"] for b in bands) == 10200


def test_price_bands_ignore_zero_and_missing():
    rows = [
        {"price_final": 0, "sold_lb": 100},   # 上游缺失价写成 0
        {"price_final": None, "sold_lb": 50},
        {"price_final": 20.0, "sold_lb": 10},
    ]
    bands = price_bands(rows)
    assert bands[0]["count"] == 1
    assert bands[0]["sold"] == 10


def test_sold_bands():
    bands = {b["label"]: b["count"] for b in sold_bands(ROWS)}
    assert bands["1000–5000"] == 2
    assert bands["5000–1万"] == 1
    assert bands["<100"] == 0


def test_shop_concentration():
    res = shop_concentration(ROWS)
    assert res["distinct"] == 4
    names = [s["name"] for s in res["shops"]]
    assert names[0] == "A旗舰店"
    assert 0 <= res["top5_share"] <= 1


def test_keyword_counts_uses_item_hits():
    words = {
        w["word"]: w["items"]
        for w in keyword_counts(["保温杯", "保温杯 吸管"], features=["保温杯", "吸管", "冲锋衣"])
    }
    assert words["保温杯"] == 2
    assert words["吸管"] == 1
    assert "冲锋衣" not in words          # 词表里有但没命中，不输出


def test_param_profile():
    profile = param_profile(ROWS)
    keys = [p["key"] for p in profile]
    assert "材质" in keys and "容量" in keys
    mat = next(p for p in profile if p["key"] == "材质")
    assert mat["coverage"] == 1.0
    assert {v["value"] for v in mat["top"]} == {"316不锈钢", "304不锈钢"}


def test_selling_points_title_and_params():
    sp = build_summary(ROWS)["selling_points"]
    words = {w["word"]: w for w in sp["title"]}
    assert "保温" in words                  # 参数值「保温」并入词表并命中标题
    assert words["保温"]["items"] >= 2
    assert 0 < words["保温"]["coverage"] <= 1
    assert words["保温"]["avg_price"] is not None

    pairs = {(p["key"], p["value"]) for p in sp["params"]}
    assert ("材质", "316不锈钢") in pairs
    assert ("功能", "吸管") in pairs          # 「保温,吸管」被拆成两个取值


def test_selling_points_drop_unhit_words():
    rows = [_row("1", "儿童保温杯", 50, 100, "A")]
    sp = selling_point_stats(rows, features=["保温", "完全不存在的词"])
    assert [w["word"] for w in sp["title"]] == ["保温"]


def test_build_summary_shape():
    summary = build_summary(ROWS)
    assert summary["counts"]["candidates"] == 5
    assert summary["counts"]["detailed"] == 4
    assert summary["price"]["stats"]["count"] == 5
    assert summary["top_products"][0]["item_id"] == "1"
    assert summary["keywords"]
    assert summary["shops"]["shops"]