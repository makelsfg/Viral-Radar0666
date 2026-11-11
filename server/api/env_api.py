"""控制层：环境与登录接口。"""
from __future__ import annotations

import os
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from bkr import __version__
from bkr.browser import CDP_URL, browser_channel, engine_name
from bkr.config import profile_dir_for

from ..services.collect_service import BusyError, manager
from ..services.env_service import env_status

router = APIRouter()
_STARTED_AT = datetime.now().isoformat(timespec="seconds")


@router.get("/health")
def health():
    """版本探针：确认端口上跑的是不是最新后端，以及当前引擎/通道。"""
    return {
        "version": __version__,
        "profile": profile_dir_for("").name,
        "engine": engine_name(),
        "channel": browser_channel(),
        "cdp": bool(CDP_URL),
        "detail_provider": os.environ.get("BK_DETAIL_PROVIDER", "local"),
        "agent_enabled": (os.environ.get("BK_AGENT_ENABLED") or "1").strip().lower()
        not in ("0", "false", "off"),
        "started_at": _STARTED_AT,
        "pid": os.getpid(),
    }


@router.get("/env")
def env():
    return env_status(manager.active())


class LoginRequest(BaseModel):
    profile: str | None = None
    site: str = "taobao"


@router.post("/login")
def start_login(req: LoginRequest | None = None):
    req = req or LoginRequest()
    try:
        return manager.start_login(req.profile, req.site)
    except BusyError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/login/{job_id}")
def login_status(job_id: str):
    job = manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job
