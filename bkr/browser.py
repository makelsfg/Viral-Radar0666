"""Playwright 浏览器启动、登录态、风控检测与引擎选择。

引擎优先级（BK_ENGINE 可强制）：
  auto（默认）→ patchright → rebrowser-playwright → 原版 playwright
  patchright / rebrowser 是带反检测补丁的 Playwright 分支（修复 CDP Runtime.enable、
  UtilityScript 调用栈等泄漏），未安装时自动回落到原版，不影响现有使用。

CDP 模式（BK_CDP_URL，例如 http://127.0.0.1:9222）：接管你手动启动的真实 Chrome，
不使用自动化启动参数；此模式下不会关闭用户浏览器。
"""
from __future__ import annotations

import importlib.util
import json
import os
import random
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from .config import PROFILE_DIR, STATE_PATH, profile_dir_for, state_path_for

CDP_URL = (os.environ.get("BK_CDP_URL") or "").strip()
CHANNEL = (os.environ.get("BK_BROWSER_CHANNEL") or "").strip()


def browser_channel() -> str:
    return CHANNEL or "chromium"


class BlockedError(RuntimeError):
    """触发验证码/风控，需要人工处理后续采。"""


class LoginError(BlockedError):
    """登录态失效，需要重新登录。"""


class EmptyResultError(RuntimeError):
    """页面解析到 0 个商品：登录失效 / 风控 / 页面结构改版。"""


LOGIN_COOKIE_NAMES = {"cookie2", "unb"}


def has_login_cookies(names) -> bool:
    """淘宝登录态判定：必须同时具备 cookie2 与 unb（只有 tracknick 是半登录）。"""
    return LOGIN_COOKIE_NAMES.issubset(set(names))


def engine_name() -> str:
    """解析实际使用的引擎（patchright / rebrowser / playwright）。"""
    pref = (os.environ.get("BK_ENGINE") or "auto").strip().lower()
    has_patchright = importlib.util.find_spec("patchright") is not None
    has_rebrowser = importlib.util.find_spec("rebrowser_playwright") is not None
    if pref == "playwright":
        return "playwright"
    if pref == "patchright":
        return "patchright" if has_patchright else "playwright"
    if pref == "rebrowser":
        return "rebrowser" if has_rebrowser else "playwright"
    if has_patchright:
        return "patchright"
    if has_rebrowser:
        return "rebrowser"
    return "playwright"


def playwright_engine():
    name = engine_name()
    try:
        if name == "patchright":
            from patchright.sync_api import sync_playwright

            return sync_playwright
        if name == "rebrowser":
            from rebrowser_playwright.sync_api import sync_playwright

            return sync_playwright
    except ImportError as exc:  # 包存在但导入失败时回落
        print(f"[engine] {name} 导入失败，回退 playwright：{exc}")
    from playwright.sync_api import sync_playwright

    return sync_playwright


@contextmanager
def browser_runtime():
    """统一入口：with browser_runtime() as p: ..."""
    fn = playwright_engine()
    with fn() as p:
        yield p


def _kill_profile_browsers(profile: Path) -> None:
    """只清理使用该 profile 目录的 Edge/Chrome 后台进程（不动日常浏览器）。"""
    if os.name != "nt":
        return
    try:
        script = (
            "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' or Name='chrome.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like '*{profile}*' }} | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            timeout=15,
        )
    except Exception:
        pass


