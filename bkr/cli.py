"""命令行入口：python -m bkr.cli <login|collect|export|seed-demo>。"""
from __future__ import annotations

import argparse
import csv

from . import db as dbm
from .config import DATA_DIR, TASKS_DIR, ConfigError, load_task, load_tasks
from .parse import parse_price


def _find_task(task_id: str):
    for p in sorted(TASKS_DIR.glob("*.y*ml")):
        t = load_task(p)
        if t.task_id == task_id:
            return t
    raise ConfigError(f"找不到任务: {task_id}（检查 config/tasks/ 下的 YAML）")


def cmd_login(args) -> None:
    from .browser import login
    login(getattr(args, "site", "taobao"), getattr(args, "profile", None))


def cmd_logout(_args) -> None:
    """清除当前 profile 的登录态（删除整个浏览器用户目录）。"""
    import shutil

    from .config import PROFILE_DIR, STATE_PATH

    print(f"将清除 profile：{PROFILE_DIR}")
    if PROFILE_DIR.exists():
        try:
            shutil.rmtree(PROFILE_DIR)
        except OSError as e:
            print(f"删除失败（请先关闭所有使用该 profile 的浏览器窗口）：{e}")
            raise SystemExit(1)
    try:
        STATE_PATH.unlink(missing_ok=True)
    except OSError:
        pass
    print("已清除。下一步：python -m bkr.cli login 扫码新账号。")


def cmd_collect(args) -> None:
    from .collector import run_task
    tasks = load_tasks() if args.task == "all" else [_find_task(args.task)]
    if not tasks:
        raise ConfigError("config/tasks/ 下没有可用的任务配置")
    for t in tasks:
        run_task(t, batch=args.batch, resume=args.resume)


def cmd_export(args) -> None:
    conn = dbm.connect()
    batches = dbm.distinct_batches(conn, args.task)
    if not batches:
        raise ConfigError(f"任务 {args.task} 还没有任何批次数据，先运行 collect")
    batch = batches[0] if args.batch in (None, "latest") else args.batch

    where = "s.task_id=? AND s.batch=?"
    if getattr(args, "only_detail", False):
        where += " AND s.is_detail=1"
    rows = conn.execute(
        f"""SELECT s.item_id, s.platform,
                   COALESCE(p.title, s.title) AS title,
                   s.price_final, s.price_text, s.sold_text, s.cart_text,
                   COALESCE(p.shop_name, s.shop_name) AS shop_name,
                   p.shop_rating, p.positive_rate, p.sku_names, p.params_json,
                   COALESCE(p.image_url, s.image_url) AS image_url,
                   p.product_url, s.captured_at
            FROM fact_snapshot s
            LEFT JOIN product p ON p.item_id = s.item_id AND p.platform = s.platform
            WHERE {where}
            ORDER BY COALESCE(s.sold_lb, -1) DESC, s.rank ASC""",
        (args.task, batch),
    ).fetchall()

    out_dir = DATA_DIR / "export"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{args.task}_{batch}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["平台", "item_id", "标题", "到手价", "已售", "加购", "店铺", "评分",
                    "好评率", "SKU", "参数", "主图", "链接", "采集时间"])
        for r in rows:
            price = r["price_final"]
            if price is None:
                price = parse_price(r["price_text"] or "")
            url = r["product_url"] or f"https://detail.tmall.com/item.htm?id={r['item_id']}"
            w.writerow([
                r["platform"], r["item_id"], r["title"], price, r["sold_text"], r["cart_text"],
                r["shop_name"], r["shop_rating"], r["positive_rate"], r["sku_names"],
                r["params_json"], r["image_url"], url, r["captured_at"],
            ])
    print(f"已导出 {len(rows)} 行 -> {path}")


def cmd_seed(_args) -> None:
    from .demo import seed
    seed()


def cmd_debug_search(args) -> None:
    from .debug_tools import debug_search
    debug_search(_find_task(args.task), page_no=args.page)


