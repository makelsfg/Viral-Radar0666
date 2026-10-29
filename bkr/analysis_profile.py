"""按品类加载分析画像（词表/材质/容量维度），让卖点聚类不绑定单一品类。"""
from __future__ import annotations

import yaml

from .config import ROOT

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


def profile_for(task_name: str) -> dict:
    """按任务名匹配画像；先匹配有 match 的，再退回"默认"。"""
    name = task_name or ""
    profiles = _load()
    for p in profiles:
        for m in p.get("match") or []:
            if m and m in name:
                return p
    for p in profiles:
        if p.get("name") == "默认":
            return p
    return {}