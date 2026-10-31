"""平台适配与多平台数据隔离单测（不访问网络）。"""
import os
import tempfile

from bkr import db
from bkr.config import load_task, profile_dir_for
from bkr.pace import get_pace
from bkr.platforms import alibaba


class _Task:
    keywords = ["保温杯"]
    exclude_words = []
    price_min = None
    price_max = None


def test_alibaba_normalize_card():
    raw = {
        "item_id": "123456789",
        "title_attr": "316不锈钢保温杯 500ml 学生",
        "text": "¥12.50\n成交 300+ 笔\n义乌市宏达日用品厂",
        "img": "//img.alicdn.com/a.jpg",
    }
    card = alibaba.normalize_card(raw, _Task())
    assert card is not None
    assert card["item_id"] == "123456789"
    assert card["title"].startswith("316")
    assert card["sold_lb"] == 300
    assert card["shop_name"] and "宏达" in card["shop_name"]
    assert card["price_text"].startswith("¥")


def test_alibaba_price_filter():
    t = _Task()
    t.price_min = 100
    raw = {"item_id": "1", "title_attr": "x", "text": "¥12.50\n成交 10 笔", "img": ""}
    assert alibaba.normalize_card(raw, t) is None


def test_task_platform_from_yaml():
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "t.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "task_id: t1\nname: 测试\nplatform: 1688\n"
            "entry:\n  keywords: ['保温杯']\n"
        )
    task = load_task(path)
    assert task.platform == "1688"


def test_pace_presets():
    assert get_pace("safe")["detail_lo"] == 20
    assert get_pace("balanced")["rest_every"] == 3
    assert get_pace("fast")["detail_hi"] < get_pace("safe")["detail_hi"]
    assert get_pace("unknown") == get_pace("safe")


def test_auth_markers():
    import bkr.auth as auth

    old = auth.cookie_names
    try:
        auth.cookie_names = lambda p: (
            {"cookie2", "unb"} if p == "tmall" else {"__cn_logon__"}
        )
        assert auth.is_authorized("tmall") is True
        assert auth.is_authorized("1688") is True

        auth.cookie_names = lambda p: {"cookie2", "tracknick"}
        assert auth.is_authorized("tmall") is False
        assert auth.is_authorized("1688") is False
    finally:
        auth.cookie_names = old


def test_profile_dir_for():
    assert "tmall" in profile_dir_for("tmall").name
    assert "1688" in profile_dir_for("1688").name
    assert profile_dir_for("") == profile_dir_for(None)


def test_product_platform_isolation():
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "t.db")
    conn = db.connect(path)
    try:
        base = {"item_id": "1", "title": "a"}
        db.upsert_product(conn, dict(base, platform="tmall"))
        db.upsert_product(conn, dict(base, platform="1688", title="b"))
        rows = conn.execute(
            "SELECT platform, title FROM product WHERE item_id='1' ORDER BY platform"
        ).fetchall()
        assert [(r["platform"], r["title"]) for r in rows] == [("1688", "b"), ("tmall", "a")]
    finally:
        conn.close()
        os.remove(path)

def test_alibaba_sold_yishou_and_units():
    # 详情页形态：已售 8000+个（页面由三个相邻 span 拼出）
    raw = {"item_id": "2", "title_attr": "男士手表",
           "text": "¥15.00\n已售 8000+个\n广州鸥芮时贸易有限公司", "img": ""}
    card = alibaba.normalize_card(raw, _Task())
    assert card["sold_lb"] == 8000
    # 成交 + 件 + 万级
    raw2 = {"item_id": "3", "title_attr": "手表", "text": "¥5.00\n成交 1.2万件", "img": ""}
    assert alibaba.normalize_card(raw2, _Task())["sold_lb"] == 12000


def test_alibaba_search_url_sold_sort():
    class _SortTask(_Task):
        sort = "sale-desc"
        pages = 1

    url = alibaba.search_url(_SortTask(), 1)
    assert "sortType=va_rmdarkgmv" in url
    # 非销量排序时不追加参数，保持默认综合
    assert "sortType" not in alibaba.search_url(_Task(), 1)


def test_alibaba_detail_title_selector_prefers_title_content():
    # 详情提取器必须优先取 .title-content 里的 h1，避开第一个卖家公司名 h1
    assert ".title-content h1" in alibaba.JS_DETAIL
