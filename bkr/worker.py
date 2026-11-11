"""单平台采集 worker：父进程（Web 任务）用子进程方式并行运行。

用法：python -m bkr.worker --task-id kw-xxx --batch 2026-11-01_101010 [--resume]
父进程负责设置 BK_PROFILE（tmall / 1688），本进程启动时读取对应 profile。
"""
from __future__ import annotations

import argparse
import json
import os

from . import database
from .collector import run_task
from .config import Task, state_path_for
from .repositories import task_repo


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def load_task(task_id: str) -> Task:
    conn = database.connect()
    try:
        r = task_repo.get_task(conn, task_id)
    finally:
        conn.close()
    if not r:
        raise SystemExit(f"任务不存在：{task_id}")
    return Task(
        task_id=r["task_id"],
        name=r["name"],
        keywords=_loads(r["keywords"], [r["name"]]),
        platform=r.get("platform") or "tmall",
        pace=r.get("pace") or "safe",
        sort=r["sort"] or "sale-desc",
        pages=int(r["pages"] or 1),
        detail_top=int(r["detail_top"] or 6),
        detail_order=str(r["detail_order"] or "sold"),
        price_min=r["price_min"],
        price_max=r["price_max"],
        exclude_words=_loads(r["exclude_words"], []),
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="bkr.worker")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    task = load_task(args.task_id)
    os.environ["BK_STATE_FILE"] = str(state_path_for(task.platform))
    print(f"[worker] start task={task.task_id} platform={task.platform} batch={args.batch}")
    run_task(task, batch=args.batch, resume=args.resume)


if __name__ == "__main__":
    main()