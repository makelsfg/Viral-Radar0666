"""AI 解读服务：调用 LLM 生成解读（成功才落库），并负责读取与导出。"""
from __future__ import annotations

import json
import os

from bkr import database
from bkr.repositories import ai_insight_repo, task_repo
from server.services.analysis_service import task_rows, task_summary

PROMPT_RULES = (
    "数据口径：已售/加购为页面区间文本的下限，不是精确销量；价格是到手价或列表展示价；"
    "详情字段只覆盖部分商品；样本量有限，可能存在个性化排序偏差。"
)
PROMPT_FORMAT = (
    "请输出五节 Markdown：\n"
    "1. 大盘概览（样本、价格结构、销量结构，150 字内）\n"
    "2. 卖点词分布（哪些卖点词/参数值覆盖率高且在走量，对应价格带，给出 3–5 条）\n"
    "3. 供给与竞争（店铺集中度、价格带竞争密度）\n"
    "4. 风险与数据边界（样本偏差、区间销量、覆盖范围、卖点词表来源）\n"
    "5. 选品与内容建议（3 条可行动建议）\n"
    "硬性约束：只允许引用上面 JSON 中的数字与商品；不要编造未提供的销量、GMV、增长率；"
    "不确定时直说样本不足。"
)


def llm_config() -> tuple[str, str, str] | None:
    base = os.environ.get("BK_LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    key = os.environ.get("BK_LLM_API_KEY", "").strip()
    model = os.environ.get("BK_LLM_MODEL", "deepseek-chat").strip()
    if not key:
        return None
    return base, key, model


def llm_status() -> dict:
    cfg = llm_config()
    return {"configured": bool(cfg), "model": cfg[2] if cfg else None}


def _payload(task_id: str, batch: str) -> dict | None:
    s = task_summary(task_id, batch)
    if not s:
        return None
    return {
        "counts": s["counts"],
        "price": s["price"]["stats"],
        "price_bands": s["price"]["bands"],
        "sold_bands": s["sold"]["bands"],
        "shops": s["shops"],
        "profile": s.get("profile"),
        "keywords": s["keywords"],
        "params": s["params"],
        "selling_points": {
            "title": (s.get("selling_points") or {}).get("title", [])[:12],
            "params": (s.get("selling_points") or {}).get("params", [])[:10],
            "coverage": (s.get("selling_points") or {}).get("coverage"),
        },
        "top_products": [
            {k: p[k] for k in ("title", "price_final", "sold_lb", "shop_name")}
            for p in s["top_products"]
        ],
    }


def _call_llm(payload: dict, intro: str) -> dict:
    cfg = llm_config()
    if not cfg:
        raise RuntimeError(
            "未配置 LLM：请设置 BK_LLM_API_KEY（可选 BK_LLM_BASE_URL / BK_LLM_MODEL）后重启服务"
        )
    prompt = (
        f"你是电商选品分析师。{intro}\n{PROMPT_RULES}\n\n"
        f"数据：{json.dumps(payload, ensure_ascii=False)}\n\n{PROMPT_FORMAT}"
    )
    try:
        import requests

        resp = requests.post(
            f"{cfg[0]}/chat/completions",
            headers={"Authorization": f"Bearer {cfg[1]}", "Content-Type": "application/json"},
            json={
                "model": cfg[2],
                "messages": [
                    {"role": "system", "content": "你是严谨的电商数据分析师，只基于给定数据作答。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.4,
            },
            timeout=90,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"LLM 调用失败：{exc}") from exc
    return {"text": text, "model": cfg[2]}


def generate(task_id: str, batch: str) -> dict:
    """生成某平台任务的 AI 解读；成功才保存（同批次覆盖旧报告）。"""
    if not task_rows(task_id, batch):
        raise LookupError("该批次没有数据")
    payload = _payload(task_id, batch)
    if payload is None:
        raise LookupError("该批次没有可分析的数据")
    result = _call_llm(payload, "以下是从搜索结果与详情页采集到的某品类一个批次的真实数据（JSON）。")
    with database.connection() as conn:
        ai_insight_repo.save(conn, task_id, batch, result["text"], result.get("model"))
    result["saved"] = True
    return result


def get_saved(task_id: str, batch: str) -> dict:
    with database.connection() as conn:
        row = ai_insight_repo.get(conn, task_id, batch)
    if not row:
        return {"saved": False, "text": None, "model": None, "created_at": None}
    return {
        "saved": True,
        "text": row["content_md"],
        "model": row.get("model"),
        "created_at": row.get("created_at"),
    }


def export_markdown(task_id: str, batch: str) -> str | None:
    """返回导出的 Markdown 内容；没有报告时返回 None。"""
    with database.connection() as conn:
        row = ai_insight_repo.get(conn, task_id, batch)
    return row["content_md"] if row else None


def file_name(task_id: str, batch: str) -> str:
    with database.connection() as conn:
        keyword = task_repo.keyword_for(conn, task_id) or "品类"
    return f"{keyword}_AI解读_{batch}.md"
