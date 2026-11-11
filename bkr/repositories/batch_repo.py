"""批次运行状态表 batch_run 的数据访问。"""
from __future__ import annotations


def get_batch(conn, batch: str, task_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM batch_run WHERE batch=? AND task_id=?", (batch, task_id)
    ).fetchone()
    return dict(row) if row else None


def ensure_batch(conn, batch: str, task_id: str) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO batch_run(batch, task_id, status, stage, started_at)
           VALUES(?, ?, 'running', 'list', datetime('now'))""",
        (batch, task_id),
    )
    conn.commit()


def update_batch(conn, batch: str, task_id: str, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(
        f"UPDATE batch_run SET {sets} WHERE batch=? AND task_id=?",
        (*fields.values(), batch, task_id),
    )
    conn.commit()


def update_status_for_tasks(conn, batch: str, task_ids: list[str], status: str, only: str) -> None:
    """把指定批次内处于 only 状态的记录改为 status（用于「待确认」恢复）。"""
    marks = ",".join("?" for _ in task_ids)
    conn.execute(
        f"UPDATE batch_run SET status=? WHERE batch=? AND task_id IN ({marks}) AND status=?",
        (status, batch, *task_ids, only),
    )
    conn.commit()


def reset_progress(conn, batch: str, task_id: str) -> None:
    """重新开始一个批次（非续采）：清空进度。"""
    conn.execute(
        """UPDATE batch_run
           SET status='running', stage='list', page_done=0, detail_done=0,
               items_count=0, error=NULL, started_at=datetime('now'), finished_at=NULL
           WHERE batch=? AND task_id=?""",
        (batch, task_id),
    )
    conn.commit()


def delete_batch_row(conn, batch: str, task_id: str) -> None:
    conn.execute("DELETE FROM batch_run WHERE batch=? AND task_id=?", (batch, task_id))
    conn.commit()


def delete_by_task(conn, task_id: str) -> None:
    conn.execute("DELETE FROM batch_run WHERE task_id=?", (task_id,))


def delete_by_batch(conn, task_id: str, batch: str) -> None:
    conn.execute("DELETE FROM batch_run WHERE task_id=? AND batch=?", (task_id, batch))


def delete_by_batch_tasks(conn, batch: str, task_ids: list[str]) -> None:
    marks = ",".join("?" for _ in task_ids)
    conn.execute(
        f"DELETE FROM batch_run WHERE batch=? AND task_id IN ({marks})",
        (batch, *task_ids),
    )


def list_batch_tasks(conn, batch: str) -> list[dict]:
    rows = conn.execute(
        """SELECT b.task_id, b.status, t.platform, t.name
           FROM batch_run b
           LEFT JOIN dim_task t ON t.task_id = b.task_id
           WHERE b.batch=?""",
        (batch,),
    ).fetchall()
    return [dict(r) for r in rows]
