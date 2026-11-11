"""大佬分析报告表 expert_run / expert_round 的数据访问。"""
from __future__ import annotations

import json
from datetime import datetime


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def create_run(
    conn,
    run_id: str,
    title: str,
    keyword: str,
    sources: list[dict],
    max_rounds: int,
    pass_score: int,
) -> None:
    conn.execute(
        """INSERT INTO expert_run
           (id, title, keyword, sources_json, status, max_rounds, pass_score, created_at)
           VALUES (?, ?, ?, ?, 'running', ?, ?, ?)""",
        (run_id, title, keyword, json.dumps(sources, ensure_ascii=False),
         max_rounds, pass_score, _now()),
    )
    conn.commit()


def list_titles(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT title FROM expert_run")}


def save_evidence(conn, run_id: str, items: list[dict]) -> None:
    slim = [
        {
            "id": str(i.get("id") or ""),
            "kind": str(i.get("kind") or ""),
            "source": str(i.get("source") or ""),
            "text": str(i.get("text") or ""),
        }
        for i in items or []
        if i.get("id")
    ]
    conn.execute(
        "UPDATE expert_run SET evidence_json=? WHERE id=?",
        (json.dumps(slim, ensure_ascii=False), run_id),
    )
    conn.commit()


def get_run(conn, run_id: str, with_rounds: bool = True) -> dict | None:
    row = conn.execute("SELECT * FROM expert_run WHERE id=?", (run_id,)).fetchone()
    if not row:
        return None
    run = dict(row)
    run["sources"] = _loads(run.pop("sources_json", None), [])
    run["evidence"] = _loads(run.pop("evidence_json", None), [])
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


def list_runs(conn, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        """SELECT id, title, keyword, status, iterations, max_rounds, pass_score,
                  created_at, finished_at
           FROM expert_run ORDER BY created_at DESC LIMIT ?""",
        (max(1, min(int(limit or 20), 100)),),
    ).fetchall()
    return [dict(r) for r in rows]


def update_run(conn, run_id: str, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(
        f"UPDATE expert_run SET {sets} WHERE id=?",
        (*fields.values(), run_id),
    )
    conn.commit()


def add_round(
    conn,
    run_id: str,
    round_no: int,
    stage: str,
    content_md: str | None = None,
    review: dict | None = None,
    verdict: str | None = None,
    score: int | None = None,
) -> None:
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


def delete_run(conn, run_id: str) -> bool:
    cur = conn.execute("DELETE FROM expert_run WHERE id=?", (run_id,))
    conn.execute("DELETE FROM expert_round WHERE run_id=?", (run_id,))
    conn.commit()
    return cur.rowcount > 0
