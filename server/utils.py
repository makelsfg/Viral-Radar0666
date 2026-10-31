"""服务端小工具：1688 图片防盗链代理地址生成与校验。"""
from __future__ import annotations

from urllib.parse import quote, urlparse


def proxied_image(platform: str, url: str | None) -> str | None:
    """1688 图片走本地代理（CDN 防盗链），其余平台原样返回。"""
    if platform == "1688" and url:
        return f"/api/image?url={quote(url, safe='')}"
    return url


def is_allowed_image_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    return parsed.hostname.endswith("alicdn.com")