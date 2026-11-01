"""Agent 工具白名单：Agent 全部可执行动作都在这里，预算与红线内置于工具本身。"""
from __future__ import annotations

import os
import random
import time

from ..browser import enable_mouse_as_touch, has_pdd_login_cookies
from ..platforms import pinduoduo as pdd


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


class ToolBox:
    def __init__(self, page, task):
        self.page = page
        self.task = task
        self.reloads_left = max(0, _env_int("BK_AGENT_MAX_RELOADS", 1))
        self.slider_left = 2
        self.login_left = 1
        self.scrolls_left = 2
        self.saves_left = 2
        self.wait_s = min(max(30, _env_int("BK_AGENT_MAX_HUMAN_WAIT", 180)), 300)
        self.dom_order: list[str] | None = None
        self.last_items: list[dict] = []
        self._url_before_sort = ""

    # —— 预算与调度 ——
    def budget(self) -> dict:
        return {
            "reloads_left": self.reloads_left,
            "slider_left": self.slider_left,
            "login_left": self.login_left,
            "scrolls_left": self.scrolls_left,
            "saves_left": self.saves_left,
        }

    def call(self, name: str, args: dict) -> dict:
        fn = getattr(self, f"_tool_{name}", None)
        if fn is None:
            return {"ok": False, "error": f"未知工具：{name}"}
        try:
            return fn(args or {})
        except Exception as exc:  # 工具异常=本步失败，由 loop 继续决策
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # —— 工具实现 ——
    def _tool_extract_candidates(self, args: dict) -> dict:
        strategy = str(args.get("strategy") or "ssr_script")
        if strategy in ("ssr_script", "ssr_window"):
            res = self.page.evaluate(pdd.JS_SSR_LIST) or {}
            items = res.get("items") or []
            self.last_items = items
            return {
                "ok": bool(res.get("ok")),
                "strategy": strategy,
                "mode": res.get("mode"),
                "count": len(items),
                "sample": [it.get("goodsName") for it in items[:2]],
            }
        if strategy == "dom_react":
            items = pdd.extract_dom_react(self.page)
            self.last_items = items
            return {
                "ok": bool(items),
                "strategy": strategy,
                "count": len(items),
                "usable": bool(items),
                "sample": [it.get("goodsName") for it in items[:2]],
            }
        if strategy == "anchors":
            raws = self.page.evaluate(pdd.JS_CARDS) or []
            return {"ok": True, "strategy": strategy, "count": len(raws)}
        if strategy == "dom_cards":
            from .observe import read_signals

            sig = read_signals(self.page)
            return {
                "ok": False,
                "strategy": strategy,
                "count": int(sig.get("productTexts") or 0),
                "usable": False,
                "note": "仅信号：DOM 卡片无货号，不能作为候选来源；请用 dom_react",
            }
        return {"ok": False, "error": f"未知策略：{strategy}"}

    def _tool_settle(self, args: dict) -> dict:
        secs = max(1, min(int(args.get("secs") or 3), 5))
        self.page.wait_for_timeout(secs * 1000)
        return {"ok": True, "waited": secs}

    def _tool_scroll_more(self, args: dict) -> dict:
        n = max(1, min(int(args.get("n") or 1), 3, self.scrolls_left or 1))
        if self.scrolls_left <= 0:
            return {"ok": False, "error": "budget_exceeded: scroll"}
        self.scrolls_left -= n
        for _ in range(n):
            try:
                self.page.mouse.wheel(0, random.randint(*pdd.SCROLL_STEP_PX))
                self.page.wait_for_timeout(pdd.SCROLL_INTERVAL_MS)
            except Exception:
                break
        from .observe import read_signals

        sig = read_signals(self.page)
        return {
            "ok": True,
            "scrolled": n,
            "product_texts": int(sig.get("productTexts") or 0),
            "ssr_hint": bool(sig.get("hasSsrScript")),
        }

    def _tool_reload_search(self, args: dict) -> dict:
        if self.reloads_left <= 0:
            return {"ok": False, "error": "budget_exceeded: reload"}
        self.reloads_left -= 1
        url = self.page.url or pdd.search_url(self.task, 1)
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_timeout(random.randint(*pdd.PAGE_SETTLE_MS))
        mode = pdd._wait_content(self.page, timeout_ms=20000)
        return {"ok": True, "reason": args.get("reason") or "", "ready": mode}

    def _tool_wait_human(self, args: dict) -> dict:
        kind = str(args.get("kind") or "slider")
        if kind == "slider":
            if self.slider_left <= 0:
                return {"ok": False, "error": "budget_exceeded: slider_wait"}
            self.slider_left -= 1
            solved = pdd.wait_human_slide(self.page, timeout=self.wait_s)
            return {"ok": solved, "kind": kind, "solved": solved}
        if self.login_left <= 0:
            return {"ok": False, "error": "budget_exceeded: login_wait"}
        self.login_left -= 1
        try:
            self.page.bring_to_front()
        except Exception:
            pass
        enable_mouse_as_touch(self.page)
        print(
            "  [agent] 页面需要人工登录：请在浏览器窗口中完成拼多多登录，"
            f"完成后自动继续（最多等待 {self.wait_s // 60} 分钟）"
        )
        deadline = time.time() + self.wait_s
        while time.time() < deadline:
            time.sleep(2)
            from .observe import read_signals

            sig = read_signals(self.page)
            head = str(sig.get("head") or "")
            url = str(sig.get("url") or "").lower()
            if "login.html" in url or any(m in head for m in pdd._LOGIN_MARKERS):
                continue
            return {"ok": True, "kind": kind, "solved": True}
        return {"ok": False, "kind": kind, "solved": False}

    def _tool_click_sort(self, args: dict) -> dict:
        label = str(args.get("label") or "销量")
        self._url_before_sort = self.page.url or ""
        used = pdd.ensure_sort(self.page, (label, "销量排序"))
        self.page.wait_for_timeout(1800)
        url_changed = (self.page.url or "") != self._url_before_sort
        dom_n = 0
        if not url_changed:
            try:
                dom = self.page.evaluate(pdd.JS_DOM_IMAGES) or []
                self.dom_order = dom
                self.page._bkr_pdd_dom_order = dom
                dom_n = len(dom)
            except Exception:
                pass
        return {"ok": bool(used), "label": used or "", "url_changed": url_changed,
                "dom_order": dom_n}

    def _tool_check_login(self, args: dict) -> dict:
        try:
            names = {c.get("name") for c in self.page.context.cookies()}
        except Exception:
            names = set()
        from .observe import read_signals

        sig = read_signals(self.page)
        head = str(sig.get("head") or "")
        return {
            "ok": True,
            "cookie_ok": has_pdd_login_cookies(names),
            "login_markers": any(m in head for m in pdd._LOGIN_MARKERS),
        }

    def _tool_save_evidence(self, args: dict) -> dict:
        if self.saves_left <= 0:
            return {"ok": False, "error": "budget_exceeded: save"}
        self.saves_left -= 1
        tag = str(args.get("tag") or "state")
        path = pdd._dump_search_page(self.page)
        return {"ok": True, "tag": tag, "path": path}