"""拼多多官方 API 适配单测（不访问网络）。"""
from bkr.providers import pdd_ddk


class _Task:
    exclude_words = []
    price_min = None
    price_max = None


def test_pdd_sign_matches_algorithm():
    assert pdd_ddk._sign({"b": "2", "a": "1"}, "s") == "5EE29085AF57D942F21F1C5BA3C2A90A"


def test_pdd_map_card():
    goods = {
        "goods_id": 123,
        "goods_name": "纯棉短袖T恤",
        "min_group_price": 2990,
        "sales_tip": "已拼10万+件",
        "mall_name": "某某旗舰店",
        "goods_thumbnail_url": "https://img.pddpic.com/a.jpg",
        "goods_sign": "sign-abc",
    }
    card = pdd_ddk.map_card(goods, _Task())
    assert card is not None
    assert card["item_id"] == "123"
    assert card["price_final"] == 29.9
    assert card["sold_lb"] == 100000
    assert card["shop_name"] == "某某旗舰店"
    assert card["goods_sign"] == "sign-abc"
    assert "goods_id=123" in card["url"]


def test_pdd_map_card_filters_price():
    t = _Task()
    t.price_min = 50
    goods = {"goods_id": 1, "goods_name": "x", "min_group_price": 2990}
    assert pdd_ddk.map_card(goods, t) is None


def test_pdd_map_detail():
    detail = {
        "goods_id": 123,
        "goods_name": "纯棉短袖T恤",
        "min_group_price": 1990,
        "sold_quantity": 5000,
        "mall_name": "某某旗舰店",
        "top_gallery_urls": ["https://img.pddpic.com/b.jpg"],
        "cat_id": 743,
    }
    d = pdd_ddk.map_detail(detail)
    assert d["price_final"] == 19.9
    assert d["sold_lb"] == 5000
    assert d["sold_text"] == "已拼5000件"
    assert d["image_url"].endswith("b.jpg")
    assert d["params"]["cat_id"] == "743"