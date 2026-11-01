"""观察器：把当前页面压缩成结构化状态。分类由确定规则完成，LLM 不做自由判断。"""
from __future__ import annotations

import hashlib
import json
import re

from ..platforms import pinduoduo as pdd

OBSERVE_JS = r"""
() => {
  const clean = (s) => String(s || '').replace(/[\u200b\u200c\u200d]/g, '');
  const txt = clean((document.body && document.body.innerText) || '');
  let hasSsrScript = false;
  for (const s of document.querySelectorAll('script')) {
    if (((s.textContent || '')).indexOf('window.rawData=') >= 0) { hasSsrScript = true; break; }
  }
  return {
    url: location.href,
    title: document.title || '',
    hasRawDataWindow: !!(window.rawData && window.rawData.stores && window.rawData.stores.store),
    hasSsrScript: hasSsrScript,
    productTexts: (txt.match(/已拼/g) || []).length,
    textLen: txt.length,
    head: txt.slice(0, 300),
    sortLabels: ['综合', '销量', '价格'].filter((k) => txt.indexOf(k) >= 0),
    domImages: document.querySelectorAll('img').length,
    emptyHint: /没有找到|暂无相关|没有相关/.test(txt),
  };
}
"""


def read_signals(page) -> dict:
    try:
        return page.evaluate(OBSERVE_JS) or {}
    except Exception as exc:
        return {"url": "", "textLen": 0, "error": f"{type(exc).__name__}: {exc}"}


def read_ssr_items(page) -> list:
    """一次轻量读取 SSR 条目（不重试、不等待）。"""
    try:
        res = page.evaluate(pdd.JS_SSR_LIST)
        return res.get("items") or []
    except Exception:
        return []


def classify(signals: dict, ssr_count: int) -> str:
    """把信号映射为状态（顺序即优先级），全部规则可单测。"""
    url = str(signals.get("url") or "").lower()
    head = str(signals.get("head") or "")
    if "captcha" in url or any(m in head for m in pdd._SLIDER_MARKERS):
        return "slider"
    if "login.html" in url or any(m in head for m in pdd._LOGIN_MARKERS):
        return "login_wall"
    if any(m in head for m in pdd._ACCESS_MARKERS):
        return "risk_page"
    if pdd.is_degraded(head):
        return "degraded"
    if ssr_count > 0:
        return "ssr_ready"
    if int(signals.get("productTexts") or 0) >= 3:
        return "client_rendered"
    if signals.get("emptyHint") or re.search("没有找到|暂无相关|没有相关", head):
        return "empty_results"
    if int(signals.get("textLen") or 0) < 500:
        return "shell"
    return "unknown"


def state_hash(state: dict) -> str:
    core = {
        "state": state.get("state"),
        "url": state.get("url"),
        "ssr": (state.get("signals") or {}).get("ssr_count"),
        "texts": (state.get("signals") or {}).get("productTexts"),
    }
    return hashlib.md5(json.dumps(core, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]


def observe(page, *, budget: dict | None = None, history: list | None = None) -> dict:
    signals = read_signals(page)
    signals["ssr_count"] = len(read_ssr_items(page))
    state = classify(signals, signals["ssr_count"])
    return {
        "url": signals.get("url") or "",
        "state": state,
        "signals": {
            "has_rawdata_window": bool(signals.get("hasRawDataWindow")),
            "has_ssr_script": bool(signals.get("hasSsrScript")),
            "ssr_count": signals["ssr_count"],
            "dom_product_texts": int(signals.get("productTexts") or 0),
            "dom_image_count": int(signals.get("domImages") or 0),
            "sort_labels": list(signals.get("sortLabels") or []),
            "text_len": int(signals.get("textLen") or 0),
        },
        "budget": budget or {},
        "history": (history or [])[-8:],
    }