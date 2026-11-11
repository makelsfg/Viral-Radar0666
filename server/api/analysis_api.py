"""控制层：分析接口（批次统计、AI 解读、单批次报告）。"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from server.services import analysis_service, insight_service

router = APIRouter()


@router.get("/analysis/summary")
def summary(task_id: str = Query(...), batch: str = Query(...)):
    data = analysis_service.task_summary(task_id, batch)
    if not data:
        raise HTTPException(status_code=404, detail="该批次没有数据")
    return data


@router.get("/analysis/llm-status")
def llm_status():
    return insight_service.llm_status()


class InsightsRequest(BaseModel):
    task_id: str
    batch: str


@router.post("/analysis/insights")
def insights(req: InsightsRequest):
    """生成某平台任务的 AI 解读；成功自动保存（同批次覆盖旧报告）。"""
    if not req.task_id:
        raise HTTPException(status_code=400, detail="缺少 task_id（AI 解读按平台分别生成）")
    try:
        return insight_service.generate(req.task_id, req.batch)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/analysis/insights/saved")
def saved_insights(task_id: str = Query(...), batch: str = Query(...)):
    """读取已保存的 AI 解读（打开分析页自动加载）。"""
    return insight_service.get_saved(task_id, batch)


@router.get("/analysis/insights/export")
def export_insights(task_id: str = Query(...), batch: str = Query(...)):
    """导出 AI 解读为 .md 文件。"""
    content = insight_service.export_markdown(task_id, batch)
    if content is None:
        raise HTTPException(status_code=404, detail="该批次还没有 AI 解读报告")
    filename = insight_service.file_name(task_id, batch)
    return StreamingResponse(
        iter([content]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/analysis/report")
def batch_report(batch: str = Query(...)):
    data = analysis_service.build_batch_report(batch)
    if not data:
        raise HTTPException(status_code=404, detail="该批次没有数据")
    return data


@router.get("/analysis/report/export")
def batch_report_export(batch: str = Query(...)):
    data = analysis_service.build_batch_report(batch)
    if not data:
        raise HTTPException(status_code=404, detail="该批次没有数据")
    filename = f"{data['keyword']}_单批次报告_{batch}.md"
    return StreamingResponse(
        iter([data["markdown"]]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
