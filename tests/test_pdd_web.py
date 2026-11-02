"""拼多多 H5 网页采集的解析单测（不访问网络/浏览器）。"""
import pytest

import bkr.auth as auth
from bkr.browser import BlockedError
from bkr.platforms import pinduoduo as pdd


class _Page:
    def __init__(self, url="https://mobile.yangkeduo.com/search_result.html", title="拼多多", text="商品 ¥19.9 已拼100件"):
        self.url = url
        self._title = title
        self._text = text

    def title(self):
        return self._title

    def inner_text(self, *_args, **_kwargs):
        return self._text


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

def test_store_level_sales_tip_ignored():
    # 实测：SSR 卡片的 salesTip 全是店铺累计口径，不得写进单品 sold
    for tip in ("本店已拼500万+件", "全店总售900万+件", "店铺已拼809件"):
        c = pdd.normalize_ssr_card(
            {"goodsID": "9", "goodsName": "手表", "price": 2990, "salesTip": tip}, _Task()
        )
        assert c["sold_text"] is None
        assert c["sold_lb"] is None


def test_slider_markers_cover_real_popup():
    assert "拼多多安全验证" in pdd._SLIDER_MARKERS
    assert "完成上方拼图" in pdd._SLIDER_MARKERS


def test_slider_page_detected_by_title_only():
    """实测：滑块页 body.innerText 可能没有滑块文案，只有 <title>安全验证</title>。"""
    page = _Page(url="https://mobile.yangkeduo.com/goods.html?goods_id=1", title="安全验证", text="")
    with pytest.raises(BlockedError):
        pdd.check_pdd_blocked(page)
    pdd.check_pdd_blocked(_Page())  # 正常商品页不误报


def test_empty_detail_is_not_written():
    """详情页解析不到任何字段时应暂停，而不是写一条 is_detail=1 的空记录。"""
    class _DetailPage(_Page):
        mouse = type("M", (), {"wheel": lambda self, *a, **k: None})()

        def wait_for_timeout(self, *_a, **_k):
            pass

        def evaluate(self, _js):
            return {"title": "", "top_text": "这是一段没有商品字段的页面文本",
                    "attrs": {}, "sku_texts": [], "image_url": "", "video_url": ""}

    with pytest.raises(BlockedError):
        pdd.extract_from_page(_DetailPage(), "1")


def test_img_key_and_reorder():
    items = [
        {"goodsID": "A", "img": "https://img.pddpic.com/mms/1/aaa.jpeg.a.jpeg"},
        {"goodsID": "B", "img": "https://img.pddpic.com/mms/2/bbb.jpeg"},
        {"goodsID": "C", "img": "https://img.pddpic.com/mms/3/ccc.png"},
    ]
    dom = [
        "https://img.pddpic.com/mms/3/ccc.png?imageView2/2/w/200",
        "https://img.pddpic.com/mms/1/aaa.jpeg.a.jpeg",
        "https://img.pddpic.com/mms/2/bbb.jpeg",
    ]
    out, matched = pdd._reorder_items_by_images(items, dom)
    assert matched == 3
    assert [x["goodsID"] for x in out] == ["C", "A", "B"]


def test_reorder_low_match_keeps_original():
    items = [{"goodsID": str(i), "img": f"https://x/{i}.jpeg"} for i in range(5)]
    out, matched = pdd._reorder_items_by_images(items, ["https://x/0.jpeg"])
    assert matched == 1
    assert out == items
