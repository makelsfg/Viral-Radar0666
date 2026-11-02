"""拼多多 H5 采集适配（网页模式）。

入口：
  搜索  https://mobile.yangkeduo.com/search_result.html?search_key=…
  详情  https://mobile.yangkeduo.com/goods.html?goods_id=…

硬约束（详见 docs/PROMPT_拼多多网页采集.md）：
  - 只做页面导航 / 滚动 / 读取已渲染的 DOM 与 SSR 数据，不调用任何 pdd 接口；
  - 搜索页为无限滚动，本批固定 1 屏（task.pages>1 时忽略并告警）；
  - 单批详情上限由 config.PDD_DETAIL_CAP 控制（≤10），超出则暂停等冷却；
  - 遇滑块 / 登录 / 访问异常 / 降级页立即停批，不重试、不绕过；
  - 拿不到的字段一律留空（None / [] / ""），禁止编造。
"""
from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime
from urllib.parse import quote

from ..browser import (
    BlockedError,
    EmptyResultError,
    LoginError,
    get_work_page,
)
from ..config import DATA_DIR
from ..pace import get_pace
from ..parse import parse_count, same_search_url
from ..sort_ui import ensure_sort

SEARCH_URL = "https://mobile.yangkeduo.com/search_result.html?search_key={q}"
DETAIL_URL = "https://mobile.yangkeduo.com/goods.html?goods_id={iid}"

# —— 页面内动作的固定节奏（实机可调，见提示词 §3.4）——
PAGE_SETTLE_MS = (2400, 2600)
SCROLL_STEP_PX = (700, 800)
SCROLL_INTERVAL_MS = 600
MAX_SCROLLS = 5

# 单批详情上限的补充约束（真正的数值在 config.PDD_DETAIL_CAP）
BATCH_COOLDOWN_MIN = 10


class PddDegradedError(BlockedError):
    """会话降级：页面仍可打开，但关键数据不再返回，需停止本批。"""


# ———————————————————— 搜索页：SSR 数据（首选） ————————————————————

JS_SSR_LIST = r"""
() => {
  const strip = (s) => String(s || '').replace(/[\u200b\u200c\u200d]/g, '');
  const mapItems = (data) => {
    const d = data && data.stores && data.stores.store && data.stores.store.data;
    const list = d && d.ssrListData && d.ssrListData.list;
    if (!Array.isArray(list)) return null;
    return {
      count: list.length,
      keys: list.length ? Object.keys(list[0]).slice(0, 40) : [],
      items: list.map((it) => ({
        goodsID: strip(it.goodsID ?? it.goods_id ?? it.goodsId ?? ''),
        goodsName: strip(it.goodsName ?? it.goods_name ?? ''),
        price: it.price ?? it.min_group_price ?? it.minGroupPrice ?? it.groupPrice ?? null,
        salesTip: strip(it.salesTip ?? it.sales_tip ?? ''),
        img: strip(it.hdThumbUrl ?? it.imgUrl ?? it.thumbUrl ?? it.image_url ?? ''),
      })),
    };
  };

  // 1) 内存对象（水合早期可用，之后可能被前端删除）
  if (window.rawData) {
    const r = mapItems(window.rawData);
    if (r) return Object.assign({ok: true, mode: 'window'}, r);
  }

  // 2) 内联 script 里的 SSR 原文（水合后 window.rawData 被删，但脚本标签仍在 DOM 中）
  const scripts = Array.from(document.querySelectorAll('script'));
  for (const s of scripts) {
    const t = s.textContent || '';
    const i = t.indexOf('window.rawData=');
    if (i < 0) continue;
    const start = t.indexOf('{', i);
    if (start < 0) continue;
    let depth = 0, inStr = false, esc = false, raw = '';
    for (let j = start; j < t.length; j++) {
      const ch = t[j];
      if (inStr) {
        if (esc) esc = false;
        else if (ch === '\\') esc = true;
        else if (ch === '"') inStr = false;
      } else if (ch === '"') {
        inStr = true;
      } else if (ch === '{') {
        depth++;
      } else if (ch === '}') {
        depth--;
        if (depth === 0) { raw = t.slice(start, j + 1); break; }
      }
    }
    if (!raw) continue;
    let data = null;
    try { data = JSON.parse(raw); } catch (e) { continue; }
    const r = mapItems(data);
    if (r) return Object.assign({ok: true, mode: 'script'}, r);
  }
  return {ok: false, why: 'window.rawData 不存在，内联脚本中也未找到可用 SSR 数据'};
}
"""

