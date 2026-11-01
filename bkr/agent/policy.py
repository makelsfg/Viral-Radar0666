"""决策层：规则兜底表（可单测/可回放）+ LLM 决策（失败即回退规则）。"""
from __future__ import annotations

import json
import os

import requests

TOOL_NAMES = (
    "extract_candidates", "settle", "scroll_more", "reload_search",
    "wait_human", "click_sort", "check_login", "save_evidence", "finish", "escalate",
)

TOOLS_DESC = """- extract_candidates(strategy: ssr_script|ssr_window|dom_react|anchors|dom_cards) 提取候选；dom_react=从 React props 恢复 CSR 页面商品；dom_cards 仅信号（无货号，不可作为数据源）
- settle(secs: 1-5) 原地等待渲染（不发请求）
- scroll_more(n: 1-3) 向下滚动触发加载
- reload_search(reason) 重新加载当前搜索页（每 URL 限次）
- wait_human(kind: slider|login) 把窗口交给人工处理并等待
- click_sort(label: 销量) 点击销量排序并测量结果
- check_login() 检查登录态
- save_evidence(tag) 保存 HTML/截图现场
- finish(summary) 结束：当前已有可用候选
- escalate(reason_code, message) 结束：上交人工（message 写给用户看的中文指引）"""


def _pick(tool: str, state: str, **args) -> dict:
    return {"tool": tool, "args": args, "thought": f"规则：{state} -> {tool}"}


def rule_decide(state: dict, budget: dict) -> dict:
    """确定性兜底表：LLM 不可用时的行为，也是离线回放测试的对象。"""
    st = state.get("state")
    hist = [h.get("tool") for h in (state.get("history") or []) if isinstance(h, dict)]
    if st == "ssr_ready":
        return _pick("finish", st, summary="SSR 已就绪")
    if st == "slider":
        if budget.get("slider_left", 0) > 0:
            return _pick("wait_human", st, kind="slider")
        return _pick(
            "escalate", st, reason_code="slider_budget_exhausted",
            message="拼多多滑块已多次等待人工仍未通过：请人工在浏览器里完成验证后，冷却后再开始采集。",
        )
    if st == "login_wall":
        if budget.get("login_left", 0) > 0:
            return _pick("wait_human", st, kind="login")
        return _pick(
            "escalate", st, reason_code="login_required",
            message="拼多多未登录：请点右上角「登录拼多多」完成手机号验证码登录，然后点「继续采集」。",
        )
    if st in ("risk_page", "degraded"):
        if "save_evidence" not in hist:
            return _pick("save_evidence", st, tag=st)
        return _pick(
            "escalate", st, reason_code=st,
            message="拼多多返回风控/降级页面：请人工确认页面状态，冷却一段时间后再采集。",
        )
    if st == "client_rendered":
        if budget.get("reloads_left", 0) > 0 and "reload_search" not in hist:
            return _pick("reload_search", st, reason="客户端渲染页，重载取 SSR")
        if "extract_candidates" not in hist:
            return _pick("extract_candidates", st, strategy="dom_react")
        if "save_evidence" not in hist:
            return _pick("save_evidence", st, tag="client_rendered")
        return _pick(
            "escalate", st, reason_code="no_ssr_after_reload",
            message="重载后仍拿不到 SSR 数据：请把 data/debug 下现场发开发者校准。",
        )
    if st == "shell":
        if hist.count("settle") < 2:
            return _pick("settle", st, secs=4)
        if budget.get("reloads_left", 0) > 0:
            return _pick("reload_search", st, reason="空壳页，重载")
        return _pick(
            "escalate", st, reason_code="shell_timeout",
            message="页面持续为空壳：可能网络/风控问题，请人工打开确认后重试。",
        )
    if st == "empty_results":
        return _pick(
            "escalate", st, reason_code="empty_results",
            message="搜索结果为空（平台返回 0 个商品）：换个关键词或稍后再试，不要连续重试。",
        )
    # unknown / 兜底
    if "save_evidence" not in hist:
        return _pick("save_evidence", st or "unknown", tag="unknown")
    return _pick(
        "escalate", st or "unknown", reason_code="unknown_state",
        message="页面状态无法识别：已保存现场，请把 data/debug 下的文件发开发者。",
    )


def _extract_json(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
        t = t.strip()
    start, end = t.find("{"), t.rfind("}")
    return t[start:end + 1] if start >= 0 and end > start else t


def _llm_config() -> tuple[str, str, str]:
    base = (os.environ.get("BK_LLM_BASE_URL") or "").strip().rstrip("/")
    key = (os.environ.get("BK_LLM_API_KEY") or "").strip()
    model = (os.environ.get("BK_AGENT_MODEL") or os.environ.get("BK_LLM_MODEL") or "").strip()
    return base, key, model


def llm_decide(state: dict, system: str, user: str, timeout: float = 15.0) -> dict | None:
    """一次 LLM 决策；任何异常/格式不符返回 None（调用方回退规则表）。"""
    base, key, model = _llm_config()
    if not (base and key and model):
        return None
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 300,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    try:
        resp = requests.post(
            f"{base}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        data = json.loads(_extract_json(content))
        if not isinstance(data, dict):
            return None
        tool = str(data.get("tool") or "").strip()
        if tool not in TOOL_NAMES:
            return None
        args = data.get("args") if isinstance(data.get("args"), dict) else {}
        return {
            "tool": tool,
            "args": args,
            "thought": str(data.get("thought") or "")[:80],
        }
    except Exception:
        return None