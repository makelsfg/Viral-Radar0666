"""详情页采集：模拟从搜索结果点击进入，抽取标题/到手价/已售/加购/店铺/SKU/参数/首图。

页面改版时只需要调整 JS_PAGE（常规字段）与 JS_PARAMS（参数面板）。
"""
from __future__ import annotations

import random

from .parse import (
    canonical_url,
    compute_final_price,
    parse_cart_text,
    parse_positive_rate,
    parse_shop_name,
    parse_shop_rating,
    parse_sold_text,
)

DETAIL_URL = "https://detail.tmall.com/item.htm?id={iid}"

JS_PAGE = r"""
() => {
  const out = {};
  const h1 = document.querySelector('h1');
  out.title = h1 ? (h1.innerText || '').trim() : ((document.title || '').split('-')[0] || '').trim();

  // 头部区域文本：从 h1 向上爬，取包含价格与已售信息的那一层
  let el = h1, header = '';
  for (let i = 0; i < 6 && el; i++) {
    el = el.parentElement;
    if (!el) break;
    const t = (el.innerText || '').trim();
    if (t.length >= 30 && t.length <= 4000) header = t;
    if (t.includes('已售') && (t.includes('¥') || t.includes('￥'))) break;
  }
  out.header = header;
  // 页面顶部正文（价格区、服务行可能不在 header 的祖先链里）
  out.top_text = ((document.body && document.body.innerText) || '').slice(0, 3500);

  // 首图：优先商品图集选择器，屏蔽举报/图标类图片
  const norm = (s) => (s && s.startsWith('//') ? 'https:' + s : (s || ''));
  const srcOf = (img) => img.currentSrc || img.src
    || img.getAttribute('data-src') || img.getAttribute('data-ks-lazyload') || '';
  const isJunk = (img, src) => {
    const meta = (img.alt || '') + ' ' + (img.title || '') + ' ' + src;
    return /举报|违法|不良|jubao|report|avatar|icon|logo|客服/i.test(meta)
      || /\/tfs\//i.test(src);
  };
  const selectors = [
    '#J_ImgBooth', '[class*="PicGallery"] img', '[class*="picGallery"] img',
    '[class*="MainPic"] img', '[class*="mainPic"] img', '[class*="PicThumb"] img'
  ];
  let image = '';
  for (const sel of selectors) {
    const img = document.querySelector(sel);
    if (!img) continue;
    const src = srcOf(img);
    if (src && !isJunk(img, src)) { image = norm(src); break; }
  }
  if (!image) {
    let best = '', bestW = 0;
    document.querySelectorAll('img').forEach(im => {
      const src = srcOf(im);
      if (!src || isJunk(im, src)) return;
      const w = im.naturalWidth || 0;
      if (w >= 150 && w > bestW) { bestW = w; best = src; }
    });
    image = norm(best);
  }
  out.image_url = image;

  const v = document.querySelector('video');
  out.video_url = v ? (v.currentSrc || v.src || '') : '';

  // SKU 名称：class 含 sku 的容器内的叶子文本
  const sku = new Set();
  document.querySelectorAll('[class*="sku"],[class*="Sku"],[class*="SKU"]').forEach(box => {
    box.querySelectorAll('*').forEach(n => {
      if (n.children.length === 0) {
        const t = (n.innerText || '').trim();
        if (t.length >= 3 && t.length <= 80) sku.add(t);
      }
    });
  });
  out.sku_texts = Array.from(sku);
  return out;
}
"""

JS_PARAMS = r"""
() => {
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const cls = (el) => (el && typeof el.className === 'string') ? el.className : '';
  const fs = (el) => parseFloat(getComputedStyle(el).fontSize) || 0;

  const boxes = Array.from(document.querySelectorAll(
    '[class*="params"],[class*="Params"],[class*="props"],[class*="Props"]'
  )).filter(visible);

  const collect = (box) => {
    const pairs = [], seen = new Set();
    const cells = Array.from(box.querySelectorAll('div, li, dd'));
    for (const cell of cells) {
      const kids = Array.from(cell.children).filter(k => (k.innerText || '').trim());
      if (kids.length !== 2) continue;
      const a = kids[0], b = kids[1];
      let labelEl, valueEl;

      const aLabel = /label|name|key|attr/i.test(cls(a));
      const bLabel = /label|name|key|attr/i.test(cls(b));
      if (aLabel && !bLabel) { labelEl = a; valueEl = b; }
      else if (bLabel && !aLabel) { labelEl = b; valueEl = a; }
      else if (fs(a) !== fs(b)) {
        // 标签字号更小
        labelEl = fs(a) < fs(b) ? a : b;
        valueEl = labelEl === a ? b : a;
      } else {
        // 默认：上面是值、下面是属性名
        valueEl = a; labelEl = b;
      }

      const label = (labelEl.innerText || '').trim();
      const value = (valueEl.innerText || '').trim();
      if (!label || !value || label.length > 20 || value.length > 60) continue;
      if (!seen.has(label)) { seen.add(label); pairs.push([label, value]); }
    }
    return pairs;
  };

  let best = [];
  for (const box of boxes) {
    const p = collect(box);
    if (p.length > best.length) best = p;
  }
  return best;
}
"""