def launch_context(p, headless: bool = False, kill_stale: bool = False, profile_dir=None):
    """返回 BrowserContext。

    profile_dir 可指定独立用户目录（并行时每平台一个）。
    启动顺序：Edge/Chrome(channel) -> 内置 Chromium(同一 profile) -> 内置 Chromium(独立回退 profile)。
    每次启动后用 new_page() 做健康检查，失败自动尝试下一种。
    """
    base_profile = Path(profile_dir) if profile_dir is not None else PROFILE_DIR
    if CDP_URL:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            try:
                ctx._bkr_owned = False  # type: ignore[attr-defined]
            except Exception:
                pass
            print(f"[browser] 已接管 CDP：{CDP_URL}")
            return ctx
        except Exception as exc:
            print(f"[browser] 连接 CDP 失败（{exc}），回退启动本地浏览器")

    attempts = []
    if CHANNEL:
        attempts.append((f"{CHANNEL}", CHANNEL, base_profile))
        attempts.append(("内置 Chromium（同一 profile）", None, base_profile))
        attempts.append(
            ("内置 Chromium（回退 profile）", None, base_profile.with_name(base_profile.name + "-fallback"))
        )
    else:
        attempts.append(("内置 Chromium", None, base_profile))

    last_exc: Exception | None = None
    for label, channel, profile_dir in attempts:
        ctx = None
        try:
            profile_dir.mkdir(parents=True, exist_ok=True)
            if kill_stale and channel:
                _kill_profile_browsers(profile_dir)
            launch_kwargs = dict(
                user_data_dir=str(profile_dir),
                headless=headless,
                viewport={"width": 1500, "height": 950},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                args=["--disable-blink-features=AutomationControlled"],
            )
            if channel:
                ctx = p.chromium.launch_persistent_context(channel=channel, **launch_kwargs)
            else:
                ctx = p.chromium.launch_persistent_context(**launch_kwargs)
            try:
                ctx._bkr_owned = True  # type: ignore[attr-defined]
            except Exception:
                pass
            # 注入按平台保存的登录态（解决 session cookie 在新浏览器里不恢复的问题）
            state_file = (os.environ.get("BK_STATE_FILE") or "").strip()
            if state_file:
                try:
                    state = json.loads(Path(state_file).read_text(encoding="utf-8"))
                    cookies = state.get("cookies") or []
                    try:
                        existing = {c.get("name") for c in ctx.cookies()}
                    except Exception:
                        existing = set()
                    missing = [c for c in cookies if c.get("name") not in existing]
                    if missing:
                        ctx.add_cookies(missing)
                        print(
                            f"[browser] 已注入登录态 {Path(state_file).name}"
                            f"（补充 {len(missing)} 个 cookie）"
                        )
                except Exception as exc:
                    print(f"[browser] 登录态注入失败：{exc}")
            # 每平台只保留一个工作标签页：列表/详情都在它上面导航；关闭弹窗不影响浏览器
            try:
                work = ctx.pages[0] if ctx.pages else ctx.new_page()
                ctx._bkr_work_page = work  # type: ignore[attr-defined]
                print("[browser] 工作标签页已就绪（每平台 1 个）")
            except Exception:
                pass
            try:
                ctx.on(
                    "close",
                    lambda _c: print(
                        f"[browser] context closed（由程序关闭={getattr(ctx, '_bkr_app_closing', False)}）"
                    ),
                )
            except Exception:
                pass
            try:
                ctx.add_init_script(
                    """
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    window.chrome = window.chrome || { runtime: {} };
                    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh'] });
                    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                    """
                )
            except Exception:
                pass
            # 健康检查：能拿到页面才算启动成功
            probe = ctx.new_page()
            probe.close()
            print(f"[browser] 使用 {label}（{profile_dir.name}）")
            return ctx
        except Exception as exc:
            last_exc = exc
            print(f"[browser] {label} 启动失败：{exc}")
            try:
                if ctx is not None:
                    ctx.close()
            except Exception:
                pass

    raise RuntimeError(
        f"浏览器启动失败：{last_exc}。请关闭所有 Edge/Chrome 窗口后重试，"
        "或在 config/settings.yaml 将 browser.channel 改为 \"\" 使用内置 Chromium"
    )


def get_work_page(ctx):
    """每个平台只保留一个工作标签页：列表/详情都在它上面导航。"""
    if CDP_URL:
        return ctx.new_page()
    page = getattr(ctx, "_bkr_work_page", None)
    if page is not None and not page.is_closed():
        return page
    page = ctx.new_page()
    try:
        ctx._bkr_work_page = page  # type: ignore[attr-defined]
    except Exception:
        pass
    return page


def close_context(ctx) -> None:
    """只关闭自己启动的浏览器；CDP 模式下保持用户浏览器不动。"""
    if CDP_URL:
        return
    if not getattr(ctx, "_bkr_owned", True):
        return
    try:
        ctx._bkr_app_closing = True  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        ctx.close()
    except Exception:
        pass


_BLOCK_MARKERS = (
    "滑动验证",
    "完成验证",
    "安全验证",
    "访问受限",
    "请输入验证码",
    "访问异常提示",
    "系统检测到当前访问存在异常",
    "访问存在异常",
)
_LOGIN_MARKERS = ("亲，请登录", "扫码登录", "密码登录")


def check_blocked(page) -> None:
    """检测登录失效 / 滑块验证 / 访问异常，命中即抛异常（由上层暂停批次）。"""
    url = (page.url or "").lower()
    if "login.taobao.com" in url or "login.tmall.com" in url or "login.1688.com" in url:
        raise LoginError("登录态已失效：请重新运行 bkr login（1688 用 --site 1688），然后执行 --resume 续采")
    if "punish" in url or "captcha" in url:
        raise BlockedError("触发了风控页面，请人工处理后续采")

    try:
        title = page.title() or ""
    except Exception:
        title = ""
    if "登录" in title and ("淘宝" in title or "天猫" in title):
        raise LoginError("打开了登录页：请先运行 bkr login 扫码登录")

    for sel in ("#nc_1_n1z", ".nc-container", ".nc_scale"):
        try:
            if page.locator(sel).count() > 0:
                raise BlockedError("出现滑块验证，请人工完成验证后执行 --resume 续采")
        except BlockedError:
            raise
        except Exception:
            pass

    try:
        body = page.inner_text("body", timeout=2000)
    except Exception:
        body = ""
    if any(m in body for m in _BLOCK_MARKERS):
        raise BlockedError("页面出现安全验证，请人工完成验证后执行 --resume 续采")
    if any(m in body for m in _LOGIN_MARKERS):
        raise LoginError("未登录或登录态失效：请先运行 bkr login，再执行 collect")


