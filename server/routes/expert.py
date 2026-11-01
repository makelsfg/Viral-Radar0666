"""大佬分析接口：创建运行、查询、取消、删除、导出。"""
from __future__ import annotations

import re
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from bkr.expert.service import service

router = APIRouter()


class SourceModel(BaseModel):
    type: str = Field(pattern="^(batch|report|note)$")
    task_id: str | None = None
    batch: str | None = None
    name: str | None = None
    content: str | None = None


class RunRequest(BaseModel):
    title: str = Field(default="大佬分析", max_length=120)
    keyword: str = Field(default="", max_length=80)
    sources: list[SourceModel] = Field(default_factory=list)
    max_rounds: int = Field(default=3, ge=1, le=5)
    pass_score: int = Field(default=85, ge=60, le=100)


def _safe_name(text: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", text or "").strip("_")
    return cleaned or "大佬分析"


@router.post("/expert/runs")
def create_run(req: RunRequest):
    try:
        run = service.create(
            title=req.title,
            keyword=req.keyword,
            sources=[s.model_dump() for s in req.sources],
            max_rounds=req.max_rounds,
            pass_score=req.pass_score,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return run


@router.get("/expert/runs")
def list_runs(limit: int = Query(default=20, ge=1, le=100)):
    return service.list(limit)


@router.get("/expert/runs/{run_id}")
def get_run(run_id: str):
    run = service.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行不存在")
    return run


@router.post("/expert/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    if not service.get(run_id):
        raise HTTPException(status_code=404, detail="运行不存在")
    ok = service.cancel(run_id)
    if not ok:
        raise HTTPException(status_code=400, detail="任务已结束，无法取消")
    return {"ok": True}


@router.delete("/expert/runs/{run_id}")
def delete_run(run_id: str):
    if not service.delete(run_id):
        raise HTTPException(status_code=404, detail="运行不存在")
    return {"ok": True}


@router.get("/expert/runs/{run_id}/export")
def export_run(run_id: str):
    run = service.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行不存在")
    report = run.get("final_report_md") or ""
    if not report:
        raise HTTPException(status_code=400, detail="报告尚未生成")
    filename = f"大佬分析_{_safe_name(run.get('title') or '')}_{run.get('created_at', '')[:10]}.md"
    return StreamingResponse(
        iter([report]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )