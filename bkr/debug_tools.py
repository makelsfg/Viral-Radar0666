"""诊断工具：打开搜索页，输出结构统计并保存 HTML/截图，定位"0 解析"原因。"""
from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import quote

from .browser import BlockedError, LoginError
from .config import DATA_DIR
from .list_collector import SEARCH_URL


def debug_search(task, page_no: int = 1) -> None:
    from playwright.sync_api import sync_playwright

    from .browser import check_blocked, launch_context

    url = SEARCH_URL.format(q=quote(task.keywords[0]), tab=task.tab, sort=task.sort, page=page_no)
    print(f"打开：{url}")

    with sync_playwright() as p:
        ctx = launch_context(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        try:
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(1500)
        except Exception:
            pass

        stats = page.evaluate(
            """() => {
                const anchors = Array.from(document.querySelectorAll('a'));
                const itemLinks = anchors.filter(a => (a.href || '').includes('item.htm'));
                const body = (document.body.innerText || '');
                return {
                    url: location.href,
                    title: document.title,
                    itemAnchors: itemLinks.length,
                    anchorTotal: anchors.length,
                    firstItemHrefs: itemLinks.slice(0, 5).map(a => a.href),
                    bodyLength: body.length,
                    bodyHead: body.slice(0, 300),
                    hasLoginTip: body.includes('亲，请登录'),
                    hasVerifyText: /滑动验证|完成验证|安全验证/.test(body),
                };
            }"""
        )
        print(json.dumps(stats, ensure_ascii=False, indent=2))

        try:
            check_blocked(page)
            print("check_blocked：未检测到登录/验证问题")
        except (BlockedError, LoginError) as e:
            print(f"check_blocked 命中：{type(e).__name__}: {e}")

        d = DATA_DIR / "debug"
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        html_path = d / f"search_{task.task_id}_p{page_no}_{stamp}.html"
        png_path = d / f"search_{task.task_id}_p{page_no}_{stamp}.png"
        html_path.write_text(page.content(), encoding="utf-8")
        try:
            page.screenshot(path=str(png_path), full_page=False)
            print(f"\n诊断文件：\n  {html_path}\n  {png_path}")
        except Exception as e:
            print(f"\n诊断文件：\n  {html_path}\n（截图失败：{e}）")

        print("若 itemAnchors 为 0：多半未登录 / 风控验证 / 页面结构改版，请把这组文件发给开发者。")

        try:
            ctx.close()
        except Exception:
            pass