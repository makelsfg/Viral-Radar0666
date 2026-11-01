"""大佬分析 Agent 的只读工具（LangChain tool 包装）。"""
from __future__ import annotations

import ast
import operator
import re
from typing import Any

from .evidence import PLATFORM_LABELS

try:  # 允许未安装 langchain 时仍能导入纯函数做单测
    from langchain_core.tools import tool as _lc_tool
except Exception:  # pragma: no cover
    _lc_tool = None


def search_evidence(pack: dict, query: str, top_k: int = 8) -> list[dict]:
    index = pack.get("index")
    return index.search(query, top_k=top_k) if index else []


def get_evidence(pack: dict, ids: list[str]) -> list[dict]:
    wanted = {str(i).strip().upper() for i in ids}
    return [e for e in pack.get("evidence") or [] if e["id"].upper() in wanted]


def _batch(pack: dict, task_id: str, batch: str) -> dict | None:
    return (pack.get("batch_data") or {}).get(f"{task_id}::{batch}")


def get_batch_overview(pack: dict, task_id: str, batch: str) -> dict:
    data = _batch(pack, task_id, batch)
    if not data:
        return {"error": "批次不存在"}
    return {"label": data["label"], "summary": data["summary"]}


def get_price_bands(pack: dict, task_id: str, batch: str) -> list[dict]:
    data = _batch(pack, task_id, batch)
    return ((data or {}).get("summary") or {}).get("price", {}).get("bands") or []


def get_sold_distribution(pack: dict, task_id: str, batch: str) -> list[dict]:
    data = _batch(pack, task_id, batch)
    return ((data or {}).get("summary") or {}).get("sold", {}).get("bands") or []


def get_top_products(pack: dict, task_id: str, batch: str, limit: int = 10) -> list[dict]:
    data = _batch(pack, task_id, batch)
    items = ((data or {}).get("summary") or {}).get("top_products") or []
    return items[: max(1, min(int(limit or 10), 30))]


def get_keyword_profile(pack: dict, task_id: str, batch: str) -> list[dict]:
    data = _batch(pack, task_id, batch)
    return ((data or {}).get("summary") or {}).get("keywords") or []


def get_param_profile(pack: dict, task_id: str, batch: str) -> list[dict]:
    data = _batch(pack, task_id, batch)
    return ((data or {}).get("summary") or {}).get("params") or []


def get_clusters(pack: dict, task_id: str, batch: str) -> list[dict]:
    data = _batch(pack, task_id, batch)
    return ((data or {}).get("summary") or {}).get("clusters") or []


_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def calculate(expression: str) -> dict:
    """只允许数字与 + - * / ( ) ** 的安全计算器。"""
    expr = (expression or "").strip()
    if len(expr) > 120:
        return {"error": "表达式过长"}
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return {"error": "表达式语法错误"}

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        raise ValueError("仅允许数字与 + - * / ( ) **")

    try:
        return {"result": ev(tree)}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


_NUM_RE = re.compile(r"(?<![A-Za-z])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(%|元|万|人|件|笔|个)?")
_EVIDENCE_RE = re.compile(r"\[E\d+\]")


def check_number_grounding(draft_md: str) -> list[dict]:
    """报告中的数字若附近没有 [E#]，列为疑似未标注证据。"""
    out = []
    for line_no, line in enumerate((draft_md or "").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        for m in _NUM_RE.finditer(line):
            if m.group(2) is None and len(m.group(1)) <= 2:
                continue  # 忽略 1、2 这类序号/小数字
            start = max(0, m.start() - 80)
            end = min(len(line), m.end() + 80)
            if _EVIDENCE_RE.search(line[start:end]):
                continue
            out.append({
                "line": line_no,
                "number": m.group(0),
                "text": line.strip()[:160],
            })
            break
    return out[:30]


def check_platform_mix(draft_md: str) -> list[dict]:
    """检查是否出现跨平台销量直接比较。"""
    labels = list(PLATFORM_LABELS.values())
    texts = ["已售", "成交", "已拼", "销量"]
    out = []
    for line_no, line in enumerate((draft_md or "").splitlines(), 1):
        hits = sum(1 for lb in labels if lb in line)
        if hits >= 2 and any(t in line for t in texts):
            out.append({"line": line_no, "text": line.strip()[:200]})
    return out


def make_tools(pack: dict) -> list[Any]:
    """构造 LangChain 工具列表；未安装 langchain 时抛出明确错误。"""
    if _lc_tool is None:
        raise RuntimeError("未安装 langchain，请先执行 pip install -r requirements.txt")

    @_lc_tool("search_evidence")
    def _search(query: str, top_k: int = 8) -> str:
        """按关键词检索证据包，返回带 E# 编号的证据片段。"""
        return str(search_evidence(pack, query, top_k))

    @_lc_tool("get_evidence")
    def _get(ids: list[str]) -> str:
        """按证据编号（如 ['E1','E2']）返回证据详情。"""
        return str(get_evidence(pack, ids))

    @_lc_tool("get_batch_overview")
    def _overview(task_id: str, batch: str) -> str:
        """返回批次概览指标（商品数、价格中位数、已售中位数、店铺集中度）。"""
        return str(get_batch_overview(pack, task_id, batch))

    @_lc_tool("get_price_bands")
    def _price(task_id: str, batch: str) -> str:
        """返回价格带分布（商品数与销量合计）。"""
        return str(get_price_bands(pack, task_id, batch))

    @_lc_tool("get_sold_distribution")
    def _sold(task_id: str, batch: str) -> str:
        """返回销量区间分布。"""
        return str(get_sold_distribution(pack, task_id, batch))

    @_lc_tool("get_top_products")
    def _top(task_id: str, batch: str, limit: int = 10) -> str:
        """返回批次内按已售下限排序的头部商品。"""
        return str(get_top_products(pack, task_id, batch, limit))

    @_lc_tool("get_keyword_profile")
    def _kw(task_id: str, batch: str) -> str:
        """返回标题关键词命中统计。"""
        return str(get_keyword_profile(pack, task_id, batch))

    @_lc_tool("get_param_profile")
    def _param(task_id: str, batch: str) -> str:
        """返回参数画像（覆盖率较高的参数键与取值）。"""
        return str(get_param_profile(pack, task_id, batch))

    @_lc_tool("get_clusters")
    def _clusters(task_id: str, batch: str) -> str:
        """返回卖点组合聚类（数量、均价、均销量、示例标题）。"""
        return str(get_clusters(pack, task_id, batch))

    @_lc_tool("calculate")
    def _calc(expression: str) -> str:
        """安全计算器：只允许数字与 + - * / ( ) **。"""
        return str(calculate(expression))

    @_lc_tool("check_number_grounding")
    def _check_numbers(draft_md: str) -> str:
        """检查报告数字是否缺少 [E#] 证据标注（审查 Agent 用）。"""
        return str(check_number_grounding(draft_md))

    @_lc_tool("check_platform_mix")
    def _check_mix(draft_md: str) -> str:
        """检查是否出现跨平台销量直接比较（审查 Agent 用）。"""
        return str(check_platform_mix(draft_md))

    return [
        _search, _get, _overview, _price, _sold, _top, _kw, _param,
        _clusters, _calc, _check_numbers, _check_mix,
    ]