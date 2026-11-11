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

    pdd = settings.get("pdd") or {}
    if isinstance(pdd, dict):
        try:
            cap = int(pdd.get("detail_batch") or 10)
        except (TypeError, ValueError):
            cap = 10
        os.environ.setdefault("BK_PDD_DETAIL_BATCH", str(max(1, min(cap, 10))))

    database = settings.get("database") or {}
    if isinstance(database, dict):
        for key, env in (
            ("host", "BK_DB_HOST"),
            ("port", "BK_DB_PORT"),
            ("user", "BK_DB_USER"),
            ("password", "BK_DB_PASSWORD"),
            ("name", "BK_DB_NAME"),
        ):
            if database.get(key) not in (None, ""):
                os.environ.setdefault(env, str(database[key]).strip())

    agent = settings.get("agent") or {}
    if isinstance(agent, dict):
        if agent.get("model"):
            os.environ.setdefault("BK_AGENT_MODEL", str(agent["model"]).strip())
        if agent.get("enabled") in (True, False):
            os.environ.setdefault("BK_AGENT_ENABLED", "1" if agent["enabled"] else "0")
        for key, env in (
            ("max_steps", "BK_AGENT_MAX_STEPS"),
            ("max_reloads_per_url", "BK_AGENT_MAX_RELOADS"),
            ("max_human_wait_s", "BK_AGENT_MAX_HUMAN_WAIT"),
            ("max_total_seconds", "BK_AGENT_MAX_SECONDS"),
        ):
            if agent.get(key) not in (None, ""):
                os.environ.setdefault(env, str(agent[key]).strip())

    expert = settings.get("expert") or {}
    if isinstance(expert, dict):
        for key, env in (
            ("max_rounds", "BK_EXPERT_MAX_ROUNDS"),
            ("pass_score", "BK_EXPERT_PASS_SCORE"),
            ("temperature", "BK_EXPERT_TEMPERATURE"),
            ("max_tokens", "BK_EXPERT_MAX_TOKENS"),
        ):
            if expert.get(key) not in (None, ""):
                os.environ.setdefault(env, str(expert[key]).strip())
        for role in ("writer", "reviewer"):
            section = expert.get(role) or {}
            if not isinstance(section, dict):
                continue
            prefix = f"BK_EXPERT_{role.upper()}_"
            for key, env in (
                ("base_url", prefix + "BASE_URL"),
                ("api_key", prefix + "API_KEY"),
                ("model", prefix + "MODEL"),
            ):
                if section.get(key):
                    os.environ.setdefault(env, str(section[key]).strip())


_apply_settings_to_env(_load_settings())

# 拼多多单批详情上限：网页模式官方建议 ≤10，settings.pdd.detail_batch 只能调低。
PDD_DETAIL_CAP = max(1, min(int(os.environ.get("BK_PDD_DETAIL_BATCH") or 10), 10))

DATA_DIR = Path(os.environ.get("BK_DATA_DIR", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
DB_PATH = Path(os.environ.get("BK_DB", DATA_DIR / "radar.db"))


def _database_config() -> dict | None:
    """MySQL 连接配置：配置了 BK_DB_HOST / settings.yaml database.host 即启用 MySQL。

    未配置时回退 SQLite（data/radar.db），不影响单机使用与测试。
    """
    host = os.environ.get("BK_DB_HOST", "").strip()
    if not host:
        return None
    try:
        port = int(os.environ.get("BK_DB_PORT", "3306").strip())
    except ValueError:
        port = 3306
    return {
        "host": host,
        "port": port,
        "user": os.environ.get("BK_DB_USER", "root").strip(),
        "password": os.environ.get("BK_DB_PASSWORD", ""),
        "name": os.environ.get("BK_DB_NAME", "baokuan_radar").strip(),
    }


DATABASE_CONFIG = _database_config()
TASKS_DIR = ROOT / "config" / "tasks"
_PROFILE = os.environ.get("BK_PROFILE", "").strip()
_CHANNEL = os.environ.get("BK_BROWSER_CHANNEL", "").strip()


def profile_dir_for(profile: str | None = None) -> Path:
    """按 profile 名（tmall/1688/空）与浏览器 channel 计算用户目录。"""
    name = (profile or "").strip()
    base = ".browser-profile" if not name else f".browser-profile-{name}"
    if _CHANNEL:
        base += f"-{_CHANNEL}"
    return ROOT / base


PROFILE_DIR = profile_dir_for(_PROFILE)
SECRETS_DIR = ROOT / ".secrets"


def state_path_for(profile: str | None = None) -> Path:
    name = (profile or "").strip()
    return SECRETS_DIR / (f"state-{name}.json" if name else "state.json")


STATE_PATH = state_path_for(None)


class ConfigError(ValueError):
    """配置错误，消息可直接展示给用户。"""


@dataclass
class Task:
    task_id: str
    name: str
    keywords: list[str]
    platform: str = "tmall"
    pace: str = "safe"
    tab: str = "mall"
    sort: str = "sale-desc"
    pages: int = 1
    detail_top: int = 6
    detail_order: str = "sold"  # sold=按已售下限（默认）；rank=页面顺序
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
        pace=str(raw.get("pace") or "safe").strip().lower(),
        tab=str(entry.get("tab") or raw.get("tab") or "mall"),
        sort=str(entry.get("sort") or raw.get("sort") or "sale-desc"),
        pages=int(raw.get("pages", entry.get("pages", 2))),
        detail_top=int(raw.get("detail_top", 20)),
        detail_order=str(raw.get("detail_order") or "sold"),
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
    if task.pace not in ("safe", "balanced", "fast"):
        raise ConfigError(f"{path.name} pace 只能是 safe / balanced / fast")
    if task.price_min is not None and task.price_max is not None and task.price_min > task.price_max:
        raise ConfigError(f"{path.name} price_min 不能大于 price_max")
    return task


def load_tasks() -> list[Task]:
    if not TASKS_DIR.exists():
        return []
    return [load_task(p) for p in sorted(TASKS_DIR.glob("*.y*ml"))]