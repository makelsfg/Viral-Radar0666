# -*- coding: utf-8 -*-
"""把历史 SQLite 数据（data/radar.db）迁移到 MySQL。

用法（在项目根目录）：
    .\\venv\\Scripts\\python.exe migrate_sqlite_to_mysql.py

说明：
- 只迁移，不删除：SQLite 原库保持不动，可随时重跑（已有主键的行会被跳过）。
- 表清单与列结构以 SQLite 侧 PRAGMA 为准，自动适配新增列。
- 需要在 config/settings.yaml 配置好 database 段（或设置 BK_DB_* 环境变量）。
"""
from __future__ import annotations

import sqlite3
import sys

from bkr import database
from bkr.config import DATABASE_CONFIG, DB_PATH

# 迁移顺序：先无外键依赖的维表/事实表，均无外键，顺序只影响可读性
TABLES = [
    "dim_task",
    "product",
    "fact_snapshot",
    "batch_run",
    "platform_auth",
    "expert_run",
    "expert_round",
    "agent_run",
    "agent_step",
]


def main() -> int:
    if not DATABASE_CONFIG:
        print("未配置 MySQL（settings.yaml 的 database 段为空），无需迁移。")
        return 1

    src = sqlite3.connect(str(DB_PATH))
    src.row_factory = sqlite3.Row
    dst = database.connect()  # MySQL
    print(f"MySQL 已连接：{DATABASE_CONFIG['host']}:{DATABASE_CONFIG['port']}/{DATABASE_CONFIG['name']}")

    total = 0
    skipped_tables = []
    for table in TABLES:
        cols = [r["name"] for r in src.execute(f"PRAGMA table_info({table})")]
        if not cols:
            skipped_tables.append(table)
            continue
        col_list = ", ".join(f"`{c}`" for c in cols)
        ph = ", ".join("%s" for _ in cols)
        sql = f"INSERT IGNORE INTO `{table}` ({col_list}) VALUES ({ph})"
        rows = [tuple(r) for r in src.execute(f"SELECT {col_list} FROM {table}")]
        ok = 0
        for row in rows:
            try:
                dst.execute(sql, row)
                dst.commit()
                ok += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  [跳过] {table} 行 {row[:2]}...：{exc}")
        print(f"  {table}: 迁移 {ok}/{len(rows)} 行")
        total += ok

    src.close()
    dst.close()
    if skipped_tables:
        print(f"跳过（SQLite 中不存在）：{', '.join(skipped_tables)}")
    print(f"完成，共迁移 {total} 行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
