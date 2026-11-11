"""按品类取卖点词表：YAML（人工覆盖）→ DB（LLM 生成）→ 空（由参数值兜底）。

词表不再是「开发者必须预知用户会问什么」，而是数据资产：
- YAML 只做种子与纠错；
- 任意新品类由 profile_llm 基于真实标题生成并落库；
- 两者都没有时，分析层只用参数值统计，不再用通用词表假装有结果。
"""
from __future__ import annotations

import yaml

from . import database
from .config import ROOT
from .profile_llm import (
    ensure_async,
    last_error,
    normalize_keyword,
    reset_state,
    retry_ready,
    state as gen_state,
)
from .repositories import profile_repo

PROFILES_PATH = ROOT / "config" / "analysis_profiles.yaml"
_cache: list[dict] | None = None


def _load() -> list[dict]:
    global _cache
    if _cache is not None:
        return _cache
    try:
        data = yaml.safe_load(PROFILES_PATH.read_text(encoding="utf-8")) or {}
        profiles = data.get("profiles") or []
        _cache = profiles if isinstance(profiles, list) else []
    except Exception:
        _cache = []
    return _cache


def _dedupe(words) -> list[str]:
    seen, out = set(), []
    for w in words or []:
        word = str(w or "").strip()
        if word and word not in seen:
            seen.add(word)
            out.append(word)
    return out


def _match_yaml(keyword: str) -> dict | None:
    """按 match 子串匹配（顺序即优先级）。

    刻意不再退回「默认」画像：通用词表会让任意品类看起来「分析正常」，
    实际是把保温/便携之类无关词算进卖点，比空结果更误导。
    未命中时交给 LLM 生成；LLM 不可用则只统计参数卖点。
    """
    for p in _load():
        for m in p.get("match") or []:
            if m and str(m) in keyword:
                return {
                    "name": p.get("name"),
                    "source": "manual",
                    "features": _dedupe(p.get("features")),
                    "materials": _dedupe(p.get("materials")),
                }
    return None


def profile_for(keyword: str, use_db: bool = True) -> dict:
    """快速路径（不调 LLM）：YAML → DB → 空。"""
    keyword = normalize_keyword(keyword)
    yaml_profile = _match_yaml(keyword) if keyword else None
    if yaml_profile and yaml_profile.get("features"):
        return yaml_profile

    if use_db:
        try:
            with database.connection() as conn:
                row = profile_repo.get(conn, keyword)
            if row and row["profile"].get("features"):
                return {
                    "name": row["profile"].get("name") or keyword,
                    "source": row["source"],
                    "features": _dedupe(row["profile"].get("features")),
                    "materials": _dedupe(row["profile"].get("materials")),
                    "model": row.get("model"),
                    "hit_rate": row.get("hit_rate"),
                }
        except Exception as exc:  # noqa: BLE001
            print(f"[profile] 读取词表失败：{type(exc).__name__}: {exc}")

    return {
        "name": None,
        "source": "none",
        "features": [],
        "materials": [],
    }


def ensure_profile(keyword: str, rows: list[dict]) -> dict:
    """分析页调用：已就绪直接返回；否则触发后台生成并返回 generating。"""
    keyword = normalize_keyword(keyword)
    profile = profile_for(keyword)
    if profile["source"] != "none":
        return {
            "keyword": keyword,
            "status": "ready",
            "source": profile["source"],
            "name": profile.get("name"),
            "words": len(profile.get("features") or []),
        }

    state = gen_state(keyword)
    if state == "generating":
        return {"keyword": keyword, "status": "generating", "source": "none"}
    if state == "done":
        # 生成过但库里查不到（被删或写库失败）：允许重新生成
        reset_state(keyword)
        state = "idle"
    if state == "failed":
        if not retry_ready(keyword):
            return {
                "keyword": keyword,
                "status": "failed",
                "source": "none",
                "message": last_error(keyword),
            }
        reset_state(keyword)

    started = ensure_async(keyword, rows)
    return {
        "keyword": keyword,
        "status": "generating" if started == "generating" else "unavailable",
        "source": "none",
        "message": None if started == "generating" else "未配置 LLM，仅统计参数卖点",
    }
