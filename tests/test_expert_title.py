"""大佬分析标题规则单测（默认标题 + 重名去重）。"""
import os
import tempfile
import types

from bkr import db
import importlib

svc = importlib.import_module("bkr.expert.service")


def test_default_title():
    assert svc.default_title("手表") == "手表 · 大佬分析"
    assert svc.default_title("") == "品类 · 大佬分析"
    assert svc.default_title("  手表  ") == "手表 · 大佬分析"


def test_unique_title_sequence():
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "t.db")
    conn = db.connect(path)
    conn.execute("INSERT INTO expert_run(id, title, sources_json, status, created_at) VALUES('a', '手表 · 大佬分析', '[]', 'running', 'x')")
    conn.commit()
    conn.close()

    svc_obj = svc.ExpertService()
    orig = svc.db
    svc.db = types.SimpleNamespace(connect=lambda: db.connect(path))
    try:
        assert svc_obj._unique_title("手表 · 大佬分析") == "手表 · 大佬分析(1)"
        conn = db.connect(path)
        conn.execute(
            "INSERT INTO expert_run(id, title, sources_json, status, created_at) VALUES('b', '手表 · 大佬分析(1)', '[]', 'running', 'x')"
        )
        conn.commit()
        conn.close()
        assert svc_obj._unique_title("手表 · 大佬分析") == "手表 · 大佬分析(2)"
        assert svc_obj._unique_title("全新标题") == "全新标题"
    finally:
        svc.db = orig
        os.remove(path)
