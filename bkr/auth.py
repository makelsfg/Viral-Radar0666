"""平台授权状态：读取各平台独立 profile 的 cookie（处理文件锁与过期）。

约定：
  tmall -> 必须 cookie2 + unb（严格，tracknick 半登录不算）
  1688  -> __cn_logon__ / _cn_ 等登录标记
"""
from __future__ import annotations

import datetime
import shutil
import sqlite3
import tempfile
from pathlib import Path

from .browser import has_login_cookies, has_pdd_login_cookies
from .config import profile_dir_for


def _query_names(db_path: Path) -> set[str]:
    # Chromium expires_utc: microseconds since 1601-01-01 (UTC); 0 = session cookie
    now_us = int(
        (datetime.datetime.utcnow() - datetime.datetime(1601, 1, 1)).total_seconds() * 1_000_000
    )
    conn = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            """SELECT name FROM Cookies
               WHERE (host_key LIKE '%taobao.com' OR host_key LIKE '%1688.com'
                      OR host_key LIKE '%pinduoduo.com' OR host_key LIKE '%yangkeduo.com')
                 AND (expires_utc = 0 OR expires_utc > ?)""",
            (now_us,),
        ).fetchall()
        return {row[0] for row in rows}
    finally:
        conn.close()


def _read_names(platform: str) -> set[str]:
    d = profile_dir_for(platform)
    candidates = [d / "Default" / "Network" / "Cookies", d / "Default" / "Cookies"]
    cookie_file = next((p for p in candidates if p.exists()), None)
    if cookie_file is None:
        return set()
    try:
        return _query_names(cookie_file)
    except sqlite3.Error:
        # Edge 运行时会锁住 Cookies，复制一份临时副本再读
        tmp_path = None
        try:
            fd, tmp_name = tempfile.mkstemp(suffix=".db")
            tmp_path = Path(tmp_name)
            import os

            os.close(fd)
            shutil.copy2(cookie_file, tmp_path)
            return _query_names(tmp_path)
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except Exception:
                    pass


def cookie_names(platform: str) -> set[str]:
    try:
        return _read_names(platform)
    except Exception:
        return set()


def _authorized_from_names(platform: str, names: set[str]) -> bool:
    if platform == "tmall":
        return has_login_cookies(names)
    if platform == "1688":
        return bool({"__cn_logon__", "_cn_"} & names)
    if platform == "pdd":
        # 拼多多走 H5 网页采集，依赖浏览器登录 cookie
        return has_pdd_login_cookies(names)
    return False


def is_authorized(platform: str) -> bool:
    return _authorized_from_names(platform, cookie_names(platform))


def probe_authorized(platform: str) -> tuple[bool, bool]:
    """返回 (是否授权, 结果是否可靠)。读不到 cookie 文件时 reliable=False。"""
    try:
        names = _read_names(platform)
    except Exception:
        return False, False
    return _authorized_from_names(platform, names), True


def effective_authorized(platform: str) -> bool:
    """UI 展示用：优先实时检查；文件被锁时回退到数据库里的授权状态。"""
    from . import db

    ok, reliable = probe_authorized(platform)
    conn = db.connect()
    try:
        if reliable:
            db.set_platform_auth(conn, platform, ok)
            return ok
        stored = db.get_platform_auth(conn, platform)
        return bool(stored)
    finally:
        conn.close()


def login_site(platform: str) -> str:
    if platform == "1688":
        return "1688"
    if platform == "pdd":
        return "pdd"
    return "taobao"