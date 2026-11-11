"""数据访问层基础设施：连接管理与 SQL 方言适配。

- 配置了 MySQL（BK_DB_* / settings.yaml database）时用 pymysql，否则用 SQLite。
- 业务代码统一写 SQLite 方言 SQL（`?` 占位符、ON CONFLICT、datetime('now')），
  由 _translate_mysql 在发送前转换成 MySQL 方言。
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import DATABASE_CONFIG, DB_PATH
from .repositories.schema import SCHEMA_MYSQL, SCHEMA_SQLITE


# ---------------------------------------------------------------------------
# SQL 方言转换
# ---------------------------------------------------------------------------

_SQL_STRING_RE = re.compile(r"('(?:[^'\\]|\\.|'')*')")


def translate_mysql(sql: str) -> str:
    """把 SQLite 方言 SQL 转成 MySQL 方言（只转换字符串字面量之外的部分）。"""
    sql = sql.replace("datetime('now')", "NOW()")
    segs = _SQL_STRING_RE.split(sql)
    for i in range(0, len(segs), 2):  # 偶数段 = 非字面量
        s = segs[i]
        s = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT IGNORE INTO", s, flags=re.I)
        s = re.sub(r"\bINSERT\s+OR\s+REPLACE\s+INTO\b", "REPLACE INTO", s, flags=re.I)
        s = re.sub(
            r"\bON\s+CONFLICT\s*\([^)]*\)\s*DO\s+UPDATE\s+SET\b",
            "AS new ON DUPLICATE KEY UPDATE",
            s,
            flags=re.I,
        )
        s = s.replace("excluded.", "new.")
        s = re.sub(r"(?<![\w`])rank(?![\w`])", "`rank`", s)  # rank 是 MySQL 保留字
        s = s.replace("?", "%s")
        segs[i] = s
    return "".join(segs)


# ---------------------------------------------------------------------------
# 行对象（兼容 sqlite3.Row 用法）
# ---------------------------------------------------------------------------

class Row:
    """支持 r["col"] / r[0] / .keys() / dict(r) 的只读行。"""

    __slots__ = ("_cols", "_vals")

    def __init__(self, cols, vals):
        object.__setattr__(self, "_cols", list(cols))
        object.__setattr__(self, "_vals", tuple(vals))

    def __setattr__(self, name, value):
        raise AttributeError("row object is read-only")

    def keys(self):
        return list(self._cols)

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self._vals[key]
        try:
            return self._vals[self._cols.index(key)]
        except ValueError:
            raise KeyError(key) from None

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)

    def __contains__(self, key):
        return key in self._cols

    def get(self, key, default=None):
        return self[key] if key in self._cols else default


class _MySQLCursor:
    def __init__(self, cur):
        self._cur = cur
        self.rowcount = cur.rowcount
        self.lastrowid = cur.lastrowid
        self._cols = [d[0] for d in cur.description] if cur.description else []

    def fetchone(self):
        row = self._cur.fetchone()
        return Row(self._cols, row) if row is not None else None

    def fetchall(self):
        return [Row(self._cols, r) for r in self._cur.fetchall()]

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row

    def close(self):
        self._cur.close()


class MySQLConnection:
    """以 sqlite3.Connection 的使用习惯包装 pymysql.Connection。"""

    def __init__(self, cfg: dict):
        import pymysql

        try:
            self._conn = pymysql.connect(
                host=cfg["host"],
                port=cfg["port"],
                user=cfg["user"],
                password=cfg["password"],
                database=cfg["name"],
                charset="utf8mb4",
                autocommit=False,
                connect_timeout=10,
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"MySQL 连接失败（{cfg['host']}:{cfg['port']}/{cfg['name']}）：{exc}。"
                "请确认 MySQL 已启动，或删除 settings.yaml 的 database 段回退 SQLite。"
            ) from exc
        cur = self._conn.cursor()
        # 对齐 SQLite 读可见性：REPEATABLE READ 下长连接看不到其他连接的新提交
        cur.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
        for stmt in (s.strip() for s in SCHEMA_MYSQL.split(";")):
            if stmt:
                cur.execute(stmt)
        cur.close()
        self._conn.commit()

    def execute(self, sql, args=()):
        cur = self._conn.cursor()
        cur.execute(translate_mysql(sql), tuple(args))
        return _MySQLCursor(cur)

    def executescript(self, script):
        for stmt in (s.strip() for s in script.split(";")):
            if stmt:
                self.execute(stmt)
        self.commit()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 连接入口
# ---------------------------------------------------------------------------

def _migrate_sqlite(conn: sqlite3.Connection) -> None:
    """旧 SQLite 库补列（幂等），仅用于历史文件。"""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(fact_snapshot)")}
    for name, decl in (
        ("title", "TEXT"),
        ("image_url", "TEXT"),
        ("shop_name", "TEXT"),
        ("page_no", "INTEGER"),
        ("platform", "TEXT DEFAULT 'tmall'"),
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE fact_snapshot ADD COLUMN {name} {decl}")

    task_cols = {row[1] for row in conn.execute("PRAGMA table_info(dim_task)")}
    for name, decl in (
        ("detail_order", "TEXT DEFAULT 'rank'"),
        ("platform", "TEXT DEFAULT 'tmall'"),
        ("pace", "TEXT DEFAULT 'safe'"),
    ):
        if name not in task_cols:
            conn.execute(f"ALTER TABLE dim_task ADD COLUMN {name} {decl}")

    run_cols = {row[1] for row in conn.execute("PRAGMA table_info(batch_run)")}
    for name, decl in (
        ("config_json", "TEXT"),
        ("agent_recovered", "INTEGER DEFAULT 0"),
    ):
        if name not in run_cols:
            conn.execute(f"ALTER TABLE batch_run ADD COLUMN {name} {decl}")

    conn.commit()


def connect(path: str | Path | None = None):
    """MySQL 已配置且未显式指定 SQLite 路径时用 MySQL；否则用 SQLite。"""
    if path is None and DATABASE_CONFIG:
        return MySQLConnection(DATABASE_CONFIG)
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA_SQLITE)
    _migrate_sqlite(conn)
    conn.commit()
    return conn


@contextmanager
def connection(path: str | Path | None = None):
    """`with connection() as conn:` 自动关闭连接。"""
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()
