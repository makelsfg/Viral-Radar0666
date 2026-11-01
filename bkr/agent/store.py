"""Agent 运行记录（agent_run / agent_step）与批次打标。"""
from __future__ import annotations

import json
from datetime import datetime

from .. import db


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def start_run(run_id: str, batch: str | None, platform: str, task_id: str) -> None:
    try:
        conn = db.connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO agent_run(run_id, batch, platform, task_id, status, started_at)"
                " VALUES(?,?,?,?,?,?)",
                (run_id, batch, platform, task_id, "running", _now()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def add_step(
    run_id: str, idx: int, state_hash_value: str, state: dict,
    tool: str, args: dict, result: dict, thought: str,
) -> None:
    try:
        slim_state = {k: state.get(k) for k in ("state", "url", "signals", "budget")}
        conn = db.connect()
        try:
            conn.execute(
                "INSERT INTO agent_step(run_id, idx, state_hash, state_json, tool, args_json,"
                " result_json, thought, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    run_id, idx, state_hash_value,
                    json.dumps(slim_state, ensure_ascii=False)[:4000],
                    tool,
                    json.dumps(args or {}, ensure_ascii=False)[:2000],
                    json.dumps(result or {}, ensure_ascii=False)[:2000],
                    thought[:200],
                    _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def finish_run(run_id: str, status: str, steps: int, llm_calls: int, summary: str) -> None:
    try:
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE agent_run SET status=?, steps=?, llm_calls=?, summary=?, finished_at=?"
                " WHERE run_id=?",
                (status, steps, llm_calls, (summary or "")[:500], _now(), run_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def mark_batch_recovered(batch: str | None, task_id: str) -> None:
    """Agent 恢复成功的批次打标（用于自愈率统计，报告/数据页后续展示）。"""
    if not batch:
        return
    try:
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE batch_run SET agent_recovered=1 WHERE batch=? AND task_id=?",
                (batch, task_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass