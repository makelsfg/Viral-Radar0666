"""搜索结果页采集（天猫 tab，销量排序）。

页面结构改版时只需要调整本文件里的 JS_CARDS。
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import quote

from .browser import EmptyResultError, check_blocked, get_work_page, polite_sleep
from .config import DATA_DIR
from .pace import get_pace
from .parse import canonical_url, extract_item_id, parse_price, parse_shop_name, parse_sold_text
from .parse import same_search_url
from .sort_ui import ensure_mall_tab, ensure_sort

SEARCH_URL = "https://s.taobao.com/search?q={q}&tab={tab}&sort={sort}&page={page}"

JS_CARDS = r"""
() => {
  const out = [];
  const seen = new Set();
  const links = Array.from(document.querySelectorAll('a[href*="item.htm"]'));
  for (const a of links) {
    const m = (a.href || '').match(/[?&]id=(\d+)/);
    if (!m || seen.has(m[1])) continue;
    seen.add(m[1]);
    // 从链接向上爬，找到包含价格/销量信息的商品卡容器
    let el = a;
    for (let i = 0; i < 7 && el.parentElement; i++) {
      el = el.parentElement;
      const t = el.innerText || '';
      if ((t.includes('¥') || t.includes('￥')) && t.length < 600) break;
    }
    const cardText = (el.innerText || '').trim().slice(0, 600);
    const imgEl = el.querySelector('img');
    let img = imgEl ? (imgEl.currentSrc || imgEl.src || '') : '';
    if (img.startsWith('//')) img = 'https:' + img;
    out.push({href: a.href, item_id: m[1], text: cardText, img: img, title_attr: a.getAttribute('title') || ''});
  }
  return out;
}
"""

_BAD_TITLE_PARTS = ("¥", "￥", "人付款", "已售", "旗舰店", "专卖店", "专营店", "人加购", "发货", "包邮", "广告")


def _fix_img(src: str) -> str:
    if not src:
        return ""
    return "https:" + src if src.startswith("//") else src


def _pick_title(raw: dict) -> str:
    title = (raw.get("title_attr") or "").strip()
    if title:
        return title
    lines = [l.strip() for l in (raw.get("text") or "").split("\n") if l.strip()]
    cand = [l for l in lines if len(l) >= 4 and not any(b in l for b in _BAD_TITLE_PARTS)]
    if cand:
        return max(cand, key=len)
    return lines[0] if lines else ""


def normalize_card(raw: dict, task) -> dict | None:
    """把一张商品卡原始数据归一化为待入库记录；不合法/被排除时返回 None。"""
    text = raw.get("text") or ""
    title = _pick_title(raw)
    for w in task.exclude_words:
        if w and (w in title or w in text):
            return None

    iid = raw.get("item_id") or extract_item_id(raw.get("href") or "")
    if not iid:
        return None

    price = parse_price(text)
    if price is not None:
        if task.price_min is not None and price < task.price_min:
            return None
        if task.price_max is not None and price > task.price_max:
            return None

    sold_text, sold_lb = parse_sold_text(text)
    m = re.search(r"[¥￥]\s*\d+(?:\.\d+)?", text)
    return {
        "item_id": iid,
        "title": title,
        "price_text": m.group(0) if m else None,
        "sold_text": sold_text,
        "sold_lb": sold_lb,
        "shop_name": parse_shop_name(text),
        "image_url": _fix_img(raw.get("img") or ""),
        "url": canonical_url(iid),
    }


def search_url(task, pageno: int) -> str:
    return SEARCH_URL.format(
        q=quote(task.keywords[0]), tab=task.tab, sort=task.sort, page=pageno
    )


def open_search_page(ctx, task, pageno: int):
    """在平台唯一的工作标签页里打开搜索页（返回 Page；不关闭）。"""
    page = get_work_page(ctx)
    url = search_url(task, pageno)
    print(f"  搜索页 {pageno}/{task.pages}: {url}")
    try:
        if same_search_url(page.url or "", url):
            print("    [复用] 工作标签页已在目标搜索页，无需重新加载")
            _apply_sort(page, task)
            return page
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_selector('a[href*="item.htm"]', timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(1500)
        _apply_sort(page, task)
        try:
            page.wait_for_selector('a[href*="item.htm"]', timeout=10000)
        except Exception:
            pass
        page.wait_for_timeout(1200)
        try:
            page.mouse.wheel(0, 1200)
            page.wait_for_timeout(800)
        except Exception:
            pass
        check_blocked(page)
    except Exception:
        raise
    return page


def _apply_sort(page, task) -> None:
    """淘宝搜索会忽略 URL 的 sort 参数，必须在页面上真实点击“天猫 → 销量”。"""
    if getattr(task, "tab", "") == "mall":
        ensure_mall_tab(page)
    ensure_sort(page, ("销量", "销量排序"))


def _dump_search_page(page, task, pageno: int) -> str:
    """0 解析时保存现场（HTML + 截图 + 关键统计），方便定位登录/风控/改版问题。"""
    d = DATA_DIR / "debug"
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = d / f"search_{task.task_id}_p{pageno}_{stamp}.html"
    png_path = d / f"search_{task.task_id}_p{pageno}_{stamp}.png"
    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
    try:
        page.screenshot(path=str(png_path), full_page=False)
    except Exception:
        pass
    try:
        stats = page.evaluate(
            """() => ({
                url: location.href,
                title: document.title,
                anchors: document.querySelectorAll('a[href*="item.htm"]').length,
                bodyLength: (document.body.innerText || '').length,
                bodyHead: (document.body.innerText || '').slice(0, 200)
            })"""
        )
        print(f"  诊断: url={stats.get('url')} title={stats.get('title')} "
              f"itemAnchors={stats.get('anchors')} bodyLength={stats.get('bodyLength')}")
        print(f"  页面文本开头: {stats.get('bodyHead')!r}")
    except Exception:
        pass
    return str(d)


def _log_sold_sample(cards: list[dict], pageno: int, task) -> None:
    """打印本页已售前列，便于人工确认“销量排序”是否真的生效。"""
    page_cards = [c for c in cards if c.get("page_no") == pageno and c.get("sold_lb")]
    if not page_cards:
        return
    vals = [c["sold_lb"] for c in page_cards[:8]]
    print(f"    [校验] 第 {pageno} 页前 {len(vals)} 个已售下限：{vals}")
    inversions = sum(1 for a, b in zip(vals, vals[1:]) if a < b)
    if inversions >= max(1, len(vals) // 3):
        print("    [校验][警告] 已售值整体不是降序，页面的销量排序可能未生效（已记录日志）")


def collect_pages(ctx, task, start_page: int = 1) -> list[dict]:
    """采集搜索结果的指定页区间，返回去重后的候选卡片列表（带 rank/page_no）。"""
    cards: list[dict] = []
    for pageno in range(start_page, task.pages + 1):
        page = open_search_page(ctx, task, pageno)
        try:
            raws = page.evaluate(JS_CARDS)
            if not raws:
                d = _dump_search_page(page, task, pageno)
                raise EmptyResultError(
                    f"搜索页 {pageno} 解析到 0 个商品（未登录/风控/页面改版）。诊断文件已保存到 {d}"
                )
            for r in raws:
                c = normalize_card(r, task)
                if c:
                    c["page_no"] = pageno
                    cards.append(c)
            print(f"  本页解析 {len(raws)} 张卡片，累计有效候选 {len(cards)} 个")
            _log_sold_sample(cards, pageno, task)
        except EmptyResultError:
            raise
        except Exception:
            raise
        finally:
            # 页面保持常驻，避免 Edge 在关闭最后一个工作标签时退出浏览器；
            # 会话结束时由 close_context 统一关闭。
            pass
        pace = get_pace(getattr(task, "pace", "safe"))
        polite_sleep(pace["list_lo"], pace["list_hi"])

    seen = set()
    out = []
    for c in cards:
        if c["item_id"] in seen:
            continue
        seen.add(c["item_id"])
        c["rank"] = len(out) + 1
        out.append(c)
    return out