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
import os
import random
import time
from contextlib import contextmanager

from .config import PROFILE_DIR, STATE_PATH

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


def launch_context(p, headless: bool = False):
    """返回 BrowserContext。CDP 优先；连不上则回退到本地浏览器（Edge/Chrome/内置）。"""
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

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    launch_kwargs = dict(
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        viewport={"width": 1500, "height": 950},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        args=["--disable-blink-features=AutomationControlled"],
    )
    ctx = None
    if CHANNEL:
        try:
            ctx = p.chromium.launch_persistent_context(channel=CHANNEL, **launch_kwargs)
            print(f"[browser] 使用 {CHANNEL}（{(PROFILE_DIR).name}）")
        except Exception as exc:  # 指定浏览器不可用时回落内置 Chromium
            print(f"[browser] {CHANNEL} 启动失败（{exc}），回退内置 Chromium")
    if ctx is None:
        ctx = p.chromium.launch_persistent_context(**launch_kwargs)
    try:
        ctx._bkr_owned = True  # type: ignore[attr-defined]
    except Exception:
        pass
    # 轻量反自动化特征（patchright/rebrowser 会在引擎层做更彻底的修复）
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
    return ctx


def close_context(ctx) -> None:
    """只关闭自己启动的浏览器；CDP 模式下保持用户浏览器不动。"""
    if CDP_URL:
        return
    if not getattr(ctx, "_bkr_owned", True):
        return
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
    if "login.taobao.com" in url or "login.tmall.com" in url:
        raise LoginError("登录态已失效：请重新运行 bkr login，然后执行 --resume 续采")
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


def login() -> None:
    """打开浏览器人工扫码登录。登录态保存在持久化 profile 中，快照仅作备份。"""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CDP_URL:
        print(f"CDP 模式：接管 {CDP_URL} 上的 Chrome（请确保已在该浏览器登录淘宝）")
    else:
        print(f"当前浏览器 profile：{PROFILE_DIR}")
    print(f"当前引擎：{engine_name()}    浏览器：{browser_channel()}")

    with browser_runtime() as p:
        ctx = launch_context(p)
        page = ctx.new_page()
        try:
            page.goto("https://www.taobao.com", wait_until="domcontentloaded")
            print("请在弹出的浏览器窗口里完成登录（扫码即可）。")
            print("⚠️ 登录完成后【不要关闭浏览器窗口】，直接回到终端按回车。")
            input()

            logged_in = False
            try:
                cookie_names = {c.get("name") for c in ctx.cookies()}
                logged_in = has_login_cookies(cookie_names)
                print(f"检测到浏览器 cookie {len(cookie_names)} 个")
                if not logged_in and "tracknick" in cookie_names:
                    print("检测到 tracknick 但缺少 cookie2/unb（半登录），请完成扫码后的全部确认步骤")
            except Exception:
                print("浏览器窗口在回车前已关闭；登录态可能已写入本地 profile")

            if logged_in:
                # 等 cookie 落盘稳定后再关闭，避免半登录
                time.sleep(2)  # noqa: F821  (time 已在模块顶部导入)
            try:
                ctx.storage_state(path=str(STATE_PATH))
                print(f"登录态快照已保存：{STATE_PATH}")
            except Exception:
                print("未能导出登录快照（浏览器已关闭）；登录态仍以本地 profile 为准")
        finally:
            try:
                page.close()
            except Exception:
                pass
            close_context(ctx)

    if logged_in:
        print("登录完成。下一步：python -m bkr.cli collect --task kids-bottle")
    else:
        print("未确认到登录 cookie。若 collect 提示未登录，请重新执行 bkr login（且不要提前关闭窗口）。")