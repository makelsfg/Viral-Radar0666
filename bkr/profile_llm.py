"""品类卖点词表：从真实标题 + 参数值用 LLM 冷启动生成，落库缓存。

设计约束：
- 采集是开放域（任意关键词），分析不能依赖开发者预先写好的词表；
- 生成慢（10~30s），因此只在**后台线程**跑，分析页先返回其余统计；
- 生成的词必须能在本批标题/参数里找到依据，否则丢弃（防模型产出泛词）；
- 参数值（如「材质=白鸭绒」）天然是卖点词，直接并入，不依赖 LLM。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import Counter

PROMPT_SYSTEM = """你是电商品类词表构建器。根据给定的品类词与真实商品标题，输出该品类的卖点特征词与材质词。

硬约束：
1. 禁止输出品类词本身及其近义变体；
2. 禁止输出品牌名、店铺名、型号、货号、纯促销词（包邮/正品/秒杀/新品/现货/限时等）；
3. 每个词必须能在给定标题或参数中至少找到 1 处依据，长度 2~8 字；
4. 优先输出能区分商品差异的词：材质、工艺、结构、功能、场景、人群；
5. features 输出 20~30 个，materials 输出 5~8 个，按区分度从高到低排列。

只输出 JSON：{"features": ["..."], "materials": ["..."]}，不要代码围栏，不要解释。"""

ALLOWED_PUNCT = re.compile(r"[^\u4e00-\u9fa5A-Za-z0-9%]+")
_PROMO = re.compile(
    r"【[^】]*】|包邮|秒杀|正品|旗舰店|官方|新品|现货|限时|买一送一|"
    r"第[一二三四五六七八九十\d]+件|下单立减|券后|补贴|拍下|发货|赠品"
)
_SPEC_ONLY = re.compile(r"^\d+(?:\.\d+)?\s*(?:ml|l|g|kg|cm|mm|w|v|mah)$", re.I)
_SPLIT_VALUES = re.compile(r"[,，、/;；|]+")

_state_lock = threading.Lock()
_inflight: dict[str, str] = {}   # keyword -> generating | done | failed
_last_error: dict[str, str] = {}
_failed_at: dict[str, float] = {}


# ---------------------------------------------------------------------------
# 输入准备
# ---------------------------------------------------------------------------

def normalize_keyword(name: str) -> str:
    """把任务名「羽绒服 · 天猫」归一化成品类词「羽绒服」。"""
    text = (name or "").strip()
    text = re.sub(r"\s*[·・|]\s*(天猫|1688|拼多多|淘宝)\s*$", "", text)
    return text.strip().lower()


def clean_title(title: str) -> str:
    text = _PROMO.sub("", title or "")
    text = ALLOWED_PUNCT.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def sample_titles(rows: list[dict], cap: int = 60) -> list[str]:
    """去促销词/去重/剔除过短标题，超过 cap 时按「头部 2/3 + 长尾 1/3」分层抽样。"""
    seen: set[str] = set()
    ordered: list[str] = []
    ranked = sorted(
        rows,
        key=lambda r: (r.get("sold_lb") is None, -(r.get("sold_lb") or 0)),
    )
    for r in ranked:
        text = clean_title(r.get("title") or "")
        if len(text) < 6 or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    if len(ordered) <= cap:
        return ordered
    head_n = int(cap * 0.67)
    head = ordered[:head_n]
    rest = ordered[head_n:]
    pick = rest[:: max(1, len(rest) // max(1, cap - head_n))][: cap - head_n]
    return head + pick


def param_pairs(rows: list[dict]) -> list[tuple[str, str]]:
    """把参数拍平成 (key, value) 对；value 可能被逗号拼成一串，需要拆开。"""
    out: list[tuple[str, str]] = []
    for r in rows:
        params = r.get("params") or {}
        if not isinstance(params, dict):
            continue
        for k, v in params.items():
            key = str(k or "").strip()
            if not key or v in (None, ""):
                continue
            for part in _SPLIT_VALUES.split(str(v)):
                value = part.strip()
                if 1 < len(value) <= 20:
                    out.append((key, value))
    return out


def param_values(rows: list[dict], min_items: int = 2) -> list[str]:
    """参数值里出现 ≥min_items 次的词（排除 500ML / 316L 这类纯规格）。"""
    counter: Counter = Counter()
    holders: dict[str, set[str]] = {}
    for idx, (_, value) in enumerate(param_pairs(rows)):
        if _SPEC_ONLY.match(value) or len(value) < 2 or len(value) > 8:
            continue
        counter[value] += 1
        holders.setdefault(value, set()).add(str(idx))
    return [w for w, c in counter.most_common() if c >= min_items]


def param_keys(rows: list[dict]) -> list[str]:
    keys: Counter = Counter()
    for r in rows:
        params = r.get("params") or {}
        if isinstance(params, dict):
            for k, v in params.items():
                if k and v not in (None, ""):
                    keys[str(k).strip()] += 1
    return [k for k, _ in keys.most_common()]


# ---------------------------------------------------------------------------
# LLM 调用
# ---------------------------------------------------------------------------

def llm_config() -> tuple[str, str, str] | None:
    base = (os.environ.get("BK_LLM_BASE_URL") or "https://api.deepseek.com").strip().rstrip("/")
    key = (os.environ.get("BK_LLM_API_KEY") or "").strip()
    model = (os.environ.get("BK_LLM_MODEL") or "deepseek-chat").strip()
    if not key:
        return None
    return base, key, model


def _user_prompt(keyword: str, titles: list[str], keys: list[str], values: list[str]) -> str:
    head = f"""品类词：{keyword}
