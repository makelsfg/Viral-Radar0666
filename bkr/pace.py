"""采集节奏预设：安全 / 平衡 / 快速。

detail_*：详情页之间的基础间隔（秒）
rest_every / rest_*：每采 N 个详情额外休息的区间（秒）
list_*：搜索页翻页间隔（秒）
"""
from __future__ import annotations

PRESETS: dict[str, dict] = {
    "safe": {
        "detail_lo": 20, "detail_hi": 40,
        "rest_every": 2, "rest_lo": 90, "rest_hi": 180,
        "list_lo": 5, "list_hi": 9,
    },
    "balanced": {
        "detail_lo": 15, "detail_hi": 25,
        "rest_every": 3, "rest_lo": 60, "rest_hi": 120,
        "list_lo": 4, "list_hi": 7,
    },
    "fast": {
        "detail_lo": 10, "detail_hi": 18,
        "rest_every": 4, "rest_lo": 45, "rest_hi": 90,
        "list_lo": 3, "list_hi": 6,
    },
}


def get_pace(name: str | None) -> dict:
    return PRESETS.get((name or "safe").strip().lower(), PRESETS["safe"])