_SKU_JUNK = {
    "颜色分类", "尺码", "规格", "图集", "参数", "视频", "切换大图模式",
    "加入购物车", "领券购买", "收藏", "店铺", "客服", "进店", "全部参数",
}


def _click_text(page, text: str) -> bool:
    try:
        loc = page.get_by_text(text, exact=True)
        if loc.count() > 0:
            loc.first.click(timeout=2500)
            page.wait_for_timeout(1500)
            return True
    except Exception:
        pass
    return False


def extract_from_page(page, item_id: str, save_html: bool = False) -> dict:
    """在已经打开的详情页上抽取字段；空内容视为风控并抛 BlockedError。"""
    from .browser import BlockedError, check_blocked

    try:
        page.wait_for_selector("h1", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(random.randint(1500, 2800))
    check_blocked(page)

    # 轻度拟人浏览：鼠标移动 + 上下滚动
    try:
        page.mouse.move(
            random.randint(250, 950), random.randint(260, 700),
            steps=random.randint(5, 9),
        )
        page.mouse.wheel(0, random.randint(300, 800))
        page.wait_for_timeout(random.randint(700, 1400))
        page.mouse.wheel(0, -random.randint(150, 400))
        page.wait_for_timeout(random.randint(400, 900))
    except Exception:
        pass

    # 参数面板：大多数时候会点开，偶尔像真人一样跳过
    params = []
    if random.random() < 0.85:
        _click_text(page, "参数")
        _click_text(page, "全部参数")
        params = page.evaluate(JS_PARAMS)

    data = page.evaluate(JS_PAGE)
    html = page.content() if save_html else None

    if not (data.get("title") or data.get("image_url")):
        raise BlockedError(
            "详情页未正常加载（可能触发访问异常/风控），已暂停采集；"
            "请停止脚本、稍后人工打开页面确认恢复后再续采"
        )

    page.wait_for_timeout(random.randint(600, 1400))

    header = data.get("header") or ""
    top_text = data.get("top_text") or ""
    region = header + "\n" + top_text

    sold_text, sold_lb = parse_sold_text(region)
    cart_text, cart_lb = parse_cart_text(region)
    price_final = compute_final_price(region)

    params_dict: dict[str, str] = {}
    for label, value in params:
        if label and label not in params_dict:
            params_dict[label] = value

    sku_names = [
        s for s in (data.get("sku_texts") or [])
        if s not in _SKU_JUNK and not s.startswith("请选择")
    ][:40]

    return {
        "item_id": item_id,
        "title": data.get("title") or "",
        "price_final": price_final,
        "sold_text": sold_text,
        "sold_lb": sold_lb,
        "cart_text": cart_text,
        "cart_lb": cart_lb,
        "shop_name": parse_shop_name(region),
        "shop_rating": parse_shop_rating(region),
        "positive_rate": parse_positive_rate(region),
        "sku_names": sku_names,
        "params": params_dict,
        "image_url": data.get("image_url") or "",
        "video_url": data.get("video_url") or "",
        "product_url": canonical_url(item_id),
        "_html": html,
    }


def collect_detail(ctx, item_id: str, save_html: bool = False) -> dict:
    """兜底/命令行方式：直接打开详情页（带来源 Referer）。"""
    page = ctx.new_page()
    try:
        page.goto(
            DETAIL_URL.format(iid=item_id),
            wait_until="domcontentloaded",
            timeout=60000,
            referer="https://s.taobao.com/",
        )
        return extract_from_page(page, item_id, save_html=save_html)
    finally:
        page.close()


def click_and_extract(ctx, list_page, item_id: str, save_html: bool = False) -> dict:
    """在搜索结果页上 hover 后点击商品卡进入详情，保留 Referer 与浏览历史。

    - 商品卡在新标签打开：处理完后关闭新标签，列表页保留
    - 同标签跳转：处理完后 back 回列表页
    - 找不到卡片/点击失败：兜底直接打开（仍带 Referer）
    """
    from playwright.sync_api import TimeoutError as PWTimeoutError

    from .browser import human_move

    detail_page = None
    same_tab = False
    selector = f'a[href*="item.htm"][href*="id={item_id}"]'
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
            human_move(
                list_page,
                box["x"] + box["width"] / 2,
                box["y"] + box["height"] / 2,
            )
        list_page.wait_for_timeout(random.randint(800, 1800))
        try:
            with ctx.expect_page(timeout=5000) as pinfo:
                link.click()
            detail_page = pinfo.value
        except PWTimeoutError:
            list_page.wait_for_timeout(2500)
            if "item.htm" in (list_page.url or ""):
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
            referer=list_page.url or "https://s.taobao.com/",
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
                    list_page.wait_for_selector('a[href*="item.htm"]', timeout=15000)
                except Exception:
                    pass
                list_page.wait_for_timeout(random.randint(1200, 2400))
            except Exception:
                pass
    return data