标题条数：{len(titles)}（少于 12 条属于小样本，只输出高置信词，宁少勿滥）
参数字段：{'、'.join(keys) or '无'}
参数高频取值：{'、'.join(values[:40]) or '无'}

真实标题：
"""
    body = "\n".join(f"{i}. {t}" for i, t in enumerate(titles, 1))
    return head + body + "\n\n只输出 JSON。"


def _extract_json(text: str) -> dict | None:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def call_llm(keyword: str, titles: list[str], keys: list[str], values: list[str],
             timeout: float = 30.0) -> tuple[dict, str]:
    cfg = llm_config()
    if not cfg:
        raise RuntimeError("未配置 LLM：请设置 BK_LLM_API_KEY 后重启服务")
    import requests

    payload = {
        "model": cfg[2],
        "temperature": 0.2,
        "max_tokens": 900,
        "messages": [
            {"role": "system", "content": PROMPT_SYSTEM},
            {"role": "user", "content": _user_prompt(keyword, titles, keys, values)},
        ],
    }
    last_exc: Exception | None = None
    for _ in range(2):
        try:
            resp = requests.post(
                f"{cfg[0]}/chat/completions",
                headers={"Authorization": f"Bearer {cfg[1]}", "Content-Type": "application/json"},
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
            data = _extract_json(resp.json()["choices"][0]["message"]["content"])
            if data is not None:
                return data, cfg[2]
            last_exc = ValueError("模型输出不是合法 JSON")
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    raise RuntimeError(f"LLM 生成词表失败：{type(last_exc).__name__}: {last_exc}")


# ---------------------------------------------------------------------------
# 校验与落库
# ---------------------------------------------------------------------------

def _ok_word(word: str, keyword: str) -> bool:
    w = str(word or "").strip()
    if not (2 <= len(w) <= 12):
        return False
    if w.lower() in keyword.lower() or keyword.lower() in w.lower():
        return False
    if re.fullmatch(r"[\d.%]+", w):
        return False
    return True


def validate_profile(raw: dict, keyword: str, titles: list[str],
                     keys: list[str], values: list[str],
                     min_items: int = 2, max_features: int = 30,
                     max_materials: int = 8) -> tuple[dict, float]:
    """过滤：长度不符、品类词、纯数字、在本批标题/参数中无依据的词。"""
    joined = "\n".join(titles)
    key_set = {k.lower() for k in keys}
    value_set = {v.lower() for v in values}
    # 小样本（详情只采了几条）时 min_items=2 会把真正的卖点词全丢掉，退化为 1
    threshold = min_items if len(titles) >= 10 else 1

    def keep(word: str) -> bool:
        if not _ok_word(word, keyword):
            return False
        w = word.strip()
        items = joined.count(w)
        if items >= threshold:
            return True
        return w.lower() in value_set or w.lower() in key_set

    def dedupe(items) -> list[str]:
        seen, out = set(), []
        for w in items or []:
            if not isinstance(w, str):
                continue
            word = w.strip()
            if not word or word in seen or not keep(word):
                continue
            seen.add(word)
            out.append(word)
        return out

    features = dedupe(raw.get("features"))[:max_features]
    materials = dedupe(raw.get("materials"))[:max_materials]
    hit = sum(1 for w in features if joined.count(w) >= 1)
    hit_rate = round(hit / len(features), 3) if features else 0.0
    return {"features": features, "materials": materials}, hit_rate


def generate_profile(keyword: str, rows: list[dict], timeout: float = 30.0) -> dict:
    """同步生成并落库；失败抛异常（调用方决定是否降级）。"""
    from . import database
    from .repositories import profile_repo

    keyword = normalize_keyword(keyword)
    if not keyword:
        raise ValueError("缺少品类词")
    titles = sample_titles(rows)
    if not titles:
        raise ValueError("该批次没有可用标题")
    keys = param_keys(rows)
    values = param_values(rows)
    raw, model = call_llm(keyword, titles, keys, values, timeout=timeout)
    profile, hit_rate = validate_profile(raw, keyword, titles, keys, values)
    if not profile["features"]:
        raise RuntimeError("模型没有产出可用卖点词（全部未命中标题或参数）")
    profile["param_keys"] = keys
    profile["generated_from"] = len(titles)
    with database.connection() as conn:
        profile_repo.save(conn, keyword, profile, source="llm", model=model, hit_rate=hit_rate)
    return {"keyword": keyword, "profile": profile, "model": model, "hit_rate": hit_rate}


def _publish(payload: dict) -> None:
    """尽力而为地推 SSE（bkr 不 import server，避免循环依赖）。"""
    try:
        from server.services.collect_service import bus

        bus.publish(payload)
    except Exception:  # noqa: BLE001
        pass


def ensure_async(keyword: str, rows: list[dict]) -> str:
    """没有词表时在后台生成一次；同关键词并发去重。返回 generating|skipped|unavailable。"""
    keyword = normalize_keyword(keyword)
    if not keyword or not rows or llm_config() is None:
        return "unavailable"
    with _state_lock:
        if _inflight.get(keyword) == "generating":
            return "generating"
        _inflight[keyword] = "generating"
        _last_error.pop(keyword, None)

    def worker() -> None:
        started = time.time()
        try:
            result = generate_profile(keyword, rows)
            _publish({
                "type": "profile",
                "keyword": keyword,
                "status": "ready",
                "hit_rate": result["hit_rate"],
                "words": len(result["profile"]["features"]),
                "elapsed": round(time.time() - started, 1),
            })
            with _state_lock:
                _inflight[keyword] = "done"
        except Exception as exc:  # noqa: BLE001
            print(f"[profile] {keyword} 词表生成失败：{type(exc).__name__}: {exc}")
            with _state_lock:
                _inflight[keyword] = "failed"
                _last_error[keyword] = str(exc)
                _failed_at[keyword] = time.time()
            _publish({
                "type": "profile",
                "keyword": keyword,
                "status": "failed",
                "message": str(exc)[:200],
            })

    threading.Thread(target=worker, daemon=True, name=f"profile-{keyword}").start()
    return "generating"


def state(keyword: str) -> str:
    keyword = normalize_keyword(keyword)
    with _state_lock:
        return _inflight.get(keyword, "idle")


def last_error(keyword: str) -> str | None:
    with _state_lock:
        return _last_error.get(normalize_keyword(keyword))


def retry_ready(keyword: str, cooldown: float = 60.0) -> bool:
    """失败后是否允许重试（冷却期内不重试，避免页面刷新就把 LLM 打爆）。"""
    keyword = normalize_keyword(keyword)
    with _state_lock:
        ts = _failed_at.get(keyword)
        return ts is None or (time.time() - ts) >= cooldown


def reset_state(keyword: str | None = None) -> None:
    """测试/重试用：清掉进程内状态。"""
    with _state_lock:
        if keyword is None:
            _inflight.clear()
            _last_error.clear()
            _failed_at.clear()
        else:
            key = normalize_keyword(keyword)
            _inflight.pop(key, None)
            _last_error.pop(key, None)
            _failed_at.pop(key, None)
