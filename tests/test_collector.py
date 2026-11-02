"""采集编排辅助逻辑单测（不访问网络/浏览器）。"""
import os
import tempfile

from bkr import db
from bkr.collector import _price_guard, _same_search_url


def test_price_guard_treats_zero_as_missing():
    assert _price_guard(0, 20) == 20
    assert _price_guard(0, 0) is None
    assert _price_guard(None, 0) is None
    assert _price_guard(0, None) is None


def test_same_search_url_tmall():
    a = "https://s.taobao.com/search?q=%E6%89%8B%E8%A1%A8&tab=mall&sort=sale-desc&page=1"
    b = "https://s.taobao.com/search?q=%E6%89%8B%E8%A1%A8&sort=sale-desc&page=1&spm=a1"
    assert _same_search_url(a, b)
    c = "https://s.taobao.com/search?q=%E6%89%8B%E8%A1%A8&tab=mall&sort=sale-desc&page=2"
    assert not _same_search_url(a, c)
    assert not _same_search_url(a, "https://s.1688.com/selloffer/offer_search.htm?keywords=x")


def test_same_search_url_1688_gbk():
    a = ("https://s.1688.com/selloffer/offer_search.htm"
         "?keywords=%CA%D6%B1%ED&beginPage=1&sortType=va_rmdarkgmv")
    b = ("https://s.1688.com/selloffer/offer_search.htm"
         "?keywords=%CA%D6%B1%ED&beginPage=1&sortType=va_rmdarkgmv&spm=x")
    assert _same_search_url(a, b)


def test_set_snapshot_detail_cart_and_sold_merge():
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "t.db")
    conn = db.connect(path)
    try:
        db.upsert_snapshot(conn, "t1", "100", "b1", "2026-10-31T21:00:00", 1,
                           "¥10.00", "已售 100+", 100, None, None, platform="tmall")
        # 列表已有已售值时不覆盖；加购以详情为准写入
        db.set_snapshot_detail(conn, "t1", "100", "b1", 9.9,
                               cart_text="1千人加购", cart_lb=1000,
                               sold_text="已售 300+", sold_lb=300)
        r = conn.execute("SELECT * FROM fact_snapshot WHERE task_id='t1'").fetchone()
        assert r["is_detail"] == 1 and r["price_final"] == 9.9
        assert r["cart_text"] == "1千人加购" and r["cart_lb"] == 1000
        assert r["sold_lb"] == 100
        # 列表缺失已售时，用详情补齐
        db.upsert_snapshot(conn, "t1", "101", "b1", "2026-10-31T21:00:00", 2,
                           "¥5.00", None, None, None, None, platform="tmall")
        db.set_snapshot_detail(conn, "t1", "101", "b1", 5.0,
                               sold_text="已售 500+", sold_lb=500)
        r2 = conn.execute("SELECT * FROM fact_snapshot WHERE item_id='101'").fetchone()
        assert r2["sold_lb"] == 500
    finally:
        conn.close()
        os.remove(path)
