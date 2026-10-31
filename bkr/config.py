"""配置加载与校验：品类任务 YAML。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "config" / "settings.yaml"


def _load_settings() -> dict:
    try:
        if not SETTINGS_PATH.exists():
            return {}
        data = yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _apply_settings_to_env(settings: dict) -> None:
    """配置文件 → 环境变量；环境变量未设置时才生效（环境变量优先级更高）。"""
    if not settings:
        return
    llm = settings.get("llm") or {}
    if isinstance(llm, dict):
        if llm.get("base_url"):
            os.environ.setdefault("BK_LLM_BASE_URL", str(llm["base_url"]).strip())
        if llm.get("api_key"):
            os.environ.setdefault("BK_LLM_API_KEY", str(llm["api_key"]).strip())
        if llm.get("model"):
            os.environ.setdefault("BK_LLM_MODEL", str(llm["model"]).strip())
    server = settings.get("server") or {}
    if isinstance(server, dict) and server.get("port"):
        os.environ.setdefault("BK_PORT", str(server["port"]).strip())
    profile = str(settings.get("profile") or "").strip()
    if profile:
        os.environ.setdefault("BK_PROFILE", profile)

    browser = settings.get("browser") or {}
    if isinstance(browser, dict):
        if browser.get("engine"):
            os.environ.setdefault("BK_ENGINE", str(browser["engine"]).strip())
        if browser.get("channel"):
            os.environ.setdefault("BK_BROWSER_CHANNEL", str(browser["channel"]).strip())
        if browser.get("cdp_url"):
            os.environ.setdefault("BK_CDP_URL", str(browser["cdp_url"]).strip())

    detail = settings.get("detail") or {}
    if isinstance(detail, dict) and detail.get("provider"):
        os.environ.setdefault("BK_DETAIL_PROVIDER", str(detail["provider"]).strip())

    apify = settings.get("apify") or {}
    if isinstance(apify, dict):
        if apify.get("token"):
            os.environ.setdefault("BK_APIFY_TOKEN", str(apify["token"]).strip())
        if apify.get("actor"):
            os.environ.setdefault("BK_APIFY_ACTOR", str(apify["actor"]).strip())


_apply_settings_to_env(_load_settings())

DATA_DIR = Path(os.environ.get("BK_DATA_DIR", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
DB_PATH = Path(os.environ.get("BK_DB", DATA_DIR / "radar.db"))
TASKS_DIR = ROOT / "config" / "tasks"
_PROFILE = os.environ.get("BK_PROFILE", "").strip()
_CHANNEL = os.environ.get("BK_BROWSER_CHANNEL", "").strip()
_profile_name = ".browser-profile" if not _PROFILE else f".browser-profile-{_PROFILE}"
if _CHANNEL:
    # 不同浏览器内核使用独立 profile，避免 Chromium/Edge 混用同一个用户目录
    _profile_name += f"-{_CHANNEL}"
PROFILE_DIR = ROOT / _profile_name
SECRETS_DIR = ROOT / ".secrets"
STATE_PATH = SECRETS_DIR / "state.json"


class ConfigError(ValueError):
    """配置错误，消息可直接展示给用户。"""


@dataclass
class Task:
    task_id: str
    name: str
    keywords: list[str]
    platform: str = "tmall"
    tab: str = "mall"
    sort: str = "sale-desc"
    pages: int = 1
    detail_top: int = 6
    detail_order: str = "rank"  # rank=按页面顺序（拟人，默认）；sold=按已售排序
    price_min: float | None = None
    price_max: float | None = None
    exclude_words: list[str] = field(default_factory=list)
    save_html: bool = True


def _opt_float(v) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError) as e:
        raise ConfigError(f"数值字段非法: {v!r}") from e


def load_task(path: str | Path) -> Task:
    """读取并校验单个任务 YAML，错误时抛 ConfigError（带字段定位）。"""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path.name} YAML 解析失败: {e}") from e

    if not raw.get("task_id"):
        raise ConfigError(f"{path.name} 缺少必填字段: task_id")

    entry = raw.get("entry") or {}
    keywords = entry.get("keywords") or raw.get("keywords")
    if not keywords or not isinstance(keywords, list):
        raise ConfigError(f"{path.name} entry.keywords 必须是非空数组")

    task = Task(
        task_id=str(raw["task_id"]),
        name=str(raw.get("name") or raw["task_id"]),
        keywords=[str(k).strip() for k in keywords if str(k).strip()],
        platform=str(raw.get("platform") or "tmall").strip().lower(),
        tab=str(entry.get("tab") or raw.get("tab") or "mall"),
        sort=str(entry.get("sort") or raw.get("sort") or "sale-desc"),
        pages=int(raw.get("pages", entry.get("pages", 2))),
        detail_top=int(raw.get("detail_top", 20)),
        detail_order=str(raw.get("detail_order") or "rank"),
        price_min=_opt_float(raw.get("price_min")),
        price_max=_opt_float(raw.get("price_max")),
        exclude_words=[str(w) for w in (raw.get("exclude_words") or [])],
        save_html=bool(raw.get("save_html", True)),
    )
    if not task.keywords:
        raise ConfigError(f"{path.name} entry.keywords 不能为空字符串")
    if task.pages < 1:
        raise ConfigError(f"{path.name} pages 必须 >= 1")
    if task.detail_top < 0:
        raise ConfigError(f"{path.name} detail_top 必须 >= 0")
    if task.detail_order not in ("rank", "sold"):
        raise ConfigError(f"{path.name} detail_order 只能是 rank 或 sold")
    if task.platform not in ("tmall", "1688", "pdd"):
        raise ConfigError(f"{path.name} platform 只能是 tmall / 1688 / pdd")
    if task.price_min is not None and task.price_max is not None and task.price_min > task.price_max:
        raise ConfigError(f"{path.name} price_min 不能大于 price_max")
    return task


def load_tasks() -> list[Task]:
    if not TASKS_DIR.exists():
        return []
    return [load_task(p) for p in sorted(TASKS_DIR.glob("*.y*ml"))]