def cmd_agent_collect(args) -> None:
    """采集监督 Agent 调试入口：打开一次搜索页，失败则由 Agent 接管恢复。"""
    from .agent.runner import supervised_recover
    from .browser import browser_runtime, close_context, launch_context
    from .config import profile_dir_for
    from .platforms import pinduoduo as pdd

    class _Task:
        platform = "pdd"
        task_id = "agent-cli"
        name = f"拼多多 · {args.keyword}"
        pace = "safe"
        sort = "sale-desc"
        pages = 1
        keywords = [args.keyword]
        exclude_words = []
        price_min = None
        price_max = None

    task = _Task()
    with browser_runtime() as engine:
        ctx = launch_context(engine, profile_dir=profile_dir_for("pdd"), platform="pdd")
        try:
            page = pdd.open_search_page(ctx, task, 1)
            res = page.evaluate(pdd.JS_SSR_LIST) or {}
            print(f"[agent-cli] 确定性路径：SSR 可用={res.get('ok')} 条数={res.get('count')}")
            result = supervised_recover(page, task, reason="CLI 调试", use_llm=not args.no_llm)
            print(
                f"[agent-cli] 结果：{result['status']}；{result['summary']}；"
                f"候选 {len(result.get('items') or [])}"
            )
        finally:
            close_context(ctx)


def cmd_pdd_probe(args) -> None:
    """拼多多实机校准：打开一次搜索页，输出 SSR 路径/字段/滚动前后条数。"""
    from .platforms.pinduoduo import probe
    probe(keyword=args.keyword, save_html=args.save_html)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bkr", description="爆款雷达 · 天猫品类采集")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="打开浏览器人工登录并保存登录态")
    p.add_argument("--site", choices=["taobao", "1688", "pdd"], default="taobao")
    p.add_argument("--profile", default=None, help="独立 profile 名（如 tmall / 1688）")

    sub.add_parser("logout", help="清除当前 profile 的登录态（换账号前使用）")

    p = sub.add_parser("collect", help="采集搜索结果与详情并入库")
    p.add_argument("--task", required=True, help="任务 ID 或 all")
    p.add_argument("--batch", default=None, help="批次号（默认按当前时间生成）")
    p.add_argument("--resume", action="store_true", help="从断点续采")

    p = sub.add_parser("export", help="导出批次数据为 CSV")
    p.add_argument("--task", required=True)
    p.add_argument("--batch", default="latest")
    p.add_argument("--only-detail", action="store_true", help="仅导出已采详情的商品")

    sub.add_parser("seed-demo", help="生成演示数据（不联网）")

    p = sub.add_parser("debug-search", help="打开搜索页输出诊断信息（HTML/截图/统计）")
    p.add_argument("--task", required=True)
    p.add_argument("--page", type=int, default=1)

    p = sub.add_parser("agent-collect", help="采集监督 Agent 调试：打开搜索页并在失败时由 Agent 接管")
    p.add_argument("--keyword", default="手表")
    p.add_argument("--no-llm", action="store_true", help="强制规则引擎（不调 LLM）")

    p = sub.add_parser("pdd-probe", help="拼多多实机校准：SSR 字段/条数/风控检查（只开 1 次搜索页）")
    p.add_argument("--keyword", default="手表")
    p.add_argument("--save-html", action="store_true", default=True,
                   help="保存搜索页 HTML/截图到 data/debug（默认开启）")
    p.add_argument("--no-save-html", dest="save_html", action="store_false")
    return parser


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "login":
            cmd_login(args)
        elif args.cmd == "logout":
            cmd_logout(args)
        elif args.cmd == "collect":
            cmd_collect(args)
        elif args.cmd == "export":
            cmd_export(args)
        elif args.cmd == "seed-demo":
            cmd_seed(args)
        elif args.cmd == "debug-search":
            cmd_debug_search(args)
        elif args.cmd == "pdd-probe":
            cmd_pdd_probe(args)
        elif args.cmd == "agent-collect":
            cmd_agent_collect(args)
    except ConfigError as e:
        print(f"配置错误: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()