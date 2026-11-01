"""大佬分析 LangGraph 状态定义。"""
from __future__ import annotations

from typing import Any, TypedDict


class ExpertState(TypedDict, total=False):
    run_id: str
    title: str
    keyword: str
    sources: list[dict]
    max_rounds: int
    pass_score: int
    evidence: dict[str, Any]
    draft_md: str
    review: dict[str, Any] | None
    iteration: int
    history: list[dict]
    status: str
    best_draft_md: str
    best_score: int
    error: str | None