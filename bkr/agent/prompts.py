"""Agent 提示词与状态渲染。"""
from __future__ import annotations

SYSTEM_TEMPLATE = """你是爆款雷达的采集监督 Agent。唯一目标：在预算和红线内，把拼多多搜索页推进到
"拿到 ≥1 条有效候选"或给出明确的人工升级理由。你只能通过工具行动，每次输出一个 JSON。

红线（违反即失败）：
- 不调用/不构造任何平台接口请求，不执行任意 JS；
- 不绕过滑块/登录——只能调用 wait_human 请人工处理；
- 不重载超过预算、不缩短节奏、不编造数据；
- 拿不到数据就 escalate，绝不硬抓。
- finish 只允许在最近一次 extract_candidates 返回 count>0 时调用；dom_cards 只是信号，不是数据源。

当前任务：平台 {platform} / 关键词 {keyword} / 批次 {batch}
预算：{budget}

可用工具（每次只选一个）：
{tools}

输出格式（只输出 JSON，不要 markdown）：
{{"tool": "<工具名>", "args": {{...}}, "thought": "≤60字中文理由"}}
"""


def build_system(task, budget: dict, tools_desc: str) -> str:
    return SYSTEM_TEMPLATE.format(
        platform=getattr(task, "platform", "pdd"),
        keyword=",".join(getattr(task, "keywords", []) or []),
        batch=getattr(task, "_batch", None) or "-",
        budget=budget,
        tools=tools_desc,
    )


def build_user(state: dict) -> str:
    import json

    slim = {
        "state": state.get("state"),
        "url": state.get("url"),
        "signals": state.get("signals"),
        "budget": state.get("budget"),
        "history": state.get("history"),
    }
    return "当前观察：\n" + json.dumps(slim, ensure_ascii=False) + "\n请输出决策 JSON。"