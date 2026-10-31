"""服务端小工具：平台图片防盗链代理地址生成与校验。"""
from __future__ import annotations

from urllib.parse import quote, urlparse

_PDD_HOSTS = ("pddpic.com", "yangkeduo.com", "pinduoduo.com")


def proxied_image(platform: str, url: str | None) -> str | None:
    """1688 / 拼多多图片走本地代理（CDN 防盗链），其余平台原样返回。"""
    if platform in ("1688", "pdd") and url:
        return f"/api/image?url={quote(url, safe='')}"
    return url


def fallback_product_url(platform: str, item_id: str) -> str:
    if platform == "1688":
        return f"https://detail.1688.com/offer/{item_id}.html"
    if platform == "pdd":
        return f"https://mobile.yangkeduo.com/goods.html?goods_id={item_id}"
    return f"https://detail.tmall.com/item.htm?id={item_id}"


def image_referer(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        host = ""
    if any(host.endswith(h) for h in _PDD_HOSTS):
        return "https://mobile.yangkeduo.com/"
    return "https://detail.1688.com/"


def is_allowed_image_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname
    return host.endswith("alicdn.com") or any(host.endswith(h) for h in _PDD_HOSTS)