def polite_sleep(lo: float = 3.0, hi: float = 5.0) -> None:
    time.sleep(random.uniform(lo, hi))


def human_move(page, x: float, y: float) -> None:
    """分段移动鼠标，带随机抖动与停顿，比一步到位更接近人手轨迹。"""
    try:
        page.mouse.move(x + random.randint(-25, 25), y + random.randint(-18, 18))
        for _ in range(random.randint(3, 6)):
            x += random.randint(-45, 45)
            y += random.randint(-25, 25)
            page.mouse.move(x, y, steps=random.randint(2, 6))
            page.wait_for_timeout(random.randint(60, 180))
    except Exception:
        pass


def wait_for_login(ctx, site: str = "taobao", timeout: int = 300, poll: float = 2.0):
    """打开站点登录页并等待完整登录态。

    返回 (ok, reason)：ok=True 表示连续两次检测到完整登录；
    reason: ok / timeout / closed。
    """
    site = (site or "taobao").strip().lower()
    target = "https://www.1688.com" if site == "1688" else "https://www.taobao.com"
    page = get_work_page(ctx)
    page.goto(target, wait_until="domcontentloaded")

    hits = 0
    deadline = time.time() + timeout
    while time.time() < deadline:
        if page.is_closed():
            return False, "closed"
        try:
            names = {c.get("name") for c in ctx.cookies()}
        except Exception:
            return False, "closed"
        url = (page.url or "").lower()
        on_login = any(
            k in url for k in ("login.taobao.com", "login.tmall.com", "login.1688.com")
        )
        if site == "1688":
            cookie_ok = bool({"__cn_logon__", "_cn_"} & names)
        else:
            cookie_ok = has_login_cookies(names)
        if cookie_ok and not on_login:
            hits += 1
            if hits >= 2:
                time.sleep(2)  # 等 cookie 落盘
                return True, "ok"
        else:
            hits = 0
        time.sleep(poll)
    return False, "timeout"


def login(site: str = "taobao", profile: str | None = None) -> None:
    """打开浏览器人工扫码登录（site=taobao/1688；profile 可指定独立用户目录）。"""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    site = (site or "taobao").strip().lower()
    pdir = profile_dir_for(profile)
    if CDP_URL:
        print(f"CDP 模式：接管 {CDP_URL} 上的 Chrome（请确保已在该浏览器登录）")
    else:
        print(f"当前浏览器 profile：{pdir.name}")
    print(f"当前引擎：{engine_name()}    浏览器：{browser_channel()}")

    target = "https://www.1688.com" if site == "1688" else "https://www.taobao.com"
    with browser_runtime() as p:
        ctx = launch_context(p, profile_dir=pdir)
        page = ctx.new_page()
        try:
            page.goto(target, wait_until="domcontentloaded")
            print(f"请在弹出的浏览器窗口里完成 {site} 登录（扫码即可）。")
            print("[注意] 登录完成后【不要关闭浏览器窗口】，直接回到终端按回车。")
            input()

            logged_in = False
            try:
                cookie_names = {c.get("name") for c in ctx.cookies()}
                if site == "1688":
                    logged_in = bool({"__cn_logon__", "cookie2", "_cn_"} & cookie_names)
                else:
                    logged_in = has_login_cookies(cookie_names)
                print(f"检测到浏览器 cookie {len(cookie_names)} 个")
                if site != "1688" and not logged_in and "tracknick" in cookie_names:
                    print("检测到 tracknick 但缺少 cookie2/unb（半登录），请完成扫码后的全部确认步骤")
            except Exception:
                print("浏览器窗口在回车前已关闭；登录态可能已写入本地 profile")

            if logged_in:
                time.sleep(2)  # 等 cookie 落盘稳定后再关闭，避免半登录
            try:
                ctx.storage_state(path=str(state_path_for(profile)))
                print(f"登录态快照已保存：{state_path_for(profile)}")
            except Exception:
                print("未能导出登录快照（浏览器已关闭）；登录态仍以本地 profile 为准")
        finally:
            try:
                page.close()
            except Exception:
                pass
            close_context(ctx)

    if logged_in:
        print(f"{site} 登录完成。")
    else:
        print(f"未确认到 {site} 登录 cookie。若采集提示未登录，请重新执行 bkr login --site {site}（且不要提前关闭窗口）。")