# 兜底：goods.html 锚点解析（SSR 路径失效时使用，仍走同一套归一化）
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
    const text = (el.innerText || '').replace(/[\u200b\u200c\u200d]/g, '').trim().slice(0, 600);
    const imgEl = el.querySelector('img');
    let img = imgEl ? (imgEl.currentSrc || imgEl.src || '') : '';
    if (img.startsWith('//')) img = 'https:' + img;
    out.push({href: a.href, item_id: m[1], text: text, img: img,
              title_attr: a.getAttribute('title') || ''});
  }
  return out;
}
"""

# CSR（客户端渲染）模式：卡片货号不在 DOM/SSR 里，而在 React fiber 的 props 中。
# 从每张商品图所在的 DOM 节点出发，沿 __reactFiber$ 向上找携带列表项 object 的 props。
JS_FIBER_CARDS = r"""
() => {
  const strip = (s) => String(s || '').replace(/[\u200b\u200c\u200d]/g, '');
  const isImg = (im) => {
    const s = im.currentSrc || im.src || '';
    return /pddpic|yangkeduo|pinduoduo/.test(s) && !/logo|icon|avatar|扫码/i.test(s);
  };
  const findItem = (el) => {
    let node = el;
    for (let i = 0; i < 8 && node; i++) {
      const fk = Object.keys(node).find(
        (k) => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$')
      );
      if (fk) {
        let fiber = node[fk];
        for (let j = 0; j < 14 && fiber; j++) {
          const p = fiber.memoizedProps || {};
          const cand = p.item || p.data || p.goods || p.payload;
          if (cand && typeof cand === 'object') {
            const id = cand.goodsID ?? cand.goods_id ?? cand.goodsId ?? '';
            if (id) return cand;
          }
          fiber = fiber.return;
        }
      }
      node = node.parentElement;
    }
    return null;
  };
  const out = [], seen = new Set();
  document.querySelectorAll('img').forEach((im) => {
    if (!isImg(im)) return;
    const it = findItem(im);
    if (!it) return;
    const id = String(it.goodsID ?? it.goods_id ?? it.goodsId ?? '');
    if (!id || seen.has(id)) return;
    seen.add(id);
    out.push({
      goodsID: id,
      goodsName: strip(it.goodsName ?? it.goods_name ?? ''),
      price: it.price ?? it.min_group_price ?? it.minGroupPrice ?? it.groupPrice ?? null,
      salesTip: strip(it.salesTip ?? it.sales_tip ?? ''),
      img: im.currentSrc || im.src || '',
    });
  });
  return out;
}
"""


def extract_dom_react(page) -> list[dict]:
    """CSR 页面从 React props 恢复商品列表（顺序即页面渲染顺序）。"""
    try:
        items = page.evaluate(JS_FIBER_CARDS) or []
    except Exception:
        return []
    return [
        {
            "goodsID": str(it.get("goodsID") or ""),
            "goodsName": it.get("goodsName") or "",
            "price": it.get("price"),
            "salesTip": it.get("salesTip") or "",
            "img": it.get("img") or "",
        }
        for it in items
        if it.get("goodsID")
    ]


# ———————————————————— 详情页：DOM + 文本 ————————————————————

JS_DETAIL = r"""
() => {
  const strip = (s) => String(s || '').replace(/[\u200b\u200c\u200d]/g, '');
  const out = {};
  const grab = (sel) => {
    const el = document.querySelector(sel);
    return el ? strip(el.innerText || '').trim() : '';
  };
  out.title = grab('h1')
    || grab('[class*="goods-name"]')
    || grab('[class*="goodsName"]');
  const bodyText = strip((document.body && document.body.innerText) || '');
  out.top_text = bodyText.slice(0, 8000);

  const junk = /logo|icon|avatar|客服|扫一扫|分享|收藏/i;
  let image = '', best = 0;
  document.querySelectorAll('img').forEach((im) => {
    const s = im.currentSrc || im.src || im.getAttribute('data-src') || '';
    if (!s || junk.test((im.alt || '') + s)) return;
    const w = im.naturalWidth || 0;
    if (w >= 150 && w > best) { best = w; image = s; }
  });
  out.image_url = image && image.startsWith('//') ? 'https:' + image : image;
  const v = document.querySelector('video');
  out.video_url = v ? (v.currentSrc || v.src || '') : '';

  // 参数栏：常见容器里的「键：值」行；拿不到就空，不猜
  const attrs = {};
  const pushKV = (k, val) => {
    k = strip(k || '').replace(/[:：]\s*$/, '').trim();
    val = strip(val || '').trim();
    if (k && val && k.length <= 20 && val.length <= 100 && !(k in attrs)) attrs[k] = val;
  };
  document.querySelectorAll(
    '[class*="param"] li, [class*="attr"] li, [class*="prop"] li, [class*="spec"] li'
  ).forEach((li) => {
    const t = strip(li.innerText || '').trim();
    const m = t.match(/^([^：:]{1,20})[：:]\s*(.{1,100})$/);
    if (m) pushKV(m[1], m[2]);
  });
  document.querySelectorAll('dl').forEach((dl) => {
    dl.querySelectorAll('dt').forEach((dt) => {
      const dd = dt.nextElementSibling;
      if (dd && dd.tagName === 'DD') pushKV(dt.innerText, dd.innerText);
    });
  });
  out.attrs = attrs;

  // 规格：只收当前已渲染的叶子文本（弹层需点击的拿不到，按约定留空）
  const sku = new Set();
  document.querySelectorAll('[class*="sku"],[class*="Sku"],[class*="spec"],[class*="Spec"]')
    .forEach((box) => {
      box.querySelectorAll('*').forEach((n) => {
        if (n.children.length === 0) {
          const t = strip(n.innerText || '').trim();
          if (t.length >= 2 && t.length <= 60) sku.add(t);
        }
      });
    });
  out.sku_texts = Array.from(sku).slice(0, 60);
  return out;
}
"""

# ———————————————————— 文本解析（纯函数，可单测） ———————————————————

_SOLD_ITEM_RE = re.compile(r"已拼\s*(\d+(?:\.\d+)?)\s*([万wW千kK]?)\+?\s*(?:件|单|笔)?")

_SHOP_SOLD_RE = re.compile(r"(?:本店|店铺)已拼[^\n]{0,14}|全店总售[^\n]{0,14}")
_BAD_TITLE_PARTS = ("¥", "￥", "已拼", "已售", "人拼", "人付款", "包邮", "退货", "补贴", "广告")
_SKU_JUNK = {
    "选择规格", "选择", "确定", "取消", "立即购买", "单独购买", "发起拼单",
    "已选", "发货", "包邮", "评价", "服务", "详情",
}
_COUPON_PRICE_RE = re.compile(r"券后(?:价)?\s*[¥￥]?\s*(\d+(?:\.\d+)?)")
_PDD_PRICE_RE = re.compile(r"[¥￥]\s*(\d+(?:\.\d+)?)")
_EVAL_COUNT_RE = re.compile(r"(?:商品评价|评价)[（(]\s*([\d,，]+)\s*[）)]")
_DELIVER_RE = re.compile(r"发货地\s*[:：]?\s*([^\n]{2,30})")
_SERVICE_KEYS = (
    "48小时发货", "48小时内发货", "7天无理由", "七天无理由", "假一赔十",
    "保证金", "包邮", "极速退款", "退货包运费", "极速发货",
)
_DEGRADED_MARKERS = ("前往app查看价格", "打开app查看", "app内打开", "前往app查看",)

# 滑块 / 登录 / 访问异常的判定文案（仅用于拼多多，不影响天猫/1688）
_SLIDER_MARKERS = (
    "请拖动下方滑块", "拖动滑块完成验证", "滑动验证", "完成安全验证",
    "拼多多安全验证", "拖动滑块", "完成上方拼图",
)
# 滑块页的 <title> 固定为“安全验证”，但验证组件文字常常只在脚本里，
# body.innerText 可能没有任何 _SLIDER_MARKERS（实测详情页因此被当成正常页解析成空记录）。
_SLIDER_TITLE_MARKERS = ("安全验证", "滑块验证")
_LOGIN_MARKERS = ("手机号登录", "扫码登录", "验证码登录", "请登录后查看")
_RATE_LIMIT_MARKERS = ("系统繁忙", "请稍后再试")
_ACCESS_MARKERS = ("访问异常", "系统繁忙", "访问受限")


def _clean(text) -> str:
    return str(text or "").replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")


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


def is_degraded(text) -> bool:
    """降级页判定只认正向证据，双缺失（店铺+评价）只告警，不在此视为降级。"""
    t = str(text or "").lower()
    return any(m in t for m in _DEGRADED_MARKERS)


def parse_pdd_price(text) -> tuple[float | None, str]:
    """返回 (价格, 口径)。被遮挡的价格（如 ¥3.?）返回 (None, ...)，不做猜测。"""
    t = _clean(text)
    for pat, label in ((_COUPON_PRICE_RE, "券后价"), (_PDD_PRICE_RE, "拼单价")):
        for m in pat.finditer(t):
            tail = t[m.end(): m.end() + 4]
            if tail.startswith(("?", "？")) or re.match(r"\s*[.．]\s*[?？]", tail):
                continue
            return round(float(m.group(1)), 2), label
    if re.search(r"[¥￥][^\d\n]{0,3}[?？]", t):
        return None, "未知（价格被遮挡）"
    return None, "未知"


def parse_pdd_sold(text) -> tuple[str | None, int | None]:
    """只取单品「已拼X件」；先剔除「本店/店铺已拼」累计片段。"""
    t = _SHOP_SOLD_RE.sub("", _clean(text))
    m = _SOLD_ITEM_RE.search(t)
    if not m:
        return None, None
    frag = m.group(0).strip()
    return frag, parse_count(m.group(1) + (m.group(2) or ""))


def parse_pdd_shop(text) -> str | None:
    """「本店已拼…」上方紧邻的非空文本行视为店铺名；找不到就 None，不猜。"""
    lines = [l.strip() for l in _clean(text).split("\n") if l.strip()]
    for i, line in enumerate(lines):
        if "本店已拼" in line or "店铺已拼" in line:
            for back in range(1, 4):
                j = i - back
                if j < 0:
                    break
                cand = lines[j]
                if 2 <= len(cand) <= 30 and not any(
                    t in cand for t in ("¥", "￥", "已拼", "评价", "收藏", "人付款", "服务")
                ):
                    return cand
            break
    return None


def _province(s: str) -> str:
    m = re.search(r"(.+?(?:省|市|自治区|特别行政区))", s)
    return m.group(1) if m else s.strip()


def parse_pdd_params(text) -> dict:
    """从详情页文本提取通用参数：评价数 / 发货地 / 服务 / 店铺已拼。"""
    out: dict = {}
    t = _clean(text)
    m = _EVAL_COUNT_RE.search(t)
    if m:
        out["评价数"] = m.group(1).replace(",", "").replace("，", "")
    m = _DELIVER_RE.search(t)
    if m:
        out["发货地"] = _province(m.group(1))
    tags = [k for k in _SERVICE_KEYS if k in t]
    if tags:
        out["服务"] = "、".join(dict.fromkeys(tags))
    m = _SHOP_SOLD_RE.search(t)
    if m:
        out["店铺已拼"] = m.group(0).strip()
    return out


# ———————————————————— 卡片归一化 ————————————————————


def _cents_to_yuan(value) -> float | None:
    """SSR 价格为整数「分」；非法/可疑值返回 None（不写进候选价）。"""
    if value is None:
        return None
    try:
        if isinstance(value, str):
            v = value.strip()
            if not re.fullmatch(r"\d+", v):
                return None
            cents = int(v)
        elif isinstance(value, bool):
            return None
        else:
            cents = int(value)
    except (TypeError, ValueError):
        return None
    if cents <= 0 or cents > 100_000_000:
        return None
    return round(cents / 100, 2)


def _pick_title(raw: dict) -> str:
    title = (raw.get("title_attr") or "").strip()
    if title:
        return title
    lines = [l.strip() for l in _clean(raw.get("text")).split("\n") if l.strip()]
    cand = [l for l in lines if len(l) >= 4 and not any(b in l for b in _BAD_TITLE_PARTS)]
    return max(cand, key=len) if cand else (lines[0] if lines else "")


def _apply_filters(task, title: str, text: str, price: float | None) -> bool:
    for w in getattr(task, "exclude_words", []) or []:
        if w and (w in title or w in text):
            return False
    if price is not None:
        if task.price_min is not None and price < task.price_min:
            return False
        if task.price_max is not None and price > task.price_max:
            return False
    return True


def normalize_ssr_card(it: dict, task) -> dict | None:
    """SSR 列表条目 → 统一候选结构（与天猫/1688 同一口径）。"""
    iid = str(it.get("goodsID") or "").strip()
    if not iid:
        return None
    title = _clean(it.get("goodsName"))
    price = _cents_to_yuan(it.get("price"))
    if not _apply_filters(task, title, title, price):
        return None
    # 注意：SSR 的 salesTip 常是店铺累计口径（"本店已拼…""全店总售…"），
    # 必须用排除店铺口径的解析器；单品销量以详情页为准。
    sold_text, sold_lb = parse_pdd_sold(_clean(it.get("salesTip")))
    return {
        "item_id": iid,
        "title": title,
        "price_text": f"¥{price:.2f}" if price is not None else None,
        "sold_text": sold_text,
        "sold_lb": sold_lb,
        "shop_name": None,
        "image_url": _fix_img(str(it.get("img") or "").strip()),
        "url": DETAIL_URL.format(iid=iid),
    }


def normalize_card(raw: dict, task) -> dict | None:
    """goods.html 锚点兜底条目 → 统一候选结构（.price.Text 口径与 SSR 一致）。"""
    text = _clean(raw.get("text"))
    title = _pick_title(raw)
    iid = str(raw.get("item_id") or "").strip()
    if not iid:
        return None
    price = None
    m = re.search(r"[¥￥]\s*(\d+(?:\.\d+)?)", text)
    if m:
        price = round(float(m.group(1)), 2)
    if not _apply_filters(task, title, text, price):
        return None
    sold_text, sold_lb = parse_pdd_sold(text)
    return {
        "item_id": iid,
        "title": title,
        "price_text": m.group(0) if m else None,
        "sold_text": sold_text,
        "sold_lb": sold_lb,
        "shop_name": None,
        "image_url": _fix_img(raw.get("img") or ""),
        "url": DETAIL_URL.format(iid=iid),
    }


# ———————————————————— 风控 / 登录 / 降级检查 ————————————————————


def _page_title(page) -> str:
    try:
        return _clean(page.title())[:200]
    except Exception:
        return ""


def _is_slider_page(url, title, text) -> bool:
    return (
        "captcha" in str(url or "").lower()
        or any(m in str(text or "") for m in _SLIDER_MARKERS)
        or any(m in str(title or "") for m in _SLIDER_TITLE_MARKERS)
    )


def _is_rate_limit_response(status: int, url: str) -> bool:
    path = str(url or "").split("?", 1)[0].rstrip("/")
    return int(status or 0) == 429 and path.endswith("/proxy/api/search")


def _rate_limit_error() -> BlockedError:
    return BlockedError(
        "拼多多搜索接口返回 429（页面显示「系统繁忙/请稍后再试」）：账号或会话被限流，"
        "请冷却 24–48 小时后再试，不要连续重试"
    )


def _watch_rate_limit(page) -> dict:
    """监听页面自身的搜索请求；429 时置位，供搜索/详情流程早停，避免继续重载。"""
    flag = getattr(page, "_bkr_pdd_rate_flag", None)
    if flag is not None:
        return flag
    flag = {"hit": False}
    try:
        page._bkr_pdd_rate_flag = flag

        def _on_resp(resp):
            try:
                url = resp.url or ""
                if resp.status >= 400 and "/proxy/api/" in url:
                    print(f"    [网络] HTTP {resp.status} {url[:160]}")
                if _is_rate_limit_response(resp.status, url):
                    flag["hit"] = True
            except Exception:
                pass

        page.on("response", _on_resp)
    except Exception:
        pass
    return flag


def check_pdd_blocked(page) -> None:
    """拼多多专属检查：滑块 / 登录页 / 访问异常（不改变天猫/1688 的判定）。"""
    url = (page.url or "").lower()
    title = _page_title(page)
    try:
        head = _clean(page.inner_text("body", timeout=3000))[:3000]
    except Exception:
        head = ""
    if "login.html" in url or any(m in head for m in _LOGIN_MARKERS):
        raise LoginError(
            "拼多多未登录或登录态失效：请点右上角「登录拼多多」完成手机号验证码登录"
            "（或终端 bkr login --site pdd），登录成功后再点「继续采集」"
        )
    if _is_slider_page(url, title, head):
        raise BlockedError("拼多多出现滑块验证，请人工在浏览器完成验证后执行 --resume 续采")
    if any(m in head for m in _RATE_LIMIT_MARKERS):
        raise BlockedError(
            "拼多多返回「系统繁忙/请稍后再试」（浏览器网络面板显示 search 接口 429）："
            "账号或会话被限流，请冷却 24–48 小时后再试，不要连续重试"
        )
    if any(m in head for m in _ACCESS_MARKERS):
        raise BlockedError("拼多多返回访问异常/系统繁忙，请人工确认恢复并冷却一段时间后再续采")


JS_DOM_IMAGES = """
() => {
  const out = [];
  document.querySelectorAll('img').forEach((im) => {
    const s = im.currentSrc || im.src || im.getAttribute('data-src') || '';
    if (/pddpic|yangkeduo|pinduoduo/.test(s) && !/logo|icon|avatar|扫码/i.test(s)) out.push(s);
  });
  return out;
}
"""


def _img_key(url: str) -> str:
    """图片 URL → 稳定键（去掉协议参数与尺寸后缀，取文件名首段）。"""
    u = str(url or "").split("?")[0]
    tail = u.rsplit("/", 1)[-1]
    return tail.split(".")[0]


def _reorder_items_by_images(items: list[dict], dom_urls: list[str]):
    """按页面渲染的图片顺序重排 SSR 条目。返回 (items, 匹配数)；匹配太少则原样返回。"""
    if not items or not dom_urls:
        return items, 0
    by_key = {}
    for it in items:
        k = _img_key(it.get("img") or "")
        if k and k not in by_key:
            by_key[k] = it
    used, ordered = set(), []
    for u in dom_urls:
        k = _img_key(u)
        it = by_key.get(k)
        if it is not None and id(it) not in used:
            used.add(id(it))
            ordered.append(it)
    rest = [it for it in items if id(it) not in used]
    matched = len(ordered)
    if matched < max(2, int(len(items) * 0.4)):
        return items, matched
    return ordered + rest, matched


def _sort_label_diag(page) -> list[dict]:
    """排序项排查：返回 DOM 中精确文本“销量”节点的位置（不点击）。"""
    js = """
    () => Array.from(document.querySelectorAll('div,span,a,button,li'))
      .filter((e) => (e.textContent || '').trim() === '销量')
      .map((e) => {
        const r = e.getBoundingClientRect();
        return {tag: e.tagName, x: Math.round(r.x), y: Math.round(r.y),
                w: Math.round(r.width), h: Math.round(r.height)};
      })
    """
    try:
        return page.evaluate(js) or []
    except Exception:
        return []


def _try_sales_sort(page, original_url: str) -> str:
    """点击“销量”后尽力获取销量序。返回 url=重开排序页 / dom=按图序重排 / none=保持综合序。"""
    label = None
    for attempt in range(2):
        try:
            label = ensure_sort(page, ("销量", "销量排序"), max_y=900)
        except Exception as exc:
            print(f"    [排序][警告] 点击“销量”失败：{exc}")
            return "none"
        if label:
            break
        if attempt == 0:
            print("    [排序] 首次未找到“销量”，等待布局稳定后重试")
            page.wait_for_timeout(2000)
    if not label:
        print(f"    [排序][诊断] DOM 中“销量”节点：{_sort_label_diag(page)[:5]}")
        print("    [排序][警告] 未找到“销量”排序项，保持综合顺序")
        return "none"
    page.wait_for_timeout(random.randint(1500, 2200))
    url_after = page.url or ""
    if "search_result" in url_after and not same_search_url(url_after, original_url):
        print("    [排序] 点击“销量”后 URL 已带排序，重开一次取销量序 SSR")
        try:
            page.goto(url_after, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(random.randint(*PAGE_SETTLE_MS))
            _wait_content(page)
        except Exception as exc:
            print(f"    [排序][警告] 重开排序页失败，保持当前数据：{exc}")
        return "url"
    try:
        order = page.evaluate(JS_DOM_IMAGES)
    except Exception:
        order = []
    if order:
        try:
            page._bkr_pdd_dom_order = order
        except Exception:
            pass
        print(f"    [排序] 已点击“销量”（URL 不变），捕获渲染顺序 {len(order)} 张图，将按图序重排")
        return "dom"
    print("    [排序][警告] 已点击“销量”但拿不到排序结果，保持综合顺序")
    return "none"


def wait_human_slide(page, timeout: int = 180) -> bool:
    """检测到滑块后不关闭浏览器，交给人工拖动，完成后自动继续。

    拼多多的滑块通常可以人工在窗口里拖过；自动流程只需耐心等待，
    轮询的是本页已渲染文本，不产生额外网络请求。
    """
    try:
        page.bring_to_front()
    except Exception:
        pass
    from ..browser import enable_mouse_as_touch

    enable_mouse_as_touch(page)  # 移动页滑块只认触摸事件，把人工鼠标操作转成触摸
    print(
        "  [人工接管] 拼多多出现滑块验证：请在浏览器窗口里拖动滑块完成拼图，"
        f"完成后自动继续（最多等待 {timeout // 60} 分钟）"
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2)
        try:
            head = _clean(page.inner_text("body", timeout=2000))[:2500]
            cur_url = (page.url or "").lower()
            title = _page_title(page)
        except Exception:
            return False
        if _is_slider_page(cur_url, title, head):
            continue
        print("  [人工接管] 滑块已通过，继续采集")
        return True
    print("  [人工接管] 等待超时：滑块未完成，本批将暂停（可人工完成后重新开始/续采）")
    return False


def _check_or_wait_human(page) -> None:
    """先做常规检查；仅当命中滑块时停下来等人工处理，其余仍按原规则暂停。"""
    try:
        check_pdd_blocked(page)
    except LoginError:
        raise
    except BlockedError:
        head = ""
        try:
            head = _clean(page.inner_text("body", timeout=2000))[:2500]
        except Exception:
            pass
        if not _is_slider_page(page.url, _page_title(page), head):
            raise
        if not wait_human_slide(page):
            raise
        check_pdd_blocked(page)


# ———————————————————— 搜索页：打开与解析 ————————————————————


def search_url(task, pageno: int) -> str:
    return SEARCH_URL.format(q=quote(task.keywords[0]))


_WAIT_READY_JS = """
() => !!(
  (window.rawData && window.rawData.stores && window.rawData.stores.store
   && window.rawData.stores.store.data)
  || (document.body && document.body.innerText.indexOf('已拼') >= 0)
  || document.querySelectorAll('a[href*="goods.html"]').length > 0
)
"""


def _wait_content(page, timeout_ms: int = 20000) -> str:
    """等待搜索页渲染/水合（商品卡片或 SSR 数据出现）。

    拼多多 H5 先返回空壳页面，数据由 JS 稍后注入；且水合完成后前端可能把
    window.rawData 从内存删除（SSR 原文仍在 script 标签里，由 JS_SSR_LIST 兜底读取）。
    返回命中方式：window / render / anchors / none。纯等待，不产生网络请求。
    """
    try:
        page.wait_for_function(_WAIT_READY_JS, timeout=timeout_ms)
    except Exception:
        return "none"
    try:
        if page.evaluate(
            "() => !!(window.rawData && window.rawData.stores && window.rawData.stores.store)"
        ):
            return "window"
        body = page.evaluate("() => (document.body && document.body.innerText) || ''")
        if "已拼" in (body or ""):
            return "render"
    except Exception:
        pass
    return "anchors"


def open_search_page(ctx, task, pageno: int):
    """拼多多 H5 为无限滚动：固定单屏采集，第 N 页参数被忽略并告警。"""
    page = get_work_page(ctx)
    rate_flag = _watch_rate_limit(page)
    url = search_url(task, 1)
    if pageno > 1 and not getattr(page, "_bkr_pdd_no_page_warn", False):
        print("  [警告] 拼多多搜索为无限滚动，本批固定采 1 屏（搜索页数参数被忽略）")
        try:
            page._bkr_pdd_no_page_warn = True
        except Exception:
            pass
    print(f"  拼多多搜索页: {url}")
    if not same_search_url(page.url or "", url):
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(random.randint(*PAGE_SETTLE_MS))
        if rate_flag.get("hit"):
            raise _rate_limit_error()
        mode = _wait_content(page)
        if rate_flag.get("hit"):
            raise _rate_limit_error()
        if mode == "none":
            print("  [警告] 页面 20 秒内未完成渲染（rawData/商品链接均未出现），继续尝试兜底解析")
        else:
            print(f"  页面已就绪（{mode}）")
        _check_or_wait_human(page)
        # —— 销量排序（尽力而为）：URL 变排序页则重开取销量序 SSR；否则按 DOM 图序重排 ——
        _try_sales_sort(page, url)
        _check_or_wait_human(page)
        _scroll_list(page)
        try:
            page._bkr_pdd_scrolled = True
        except Exception:
            pass
    elif not getattr(page, "_bkr_pdd_scrolled", False):
        _scroll_list(page)
        try:
            page._bkr_pdd_scrolled = True
        except Exception:
            pass
    return page


def _scroll_list(page) -> None:
    """滚动 3–5 屏，让搜索页加载更多商品（页面自身行为，不调用接口）。"""
    for _ in range(random.randint(3, MAX_SCROLLS)):
        try:
            page.mouse.wheel(0, random.randint(*SCROLL_STEP_PX))
            page.wait_for_timeout(SCROLL_INTERVAL_MS)
        except Exception:
            break
    try:
        page.wait_for_timeout(600)
    except Exception:
        pass


def _dump_search_page(page) -> str:
    d = DATA_DIR / "debug"
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = d / f"pdd_search_{stamp}.html"
    png_path = d / f"pdd_search_{stamp}.png"
    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
    try:
        page.screenshot(path=str(png_path), full_page=False)
    except Exception:
        pass
    return str(d)


def _read_ssr(page) -> dict:
    """读取 SSR 列表（最多 3 次，间隔 1.2s）。"""
    res: dict = {}
    for _attempt in range(3):
        try:
            res = page.evaluate(JS_SSR_LIST)
        except Exception as exc:
            res = {"ok": False, "why": f"{type(exc).__name__}: {exc}"}
        if res.get("ok") and (res.get("items") or []):
            break
        try:
            page.wait_for_timeout(1200)
        except Exception:
            break  # 窗口被人工关闭：停止重试，把已读结果交回上层
    return res


def _recover_ssr_page(page, task) -> bool:
    """滑块通过后拼多多可能给客户端渲染版（DOM 有商品、无 SSR 原文）。

    此时重新加载一次搜索页以获取服务端渲染版本。每批最多执行一次。
    """
    if getattr(page, "_bkr_pdd_reloaded", False):
        return False
    url = search_url(task, 1)
    print("  [恢复] 当前页面没有 SSR 原文（常见于滑块通过后的客户端渲染），重新加载一次搜索页")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(random.randint(*PAGE_SETTLE_MS))
        _wait_content(page)
        _check_or_wait_human(page)
        _try_sales_sort(page, url)
        page._bkr_pdd_reloaded = True
        return True
    except Exception as exc:
        print(f"  [恢复][警告] 重新加载失败：{exc}")
        return False


def collect_pages(ctx, task, start_page: int = 1) -> list[dict]:
    """搜索页 → 候选列表。优先读 SSR 数据，SSR 失效时退回锚点解析。"""
    if task.pages > 1:
        print(f"  [警告] 拼多多为无限滚动搜索，忽略搜索页数 {task.pages}（固定 1 屏）")
    page = open_search_page(ctx, task, 1)

    cards: list[dict] = []
    res = _read_ssr(page)
    if not (res.get("ok") and (res.get("items") or [])) and _recover_ssr_page(page, task):
        res = _read_ssr(page)
    if res.get("ok"):
        items = res.get("items") or []
        print(f"  拼多多 SSR 列表 {len(items)} 条（读取方式：{res.get('mode') or 'script'}）")
        if items:
            keys = ", ".join(res.get("keys") or [])
            print(f"    [校准] SSR 字段：{keys[:160]}")
            storeish = sum(
                1 for it in items
                if any(k in str(it.get("salesTip") or "") for k in ("本店已拼", "店铺已拼", "全店总售"))
            )
            if storeish:
                print(
                    f"    [口径] {storeish}/{len(items)} 条卡片销量是店铺累计口径，已忽略"
                    "（单品销量以详情页为准）"
                )
        dom_order = getattr(page, "_bkr_pdd_dom_order", None)
        if items and dom_order:
            items, matched = _reorder_items_by_images(items, dom_order)
            if matched >= 2:
                print(f"    [排序] 已按页面渲染顺序重排候选（图片匹配 {matched}/{len(items)}）")
        for it in items:
            c = normalize_ssr_card(it, task)
            if c:
                c["page_no"] = 1
                cards.append(c)
    else:
        why = res.get("why") or "未知"
        print(f"  [警告] 未找到 SSR 数据路径：{why}")
        keys = res.get("dataKeys") or res.get("storesKeys") or []
        if keys:
            print(f"    [排查] 现有 keys：{', '.join(str(k) for k in keys[:20])}")

    if not cards:
        raws = page.evaluate(JS_CARDS)
        for r in raws:
            c = normalize_card(r, task)
            if c:
                c["page_no"] = 1
                cards.append(c)
        if cards:
            print(f"  [兜底] SSR 不可用，使用 goods.html 锚点解析（{len(cards)} 个）；请把此现象反馈校准 SSR 路径")

    if not cards:
        # CSR 页面兜底：商品已渲染但无 SSR —— 从 React fiber 恢复列表
        try:
            from ..agent.observe import read_signals

            sig = read_signals(page)
            if int(sig.get("productTexts") or 0) >= 3:
                items = extract_dom_react(page)
                for it in items:
                    c = normalize_ssr_card(it, task)
                    if c:
                        c["page_no"] = 1
                        cards.append(c)
                if cards:
                    print(f"  [CSR] 从 React props 恢复候选 {len(cards)} 个")
        except Exception as exc:
            print(f"  [CSR][警告] fiber 提取失败：{type(exc).__name__}: {exc}")

    if not cards:
        # 确定性路径失败 → 采集监督 Agent 接管（规则引擎打底，LLM 可选）
        try:
            from ..agent.runner import agent_enabled, supervised_recover

            if agent_enabled():
                result = supervised_recover(page, task, reason="列表解析失败")
                for it in result.get("items") or []:
                    c = normalize_ssr_card(it, task)
                    if c:
                        c["page_no"] = 1
                        cards.append(c)
                if cards:
                    print(f"  [agent] 恢复候选 {len(cards)} 个（{result.get('summary')}）")
        except Exception as exc:  # Agent 自身异常不得掩盖原错误
            print(f"  [agent] 接管异常：{type(exc).__name__}: {exc}")

    if not cards:
        d = _dump_search_page(page)
        raise EmptyResultError(
            f"拼多多搜索页解析到 0 个商品（未登录/风控/改版）。诊断文件：{d}；"
            "建议先运行 python -m bkr.cli pdd-probe 校准"
        )

    solds = [c["sold_lb"] for c in cards if c.get("sold_lb")]
    if solds:
        vals = solds[:8]
        print(f"    [校验] 已拼下限前 {len(vals)} 个：{vals}")
        inv = sum(1 for a, b in zip(vals, vals[1:]) if a < b)
        if inv >= max(1, len(vals) // 3):
            print("    [校验][警告] 已拼值整体不是降序，拼多多排序可能未生效")

    seen, out = set(), []
    for c in cards:
        if c["item_id"] in seen:
            continue
        seen.add(c["item_id"])
        c["rank"] = len(out) + 1
        out.append(c)
    return out


# ———————————————————— 详情页 ————————————————————


def extract_from_page(page, item_id: str, save_html: bool = False) -> dict:
    page.wait_for_timeout(random.randint(*PAGE_SETTLE_MS))
    _check_or_wait_human(page)
    for _ in range(random.randint(3, MAX_SCROLLS)):
        try:
            page.mouse.wheel(0, random.randint(*SCROLL_STEP_PX))
            page.wait_for_timeout(SCROLL_INTERVAL_MS)
        except Exception:
            break

    data = page.evaluate(JS_DETAIL)
    html = page.content() if save_html else None
    title = _clean(data.get("title")).strip()
    region = _clean(data.get("top_text"))
    if not title and not region:
        raise BlockedError("拼多多详情页未正常加载（可能登录失效/风控），已暂停")

    if is_degraded(region):
        raise PddDegradedError(
            "拼多多详情页出现降级提示（前往App查看价格等），本批已停；"
            "请人工确认后冷却一段时间再续采"
        )

    price, caliber = parse_pdd_price(region)
    sold_text, sold_lb = parse_pdd_sold(region)
    shop_name = parse_pdd_shop(region)
    params = dict(data.get("attrs") or {})
    params.update(parse_pdd_params(region))
    if not title and price is None and not sold_text and not shop_name and not params:
        # 实测滑块页 body 可能仍有少量文本，若不拦截会写出 is_detail=1 的空记录。
        raise BlockedError(
            "拼多多详情页未解析到任何字段（可能滑块/登录/改版），已暂停，不写空记录；"
            "请人工确认后冷却一段时间再续采"
        )
    params["价格口径"] = caliber

    has_eval = bool(_EVAL_COUNT_RE.search(region))
    if not shop_name and not has_eval:
        print("    [警告] 店铺名与评价区均未识别（可能是布局变体，本条按已采数据入库）")

    skus = [
        s.strip() for s in (data.get("sku_texts") or [])
        if s.strip() and s.strip() not in _SKU_JUNK and len(s.strip()) <= 60
    ][:40]

    return {
        "item_id": item_id,
        "title": title,
        "price_final": price,
        "sold_text": sold_text,
        "sold_lb": sold_lb,
        "cart_text": None,  # 拼多多 H5 无加购数，不用销量冒充
        "cart_lb": None,
        "shop_name": shop_name,
        "shop_rating": None,
        "positive_rate": None,
        "sku_names": skus,
        "params": params if params else {},
        "image_url": _fix_img(data.get("image_url") or ""),
        "video_url": data.get("video_url") or "",
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


# ———————————————————— 实机校准探针（不写数据库） ————————————————————


def probe(keyword: str = "手表", save_html: bool = True) -> None:
    """打开拼多多搜索页，输出 SSR 路径/字段/滚动前后条数，供首跑校准。

    用法：python -m bkr.cli pdd-probe --keyword 手表
    不写数据库、不采详情，只有一次搜索页加载。
    """
    from ..browser import browser_runtime, close_context, launch_context, has_pdd_login_cookies
    from ..config import profile_dir_for

    with browser_runtime() as p:
        ctx = launch_context(p, profile_dir=profile_dir_for("pdd"), platform="pdd")
        try:
            try:
                names = {c.get("name") for c in ctx.cookies()}
                print(f"[probe] 登录态：{'已登录' if has_pdd_login_cookies(names) else '未登录（请先 bkr login --site pdd）'}")
            except Exception:
                pass
            page = ctx.new_page()
            rate_flag = _watch_rate_limit(page)
            url = SEARCH_URL.format(q=quote(keyword))
            print(f"[probe] 打开：{url}")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(random.randint(*PAGE_SETTLE_MS))
            if rate_flag.get("hit"):
                print(
                    "[probe] 搜索接口 429：账号/会话被限流，本次 probe 提前结束；"
                    "请冷却 24–48 小时后再试，不要连续重试"
                )
                if save_html:
                    print(f"[probe] 现场已保存：{_dump_search_page(page)}")
                return
            mode = _wait_content(page)
            if rate_flag.get("hit"):
                print(
                    "[probe] 搜索接口 429：账号/会话被限流，本次 probe 提前结束；"
                    "请冷却 24–48 小时后再试，不要连续重试"
                )
                if save_html:
                    print(f"[probe] 现场已保存：{_dump_search_page(page)}")
                return
            print(f"[probe] 渲染就绪：{mode}（空壳页会等待最多 20 秒）")
            try:
                check_pdd_blocked(page)
            except LoginError as e:
                print(f"[probe] 登录检查未通过：{e}")
                if save_html:
                    print(f"[probe] 现场已保存：{_dump_search_page(page)}")
                return
            except BlockedError as e:
                try:
                    head = _clean(page.inner_text("body", timeout=2000))[:2500]
                except Exception:
                    head = ""
                if not _is_slider_page(page.url, _page_title(page), head):
                    print(f"[probe] 命中风控/限流（非滑块）：{e}")
                    print(
                        "[probe] 未进入人工等待。若是「系统繁忙/请稍后再试」，"
                        "说明账号或会话被搜索接口限流（429），请冷却 24–48 小时后再试，不要连续重试"
                    )
                    if save_html:
                        print(f"[probe] 现场已保存：{_dump_search_page(page)}")
                    return
                print(f"[probe] 命中滑块：{e}")
                if not wait_human_slide(page):
                    if save_html:
                        print(f"[probe] 现场已保存：{_dump_search_page(page)}")
                    return
                try:
                    check_pdd_blocked(page)
                except (LoginError, BlockedError) as e2:
                    print(f"[probe] 人工处理后仍未通过：{e2}")
                    if save_html:
                        print(f"[probe] 现场已保存：{_dump_search_page(page)}")
                    return

            res = _read_ssr(page)
            if not (res.get("ok") and (res.get("items") or [])):
                class _T:
                    keywords = [keyword]

                if _recover_ssr_page(page, _T()):
                    res = _read_ssr(page)
            if not (res.get("ok") and (res.get("items") or [])):
                fiber = extract_dom_react(page)
                sample = [it.get("goodsName") for it in fiber[:2]]
                print(f"[probe] SSR 不可用，React props 提取：{len(fiber)} 条；样例：{sample}")
            print(f"[probe] SSR 可用：{res.get('ok')}，读取方式：{res.get('mode') or '—'}，原因：{res.get('why') or '—'}")
            print(f"[probe] SSR 条数（排序前）：{res.get('count')}")
            keys = res.get("keys") or []
            if keys:
                print(f"[probe] 首个条目的字段 keys：{keys}")
            if not res.get("ok"):
                dkeys = res.get("dataKeys") or res.get("storesKeys") or []
                if dkeys:
                    print(f"[probe] 现有 keys：{dkeys}")

            url_before = page.url or ""
            sort_mode = _try_sales_sort(page, url_before)
            print(f"[probe] 排序尝试结果：{sort_mode}；当前 URL：{page.url}")
            res_sort = page.evaluate(JS_SSR_LIST)
            items_sort = res_sort.get("items") or []
            print(f"[probe] 排序后 SSR 条数：{len(items_sort)}，首个 goodsID："
                  f"{items_sort[0].get('goodsID') if items_sort else None}")
            dom_order = getattr(page, "_bkr_pdd_dom_order", None)
            print(f"[probe] DOM 图序捕获数量：{len(dom_order) if dom_order else 0}")

            _scroll_list(page)
            res2 = page.evaluate(JS_SSR_LIST)
            print(f"[probe] SSR 条数（滚动后）：{res2.get('count')}（若不变，说明滚动不追加 SSR 数据）")

            anchors = page.evaluate(JS_CARDS)
            print(f"[probe] DOM 锚点卡片数：{len(anchors)}")

            sample = (res2.get("items") or res.get("items") or [])[:2]
            if sample:
                print(f"[probe] 样例（截断）：{json.dumps(sample, ensure_ascii=False)[:600]}")

            if save_html:
                print(f"[probe] 现场已保存：{_dump_search_page(page)}")
            print("[probe] 完成。若 SSR 条数 > 0 且字段 keys 正常，可开始采集；否则把输出与 data/debug 下的文件发开发者。")
        finally:
            close_context(ctx)