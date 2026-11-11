"""平台登录授权状态表 platform_auth 的数据访问。"""
from __future__ import annotations

from datetime import datetime


def set_auth(conn, platform: str, authorized: bool, note: str | None = None) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO platform_auth(platform, authorized, updated_at, note)
           VALUES(?,?,?,?)
           ON CONFLICT(platform) DO UPDATE SET
             authorized=excluded.authorized, updated_at=excluded.updated_at, note=excluded.note""",
        (platform, 1 if authorized else 0, now, note),
    )
    conn.commit()


def get_auth(conn, platform: str) -> bool | None:
    row = conn.execute(
        "SELECT authorized FROM platform_auth WHERE platform=?", (platform,)
    ).fetchone()
    return bool(row["authorized"]) if row else None
