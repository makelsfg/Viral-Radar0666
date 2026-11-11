"""AI 解读报告表 ai_insight 的数据访问（每个平台任务 × 批次一份，覆盖式）。"""
from __future__ import annotations

from datetime import datetime


def save(conn, task_id: str, batch: str, content_md: str, model: str | None) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "DELETE FROM ai_insight WHERE task_id=? AND batch=? AND kind='task'",
        (task_id, batch),
    )
    conn.execute(
        """INSERT INTO ai_insight(task_id, batch, kind, content_md, model, created_at)
           VALUES(?,?,'task',?,?,?)""",
        (task_id, batch, content_md, model, now),
    )
    conn.commit()


def get(conn, task_id: str, batch: str) -> dict | None:
    row = conn.execute(
        """SELECT task_id, batch, content_md, model, created_at
           FROM ai_insight WHERE task_id=? AND batch=? AND kind='task'""",
        (task_id, batch),
    ).fetchone()
    return dict(row) if row else None


def list_pairs(conn) -> dict[str, list[str]]:
    """返回 {task_id: [batch, ...]}，用于「只看有 AI 分析」过滤。"""
    rows = conn.execute(
        "SELECT task_id, batch FROM ai_insight WHERE kind='task' ORDER BY batch DESC"
    ).fetchall()
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["task_id"], []).append(r["batch"])
    return out
