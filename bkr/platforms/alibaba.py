"""1688 采集适配：搜索结果页 + 商品详情页（首跑需按真实页面校准选择器）。

入口：
  搜索  https://s.1688.com/selloffer/offer_search.htm?keywords=…&beginPage=N
  详情  https://detail.1688.com/offer/{offerId}.html
"""
from __future__ import annotations

import random
import re
from urllib.parse import quote

from ..browser import BlockedError, check_blocked, get_work_page, human_move, polite_sleep
from ..pace import get_pace
from ..parse import parse_count, parse_price, same_search_url

SEARCH_URL = "https://s.1688.com/selloffer/offer_search.htm?keywords={q}&beginPage={page}"
DETAIL_URL = "https://detail.1688.com/offer/{iid}.html"

# 1688 没有独立的“销量排序”，最接近的是“成交额”排序（近30天成交），
# 与天猫 sale-desc 的口径方向一致。URL 参数值取自 1688 搜索页排序页签。
# 注意：此参数未经实机登录态验证，若首跑后发现排序页签未落在“成交额”，
# 修改这里的值即可（候选值：va_rmdarkgmv / va_darkgmv）。
SORT_PARAM = "va_rmdarkgmv"

JS_CARDS = r"""
() => {
  const out = [], seen = new Set();
  const links = Array.from(document.querySelectorAll('a[href*="/offer/"]'));
  for (const a of links) {
    const m = (a.href || '').match(/offer\/(\d+)\.html/);
    if (!m || seen.has(m[1])) continue;
    seen.add(m[1]);
    let el = a;
    for (let i = 0; i < 7 && el.parentElement; i++) {
      el = el.parentElement;
      const t = el.innerText || '';
      if ((t.includes('¥') || t.includes('￥')) && t.length < 1000) break;
    }
    const text = (el.innerText || '').trim().slice(0, 1000);
    const imgEl = el.querySelector('img');
    let img = imgEl ? (imgEl.currentSrc || imgEl.src || '') : '';
    if (img.startsWith('//')) img = 'https:' + img;
    out.push({href: a.href, item_id: m[1], text: text, img: img, title_attr: a.getAttribute('title') || ''});
  }
  return out;
}
"""

_BAD_TITLE = ("¥", "￥", "成交", "起批", "件起订", "发货", "广告", "1688", "回头率")
_SHOP_RE = re.compile(
    r"([一-龥A-Za-z0-9（）()]{2,26}(?:有限公司|有限责任公司|工厂|厂|商行|经营部|旗舰店|专营店|专卖店|店))"
)
# 1688 的销量文案有多种形态：列表卡片常见“成交 100+ 件”，详情页是
# “已售 8000+ 个”（三个相邻 span 拼出）或“成交 500 笔”。单位和前缀都放宽。
_SOLD_RE = re.compile(r"(?:成交|已售|销量)\s*([\d.]+[万wW千kK]?\+?)\s*(?:笔|件|个|单)?")


def _pick_title(raw: dict) -> str:
    title = (raw.get("title_attr") or "").strip()
    if title:
        return title
    lines = [l.strip() for l in (raw.get("text") or "").split("\n") if l.strip()]
    cand = [l for l in lines if len(l) >= 4 and not any(b in l for b in _BAD_TITLE)]
    return max(cand, key=len) if cand else (lines[0] if lines else "")


def normalize_card(raw: dict, task) -> dict | None:
    text = raw.get("text") or ""
    title = _pick_title(raw)
    for w in task.exclude_words:
        if w and (w in title or w in text):
            return None
    iid = raw.get("item_id") or ""
    if not iid:
        return None

    price = parse_price(text)
    if price is not None:
        if task.price_min is not None and price < task.price_min:
            return None
        if task.price_max is not None and price > task.price_max:
            return None

    m = _SOLD_RE.search(text)
    sold_text = m.group(0) if m else None
    sold_lb = parse_count(m.group(1)) if m else None

    shop = _SHOP_RE.search(text)
    pm = re.search(r"[¥￥]\s*\d+(?:\.\d+)?(?:\s*[-–~]\s*\d+(?:\.\d+)?)?", text)
    return {
        "item_id": iid,
        "title": title,
        "price_text": pm.group(0) if pm else None,
        "sold_text": sold_text,
        "sold_lb": sold_lb,
        "shop_name": shop.group(1) if shop else None,
        "image_url": raw.get("img") or "",
        "url": DETAIL_URL.format(iid=iid),
    }


def _quote_keyword(keyword: str) -> str:
    """1688 搜索参数使用 GBK 编码；无法编码的字符退回 UTF-8。"""
    try:
        return quote(keyword, encoding="gbk", errors="strict")
    except UnicodeEncodeError:
        return quote(keyword, encoding="utf-8")


def search_url(task, pageno: int) -> str:
    url = SEARCH_URL.format(q=_quote_keyword(task.keywords[0]), page=pageno)
    if (getattr(task, "sort", "") or "") == "sale-desc":
        url += f"&sortType={SORT_PARAM}"
    return url


