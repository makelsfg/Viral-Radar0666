"""采集监督 Agent 轨迹表 agent_run / agent_step 的数据访问（失败静默，不影响采集）。"""
from __future__ import annotations

import json
from datetime import datetime


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def start_run(conn, run_id: str, batch: str | None, platform: str, task_id: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO agent_run(run_id, batch, platform, task_id, status, started_at)"
        " VALUES(?,?,?,?,?,?)",
        (run_id, batch, platform, task_id, "running", _now()),
    )
    conn.commit()


def add_step(
    conn,
    run_id: str,
    idx: int,
    state_hash: str,
    state: dict,
    tool: str,
    args: dict,
    result: dict,
    thought: str,
) -> None:
    slim_state = {k: state.get(k) for k in ("state", "url", "signals", "budget")}
    conn.execute(
        "INSERT INTO agent_step(run_id, idx, state_hash, state_json, tool, args_json,"
        " result_json, thought, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            run_id, idx, state_hash,
            json.dumps(slim_state, ensure_ascii=False)[:4000],
            tool,
            json.dumps(args or {}, ensure_ascii=False)[:2000],
            json.dumps(result or {}, ensure_ascii=False)[:2000],
            thought[:200],
            _now(),
        ),
    )
    conn.commit()


def finish_run(conn, run_id: str, status: str, steps: int, llm_calls: int, summary: str) -> None:
    conn.execute(
        "UPDATE agent_run SET status=?, steps=?, llm_calls=?, summary=?, finished_at=?"
        " WHERE run_id=?",
        (status, steps, llm_calls, (summary or "")[:500], _now(), run_id),
    )
    conn.commit()


def mark_batch_recovered(conn, batch: str, task_id: str) -> None:
    conn.execute(
        "UPDATE batch_run SET agent_recovered=1 WHERE batch=? AND task_id=?",
        (batch, task_id),
    )
    conn.commit()
