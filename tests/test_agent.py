"""采集监督 Agent 单测（不访问网络/浏览器）。"""
import os
import tempfile
import types

from bkr import db
from bkr.agent import policy, store
from bkr.agent.observe import classify
from bkr.agent.replay import replay


def _sig(**over):
    base = {
        "url": "https://mobile.yangkeduo.com/search_result.html",
        "head": "商品 ¥199 已拼300件",
        "productTexts": 5,
        "textLen": 3000,
        "emptyHint": False,
    }
    base.update(over)
    return base


def test_classify_states():
    assert classify(_sig(head="拼多多安全验证 拖动滑块完成上方拼图"), 0) == "slider"
    assert classify(_sig(url="https://mobile.yangkeduo.com/login.html"), 0) == "login_wall"
    assert classify(_sig(head="访问异常 系统繁忙"), 0) == "risk_page"
    assert classify(_sig(head="前往APP查看价格"), 0) == "degraded"
    assert classify(_sig(), 20) == "ssr_ready"
    assert classify(_sig(), 0) == "client_rendered"
    assert classify(_sig(productTexts=0, head="没有找到相关商品", textLen=800), 0) == "empty_results"
    assert classify(_sig(productTexts=0, head="", textLen=100), 0) == "shell"
    assert classify(_sig(productTexts=0, head="随便什么广告", textLen=3000), 0) == "unknown"


def test_rule_table():
    full = {"reloads_left": 1, "slider_left": 2, "login_left": 1,
            "scrolls_left": 2, "saves_left": 2}
    assert policy.rule_decide({"state": "ssr_ready"}, full)["tool"] == "finish"
    assert policy.rule_decide({"state": "client_rendered"}, full)["tool"] == "reload_search"
    no_reload = dict(full, reloads_left=0)
    # 重载用尽 → 先尝试 React props 提取，再存证，最后 escalate
    after_reload = {"state": "client_rendered", "history": [{"tool": "reload_search"}]}
    d = policy.rule_decide(after_reload, no_reload)
    assert d["tool"] == "extract_candidates" and d["args"]["strategy"] == "dom_react"
    after_extract = {"state": "client_rendered",
                     "history": [{"tool": "reload_search"}, {"tool": "extract_candidates"}]}
    assert policy.rule_decide(after_extract, no_reload)["tool"] == "save_evidence"
    saved = {"state": "client_rendered",
             "history": [{"tool": "extract_candidates"}, {"tool": "save_evidence"}]}
    assert policy.rule_decide(saved, no_reload)["tool"] == "escalate"
    assert policy.rule_decide({"state": "slider"}, full)["tool"] == "wait_human"
    assert policy.rule_decide({"state": "slider"}, dict(full, slider_left=0))["tool"] == "escalate"
    assert policy.rule_decide({"state": "shell"}, full)["tool"] == "settle"
    two = {"state": "shell", "history": [{"tool": "settle"}, {"tool": "settle"}]}
    assert policy.rule_decide(two, full)["tool"] == "reload_search"
    assert policy.rule_decide({"state": "empty_results"}, full)["tool"] == "escalate"


def test_replay_sequence():
    budget = {"reloads_left": 1, "slider_left": 2, "login_left": 1,
              "scrolls_left": 2, "saves_left": 2}
    states = [{"state": "client_rendered"}, {"state": "ssr_ready"}]
    assert replay(states, budget) == ["reload_search", "finish"]


def test_extract_json_fences():
    assert policy._extract_json('```json\n{"tool":"finish","args":{}}\n```') == (
        '{"tool":"finish","args":{}}'
    )


def test_store_roundtrip():
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "t.db")
    conn = db.connect(path)
    conn.close()
    orig = store.db
    store.db = types.SimpleNamespace(connect=lambda: db.connect(path))
    try:
        store.start_run("r1", "b1", "pdd", "t1")
        store.add_step(
            "r1", 1, "abc",
            {"state": "slider", "url": "u", "signals": {}, "budget": {}},
            "wait_human", {"kind": "slider"}, {"ok": True}, "规则",
        )
        store.finish_run("r1", "success", 1, 0, "恢复成功")
        store.mark_batch_recovered("b1", "t1")
        c = db.connect(path)
        try:
            run = c.execute("SELECT status, steps FROM agent_run WHERE run_id='r1'").fetchone()
            assert (run["status"], run["steps"]) == ("success", 1)
            step = c.execute("SELECT tool FROM agent_step WHERE run_id='r1'").fetchone()
            assert step["tool"] == "wait_human"
        finally:
            c.close()
    finally:
        store.db = orig
        os.remove(path)

def test_fiber_extractor_markers():
    from bkr.platforms import pinduoduo as pdd

    assert "__reactFiber$" in pdd.JS_FIBER_CARDS
    assert "goodsID" in pdd.JS_FIBER_CARDS
    assert callable(pdd.extract_dom_react)
