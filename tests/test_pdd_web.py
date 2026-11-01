"""拼多多 H5 网页采集的解析单测（不访问网络/浏览器）。"""
import bkr.auth as auth
from bkr.platforms import pinduoduo as pdd


class _Task:
    keywords = ["手表"]
    exclude_words = []
    price_min = None
    price_max = None


def test_ssr_card_cents_to_yuan():
    c = pdd.normalize_ssr_card(
        {"goodsID": "123", "goodsName": "男士手表", "price": 2990,
         "salesTip": "已拼10万+件", "img": "//img.pddpic.com/a.jpg"},
        _Task(),
    )
    assert c["item_id"] == "123"
    assert c["price_text"] == "¥29.90"
    assert c["sold_lb"] == 100000
    assert c["image_url"].startswith("https://")


def test_ssr_card_filters_and_missing_id():
    assert pdd.normalize_ssr_card({"goodsID": "", "goodsName": "x"}, _Task()) is None
    t = _Task()
    t.exclude_words = ["配件"]
    assert pdd.normalize_ssr_card({"goodsID": "1", "goodsName": "手表配件"}, t) is None
    t2 = _Task()
    t2.price_max = 10
    assert pdd.normalize_ssr_card({"goodsID": "2", "goodsName": "手表", "price": 2990}, t2) is None


def test_ssr_card_suspicious_price_is_none():
    for bad in (0, -1, "abc", 200_000_001, True):
        c = pdd.normalize_ssr_card({"goodsID": "3", "goodsName": "手表", "price": bad}, _Task())
        assert c["price_text"] is None


def test_sales_tip_variants():
    for tip, lb in (("已拼10万+件", 100000), ("已拼 300+ 件", 300), ("已拼1.2万件", 12000)):
        _, v = pdd.parse_pdd_sold(tip)
        assert v == lb


def test_item_sold_vs_shop_sold():
    text = "本店已拼5万+件\n该商品已拼 300+ 件"
    frag, lb = pdd.parse_pdd_sold(text)
    assert lb == 300
    assert "300" in frag
    params = pdd.parse_pdd_params(text)
    assert "店铺已拼" in params and "5万" in params["店铺已拼"]


def test_obscured_price_returns_none():
    assert pdd.parse_pdd_price("限时秒杀 ¥3.?\n")[0] is None
    assert pdd.parse_pdd_price("¥29.90 已拼100件")[0] == 29.9
    price, label = pdd.parse_pdd_price("券后价¥25.5 ¥29.9")
    assert price == 25.5 and label == "券后价"


def test_eval_count_delivery_and_services():
    params = pdd.parse_pdd_params(
        "商品评价(16,523)\n发货地：广东省 东莞市\n48小时发货 7天无理由 包邮 包邮"
    )
    assert params["评价数"] == "16523"
    assert params["发货地"] == "广东省"
    assert params["服务"].count("包邮") == 1


def test_zero_width_shop_name():
    text = "阿\u200b里\u200b手\u200b表\u200b旗\u200b舰\u200b店\n本店已拼1000+件"
    assert pdd.parse_pdd_shop(text) == "阿里手表旗舰店"


def test_degraded_detection():
    assert pdd.is_degraded("前往APP查看价格") is True
    assert pdd.is_degraded("打开App查看") is True
    assert pdd.is_degraded("正常商品详情页文本") is False


def test_pdd_auth_uses_login_cookies():
    old = auth.cookie_names
    try:
        auth.cookie_names = lambda p: {"PDDAccessToken", "pdd_user_id"}
        assert auth.is_authorized("pdd") is True
        auth.cookie_names = lambda p: set()
        assert auth.is_authorized("pdd") is False
    finally:
        auth.cookie_names = old


def test_pdd_pages_fixed_single_screen():
    # 拼多多为无限滚动：collect_pages 对 pages>1 也只按 1 屏处理（逻辑在 open_search_page）
    assert pdd.MAX_SCROLLS >= 3
    assert 2000 < pdd.PAGE_SETTLE_MS[0] <= pdd.PAGE_SETTLE_MS[1] < 4000