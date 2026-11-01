"""大佬分析 LangGraph 路径单测（需要安装 langgraph；无依赖时跳过）。"""
import os
import tempfile

import pytest

from bkr import db
from bkr.expert import evidence as ev
from bkr.expert import graph as g
from bkr.expert import store

pytest.importorskip("langgraph")


def _seed(path):
    conn = db.connect(path)
    task = type("T", (), {
        "task_id": "t1", "name": "手表 · 天猫", "keywords": ["手表"],
        "platform": "tmall", "pace": "safe", "tab": "mall", "sort": "sale-desc",
        "pages": 1, "detail_top": 0, "detail_order": "sold",
        "price_min": None, "price_max": None, "exclude_words": [], "save_html": False,
    })()
    db.upsert_task(conn, task)
    db.ensure_batch(conn, "b1", "t1")
    db.upsert_snapshot(conn, "t1", "100", "b1", "2026-11-01T10:00:00", 1,
                       "¥199", "已售 5000+", 5000, None, None,
                       title="手表", platform="tmall")
    conn.commit()
    conn.close()


def _setup(monkeypatch, path):
    _seed(path)
    orig = db.connect
    monkeypatch.setattr(db, "connect", lambda p=None: orig(p or path))


def test_graph_revise_then_pass(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(), "g.db")
    _setup(monkeypatch, path)
    store.create_run("ex-g1", "测试", "手表", [{"type": "batch", "task_id": "t1", "batch": "b1"}], 3, 85)

    seq = [
        {"verdict": "revise", "score": 60, "dimension_scores": {"evidence": 60}, "issues": [
            {"severity": "major", "location": "整体", "problem": "x", "fix": "y"}
        ], "summary": "再改"},
        {"verdict": "pass", "score": 90, "dimension_scores": {"evidence": 90, "actionability": 90},
         "issues": [], "summary": "通过"},
    ]
    monkeypatch.setattr(g, "_writer", lambda state, feedback, round_no: f"# 草稿{round_no}")
    monkeypatch.setattr(g, "_reviewer", lambda state: seq.pop(0))

    events = []
    graph = g.build_graph("ex-g1", lambda e, p: events.append((e, p)), None)
    state = {
        "run_id": "ex-g1", "title": "测试", "keyword": "手表",
        "sources": [{"type": "batch", "task_id": "t1", "batch": "b1"}],
        "max_rounds": 3, "pass_score": 85, "iteration": 1,
        "history": [], "best_score": -1, "best_draft_md": "", "draft_md": "",
        "review": None, "status": "running", "error": None,
    }
    result = graph.invoke(state, config={"recursion_limit": 30})
    assert result["status"] == "done"
    assert store.get_run("ex-g1")["status"] == "done"
    assert len(store.get_run("ex-g1")["rounds"]) >= 4
    assert any(e == "done" for e, _ in events)


def test_graph_max_rounds_needs_human(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(), "g2.db")
    _setup(monkeypatch, path)
    store.create_run("ex-g2", "测试", "手表", [{"type": "batch", "task_id": "t1", "batch": "b1"}], 2, 85)

    monkeypatch.setattr(g, "_writer", lambda state, feedback, round_no: f"# 草稿{round_no}")
    monkeypatch.setattr(g, "_reviewer", lambda state: {
        "verdict": "revise", "score": 50,
        "dimension_scores": {"evidence": 50, "actionability": 50},
        "issues": [{"severity": "blocker", "location": "整体", "problem": "x", "fix": "y"}],
        "summary": "不行",
    })

    graph = g.build_graph("ex-g2", lambda e, p: None, None)
    state = {
        "run_id": "ex-g2", "title": "测试", "keyword": "手表",
        "sources": [{"type": "batch", "task_id": "t1", "batch": "b1"}],
        "max_rounds": 2, "pass_score": 85, "iteration": 1,
        "history": [], "best_score": -1, "best_draft_md": "", "draft_md": "",
        "review": None, "status": "running", "error": None,
    }
    result = graph.invoke(state, config={"recursion_limit": 30})
    assert result["status"] == "needs_human"
    run = store.get_run("ex-g2")
    assert run["status"] == "needs_human"
    assert run["final_report_md"]