def open_search_page(ctx, task, pageno: int):
    """在平台唯一的工作标签页里打开搜索页（返回 Page；不关闭）。"""
    page = get_work_page(ctx)
    url = search_url(task, pageno)
    print(f"  1688 搜索页 {pageno}/{task.pages}: {url}")
    try:
        if same_search_url(page.url or "", url):
            print("    [复用] 工作标签页已在目标搜索页，无需重新加载")
            return page
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_selector('a[href*="/offer/"]', timeout=20000)
        except Exception:
            pass
        page.wait_for_timeout(2500)
        try:
            page.mouse.wheel(0, 1200)
            page.wait_for_timeout(1000)
        except Exception:
            pass
        check_blocked(page)
    except Exception:
        raise
    return page


def collect_pages(ctx, task, start_page: int = 1) -> list[dict]:
    cards: list[dict] = []
    for pageno in range(start_page, task.pages + 1):
        page = open_search_page(ctx, task, pageno)
        try:
            raws = page.evaluate(JS_CARDS)
            if not raws:
                from ..list_collector import _dump_search_page

                d = _dump_search_page(page, task, pageno)
                from ..browser import EmptyResultError

                raise EmptyResultError(
                    f"1688 搜索页 {pageno} 解析到 0 个商品（未登录/风控/改版）。诊断文件：{d}"
                )
            for r in raws:
                c = normalize_card(r, task)
                if c:
                    c["page_no"] = pageno
                    cards.append(c)
            print(f"  1688 本页解析 {len(raws)} 张卡片，累计 {len(cards)} 个")
        finally:
            # 页面常驻，避免关闭最后一个标签导致 Edge 退出
            pass
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


JS_DETAIL = r"""
() => {
  const out = {};
  // 1688 详情页有多个 h1：第一个往往是卖家公司名（如“XX有限公司”），
  // 真正的商品标题在 .title-content 容器里的 h1。优先取后者；
  // 兜底时跳过公司名形态的 h1。
  const companyRe = /(有限公司|有限责任公司|工厂|商行|经营部|公司)$/;
  const pickTitle = () => {
    const direct = document.querySelector('.title-content h1')
      || document.querySelector('[class*="title-content"] h1');
    if (direct && (direct.innerText || '').trim()) return direct;
    const hs = Array.from(document.querySelectorAll('h1'));
    for (const h of hs) {
      const t = (h.innerText || '').trim();
      if (t.length >= 8 && !companyRe.test(t)) return h;
    }
    return hs[0] || document.querySelector('[class*="title"]');
  };
  const titleEl = pickTitle();
  out.title = titleEl ? (titleEl.innerText || '').trim().split('\n')[0]
                      : ((document.title || '').split('-')[0] || '').trim();

  let el = titleEl, header = '';
  for (let i = 0; i < 7 && el; i++) {
    el = el.parentElement;
    if (!el) break;
    const t = (el.innerText || '').trim();
    if (t.length >= 30 && t.length <= 6000) header = t;
    if (/成交/.test(t) && (t.includes('¥') || t.includes('￥'))) break;
  }
  out.header = header;
  out.top_text = ((document.body && document.body.innerText) || '').slice(0, 4000);

  const norm = (s) => (s && s.startsWith('//') ? 'https:' + s : (s || ''));
  const srcOf = (img) => img.currentSrc || img.src || img.getAttribute('data-src') || '';
  const isJunk = (img, src) => {
    const meta = (img.alt || '') + ' ' + (img.title || '') + ' ' + src;
    return /logo|icon|avatar|客服|1688\.com\/img|alicdn\.com\/tfs/i.test(meta);
  };
  const sels = ['[class*="detail-gallery"] img', '[class*="main-pic"] img',
                '[class*="mainPic"] img', '[class*="preview"] img', '[class*="offer-pic"] img'];
  let image = '';
  for (const sel of sels) {
    const img = document.querySelector(sel);
    if (!img) continue;
    const s = srcOf(img);
    if (s && !isJunk(img, s)) { image = norm(s); break; }
  }
  if (!image) {
    let best = '', bw = 0;
    document.querySelectorAll('img').forEach(im => {
      const s = srcOf(im);
      if (!s || isJunk(im, s)) return;
      const w = im.naturalWidth || 0;
      if (w >= 150 && w > bw) { bw = w; best = s; }
    });
    image = norm(best);
  }
  out.image_url = image;
  const v = document.querySelector('video');
  out.video_url = v ? (v.currentSrc || v.src || '') : '';

  const sku = new Set();
  document.querySelectorAll('[class*="sku"],[class*="Sku"],[class*="SKU"]').forEach(box => {
    box.querySelectorAll('*').forEach(n => {
      if (n.children.length === 0) {
        const t = (n.innerText || '').trim();
        if (t.length >= 2 && t.length <= 80) sku.add(t);
      }
    });
  });
  out.sku_texts = Array.from(sku);

  const attrs = {};
  document.querySelectorAll('dl').forEach(dl => {
    dl.querySelectorAll('dt').forEach(dt => {
      const dd = dt.nextElementSibling;
      if (dd && dd.tagName === 'DD') {
        const k = (dt.innerText || '').trim().replace(/[:：]$/, '');
        const v = (dd.innerText || '').trim();
        if (k && v && k.length <= 20 && v.length <= 100) attrs[k] = v;
      }
    });
  });
  document.querySelectorAll('tr').forEach(tr => {
    const th = tr.querySelector('th'), td = tr.querySelector('td');
    if (th && td) {
      const k = (th.innerText || '').trim();
      const v = (td.innerText || '').trim();
      if (k && v && k.length <= 20 && v.length <= 100) attrs[k] = v;
    }
  });
  out.attrs = attrs;
  return out;
}
"""

