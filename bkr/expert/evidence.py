"""把采集批次与上传文档整理成统一的 Evidence Pack。"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from .. import database
from ..analysis import build_summary
from ..analysis_profile import profile_for
from ..parse import parse_price
from ..repositories import snapshot_repo, task_repo

PLATFORM_LABELS = {"tmall": "天猫", "1688": "1688", "pdd": "拼多多"}
PROFILE_SOURCE_LABELS = {
    "manual": "人工配置",
    "llm": "自动生成",
    "discovered": "自动发现",
    "none": "未生成",
}
MAX_EVIDENCE_CHARS = 120_000
DOC_CHUNK = 1000


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _positive(value) -> float | None:
    """价格护栏：None/0/非法值都视为缺失（上游会把缺失列表价写成 0）。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def batch_rows(task_id: str, batch: str) -> list[dict]:
    """读取一个批次的商品行（价格/销量/标题/店铺/参数），复用分析口径。"""
    conn = database.connect()
    try:
        rows = snapshot_repo.list_task_products(conn, task_id, batch)
    finally:
        conn.close()

    out = []
    for r in rows:
        price = _positive(r["price_final"])
        source = "detail"
        if price is None:
            price = _positive(parse_price(r["price_text"] or ""))
            source = "list" if price is not None else None
        out.append({
            "item_id": r["item_id"],
            "platform": r["platform"],
            "title": r["title"],
            "price_final": price,
            "price_source": source,
            "sold_lb": r["sold_lb"],
            "sold_text": r["sold_text"],
            "cart_lb": r["cart_lb"],
            "shop_name": r["shop_name"],
            "image_url": r["image_url"],
            "product_url": r["product_url"],
            "params": _loads(r["params_json"], {}),
            "skus": _loads(r["sku_names"], []),
            "is_detail": r["is_detail"],
        })
    return out


def task_info(task_id: str) -> dict:
    conn = database.connect()
    try:
        row = task_repo.get_task(conn, task_id)
    finally:
        conn.close()
    if not row:
        return {"task_id": task_id, "name": task_id, "platform": "tmall", "keywords": []}
    return {
        "task_id": row["task_id"],
        "name": row["name"],
        "platform": row["platform"] or "tmall",
        "keywords": _loads(row["keywords"], []),
    }


def _fmt_num(v) -> str:
    if v is None:
        return "—"
    return f"{v:g}" if isinstance(v, float) else str(v)


def _summary_text(summary: dict) -> str:
    c = summary.get("counts") or {}
    price = (summary.get("price") or {}).get("stats") or {}
    sold = summary.get("sold") or {}
    shops = summary.get("shops") or {}
    parts = [
        f"候选 {c.get('candidates', 0)} 个，已采详情 {c.get('detailed', 0)} 个",
        f"有价格 {c.get('with_price', 0)} 个（详情价 {c.get('with_price_detail', 0)} / 列表价 {c.get('with_price_list', 0)}）",
        f"价格中位数 {_fmt_num(price.get('median'))} 元，P25 {_fmt_num(price.get('p25'))}，P75 {_fmt_num(price.get('p75'))}",
        f"已售中位数 {_fmt_num(sold.get('median'))}",
    ]
    top_share = shops.get("top5_share")
    if top_share is not None:
        parts.append(f"TOP5 店铺销量占比 {round(float(top_share) * 100, 1)}%")
    return "；".join(parts)


def _tokens(text: str) -> list[str]:
    text = (text or "").lower()
    words = re.findall(r"[a-z0-9]+", text)
    cjk = re.findall(r"[一-鿿]", text)
    bigrams = [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)]
    return words + cjk + bigrams


def _chunks(text: str, size: int = DOC_CHUNK) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    out = []
    for i in range(0, len(text), size):
        part = text[i:i + size].strip()
        if part:
            out.append(part)
    return out


class EvidenceIndex:
    """证据检索：优先 BM25，缺依赖时退化为关键词重叠。"""

    def __init__(self, items: list[dict]):
        self.items = [i for i in items]
        self._bm25 = None
        try:
            from rank_bm25 import BM25Okapi

            corpus = [_tokens(i.get("text") or "") for i in self.items]
            if corpus:
                self._bm25 = BM25Okapi(corpus)
        except Exception:
            self._bm25 = None

    def search(self, query: str, top_k: int = 8) -> list[dict]:
        query = (query or "").strip()
        if not query or not self.items:
            return []
        top_k = max(1, min(int(top_k or 8), 20))
        if self._bm25 is not None:
            scores = self._bm25.get_scores(_tokens(query))
            ranked = sorted(zip(scores, self.items), key=lambda x: x[0], reverse=True)
            return [i for s, i in ranked[:top_k] if s > 0] or [i for _, i in ranked[:top_k]]
        q = set(_tokens(query))
        ranked = []
        for item in self.items:
            text = item.get("text") or ""
            score = len(q & set(_tokens(text)))
            if score:
                ranked.append((score, item))
        ranked.sort(key=lambda x: x[0], reverse=True)
        return [i for _, i in ranked[:top_k]]


def build_evidence(sources: list[dict]) -> dict[str, Any]:
    """sources: [{type: batch|report|note, ...}] → Evidence Pack。"""
    evidence: list[dict] = []
    batches: list[dict] = []
    batch_data: dict[str, dict] = {}
    docs: list[dict] = []
    total_chars = 0

    counter = 0

    def add(kind: str, source: str, text: str, data: Any = None) -> dict:
        nonlocal counter, total_chars
        counter += 1
        item = {
            "id": f"E{counter}",
            "kind": kind,
            "source": source,
            "text": text,
            "data": data,
        }
        evidence.append(item)
        total_chars += len(text or "")
        return item

    for src in sources:
        kind = src.get("type")
        if kind == "batch":
            task_id = str(src.get("task_id") or "")
            batch = str(src.get("batch") or "")
            if not task_id or not batch:
                continue
            info = task_info(task_id)
            label = PLATFORM_LABELS.get(info["platform"], info["platform"])
            rows = batch_rows(task_id, batch)
            if not rows:
                continue
            profile = profile_for(info["name"])
            summary = build_summary(rows, profile)
            source_label = f"{label} / {info['name']} / {batch}"
            batches.append({
                "task_id": task_id,
                "batch": batch,
                "platform": info["platform"],
                "label": label,
                "name": info["name"],
                "items": len(rows),
                "details": sum(1 for r in rows if r.get("is_detail")),
            })
            batch_data[f"{task_id}::{batch}"] = {
                "info": info,
                "summary": summary,
                "rows": rows,
                "label": source_label,
            }
            add("metric", source_label, _summary_text(summary), summary)
            add("distribution", source_label,
                "价格带分布：" + "；".join(
                    f"{b.get('label')} 元 {b.get('count')} 个 / 销量合计 {b.get('sold')}"
                    for b in (summary.get("price") or {}).get("bands") or []
                ),
                (summary.get("price") or {}).get("bands"))
            add("distribution", source_label,
                "销量区间分布：" + "；".join(
                    f"{b.get('label')} {b.get('count')} 个"
                    for b in (summary.get("sold") or {}).get("bands") or []
                ),
                (summary.get("sold") or {}).get("bands"))
            shops = summary.get("shops") or {}
            add("metric", source_label,
                "店铺集中度：" + "；".join(
                    f"{s.get('name')} {s.get('count')} 个 / 销量 {s.get('sold')}"
                    for s in shops.get("shops") or []
                ),
                shops)
            add("distribution", source_label,
                "标题关键词：" + "；".join(
                    f"{k.get('word')}×{k.get('count')}"
                    for k in (summary.get("keywords") or [])[:15]
                ),
                summary.get("keywords"))
            add("distribution", source_label,
                "参数画像：" + "；".join(
                    f"{p.get('key')}=" + "/".join(
                        f"{v.get('value')}({v.get('count')})" for v in p.get("top") or []
                    )
                    for p in (summary.get("params") or [])[:8]
                ),
                summary.get("params"))
            points = summary.get("selling_points") or {}
            add("distribution", source_label,
                "卖点词（标题命中）：" + "；".join(
                    f"{k.get('word')}（{k.get('items')} 个商品，均价 {_fmt_num(k.get('avg_price'))}，"
                    f"均销量 {_fmt_num(k.get('avg_sold'))}，覆盖 "
                    f"{round(float(k.get('coverage') or 0) * 100)}%）"
                    for k in (points.get("title") or [])[:12]
                ),
                points.get("title"))
            add("distribution", source_label,
                "参数卖点：" + "；".join(
                    f"{p.get('key')}={p.get('value')}（{p.get('items')} 个商品，"
                    f"均价 {_fmt_num(p.get('avg_price'))}，均销量 {_fmt_num(p.get('avg_sold'))}）"
                    for p in (points.get("params") or [])[:12]
                ),
                points.get("params"))
            prof = summary.get("profile") or {}
            add("metric", source_label,
                f"卖点词表：来源 {PROFILE_SOURCE_LABELS.get(prof.get('source') or 'none', '未生成')}，"
                f"{prof.get('words') or 0} 个词，覆盖商品 "
                f"{round(float(prof.get('coverage') or 0) * 100)}%",
                prof)
            for p in (summary.get("top_products") or [])[:10]:
                add("product", source_label,
                    f"TOP 商品：{p.get('title')}；价格 {_fmt_num(p.get('price_final'))}；"
                    f"已售 {p.get('sold_text') or '—'}；店铺 {p.get('shop_name') or '—'}",
                    p)

        elif kind in ("report", "note"):
            name = str(src.get("name") or ("上传报告" if kind == "report" else "手动笔记"))
            content = str(src.get("content") or "").strip()
            if not content:
                continue
            if kind == "note":
                add("note", name, content)
            else:
                for idx, chunk in enumerate(_chunks(content)):
                    if total_chars > MAX_EVIDENCE_CHARS:
                        break
                    item = add("doc", f"{name}（片段 {idx + 1}）", chunk)
                    docs.append(item)

    # 控制证据包大小：优先保留 metric/product，再保留文档
    if total_chars > MAX_EVIDENCE_CHARS:
        kept = []
        used = 0
        for item in sorted(
            evidence,
            key=lambda i: {"metric": 0, "product": 1, "distribution": 2, "note": 3, "doc": 4}.get(i["kind"], 5),
        ):
            size = len(item.get("text") or "")
            if used + size > MAX_EVIDENCE_CHARS:
                continue
            kept.append(item)
            used += size
        evidence = sorted(kept, key=lambda i: int(i["id"][1:]))

    return {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "batches": batches,
            "notes": [e["text"] for e in evidence if e["kind"] == "note"],
            "evidence_count": len(evidence),
        },
        "evidence": evidence,
        "batch_data": batch_data,
        "index": EvidenceIndex(evidence),
    }


def pack_for_prompt(pack: dict, limit: int = 60_000) -> str:
    """把证据包压成给模型阅读的文本（保留 E#）。"""
    lines = []
    used = 0
    for item in pack.get("evidence") or []:
        line = f"[{item['id']}]（{item['source']}）{item['text']}"
        if used + len(line) > limit:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)