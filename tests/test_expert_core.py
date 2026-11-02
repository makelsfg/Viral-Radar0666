"""大佬分析：证据包、工具与持久化单测（不访问网络/LLM）。"""
import os
import tempfile

from bkr import db
from bkr.expert import store
from bkr.expert import evidence as ev
from bkr.expert import tools


def _seed(conn, task_id="t1", batch="b1"):
    task = type("T", (), {
        "task_id": task_id, "name": "手表 · 天猫", "keywords": ["手表"],
        "platform": "tmall", "pace": "safe", "tab": "mall", "sort": "sale-desc",
        "pages": 1, "detail_top": 1, "detail_order": "sold",
        "price_min": None, "price_max": None, "exclude_words": [], "save_html": False,
    })()
    db.upsert_task(conn, task)
    db.ensure_batch(conn, batch, task_id)
    db.upsert_snapshot(
        conn, task_id, "100", batch, "2026-11-01T10:00:00", 1,
        "¥199.00", "已售 5000+", 5000, None, None,
        title="男士机械手表 防水", image_url="", shop_name="甲店",
        page_no=1, platform="tmall",
    )
    db.upsert_product(conn, {
        "platform": "tmall", "item_id": "100", "title": "男士机械手表 防水",
        "shop_name": "甲店", "shop_rating": None, "positive_rate": None,
        "sku_names": "[]", "params_json": '{"机芯": "机械"}',
        "image_url": "", "video_url": "", "product_url": "https://example.com/100",
        "updated_at": "", "first_seen_at": "",
    })
    conn.commit()


def test_build_evidence_and_search(monkeypatch):
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "e.db")
    conn = db.connect(path)
    _seed(conn)
    conn.close()

    orig = db.connect
    monkeypatch.setattr(ev.db, "connect", lambda p=None: orig(p or path))
    pack = ev.build_evidence([
        {"type": "batch", "task_id": "t1", "batch": "b1"},
        {"type": "report", "name": "旧报告.md", "content": "手表市场以 100–300 元为主。" * 20},
        {"type": "note", "name": "补充", "content": "我们已有表带供应链。"},
    ])
    assert pack["meta"]["evidence_count"] > 0
    assert pack["meta"]["batches"][0]["platform"] == "tmall"
    assert pack["batch_data"]["t1::b1"]["summary"]["counts"]["candidates"] == 1
    ids = [e["id"] for e in pack["evidence"]]
    assert ids[0] == "E1" and len(ids) == len(set(ids))
    assert any(e["kind"] == "doc" for e in pack["evidence"])
    assert any(e["kind"] == "note" for e in pack["evidence"])
    hits = pack["index"].search("供应链", top_k=3)
    assert hits
    text = ev.pack_for_prompt(pack)
    assert "[E1]" in text


def test_batch_rows_treats_zero_price_as_missing(monkeypatch):
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "z.db")
    conn = db.connect(path)
    db.upsert_snapshot(
        conn, "t1", "zero", "b1", "2026-11-01T10:00:00", 1,
        "¥0.00", None, None, None, None,
        title="零价占位", image_url="", shop_name="", page_no=1, platform="1688",
    )
    conn.commit()
    conn.close()

    orig = db.connect
    monkeypatch.setattr(ev.db, "connect", lambda p=None: orig(p or path))
    rows = ev.batch_rows("t1", "b1")
    assert rows[0]["price_final"] is None
    assert rows[0]["price_source"] is None


def test_expert_tools_calculate_and_checks():
    assert tools.calculate("(199-80)*1000")["result"] == 119000
    assert "error" in tools.calculate("__import__('os').system('x')")
    draft = "## 1\n价格 199 元贡献 40% 销量。\n证据支持 [E2] 的结论。"
    hits = tools.check_number_grounding(draft)
    assert any("199" in h["number"] or "40" in h["number"] for h in hits)
    mix = tools.check_platform_mix("天猫已售 5000，高于 1688 已售 3000。")
    assert mix


def test_store_roundtrip(monkeypatch):
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "s.db")
    conn = db.connect(path)
    conn.close()
    orig = db.connect
    monkeypatch.setattr(store.db, "connect", lambda p=None: orig(p or path))

    run = store.create_run("ex-1", "测试报告", "手表", [
        {"type": "note", "name": "n", "content": "c"},
    ], 3, 85)
    assert run["id"] == "ex-1" and run["status"] == "running"
    store.add_round("ex-1", 1, "draft", content_md="草稿")
    store.add_round("ex-1", 1, "review", review={"verdict": "revise", "score": 70},
                    verdict="revise", score=70)
    detail = store.get_run("ex-1")
    assert len(detail["rounds"]) == 2
    assert detail["rounds"][1]["review"]["score"] == 70
    store.update_run("ex-1", status="done", final_report_md="# 报告")
    assert store.get_run("ex-1")["final_report_md"] == "# 报告"
    assert store.list_runs()[0]["id"] == "ex-1"
    assert store.delete_run("ex-1")
    assert store.get_run("ex-1") is None