_SKU_JUNK = {"规格", "数量", "库存", "发货", "加入进货单", "立即订购", "收藏", "客服"}


def extract_from_page(page, item_id: str, save_html: bool = False) -> dict:
    from ..browser import BlockedError, check_blocked

    try:
        page.wait_for_selector("h1, [class*='title']", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(random.randint(1500, 2800))
    check_blocked(page)

    try:
        page.mouse.move(random.randint(250, 950), random.randint(260, 700), steps=random.randint(5, 9))
        page.mouse.wheel(0, random.randint(300, 800))
        page.wait_for_timeout(random.randint(700, 1400))
        page.mouse.wheel(0, -random.randint(150, 400))
        page.wait_for_timeout(random.randint(400, 900))
    except Exception:
        pass

    data = page.evaluate(JS_DETAIL)
    html = page.content() if save_html else None
    if not (data.get("title") or data.get("image_url")):
        raise BlockedError(
            "1688 详情页未正常加载（可能登录失效/风控/改版），已暂停；"
            "请人工打开确认后执行 --resume"
        )

    header = data.get("header") or ""
    top_text = data.get("top_text") or ""
    region = header + "\n" + top_text

    m = _SOLD_RE.search(region)
    sold_text = m.group(0) if m else None
    sold_lb = parse_count(m.group(1)) if m else None

    price = parse_price(header) or parse_price(top_text)
    shop = _SHOP_RE.search(region)
    attrs = data.get("attrs") or {}
    skus = [
        s for s in (data.get("sku_texts") or [])
        if s not in _SKU_JUNK and len(s) <= 60
    ][:40]

    return {
        "item_id": item_id,
        "title": data.get("title") or "",
        "price_final": price,
        "sold_text": sold_text,
        "sold_lb": sold_lb,
        "cart_text": None,
        "cart_lb": None,
        "shop_name": shop.group(1) if shop else None,
        "shop_rating": None,
        "positive_rate": None,
        "sku_names": skus,
        "params": attrs,
        "image_url": data.get("image_url") or "",
        "video_url": data.get("video_url") or "",
        "product_url": DETAIL_URL.format(iid=item_id),
        "_html": html,
    }


def click_and_extract(ctx, list_page, item_id: str, save_html: bool = False) -> dict:
    """在 1688 搜索结果页 hover + 点击进入详情。"""
    from playwright.sync_api import TimeoutError as PWTimeoutError

    detail_page = None
    same_tab = False
    selector = f'a[href*="/offer/"][href*="{item_id}"]'
    link = list_page.locator(selector).first

    try:
        count = link.count()
    except Exception:
        count = 0

    if count > 0:
        try:
            link.scroll_into_view_if_needed(timeout=8000)
        except Exception:
            pass
        box = None
        try:
            box = link.bounding_box()
        except Exception:
            pass
        if box:
            human_move(list_page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        list_page.wait_for_timeout(random.randint(800, 1800))
        try:
            with ctx.expect_page(timeout=5000) as pinfo:
                link.click()
            detail_page = pinfo.value
        except PWTimeoutError:
            list_page.wait_for_timeout(2500)
            if "/offer/" in (list_page.url or ""):
                detail_page = list_page
                same_tab = True
        except Exception:
            pass

    if detail_page is None:
        detail_page = ctx.new_page()
        detail_page.goto(
            DETAIL_URL.format(iid=item_id),
            wait_until="domcontentloaded",
            timeout=60000,
            referer=list_page.url or "https://s.1688.com/",
        )

    try:
        detail_page.wait_for_load_state("domcontentloaded", timeout=30000)
    except Exception:
        pass

    try:
        data = extract_from_page(detail_page, item_id, save_html=save_html)
    finally:
        if detail_page is not list_page:
            try:
                detail_page.close()
            except Exception:
                pass
        elif same_tab:
            try:
                list_page.go_back(wait_until="domcontentloaded", timeout=30000)
                try:
                    list_page.wait_for_selector('a[href*="/offer/"]', timeout=15000)
                except Exception:
                    pass
                list_page.wait_for_timeout(random.randint(1200, 2400))
            except Exception:
                pass
    return data