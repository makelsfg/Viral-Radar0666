"""大佬分析运行记录持久化。"""
from __future__ import annotations

import json
from datetime import datetime

from .. import db


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def create_run(
    run_id: str,
    title: str,
    keyword: str,
    sources: list[dict],
    max_rounds: int,
    pass_score: int,
) -> dict:
    conn = db.connect()
    try:
        conn.execute(
            """INSERT INTO expert_run
               (id, title, keyword, sources_json, status, max_rounds, pass_score, created_at)
               VALUES (?, ?, ?, ?, 'running', ?, ?, ?)""",
            (
                run_id, title, keyword,
                json.dumps(sources, ensure_ascii=False),
                max_rounds, pass_score, _now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_run(run_id) or {}


def _row_to_run(row) -> dict:
    d = dict(row)
    d["sources"] = _loads(d.pop("sources_json", None), [])
    return d


def get_run(run_id: str, with_rounds: bool = True) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM expert_run WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        run = _row_to_run(row)
        if with_rounds:
            rounds = conn.execute(
                """SELECT round_no, stage, content_md, review_json, verdict, score, created_at
                   FROM expert_round WHERE run_id=? ORDER BY round_no, stage""",
                (run_id,),
            ).fetchall()
            run["rounds"] = []
            for r in rounds:
                d = dict(r)
                d["review"] = _loads(d.pop("review_json", None), None)
                run["rounds"].append(d)
        return run
    finally:
        conn.close()


def list_runs(limit: int = 20) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT id, title, keyword, status, iterations, max_rounds, pass_score,
                      created_at, finished_at
               FROM expert_run ORDER BY created_at DESC LIMIT ?""",
            (max(1, min(int(limit or 20), 100)),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_run(run_id: str, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    conn = db.connect()
    try:
        conn.execute(
            f"UPDATE expert_run SET {sets} WHERE id=?",
            (*fields.values(), run_id),
        )
        conn.commit()
    finally:
        conn.close()


def add_round(
    run_id: str,
    round_no: int,
    stage: str,
    content_md: str | None = None,
    review: dict | None = None,
    verdict: str | None = None,
    score: int | None = None,
) -> None:
    conn = db.connect()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO expert_round
               (run_id, round_no, stage, content_md, review_json, verdict, score, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, round_no, stage, content_md,
                json.dumps(review, ensure_ascii=False) if review else None,
                verdict, score, _now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def delete_run(run_id: str) -> bool:
    conn = db.connect()
    try:
        cur = conn.execute("DELETE FROM expert_run WHERE id=?", (run_id,))
        conn.execute("DELETE FROM expert_round WHERE run_id=?", (run_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()