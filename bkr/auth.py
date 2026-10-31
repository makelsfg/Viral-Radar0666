"""平台授权状态：直接读取各平台独立 profile 的 cookie 判断。

约定：
  tmall -> 必须 cookie2 + unb（严格，tracknick 半登录不算）
  1688  -> __cn_logon__ / _cn_ 等登录标记
"""
from __future__ import annotations

import sqlite3

from .browser import has_login_cookies
from .config import profile_dir_for


def cookie_names(platform: str) -> set[str]:
    d = profile_dir_for(platform)
    candidates = [d / "Default" / "Network" / "Cookies", d / "Default" / "Cookies"]
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


def is_authorized(platform: str) -> bool:
    names = cookie_names(platform)
    if platform == "tmall":
        return has_login_cookies(names)
    if platform == "1688":
        return bool({"__cn_logon__", "_cn_"} & names)
    return False


def login_site(platform: str) -> str:
    return "1688" if platform == "1688" else "taobao"