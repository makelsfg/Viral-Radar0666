"""环境与登录接口。"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from bkr import __version__
from bkr import db as dbm
from bkr.auth import effective_authorized
from bkr.browser import CDP_URL, browser_channel, engine_name
from bkr.config import profile_dir_for

from ..jobs import BusyError, manager

router = APIRouter()
_STARTED_AT = datetime.now().isoformat(timespec="seconds")


def _cookie_names(profile: str) -> set[str]:
    return cookie_names(profile)


def _is_1688_logged_in(names: set[str]) -> bool:
    return bool({"__cn_logon__", "_cn_"} & names)


@router.get("/health")
def health():
    """版本探针：确认 8765 端口上跑的是不是最新后端，以及当前引擎/通道。"""
    return {
        "version": __version__,
        "profile": profile_dir_for("").name,
        "engine": engine_name(),
        "channel": browser_channel(),
        "cdp": bool(CDP_URL),
        "detail_provider": os.environ.get("BK_DETAIL_PROVIDER", "local"),
        "started_at": _STARTED_AT,
        "pid": os.getpid(),
    }


@router.get("/env")
def env():
    busy = manager.active()

    def status(platform: str) -> bool:
        if not busy:
            return effective_authorized(platform)
        # 采集进行中 Edge 会锁 Cookies：直接返回数据库里的授权状态，避免翻红
        conn = dbm.connect()
        try:
            stored = dbm.get_platform_auth(conn, platform)
        finally:
            conn.close()
        return bool(stored)

    return {
        "logged_in": status("tmall"),
        "busy": busy,
        "profiles": {
            "tmall": status("tmall"),
            "1688": status("1688"),
        },
    }


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