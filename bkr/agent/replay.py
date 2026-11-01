"""离线回放：用状态序列驱动规则引擎（CI 测试用，不碰浏览器）。"""
from .policy import rule_decide


def replay(states: list[dict], budget: dict) -> list[str]:
    """给定状态序列，返回规则引擎的动作序列。"""
    actions: list[str] = []
    history: list[dict] = []
    for st in states:
        state = dict(st, history=list(history))
        decision = rule_decide(state, budget)
        actions.append(decision["tool"])
        history.append({"tool": decision["tool"], "result": {}})
    return actions
