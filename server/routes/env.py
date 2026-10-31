"""环境与登录接口。"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from bkr import __version__
from bkr.browser import CDP_URL, browser_channel, engine_name, has_login_cookies
from bkr.config import profile_dir_for

from ..jobs import BusyError, manager

router = APIRouter()
_STARTED_AT = datetime.now().isoformat(timespec="seconds")


def _cookie_names(profile: str) -> set[str]:
    candidates = [
        profile_dir_for(profile) / "Default" / "Network" / "Cookies",
        profile_dir_for(profile) / "Default" / "Cookies",
    ]
    cookie_file = next((p for p in candidates if p.exists()), None)
    if cookie_file is None:
        return set()
    try:
        conn = sqlite3.connect(cookie_file.as_uri() + "?mode=ro", uri=True)
        try:
            return {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM Cookies WHERE host_key LIKE '%taobao.com' OR host_key LIKE '%1688.com'"
                ).fetchall()
            }
        finally:
            conn.close()
    except sqlite3.Error:
        return set()


def _is_1688_logged_in(names: set[str]) -> bool:
    return bool({"__cn_logon__", "_cn_", "cookie2"} & names)


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
    tmall_names = _cookie_names("tmall")
    ali_names = _cookie_names("1688")
    return {
        "logged_in": has_login_cookies(tmall_names),
        "busy": manager.active(),
        "profiles": {
            "tmall": has_login_cookies(tmall_names),
            "1688": _is_1688_logged_in(ali_names),
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