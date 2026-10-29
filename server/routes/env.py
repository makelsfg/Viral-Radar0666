"""环境与登录接口。"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime

from fastapi import APIRouter, HTTPException

from bkr import __version__
from bkr.config import PROFILE_DIR

from ..jobs import BusyError, manager

router = APIRouter()
_STARTED_AT = datetime.now().isoformat(timespec="seconds")


@router.get("/health")
def health():
    """版本探针：用于确认当前 8765 端口上跑的是不是最新后端。"""
    return {
        "version": __version__,
        "profile": PROFILE_DIR.name,
        "started_at": _STARTED_AT,
        "pid": os.getpid(),
    }


def is_logged_in() -> bool:
    """读取持久化 profile 中的 cookie，判断淘宝登录态是否有效。"""
    candidates = [
        PROFILE_DIR / "Default" / "Network" / "Cookies",
        PROFILE_DIR / "Default" / "Cookies",
    ]
    cookie_file = next((p for p in candidates if p.exists()), None)
    if cookie_file is None:
        return False
    try:
        # as_uri 会做百分号编码，兼容 Windows 中文路径
        conn = sqlite3.connect(cookie_file.as_uri() + "?mode=ro", uri=True)
        try:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM Cookies WHERE host_key LIKE '%taobao.com'"
                ).fetchall()
            }
        finally:
            conn.close()
        return bool({"unb", "tracknick"} & names)
    except sqlite3.Error:
        return False


@router.get("/env")
def env():
    return {"logged_in": is_logged_in(), "busy": manager.active()}


@router.post("/login")
def start_login():
    try:
        return manager.start_login()
    except BusyError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/login/{job_id}")
def login_status(job_id: str):
    job = manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job