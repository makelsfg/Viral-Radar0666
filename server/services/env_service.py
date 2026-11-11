"""环境服务：平台登录状态（采集期优先读库，避免 Cookies 被锁时误报）。"""
from __future__ import annotations

from bkr import database
from bkr.auth import effective_authorized
from bkr.repositories import platform_auth_repo


def platform_status(platform: str, busy: bool) -> bool:
    if not busy:
        return effective_authorized(platform)
    with database.connection() as conn:
        stored = platform_auth_repo.get_auth(conn, platform)
    return bool(stored)


def env_status(busy: bool) -> dict:
    return {
        "logged_in": platform_status("tmall", busy),
        "busy": busy,
        "profiles": {
            "tmall": platform_status("tmall", busy),
            "1688": platform_status("1688", busy),
            "pdd": platform_status("pdd", busy),
        },
    }
