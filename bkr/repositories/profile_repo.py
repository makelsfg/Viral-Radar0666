"""数据访问层：品类卖点词表（LLM 生成 / 人工覆盖）缓存。"""
from __future__ import annotations

import json
from datetime import datetime


def get(conn, keyword: str) -> dict | None:
    row = conn.execute(
        "SELECT keyword, profile_json, source, model, hit_rate, created_at, updated_at "
        "FROM analysis_profile WHERE keyword = ?",
        (keyword,),
    ).fetchone()
    if not row:
        return None
    try:
        profile = json.loads(row["profile_json"] or "{}")
    except (TypeError, ValueError):
        profile = {}
    return {
        "keyword": row["keyword"],
        "profile": profile if isinstance(profile, dict) else {},
        "source": row["source"] or "llm",
        "model": row["model"],
        "hit_rate": row["hit_rate"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def save(conn, keyword: str, profile: dict, source: str = "llm",
         model: str | None = None, hit_rate: float | None = None) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO analysis_profile "
        "(keyword, profile_json, source, model, hit_rate, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(keyword) DO UPDATE SET "
        "profile_json = excluded.profile_json, source = excluded.source, "
        "model = excluded.model, hit_rate = excluded.hit_rate, "
        "updated_at = excluded.updated_at",
        (
            keyword,
            json.dumps(profile, ensure_ascii=False, default=str),
            source,
            model,
            hit_rate,
            now,
            now,
        ),
    )
    conn.commit()


def delete(conn, keyword: str) -> None:
    conn.execute("DELETE FROM analysis_profile WHERE keyword = ?", (keyword,))
    conn.commit()


def update_hit_rate(conn, keyword: str, hit_rate: float) -> None:
    conn.execute(
        "UPDATE analysis_profile SET hit_rate = ?, updated_at = ? WHERE keyword = ?",
        (hit_rate, datetime.now().isoformat(timespec="seconds"), keyword),
    )
    conn.commit()
