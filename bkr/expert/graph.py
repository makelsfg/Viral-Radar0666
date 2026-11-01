"""大佬分析 LangGraph：证据 → 写稿 → 审查 → 修订/收束。"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

from .. import db
from . import store
from .evidence import PLATFORM_LABELS, build_evidence, pack_for_prompt
from .prompts import (
    REVIEWER_SYSTEM,
    WRITER_SYSTEM,
    parse_review,
    reviewer_user,
    writer_user,
)
from .tools import (
    check_number_grounding,
    check_platform_mix,
    make_tools,
)
from .state import ExpertState


class ExpertCancelled(RuntimeError):
    pass


def _env(role: str, key: str, fallback: str = "") -> str:
    prefix = f"BK_EXPERT_{role.upper()}_"
    return (os.environ.get(prefix + key) or os.environ.get(f"BK_LLM_{key}") or fallback).strip()


def _llm(role: str):
    try:
        from langchain_openai import ChatOpenAI
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "未安装 langchain-openai，请先在项目 venv 执行 pip install -r requirements.txt"
        ) from exc
    base_url = _env(role, "BASE_URL", os.environ.get("BK_LLM_BASE_URL", ""))
    api_key = _env(role, "API_KEY", os.environ.get("BK_LLM_API_KEY", ""))
    model = _env(role, "MODEL", os.environ.get("BK_LLM_MODEL", ""))
    if not api_key:
        raise RuntimeError(
            f"{'大佬' if role == 'writer' else '审查'}模型的 API Key 未配置："
            f"请在 config/settings.yaml 的 expert.{role} 填写 base_url/api_key/model"
        )
    if not model:
        raise RuntimeError(f"expert.{role}.model 未配置")
    temperature = float(os.environ.get("BK_EXPERT_TEMPERATURE", "0.3"))
    max_tokens = int(os.environ.get("BK_EXPERT_MAX_TOKENS", "12000"))
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url or None,
        temperature=temperature if role == "writer" else 0.1,
        max_tokens=max_tokens,
        timeout=180,
        max_retries=2,
    )


def _text(resp) -> str:
    content = getattr(resp, "content", resp)
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                parts.append(str(c.get("text") or ""))
            else:
                parts.append(str(c))
        return "".join(parts)
    return str(content or "")


def _invoke_with_tools(llm, messages, tools, max_steps: int = 4, use_tools: bool = True) -> str:
    """有界工具循环；供应商不支持工具调用时退回普通对话。use_tools=False 直接单轮对话。"""
    if not use_tools:
        resp = llm.invoke(messages)
        text = _text(resp)
        if not text.strip():
            meta = getattr(resp, "response_metadata", None) or {}
            print(f"[expert] 模型返回空内容（finish_reason={meta.get('finish_reason')}）")
        return text
    try:
        from langchain_core.messages import HumanMessage, ToolMessage

        bound = llm.bind_tools(tools)
    except Exception:
        return _text(llm.invoke(messages))
    tool_map = {t.name: t for t in tools}
    for _ in range(max_steps):
        try:
            resp = bound.invoke(messages)
        except Exception:
            return _text(llm.invoke(messages))
        messages.append(resp)
        calls = getattr(resp, "tool_calls", None) or []
        if not calls:
            return _text(resp)
        for call in calls:
            name = call.get("name") or ""
            fn = tool_map.get(name)
            try:
                result = fn.invoke(call.get("args") or {}) if fn else f"未知工具：{name}"
            except Exception as exc:  # noqa: BLE001
                result = f"工具执行错误：{type(exc).__name__}: {exc}"
            messages.append(
                ToolMessage(content=str(result), tool_call_id=call.get("id") or name)
            )
    messages.append(
        HumanMessage(content="不要再调用工具。请基于已有信息直接输出最终报告全文。")
    )
    resp = llm.invoke(messages)
    text = _text(resp)
    if not text.strip():
        meta = getattr(resp, "response_metadata", None) or {}
        print(f"[expert] 工具循环后仍为空（finish_reason={meta.get('finish_reason')}）")
    return text


def _sources_label(evidence: dict) -> str:
    parts = []
    for b in (evidence.get("meta") or {}).get("batches") or []:
        parts.append(f"{b['label']}/{b['name']}/{b['batch']}")
    return "；".join(parts) or "未知来源"


def _writer(
    state: ExpertState,
    feedback: dict | None,
    round_no: int,
) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage

    evidence = state["evidence"]
    llm = _llm("writer")
    tools = make_tools(evidence)
    messages = [
        SystemMessage(content=WRITER_SYSTEM),
        HumanMessage(
            content=writer_user(
                title=state["title"],
                keyword=state.get("keyword") or "",
                evidence_text=pack_for_prompt(evidence),
                sources_label=_sources_label(evidence),
                generated_at=(evidence.get("meta") or {}).get("generated_at", ""),
                round_no=round_no,
                feedback=feedback,
            )
        ),
    ]
    # 修订轮不挂工具：多为编辑/补全任务，钉死单轮可显著降低“工具循环后空输出”的概率
    text = _invoke_with_tools(llm, messages, tools, use_tools=(feedback is None))
    if (text or "").strip():
        return text
    from langchain_core.messages import HumanMessage

    messages.append(HumanMessage(
        content="上一次输出为空。请直接输出完整报告全文（Markdown，八节），不要调用任何工具。"
    ))
    retry = _invoke_with_tools(llm, messages, tools, use_tools=False)
    if (retry or "").strip():
        print("[expert] 空输出已通过无工具重试恢复")
    return retry


def _reviewer(state: ExpertState) -> dict:
    from langchain_core.messages import HumanMessage, SystemMessage

    draft = state.get("draft_md") or ""
    evidence = state["evidence"]
    checks = {
        "ungrounded_numbers": check_number_grounding(draft),
        "platform_mix": check_platform_mix(draft),
    }
    llm = _llm("reviewer")
    messages = [
        SystemMessage(content=REVIEWER_SYSTEM),
        HumanMessage(
            content=reviewer_user(
                draft_md=draft,
                evidence_text=pack_for_prompt(evidence, 50_000),
                checks=checks,
                pass_score=int(state.get("pass_score") or 85),
            )
        ),
    ]
    raw = _text(llm.invoke(messages))
    review = parse_review(raw)
    if review is None:
        messages.append(HumanMessage(
            content="你的输出不是合法 JSON。请重新只输出 JSON 对象，不要代码围栏。"
        ))
        raw = _text(llm.invoke(messages))
        review = parse_review(raw)
    if review is None:
        review = {
            "verdict": "revise",
            "score": 0,
            "dimension_scores": {},
            "issues": [{
                "severity": "blocker",
                "location": "整体",
                "problem": "审查 Agent 输出无法解析为 JSON",
                "fix": "重新生成报告并确保审查输出严格 JSON",
                "evidence_ids": [],
            }],
            "summary": raw[:300],
        }

    # 代码侧兜底执行通过条件，防止审查 Agent“放水”
    dims = review.get("dimension_scores") or {}
    if not isinstance(dims, dict):
        dims = {}
    issues = review.get("issues")
    if not isinstance(issues, list):
        issues = []
        review["issues"] = issues
    blockers = [
        i for i in issues
        if isinstance(i, dict) and str(i.get("severity")).lower() == "blocker"
    ]
    try:
        score = int(review.get("score") or 0)
    except (TypeError, ValueError):
        score = 0
    review["score"] = score
    evidence_score = float(dims.get("evidence") or 0)
    action_score = float(dims.get("actionability") or 0)
    if review.get("verdict") == "pass" and (
        score < int(state.get("pass_score") or 85)
        or blockers
        or evidence_score < 75
        or action_score < 70
    ):
        review["verdict"] = "revise"
        review.setdefault("issues", []).append({
            "severity": "major",
            "location": "整体",
            "problem": "未达到通过硬条件（分数/证据/可执行性）",
            "fix": "补齐证据引用，强化行动方案后重新提交",
            "evidence_ids": [],
        })
    return review


def build_graph(run_id: str, publisher: Callable[[str, dict], None],
                cancel_event):
    """构造并编译 LangGraph；每次运行独立闭包，方便推送事件。"""
    try:
        from langgraph.graph import END, START, StateGraph
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "未安装 langgraph，请先在项目 venv 执行 pip install -r requirements.txt"
        ) from exc

    def check_cancel() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise ExpertCancelled("用户已取消")

    def node_prepare(state: ExpertState) -> dict:
        check_cancel()
        publisher("stage", {"stage": "evidence", "message": "正在整理证据"})
        evidence = build_evidence(state.get("sources") or [])
        if not evidence.get("evidence"):
            raise RuntimeError("没有可用证据：请选择至少一个非空批次，或上传报告/笔记")
        store.update_run(run_id, iterations=0)
        publisher("stage", {
            "stage": "evidence_done",
            "message": f"证据整理完成（{evidence['meta']['evidence_count']} 条）",
        })
        return {"evidence": evidence}

    def node_draft(state: ExpertState) -> dict:
        check_cancel()
        round_no = int(state.get("iteration") or 1)
        publisher("stage", {"stage": "draft", "iteration": round_no,
                            "message": f"大佬正在撰写第 {round_no} 轮报告"})
        draft = _writer(state, None, round_no)
        if not (draft or "").strip():
            raise RuntimeError("大佬 Agent 返回了空报告")
        store.add_round(run_id, round_no, "draft", content_md=draft)
        store.update_run(run_id, iterations=round_no)
        return {
            "draft_md": draft,
            "iteration": round_no,
            "history": (state.get("history") or []) + [{
                "round": round_no, "stage": "draft", "length": len(draft),
            }],
        }

    def node_review(state: ExpertState) -> dict:
        check_cancel()
        round_no = int(state.get("iteration") or 1)
        publisher("stage", {"stage": "review", "iteration": round_no,
                            "message": "审查 Agent 正在审查"})
        review = _reviewer(state)
        store.add_round(
            run_id, round_no, "review",
            review=review,
            verdict=review.get("verdict"),
            score=review.get("score"),
        )
        best_draft = state.get("best_draft_md") or ""
        best_score = int(state.get("best_score") or -1)
        if int(review.get("score") or 0) > best_score:
            best_draft = state.get("draft_md") or best_draft
            best_score = int(review.get("score") or 0)
        publisher("review_done", {
            "iteration": round_no,
            "review": review,
            "message": f"审查结果：{review.get('verdict')}（{review.get('score')} 分）",
        })
        return {
            "review": review,
            "best_draft_md": best_draft,
            "best_score": best_score,
            "history": (state.get("history") or []) + [{
                "round": round_no, "stage": "review",
                "verdict": review.get("verdict"), "score": review.get("score"),
            }],
        }

    def node_revise(state: ExpertState) -> dict:
        check_cancel()
        round_no = int(state.get("iteration") or 1) + 1
        review = state.get("review") or {}
        publisher("stage", {"stage": "revise", "iteration": round_no,
                            "message": f"根据审查意见重写第 {round_no} 轮"})
        draft = _writer(state, review, round_no)
        if not (draft or "").strip():
            raise RuntimeError("大佬 Agent 修订后返回了空报告")
        store.add_round(run_id, round_no, "revise", content_md=draft)
        store.update_run(run_id, iterations=round_no)
        return {
            "draft_md": draft,
            "iteration": round_no,
            "history": (state.get("history") or []) + [{
                "round": round_no, "stage": "revise", "length": len(draft),
            }],
        }

    def node_finalize(state: ExpertState) -> dict:
        draft = state.get("draft_md") or ""
        store.update_run(run_id, status="done", final_report_md=draft)
        publisher("done", {"status": "done", "score": (state.get("review") or {}).get("score")})
        return {"status": "done", "best_draft_md": draft}

    def node_human(state: ExpertState) -> dict:
        draft = state.get("best_draft_md") or state.get("draft_md") or ""
        review = state.get("review") or {}
        store.update_run(run_id, status="needs_human", final_report_md=draft)
        publisher("done", {"status": "needs_human", "score": review.get("score"),
                           "message": "达到最大轮次仍未通过，需人工复核"})
        return {"status": "needs_human", "draft_md": draft}

    def route(state: ExpertState) -> str:
        review = state.get("review") or {}
        if review.get("verdict") == "pass":
            return "finalize"
        if int(state.get("iteration") or 1) < int(state.get("max_rounds") or 3):
            return "revise"
        return "human"

    graph = StateGraph(ExpertState)
    graph.add_node("prepare_evidence", node_prepare)
    graph.add_node("expert_draft", node_draft)
    graph.add_node("reviewer", node_review)
    graph.add_node("expert_revise", node_revise)
    graph.add_node("finalize", node_finalize)
    graph.add_node("human_fallback", node_human)

    graph.add_edge(START, "prepare_evidence")
    graph.add_edge("prepare_evidence", "expert_draft")
    graph.add_edge("expert_draft", "reviewer")
    graph.add_conditional_edges(
        "reviewer", route,
        {"finalize": "finalize", "revise": "expert_revise", "human": "human_fallback"},
    )
    graph.add_edge("expert_revise", "reviewer")
    graph.add_edge("finalize", END)
    graph.add_edge("human_fallback", END)
    return graph.compile()