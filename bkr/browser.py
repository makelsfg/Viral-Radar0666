"""Playwright 浏览器启动、登录态与风控检测。

playwright 采用惰性导入：只跑模拟数据/单测时无需安装浏览器。
"""
from __future__ import annotations

import random
import time

from .config import PROFILE_DIR, STATE_PATH


class BlockedError(RuntimeError):
    """触发验证码/风控，需要人工处理后续采。"""


class LoginError(BlockedError):
    """登录态失效，需要重新登录。"""


class EmptyResultError(RuntimeError):
    """页面解析到 0 个商品：登录失效 / 风控 / 页面结构改版。"""


def launch_context(p, headless: bool = False):
    """启动带持久化用户目录的 Chromium（登录态、指纹、cookie 都留在本机）。"""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        viewport={"width": 1500, "height": 950},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        args=["--disable-blink-features=AutomationControlled"],
    )
    # 轻量反自动化特征：navigator.webdriver / chrome 对象 / languages / plugins
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
    """检测登录失效 / 滑块验证，命中即抛异常（由上层暂停批次）。"""
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
    from playwright.sync_api import sync_playwright

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"当前浏览器 profile：{PROFILE_DIR}")
    with sync_playwright() as p:
        ctx = launch_context(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.taobao.com", wait_until="domcontentloaded")
        print("请在弹出的浏览器窗口里完成登录（扫码即可）。")
        print("⚠️ 登录完成后【不要关闭浏览器窗口】，直接回到终端按回车。")
        input()

        logged_in = False
        try:
            cookies = ctx.cookies()
            logged_in = any(
                c.get("name") in ("cookie2", "_tb_token_", "sgcookie") for c in cookies
            )
            print(f"检测到浏览器 cookie {len(cookies)} 个")
        except Exception:
            print("浏览器窗口在回车前已关闭；登录态可能已写入本地 profile（.browser-profile/）")

        try:
            ctx.storage_state(path=str(STATE_PATH))
            print(f"登录态快照已保存：{STATE_PATH}")
        except Exception:
            print("未能导出登录快照（浏览器已关闭）；登录态仍以本地 profile 为准")

        try:
            ctx.close()
        except Exception:
            pass

    if logged_in:
        print("登录完成。下一步：python -m bkr.cli collect --task kids-bottle")
    else:
        print("未确认到登录 cookie。若 collect 提示未登录，请重新执行 bkr login（且不要提前关闭窗口）。")