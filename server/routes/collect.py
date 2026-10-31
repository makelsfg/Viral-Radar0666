"""采集接口：按关键词与平台创建任务（每平台独立任务），支持续采。"""
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

ALLOWED_PLATFORMS = {"tmall", "1688"}  # 拼多多实验版下一阶段接入
PLATFORM_LABELS = {"tmall": "天猫", "1688": "1688", "pdd": "拼多多"}


class CollectRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=50)
    platforms: list[str] = Field(default_factory=lambda: ["tmall"])
    pages: int = Field(default=1, ge=1, le=10)
    detail_top: int = Field(default=6, ge=0, le=200)
    detail_order: str = Field(default="rank", pattern="^(rank|sold)$")
    pace: str = Field(default="safe", pattern="^(safe|balanced|fast)$")
    mode: str = Field(default="standard", pattern="^(standard|conservative)$")
    price_min: float | None = Field(default=None, ge=0)
    price_max: float | None = Field(default=None, ge=0)
    exclude_words: list[str] = Field(default_factory=list)


def _task_id_for(keyword: str, platform: str) -> str:
    digest = hashlib.md5(f"{keyword.strip()}|{platform}".encode("utf-8")).hexdigest()[:8]
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
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}")
    return Task(
        task_id=r["task_id"],
        name=r["name"],
        keywords=_loads(r["keywords"], [r["name"]]),
        platform=(r["platform"] if "platform" in r.keys() else "tmall") or "tmall",
        pace=(r["pace"] if "pace" in r.keys() else "safe") or "safe",
        sort=r["sort"] or "sale-desc",
        pages=int(r["pages"] or 1),
        detail_top=int(r["detail_top"] or 6),
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

    platforms: list[str] = []
    for p in req.platforms or []:
        p = (p or "").strip().lower()
        if p and p not in platforms:
            platforms.append(p)
    if not platforms:
        raise HTTPException(status_code=400, detail="请至少选择一个采集平台")
    invalid = [p for p in platforms if p not in ALLOWED_PLATFORMS]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"平台暂未接入：{', '.join(invalid)}（当前支持：天猫、1688）",
        )

    tasks = []
    for platform in platforms:
        label = PLATFORM_LABELS.get(platform, platform)
        tasks.append(
            Task(
                task_id=_task_id_for(keyword, platform),
                name=f"{keyword} · {label}",
                keywords=[keyword],
                platform=platform,
                pace=req.pace,
                pages=1 if req.mode == "conservative" else req.pages,
                detail_top=0 if req.mode == "conservative" else req.detail_top,
                detail_order=req.detail_order,
                price_min=req.price_min,
                price_max=req.price_max,
                exclude_words=[w.strip() for w in req.exclude_words if w.strip()],
            )
        )

    try:
        return manager.start_collect_many(tasks, batch_id())
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
    task_ids = job.get("_task_ids") or [job["task_id"]]
    tasks = [_task_from_db(tid) for tid in task_ids]
    try:
        return manager.start_collect_many(tasks, job["batch"], resume=True)
    except BusyError as e:
        raise HTTPException(status_code=409, detail=str(e))