"""Agent 主循环：观察 → 决策（LLM/规则）→ 工具 → 复观察，直到成功或上交人工。"""
from __future__ import annotations

import os
import time
import uuid

from . import store
from .observe import observe, state_hash
from .policy import TOOLS_DESC, llm_decide, rule_decide
from .prompts import build_system, build_user
from .tools import ToolBox


def agent_enabled() -> bool:
    return (os.environ.get("BK_AGENT_ENABLED") or "1").strip().lower() not in ("0", "false", "off")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


def _brief(result: dict) -> dict:
    keys = ("ok", "error", "count", "mode", "ready", "solved", "url_changed",
            "dom_order", "scrolled", "product_texts", "path")
    return {k: result[k] for k in keys if k in result}


def supervised_recover(page, task, reason: str = "", use_llm: bool | None = None) -> dict:
    """确定性路径失败后的接管入口。

    返回 {"ok": bool, "status": "success|escalated|failed", "items": [...], "summary": str}
    """
    batch = getattr(task, "_batch", None)
    platform = getattr(task, "platform", "pdd")
    task_id = getattr(task, "task_id", "")
    toolbox = ToolBox(page, task)

    use_llm = agent_enabled() if use_llm is None else bool(use_llm)
    max_steps = max(1, _env_int("BK_AGENT_MAX_STEPS", 8))
    max_seconds = max(30, _env_int("BK_AGENT_MAX_SECONDS", 600))
    deadline = time.time() + max_seconds

    run_id = uuid.uuid4().hex[:10]
    store.start_run(run_id, batch, platform, task_id)
    print(f"  [agent] 接管（原因：{reason or '确定性路径失败'}；llm={'on' if use_llm else 'off'}；"
          f"budget steps={max_steps}）")

    history: list[dict] = []
    llm_calls = 0
    llm_fail_streak = 0
    steps = 0
    status, summary, items = "failed", "", []
    last_sig, same_sig = "", 0

    for idx in range(1, max_steps + 1):
        if time.time() > deadline:
            summary = "总耗时超限"
            break
        state = observe(page, budget=toolbox.budget(), history=history)
        sig = state_hash(state)
        system = build_system(task, toolbox.budget(), TOOLS_DESC)
        user = build_user(state)

        decision = None
        if use_llm and llm_fail_streak < 2:
            decision = llm_decide(state, system, user)
            llm_calls += 1
            if decision is None:
                llm_fail_streak += 1
        if decision is None:
            decision = rule_decide(state, toolbox.budget())

        tool = decision.get("tool") or "escalate"
        args = decision.get("args") or {}
        thought = decision.get("thought") or ""
        print(f"  [agent] step={idx} state={state['state']} tool={tool} "
              f"args={args} thought={thought}")

        if tool == "finish":
            res = toolbox.call("extract_candidates", {"strategy": "ssr_script"})
            got = len(toolbox.last_items)
            store.add_step(run_id, idx, sig, state, tool, args, _brief(res), thought)
            if got > 0:
                items = toolbox.last_items
                status = "success"
                summary = f"恢复 {got} 条候选（{args.get('summary') or 'SSR'}）"
                steps = idx
                break
            history.append({"tool": "finish", "result": {"ok": False, "count": 0}})
            same_sig = same_sig + 1 if sig == last_sig else 1
            last_sig = sig
            if same_sig >= 3:
                summary = "finish 无数据且连续无进展"
                break
            continue

        if tool == "escalate":
            status = "escalated"
            summary = str(args.get("message") or args.get("reason_code") or "escalate")
            store.add_step(run_id, idx, sig, state, tool, args, {"ok": False}, thought)
            steps = idx
            break

        result = toolbox.call(tool, args)
        store.add_step(run_id, idx, sig, state, tool, args, _brief(result), thought)
        history.append({"tool": tool, "result": _brief(result)})
        steps = idx

        if sig == last_sig:
            same_sig += 1
        else:
            same_sig = 1
        last_sig = sig
        if same_sig >= 3:
            status = "escalated"
            summary = "连续三步状态无变化，停止（避免空转）"
            break

    store.finish_run(run_id, status, steps, llm_calls, summary or "未完成")
    if status == "success":
        store.mark_batch_recovered(batch, task_id)
    print(f"  [agent] 结束：{status}；{summary}（steps={steps}, llm_calls={llm_calls}）")
    return {"ok": status == "success", "status": status, "items": items, "summary": summary}