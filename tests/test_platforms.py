"""平台适配与多平台数据隔离单测（不访问网络）。"""
import os
import tempfile

from bkr import db
from bkr.config import load_task
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