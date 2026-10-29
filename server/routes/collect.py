"""采集接口：按关键词与采样参数创建任务，支持续采。"""
from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from bkr import db
from bkr.collector import batch_id
from bkr.config import Task

from ..jobs import BusyError, manager

router = APIRouter()


class CollectRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=50)
    pages: int = Field(default=1, ge=1, le=10)
    detail_top: int = Field(default=6, ge=0, le=200)
    detail_order: str = Field(default="rank", pattern="^(rank|sold)$")
    price_min: float | None = Field(default=None, ge=0)
    price_max: float | None = Field(default=None, ge=0)
    exclude_words: list[str] = Field(default_factory=list)


def _task_id_for(keyword: str) -> str:
    digest = hashlib.md5(keyword.strip().encode("utf-8")).hexdigest()[:8]
    return f"kw-{digest}"


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _task_from_db(task_id: str) -> Task:
    conn = db.connect()
    try:
        r = conn.execute("SELECT * FROM dim_task WHERE task_id=?", (task_id,)).fetchone()
    finally:
        conn.close()
    if not r:
        raise HTTPException(status_code=404, detail="任务不存在")
    return Task(
        task_id=r["task_id"],
        name=r["name"],
        keywords=_loads(r["keywords"], [r["name"]]),
        sort=r["sort"] or "sale-desc",
        pages=int(r["pages"] or 2),
        detail_top=int(r["detail_top"] or 10),
        detail_order=str(r["detail_order"] or "rank"),
        price_min=r["price_min"],
        price_max=r["price_max"],
        exclude_words=_loads(r["exclude_words"], []),
    )


@router.post("/collect")
def collect(req: CollectRequest):
    keyword = req.keyword.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="请输入商品关键词")
    if req.price_min is not None and req.price_max is not None and req.price_min > req.price_max:
        raise HTTPException(status_code=400, detail="价格下限不能大于上限")

    task = Task(
        task_id=_task_id_for(keyword),
        name=keyword,
        keywords=[keyword],
        pages=req.pages,
        detail_top=req.detail_top,
        detail_order=req.detail_order,
        price_min=req.price_min,
        price_max=req.price_max,
        exclude_words=[w.strip() for w in req.exclude_words if w.strip()],
    )
    try:
        return manager.start_collect(task, batch_id())
    except BusyError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/jobs/{job_id}")
def job_status(job_id: str):
    job = manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@router.post("/jobs/{job_id}/resume")
def resume(job_id: str):
    job = manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.get("type") != "collect":
        raise HTTPException(status_code=400, detail="该任务不可续采")
    if job.get("status") != "paused_captcha":
        raise HTTPException(status_code=400, detail="当前状态无需续采")
    task = _task_from_db(job["task_id"])
    try:
        return manager.start_collect(task, job["batch"], resume=True)
    except BusyError as e:
        raise HTTPException(status_code=409, detail=str(e))