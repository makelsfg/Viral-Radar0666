"""拼多多采集适配（移动网页实验版）。

入口：
  搜索  https://mobile.yangkeduo.com/search_result.html?search_key=…
  详情  https://mobile.yangkeduo.com/goods.html?goods_id=…

说明：拼多多没有稳定的桌面网页搜索，H5 页面需要移动端 UA 与登录态，
选择器可能随版本变化；失败时会保存 data/debug 现场并明确报错，不伪造数据。
"""
from __future__ import annotations

import random
import re
from urllib.parse import quote

from ..browser import BlockedError, check_blocked, get_work_page, polite_sleep
from ..pace import get_pace
from ..parse import parse_count, parse_price, same_search_url
from ..sort_ui import ensure_sort

SEARCH_URL = "https://mobile.yangkeduo.com/search_result.html?search_key={q}"
DETAIL_URL = "https://mobile.yangkeduo.com/goods.html?goods_id={iid}"

JS_CARDS = r"""
() => {
  const out = [], seen = new Set();
  const links = Array.from(document.querySelectorAll('a[href*="goods.html"]'));
  for (const a of links) {
    const m = (a.href || '').match(/goods_id=(\d+)/);
    if (!m || seen.has(m[1])) continue;
    seen.add(m[1]);
    let el = a;
    for (let i = 0; i < 7 && el.parentElement; i++) {
      el = el.parentElement;
      const t = el.innerText || '';
      if ((t.includes('¥') || t.includes('￥')) && t.length < 600) break;
    }
    const text = (el.innerText || '').trim().slice(0, 600);
    const imgEl = el.querySelector('img');
    let img = imgEl ? (imgEl.currentSrc || imgEl.src || '') : '';
    if (img.startsWith('//')) img = 'https:' + img;
    out.push({href: a.href, item_id: m[1], text: text, img: img,
              title_attr: a.getAttribute('title') || ''});
  }
  return out;
}
"""

JS_DETAIL = r"""
() => {
  const out = {};
  const txt = (document.body.innerText || '');
  out.header = txt.slice(0, 3000);
  const h1 = document.querySelector('h1')
    || document.querySelector('[class*="goods-name"]')
    || document.querySelector('[class*="goodsName"]');
  out.title = h1 ? (h1.innerText || '').trim().split('\n')[0]
                 : (document.title || '').split('-')[0].trim();
  const imgs = Array.from(document.querySelectorAll('img'))
    .map(i => i.currentSrc || i.src || '')
    .filter(u => u.startsWith('http'));
  out.image_url = imgs.find(u => /pddpic|yangkeduo|pinduoduo/.test(u)) || imgs[0] || '';
  out.sku_texts = Array.from(document.querySelectorAll('[class*="sku"]'))
    .map(e => e.innerText || '').slice(0, 40);
  return out;
}
"""

_SOLD_RE = re.compile(r"(?:已拼|已售|销量)\s*([\d.]+[万wW千kK]?\+?)\s*(?:件|单|笔)?")
_BAD_TITLE_PARTS = ("¥", "￥", "已拼", "已售", "人拼", "人付款", "包邮", "退货", "补贴", "广告")
_SKU_JUNK = {"选择规格", "选择", "确定", "取消", "立即购买"}


def _fix_img(src: str) -> str:
    if not src:
        return ""
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("http://") and any(
        h in src for h in ("pddpic.com", "yangkeduo.com", "pinduoduo.com")
    ):
        return "https://" + src[len("http://"):]
    return src


def _pick_title(raw: dict) -> str:
    title = (raw.get("title_attr") or "").strip()
    if title:
        return title
    lines = [l.strip() for l in (raw.get("text") or "").split("\n") if l.strip()]
    cand = [l for l in lines if len(l) >= 4 and not any(b in l for b in _BAD_TITLE_PARTS)]
    return max(cand, key=len) if cand else (lines[0] if lines else "")


def normalize_card(raw: dict, task) -> dict | None:
    text = raw.get("text") or ""
    title = _pick_title(raw)
    for w in getattr(task, "exclude_words", []):
        if w and (w in title or w in text):
            return None
    iid = raw.get("item_id")
    if not iid:
        return None
    price = parse_price(text)
    if price is not None:
        if task.price_min is not None and price < task.price_min:
            return None
        if task.price_max is not None and price > task.price_max:
            return None
    m = _SOLD_RE.search(text)
    price_m = re.search(r"[¥￥]\s*\d+(?:\.\d+)?", text)
    return {
        "item_id": iid,
        "title": title,
        "price_text": price_m.group(0) if price_m else None,
        "sold_text": m.group(0) if m else None,
        "sold_lb": parse_count(m.group(1)) if m else None,
        "shop_name": None,
        "image_url": _fix_img(raw.get("img") or ""),
        "url": DETAIL_URL.format(iid=iid),
    }


