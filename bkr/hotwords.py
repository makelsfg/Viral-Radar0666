"""热搜联想词：淘宝搜索建议服务（公开、免签名 JSON 接口，低频查询）。

说明：该接口是淘宝网页搜索框的联想数据源，单次查询仅一次轻量 HTTP 请求，
不涉及登录态与签名；仅用于个人选品参考，不建议批量刷。
"""
from __future__ import annotations

import re

SUGGEST_URL = "https://suggest.taobao.com/sug"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://www.taobao.com/",
}


def parse_suggest_payload(payload) -> list[str]:
    """解析联想词返回：支持 dict（JSON）或 JSONP/文本。"""
    words: list[str] = []

    def add(word: str) -> None:
        w = (word or "").strip()
        if w and w not in words:
            words.append(w)

    if isinstance(payload, dict):
        for item in payload.get("result") or []:
            if isinstance(item, (list, tuple)) and item:
                add(str(item[0]))
        return words

    if isinstance(payload, str):
        for m in re.finditer(r'\["([^"]{1,40})"', payload):
            add(m.group(1))
    return words


def fetch_hotwords(keyword: str, limit: int = 20) -> list[str]:
    import requests

    resp = requests.get(
        SUGGEST_URL,
        params={"code": "utf-8", "q": keyword},
        headers=_HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    try:
        payload = resp.json()
    except ValueError:
        payload = resp.text
    return parse_suggest_payload(payload)[:limit]