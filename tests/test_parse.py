"""解析函数单测：用页面上的真实文本样式验证口径。"""
from bkr.parse import (
    canonical_url,
    compute_final_price,
    extract_item_id,
    parse_cart_text,
    parse_count,
    parse_positive_rate,
    parse_price,
    parse_shop_rating,
    parse_sold_text,
)


def test_parse_count_basic():
    assert parse_count("600") == 600
    assert parse_count("500+") == 500
    assert parse_count("1,200+") == 1200


def test_parse_count_units():
    assert parse_count("2万+") == 20000
    assert parse_count("1.2万") == 12000
    assert parse_count("3千+") == 3000


def test_extract_item_id():
    url = "https://detail.tmall.com/item.htm?id=610000000001&ali_refid=a3_430582_1006"
    assert extract_item_id(url) == "610000000001"
    assert extract_item_id("https://www.tmall.com") is None
    assert canonical_url("1") == "https://detail.tmall.com/item.htm?id=1"


def test_sold_after_verb():
    frag, lb = parse_sold_text("¥179 优惠后 500+人付款 包邮")
    assert lb == 500
    assert "人付款" in frag


def test_sold_before_verb():
    frag, lb = parse_sold_text("已售 500+")
    assert lb == 500
    assert "已售" in frag


def test_sold_wan():
    assert parse_sold_text("2万+人付款")[1] == 20000
    assert parse_sold_text("月销 3000+")[1] == 3000


def test_sold_absent():
    assert parse_sold_text("儿童保温杯 316 不锈钢")[1] is None


def test_cart():
    frag, lb = parse_cart_text("超600人加购")
    assert lb == 600
    assert "人加购" in frag


def test_parse_price():
    assert parse_price("优惠前 ¥179") == 179.0
    assert parse_price("¥49.9 优惠后") == 49.9
    assert parse_price("没有价格") is None


def test_final_price_with_reduction():
    assert compute_final_price("优惠前 ¥179", "已享受：官方立减30元") == 149.0


def test_final_price_coupon():
    assert compute_final_price("券后价 ¥139.5") == 139.5


def test_final_price_to_hand_subsidy():
    # 天猫详情页常见："¥280 补贴到手价 ¥389 建议零售价 ..."
    assert compute_final_price("¥280 补贴到手价 ¥389 建议零售价 ¥60 优惠") == 280.0


def test_final_price_to_hand_explicit():
    assert compute_final_price("预计到手价 ¥149.00") == 149.0


def test_final_price_floor_guard():
    # 立减金额异常时不把价格算成负数，回退到展示价
    assert compute_final_price("¥10", "立减30元") == 10.0


def test_positive_rate_and_rating():
    assert parse_positive_rate("88VIP好评率96%") == "96%"
    assert parse_shop_rating("天猫 4.8 分") == 4.8