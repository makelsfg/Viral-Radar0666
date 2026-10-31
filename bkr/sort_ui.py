"""搜索结果页排序/平台页签操作。

淘宝/天猫和 1688 的搜索页会忽略 URL 里的 sort 参数（直接跳回“综合”），
所以这里在页面加载后用真实点击切换“销量”（1688 为“销量/成交额”），
并尽量从 DOM 状态确认点击是否生效；确认不了会在日志里明确警告。
"""
from __future__ import annotations

import re

_ACTIVE_RE = re.compile(r"(?:^|[-_ ])(?:active|selected|current|checked)(?:[-_ ]|$)", re.I)


def find_text_elements(page, text: str, min_y: int = 60, max_y: int = 560, limit: int = 10):
    """返回视口内、纵向位置在 [min_y, max_y] 的精确文本元素（按从上到下排序）。"""
    found = []
    try:
        loc = page.get_by_text(text, exact=True)
        n = min(loc.count(), 20)
    except Exception:
        return []
    for i in range(n):
        el = loc.nth(i)
        try:
            if not el.is_visible():
                continue
            box = el.bounding_box()
            if not box or not (min_y <= box["y"] <= max_y):
                continue
            found.append((box["y"], box["x"], el))
        except Exception:
            continue
    found.sort(key=lambda t: (t[0], t[1]))
    return [el for _, _, el in found[:limit]]


def is_active(el) -> bool:
    """从元素自身/祖先的 class、aria-selected 判断是否处于选中态。"""
    try:
        return bool(el.evaluate(
            """e => {
              let n = e;
              for (let i = 0; i < 4 && n; i++, n = n.parentElement) {
                const cls = String(n.className || '');
                if (/(^|[-_ ])(active|selected|current|checked)([-_ ]|$)/i.test(cls)) return true;
                const aria = n.getAttribute ? (n.getAttribute('aria-selected') || '') : '';
                if (aria === 'true') return true;
              }
              return false;
            }"""
        ))
    except Exception:
        return False


def _is_label_active(page, text: str, max_y: int = 560) -> bool:
    for el in find_text_elements(page, text, max_y=max_y):
        if is_active(el):
            return True
    return False


def click_text(page, text: str, min_y: int = 60, max_y: int = 560) -> bool:
    for el in find_text_elements(page, text, min_y=min_y, max_y=max_y):
        try:
            el.click(timeout=4000)
            return True
        except Exception:
            pass
        try:
            # 页面常有浮层/吸顶条遮挡，直接派发点击到目标元素
            el.click(force=True, timeout=3000)
            return True
        except Exception:
            continue
    return False


def ensure_mall_tab(page) -> None:
    """如果页面有“所有宝贝/天猫/淘宝”平台页签且天猫未选中，点击天猫。"""
    try:
        if not find_text_elements(page, "所有宝贝", max_y=360):
            return
        if _is_label_active(page, "天猫", max_y=360):
            return
        if click_text(page, "天猫", min_y=80, max_y=360):
            print("    [平台] 已点击“天猫”页签")
            page.wait_for_timeout(2500)
    except Exception:
        pass


def ensure_sort(page, labels: tuple[str, ...], max_y: int = 560) -> str | None:
    """按优先级点击排序项（如 (“销量”,) / (“销量”, “成交额”)）。

    返回实际使用的排序标签；找不到控件时返回 None（调用方应继续本地按已售排序）。
    """
    try:
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(400)
    except Exception:
        pass
    for label in labels:
        els = find_text_elements(page, label, max_y=max_y)
        if not els:
            continue
        if is_active(els[0]):
            print(f"    [排序] “{label}”已处于选中状态")
            return label
        if not click_text(page, label, max_y=max_y):
            print(f"    [排序] 找到“{label}”但点击失败")
            continue
        page.wait_for_timeout(2800)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(1200)
        if _is_label_active(page, label, max_y=max_y):
            print(f"    [排序] 已点击“{label}”，页面确认选中")
            return label
        print(f"    [排序] 已点击“{label}”，但无法从页面确认选中状态")
        return label
    print(f"    [排序][警告] 页面未找到排序项 {labels}；将保持当前顺序，并在本地按已售下限排序")
    return None