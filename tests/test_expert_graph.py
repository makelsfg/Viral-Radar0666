"""大佬分析 LangGraph 路径单测（需要安装 langgraph；无依赖时跳过）。"""
import os
import tempfile

import pytest

from bkr import database
from bkr.expert import graph as g
from bkr.repositories import batch_repo, expert_repo, snapshot_repo, task_repo

pytest.importorskip("langgraph")


def _seed(path):
    with database.connection(path) as conn:
        task = type("T", (), {
            "task_id": "t1", "name": "手表 · 天猫", "keywords": ["手表"],
            "platform": "tmall", "pace": "safe", "tab": "mall", "sort": "sale-desc",
            "pages": 1, "detail_top": 0, "detail_order": "sold",
            "price_min": None, "price_max": None, "exclude_words": [], "save_html": False,
        })()
        task_repo.upsert_task(conn, task)
        batch_repo.ensure_batch(conn, "b1", "t1")
        snapshot_repo.upsert_snapshot(
            conn, "t1", "100", "b1", "2026-11-01T10:00:00", 1,
            "¥199", "已售 5000+", 5000, None, None,
            title="手表", platform="tmall",
        )
        conn.commit()


def _setup(monkeypatch, path):
    _seed(path)
    orig = database.connect
    monkeypatch.setattr(database, "connect", lambda p=None: orig(p or path))


def _create_run(path, run_id, max_rounds):
    with database.connection(path) as conn:
        expert_repo.create_run(
            conn, run_id, "测试", "手表",
            [{"type": "batch", "task_id": "t1", "batch": "b1"}], max_rounds, 85,
        )


def _get_run(path, run_id):
    with database.connection(path) as conn:
        return expert_repo.get_run(conn, run_id)


def test_graph_revise_then_pass(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(), "g.db")
    _setup(monkeypatch, path)
    _create_run(path, "ex-g1", 3)

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
    run = _get_run(path, "ex-g1")
    assert run["status"] == "done"
    assert len(run["rounds"]) >= 4
    assert any(e == "done" for e, _ in events)


def test_graph_max_rounds_needs_human(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(), "g2.db")
    _setup(monkeypatch, path)
    _create_run(path, "ex-g2", 2)

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
    run = _get_run(path, "ex-g2")
    assert run["status"] == "needs_human"
    assert run["final_report_md"]
