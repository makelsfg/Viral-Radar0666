"""热词解析与落库单测（不访问网络）。"""
import os
import tempfile

from bkr import db
from bkr.hotwords import parse_suggest_payload


def test_parse_dict_payload():
    payload = {
        "result": [
            ["儿童保温杯", "123"],
            ["保温杯 大容量", "45"],
            ["儿童保温杯", "9"],
        ]
    }
    assert parse_suggest_payload(payload) == ["儿童保温杯", "保温杯 大容量"]


def test_parse_text_payload():
    text = '{"result":[["保温杯 316","1"],["学生水杯","2"]]}'
    assert parse_suggest_payload(text) == ["保温杯 316", "学生水杯"]


def test_parse_empty():
    assert parse_suggest_payload({"result": []}) == []
    assert parse_suggest_payload("") == []


def test_hotword_db_roundtrip():
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "t.db")
    conn = db.connect(path)
    try:
        db.save_hotwords(conn, "保温杯", ["儿童保温杯", "大容量保温杯"])
        rows = db.list_hotwords(conn, "保温杯")
        assert [r["word"] for r in rows] == ["儿童保温杯", "大容量保温杯"]
        assert rows[0]["rank"] == 1

        keywords = db.hotword_keywords(conn)
        assert keywords[0]["keyword"] == "保温杯"
        assert keywords[0]["count"] == 2

        # 再次保存应更新而不是重复
        db.save_hotwords(conn, "保温杯", ["儿童保温杯", "学生水杯"])
        rows = db.list_hotwords(conn, "保温杯")
        assert len(rows) == 3
    finally:
        conn.close()
        os.remove(path)