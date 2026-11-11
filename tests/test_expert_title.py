"""大佬分析标题规则单测（默认标题 + 重名去重）。"""
import os
import tempfile

from bkr import database
from server.services import expert_service as svc


def test_default_title():
    assert svc.default_title("手表") == "手表 · 大佬分析"
    assert svc.default_title("") == "品类 · 大佬分析"
    assert svc.default_title("  手表  ") == "手表 · 大佬分析"


def test_unique_title_sequence(monkeypatch):
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "t.db")
    with database.connection(path) as conn:
        conn.execute(
            "INSERT INTO expert_run(id, title, sources_json, status, created_at) "
            "VALUES('a', '手表 · 大佬分析', '[]', 'running', 'x')"
        )
        conn.commit()

    orig = database.connect
    monkeypatch.setattr(database, "connect", lambda p=None: orig(p or path))
    try:
        svc_obj = svc.ExpertService()
        assert svc_obj._unique_title("手表 · 大佬分析") == "手表 · 大佬分析(1)"
        with database.connection(path) as conn:
            conn.execute(
                "INSERT INTO expert_run(id, title, sources_json, status, created_at) "
                "VALUES('b', '手表 · 大佬分析(1)', '[]', 'running', 'x')"
            )
            conn.commit()
        assert svc_obj._unique_title("手表 · 大佬分析") == "手表 · 大佬分析(2)"
        assert svc_obj._unique_title("全新标题") == "全新标题"
    finally:
        os.remove(path)
