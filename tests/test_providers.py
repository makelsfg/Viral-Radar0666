"""引擎选择、CDP 关闭保护、Apify 映射与价格护栏单测（不访问网络）。"""
import bkr.browser as browser_mod
from bkr.browser import close_context, engine_name, has_login_cookies
from bkr.collector import _price_guard
from bkr.providers.apify_tmall import map_to_product


SAMPLE = {
    "itemId": "100000000001",
    "title": "测试商品 316不锈钢保温杯",
    "url": "https://world.taobao.com/item/100000000001.htm",
    "taobaoUrl": "https://detail.tmall.com/item.htm?id=100000000001",
    "price": 876.0,
    "promotionPrice": 799.0,
    "shopName": "测试旗舰店",
    "dsrDescription": 4.84,
    "reviewCount": 58,
    "mainImage": "https://img.alicdn.com/a.png",
    "images": ["https://img.alicdn.com/a.png"],
    "attributes": {"材质": "316不锈钢", "容量": "500ML"},
    "skus": [{"skuId": "sku_1", "name": "材质:316不锈钢;容量:500ML", "price": 799.0}],
}


def test_apify_mapping():
    rec = map_to_product(SAMPLE)
    assert rec["item_id"] == "100000000001"
    assert rec["title"].startswith("测试商品")
    assert rec["price_final"] == 799.0
    assert rec["shop_name"] == "测试旗舰店"
    assert rec["shop_rating"] == 4.84
    assert rec["sku_names"] == ["材质:316不锈钢;容量:500ML"]
    assert rec["params"]["材质"] == "316不锈钢"
    assert rec["product_url"].startswith("https://detail.tmall.com/")


def test_apify_mapping_missing_price():
    rec = map_to_product({"itemId": "1", "title": "x"})
    assert rec["price_final"] is None
    assert rec["sku_names"] == []
    assert rec["params"] == {}


def test_price_guard():
    assert _price_guard(None, 100) == 100
    assert _price_guard(80, 100) == 80
    assert _price_guard(10, 100) == 100
    assert _price_guard(100, None) == 100


def test_engine_name_is_known():
    assert engine_name() in ("playwright", "patchright", "rebrowser")


def test_login_cookies_strict():
    # 只有 tracknick/sgcookie 属于半登录，必须 cookie2 + unb 才算完整
    assert not has_login_cookies({"tracknick", "sgcookie", "lgc"})
    assert not has_login_cookies({"cookie2"})
    assert has_login_cookies({"cookie2", "unb", "tracknick"})


class _DummyCtx:
    def __init__(self, owned: bool):
        self._bkr_owned = owned
        self.closed = False

    def close(self):
        self.closed = True


def test_close_context_owned():
    old_cdp = browser_mod.CDP_URL
    browser_mod.CDP_URL = ""
    try:
        ctx = _DummyCtx(owned=True)
        close_context(ctx)
        assert ctx.closed is True
    finally:
        browser_mod.CDP_URL = old_cdp


def test_close_context_not_owned():
    old_cdp = browser_mod.CDP_URL
    browser_mod.CDP_URL = ""
    try:
        ctx = _DummyCtx(owned=False)
        close_context(ctx)
        assert ctx.closed is False
    finally:
        browser_mod.CDP_URL = old_cdp