def search_url(task, pageno: int) -> str:
    return SEARCH_URL.format(q=quote(task.keywords[0]))


def open_search_page(ctx, task, pageno: int):
    """拼多多 H5 为无限滚动：第 N 页通过向下滚动加载；返回工作标签页。"""
    page = get_work_page(ctx)
    url = search_url(task, pageno)
    print(f"  拼多多搜索页 {pageno}/{task.pages}: {url}")
    try:
        if not same_search_url(page.url or "", url):
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_selector('a[href*="goods.html"]', timeout=20000)
            except Exception:
                pass
            page.wait_for_timeout(2000)
        ensure_sort(page, ("销量", "销量排序"))
        try:
            page.wait_for_selector('a[href*="goods.html"]', timeout=10000)
        except Exception:
            pass
        # 第 2 页起向下滚动加载更多
        for _ in range(max(0, pageno - 1) * 4):
            try:
                page.mouse.wheel(0, random.randint(1200, 1800))
                page.wait_for_timeout(random.randint(900, 1500))
            except Exception:
                break
        page.wait_for_timeout(800)
        check_blocked(page)
    except Exception:
        raise
    return page


def collect_pages(ctx, task, start_page: int = 1) -> list[dict]:
    cards: list[dict] = []
    for pageno in range(start_page, task.pages + 1):
        page = open_search_page(ctx, task, pageno)
        raws = page.evaluate(JS_CARDS)
        if not raws:
            from ..list_collector import _dump_search_page

            d = _dump_search_page(page, task, pageno)
            from ..browser import EmptyResultError

            raise EmptyResultError(
                f"拼多多搜索页 {pageno} 解析到 0 个商品（未登录/风控/改版）。诊断文件：{d}"
            )
        for r in raws:
            c = normalize_card(r, task)
            if c:
                c["page_no"] = pageno
                cards.append(c)
        print(f"  拼多多本页解析 {len(raws)} 张卡片，累计 {len(cards)} 个")
        solds = [c["sold_lb"] for c in cards
                 if c.get("page_no") == pageno and c.get("sold_lb")]
        if solds:
            vals = solds[:8]
            print(f"    [校验] 拼多多第 {pageno} 页前 {len(vals)} 个已拼下限：{vals}")
            inv = sum(1 for a, b in zip(vals, vals[1:]) if a < b)
            if inv >= max(1, len(vals) // 3):
                print("    [校验][警告] 已拼值整体不是降序，拼多多排序可能未生效")
        pace = get_pace(getattr(task, "pace", "safe"))
        polite_sleep(pace["list_lo"], pace["list_hi"])

    seen, out = set(), []
    for c in cards:
        if c["item_id"] in seen:
            continue
        seen.add(c["item_id"])
        c["rank"] = len(out) + 1
        out.append(c)
    return out


def extract_from_page(page, item_id: str, save_html: bool = False) -> dict:
    try:
        page.wait_for_selector("h1, [class*='goods-name'], [class*='goodsName']", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(random.randint(1500, 2500))
    data = page.evaluate(JS_DETAIL)
    html = page.content() if save_html else None
    if not (data.get("title") or data.get("image_url")):
        raise BlockedError(
            "拼多多详情页未正常加载（可能登录失效/风控/改版），已暂停；"
            "请人工打开确认后重试"
        )
    header = data.get("header") or ""
    m = _SOLD_RE.search(header)
    skus = [
        s.strip() for s in (data.get("sku_texts") or [])
        if s.strip() and s.strip() not in _SKU_JUNK and len(s.strip()) <= 60
    ][:40]
    return {
        "item_id": item_id,
        "title": data.get("title") or "",
        "price_final": parse_price(header),
        "sold_text": m.group(0) if m else None,
        "sold_lb": parse_count(m.group(1)) if m else None,
        "cart_text": None,
        "cart_lb": None,
        "shop_name": None,
        "shop_rating": None,
        "positive_rate": None,
        "sku_names": skus,
        "params": {},
        "image_url": data.get("image_url") or "",
        "video_url": "",
        "product_url": DETAIL_URL.format(iid=item_id),
        "_html": html,
    }


def click_and_extract(ctx, list_page, item_id: str, save_html: bool = False) -> dict:
    """详情在临时标签页打开（带搜索页 Referer），采完关闭，不影响工作标签页。"""
    detail_page = ctx.new_page()
    try:
        detail_page.goto(
            DETAIL_URL.format(iid=item_id),
            wait_until="domcontentloaded",
            timeout=60000,
            referer=list_page.url or "https://mobile.yangkeduo.com/",
        )
        return extract_from_page(detail_page, item_id, save_html=save_html)
    finally:
        try:
            detail_page.close()
        except Exception:
            pass