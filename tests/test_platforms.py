"""平台适配与多平台数据隔离单测（不访问网络）。"""
import os
import tempfile

from bkr import database
from bkr.repositories import platform_auth_repo, product_repo
from bkr.config import load_task, profile_dir_for
from bkr.pace import get_pace
from bkr.platforms import alibaba, pinduoduo


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


def test_alibaba_sold_variants():
    t = _Task()
    for text, want in [
        ("成交 300+ 笔\n义乌市宏达日用品厂", 300),
        ("已售 90+ 个\n义乌市宏达日用品厂", 90),
        ("已售3.0万+个\n工厂", 30000),
    ]:
        card = alibaba.normalize_card(
            {"item_id": "1", "title_attr": "x", "text": text, "img": ""}, t
        )
        assert card is not None
        assert card["sold_lb"] == want


def test_pdd_normalize_card_sold_variants():
    t = _Task()
    for text, want in [
        ("¥29.90\n已拼10万+件\n纯棉短袖T恤", 100000),
        ("¥9.90\n已拼 300+ 件\n收纳盒", 300),
        ("¥19.90\n已拼1.2万件\n保温杯", 12000),
    ]:
        card = pinduoduo.normalize_card(
            {"item_id": "1", "title_attr": "纯棉短袖T恤", "text": text, "img": ""}, t
        )
        assert card is not None
        assert card["sold_lb"] == want


def test_pdd_price_filter():
    t = _Task()
    t.price_min = 50
    card = pinduoduo.normalize_card(
        {"item_id": "1", "title_attr": "保温杯", "text": "¥29.90\n已拼100+件", "img": ""}, t
    )
    assert card is None


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


def test_platform_auth_db():
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "t.db")
    conn = database.connect(path)
    try:
        assert platform_auth_repo.get_auth(conn, "tmall") is None
        platform_auth_repo.set_auth(conn, "tmall", True)
        assert platform_auth_repo.get_auth(conn, "tmall") is True
        platform_auth_repo.set_auth(conn, "tmall", False, "窗口关闭")
        assert platform_auth_repo.get_auth(conn, "tmall") is False
    finally:
        conn.close()
        os.remove(path)


def test_proxied_image():
    from server.utils import is_allowed_image_url, proxied_image

    url = "https://cbu01.alicdn.com/img/ibank/a.jpg_.webp"
    assert proxied_image("1688", url).startswith("/api/image?url=")
    assert proxied_image("tmall", url) == url
    assert is_allowed_image_url(url) is True
    assert is_allowed_image_url("https://evil.example.com/a.jpg") is False


def test_product_platform_isolation():
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "t.db")
    conn = database.connect(path)
    try:
        base = {"item_id": "1", "title": "a"}
        product_repo.upsert_product(conn, dict(base, platform="tmall"))
        product_repo.upsert_product(conn, dict(base, platform="1688", title="b"))
        rows = conn.execute(
            "SELECT platform, title FROM product WHERE item_id='1' ORDER BY platform"
        ).fetchall()
        assert [(r["platform"], r["title"]) for r in rows] == [("1688", "b"), ("tmall", "a")]
    finally:
        conn.close()
        os.remove(path)