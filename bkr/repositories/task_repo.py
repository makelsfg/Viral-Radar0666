"""任务配置表 dim_task 的数据访问。"""
from __future__ import annotations

import json


def upsert_task(conn, task) -> None:
    conn.execute(
        """INSERT INTO dim_task(task_id, platform, name, keywords, sort, pages, detail_top,
                               detail_order, pace, price_min, price_max, exclude_words, enabled, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,datetime('now'))
           ON CONFLICT(task_id) DO UPDATE SET
             platform=excluded.platform, name=excluded.name, keywords=excluded.keywords,
             sort=excluded.sort, pages=excluded.pages, detail_top=excluded.detail_top,
             detail_order=excluded.detail_order, pace=excluded.pace,
             price_min=excluded.price_min, price_max=excluded.price_max,
             exclude_words=excluded.exclude_words""",
        (
            task.task_id,
            getattr(task, "platform", "tmall"),
            task.name,
            json.dumps(task.keywords, ensure_ascii=False),
            task.sort,
            task.pages,
            task.detail_top,
            task.detail_order,
            getattr(task, "pace", "safe"),
            task.price_min,
            task.price_max,
            json.dumps(task.exclude_words, ensure_ascii=False),
        ),
    )
    conn.commit()


def get_task(conn, task_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM dim_task WHERE task_id=?", (task_id,)).fetchone()
    return dict(row) if row else None


def list_tasks(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT task_id, name, keywords, pages, detail_top FROM dim_task ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def keyword_for(conn, task_id: str, fallback: str = "") -> str:
    row = conn.execute(
        "SELECT keywords, name FROM dim_task WHERE task_id=?", (task_id,)
    ).fetchone()
    if not row:
        return fallback or task_id
    try:
        kws = json.loads(row["keywords"]) if row["keywords"] else []
    except (TypeError, ValueError):
        kws = []
    if kws:
        return str(kws[0])
    return (row["name"] or fallback or task_id).split(" · ")[0]


def task_name(conn, task_id: str) -> str:
    row = conn.execute("SELECT name FROM dim_task WHERE task_id=?", (task_id,)).fetchone()
    return row["name"] if row else ""


def delete_task(conn, task_id: str) -> None:
    conn.execute("DELETE FROM dim_task WHERE task_id=?", (task_id,))
    conn.commit()
