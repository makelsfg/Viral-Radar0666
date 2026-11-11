import os
import tempfile

from bkr import database
from bkr.repositories import ai_insight_repo


def _conn():
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    return database.connect(path)


def test_ai_insight_upsert_and_get():
    conn = _conn()
    try:
        assert ai_insight_repo.get(conn, "t1", "b1") is None
        ai_insight_repo.save(conn, "t1", "b1", "# 第一版", "m1")
        row = ai_insight_repo.get(conn, "t1", "b1")
        assert row["content_md"] == "# 第一版"
        assert row["model"] == "m1"
        # 覆盖式保存：重新生成后旧报告被替换
        ai_insight_repo.save(conn, "t1", "b1", "# 第二版", "m2")
        rows = conn.execute(
            "SELECT COUNT(*) FROM ai_insight WHERE task_id='t1' AND batch='b1'"
        ).fetchone()[0]
        assert rows == 1
        assert ai_insight_repo.get(conn, "t1", "b1")["content_md"] == "# 第二版"
        # 不同批次互不影响
        ai_insight_repo.save(conn, "t1", "b2", "# 另一批次", "m2")
        assert ai_insight_repo.get(conn, "t1", "b1")["content_md"] == "# 第二版"
        assert ai_insight_repo.get(conn, "t1", "b2")["content_md"] == "# 另一批次"
    finally:
        conn.close()


def test_ai_insight_pairs_for_filter():
    conn = _conn()
    try:
        ai_insight_repo.save(conn, "t1", "b1", "x", None)
        ai_insight_repo.save(conn, "t1", "b2", "y", None)
        ai_insight_repo.save(conn, "t2", "b9", "z", None)
        pairs = ai_insight_repo.list_pairs(conn)
        assert pairs["t1"] == ["b2", "b1"]
        assert pairs["t2"] == ["b9"]
    finally:
        conn.close()
