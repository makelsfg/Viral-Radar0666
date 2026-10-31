"""SQLite 数据层：任务、商品、快照、批次状态。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS dim_task (
  task_id       TEXT PRIMARY KEY,
  platform      TEXT NOT NULL DEFAULT 'tmall',
  name          TEXT NOT NULL,
  keywords      TEXT NOT NULL,
  sort          TEXT DEFAULT 'sale-desc',
  pages         INTEGER DEFAULT 2,
  detail_top    INTEGER DEFAULT 20,
  detail_order  TEXT DEFAULT 'rank',
  price_min     REAL,
  price_max     REAL,
  exclude_words TEXT,
  enabled       INTEGER DEFAULT 1,
  created_at    TEXT
);

CREATE TABLE IF NOT EXISTS product (
  platform      TEXT NOT NULL DEFAULT 'tmall',
  item_id       TEXT NOT NULL,
  title         TEXT,
  shop_name     TEXT,
  shop_rating   REAL,
  positive_rate TEXT,
  sku_names     TEXT,
  params_json   TEXT,
  image_url     TEXT,
  video_url     TEXT,
  product_url   TEXT,
  first_seen_at TEXT,
  updated_at    TEXT,
  PRIMARY KEY (platform, item_id)
);

CREATE TABLE IF NOT EXISTS fact_snapshot (
  snapshot_id  INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id      TEXT NOT NULL,
  platform     TEXT NOT NULL DEFAULT 'tmall',
  item_id      TEXT NOT NULL,
  batch        TEXT NOT NULL,
  captured_at  TEXT NOT NULL,
  rank         INTEGER,
  price_final  REAL,
  price_text   TEXT,
  sold_text    TEXT,
  sold_lb      INTEGER,
  cart_text    TEXT,
  cart_lb      INTEGER,
  title        TEXT,
  image_url    TEXT,
  shop_name    TEXT,
  page_no      INTEGER,
  is_detail    INTEGER DEFAULT 0,
  UNIQUE (task_id, item_id, batch)
);
CREATE INDEX IF NOT EXISTS idx_snap_task_batch ON fact_snapshot (task_id, batch);
CREATE INDEX IF NOT EXISTS idx_snap_item ON fact_snapshot (item_id);

CREATE TABLE IF NOT EXISTS batch_run (
  batch        TEXT NOT NULL,
  task_id      TEXT NOT NULL,
  status       TEXT NOT NULL,
  stage        TEXT,
  page_done    INTEGER DEFAULT 0,
  detail_done  INTEGER DEFAULT 0,
  items_count  INTEGER DEFAULT 0,
  captcha_hits INTEGER DEFAULT 0,
  config_json  TEXT,
  error        TEXT,
  started_at   TEXT,
  finished_at  TEXT,
  PRIMARY KEY (batch, task_id)
);

CREATE TABLE IF NOT EXISTS hot_word (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  keyword    TEXT NOT NULL,
  word       TEXT NOT NULL,
  rank       INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE (keyword, word)
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """为旧库补充新增列（幂等）。"""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(fact_snapshot)")}
    for name, decl in (
        ("title", "TEXT"),
        ("image_url", "TEXT"),
        ("shop_name", "TEXT"),
        ("page_no", "INTEGER"),
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE fact_snapshot ADD COLUMN {name} {decl}")

    task_cols = {row[1] for row in conn.execute("PRAGMA table_info(dim_task)")}
    if "detail_order" not in task_cols:
        conn.execute("ALTER TABLE dim_task ADD COLUMN detail_order TEXT DEFAULT 'rank'")
    if "platform" not in task_cols:
        conn.execute("ALTER TABLE dim_task ADD COLUMN platform TEXT DEFAULT 'tmall'")

    run_cols = {row[1] for row in conn.execute("PRAGMA table_info(batch_run)")}
    if "config_json" not in run_cols:
        conn.execute("ALTER TABLE batch_run ADD COLUMN config_json TEXT")

    snap_cols = {row[1] for row in conn.execute("PRAGMA table_info(fact_snapshot)")}
    if "platform" not in snap_cols:
        conn.execute("ALTER TABLE fact_snapshot ADD COLUMN platform TEXT DEFAULT 'tmall'")

    prod_cols = {row[1] for row in conn.execute("PRAGMA table_info(product)")}
    if prod_cols and "platform" not in prod_cols:
        conn.executescript(
            """
            ALTER TABLE product RENAME TO product_old;
            CREATE TABLE product (
              platform      TEXT NOT NULL DEFAULT 'tmall',
              item_id       TEXT NOT NULL,
              title         TEXT,
              shop_name     TEXT,
              shop_rating   REAL,
              positive_rate TEXT,
              sku_names     TEXT,
              params_json   TEXT,
              image_url     TEXT,
              video_url     TEXT,
              product_url   TEXT,
              first_seen_at TEXT,
              updated_at    TEXT,
              PRIMARY KEY (platform, item_id)
            );
            INSERT INTO product(platform, item_id, title, shop_name, shop_rating,
                                positive_rate, sku_names, params_json, image_url,
                                video_url, product_url, first_seen_at, updated_at)
            SELECT 'tmall', item_id, title, shop_name, shop_rating,
                   positive_rate, sku_names, params_json, image_url,
                   video_url, product_url, first_seen_at, updated_at
            FROM product_old;
            DROP TABLE product_old;
            """
        )
    conn.commit()


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def upsert_task(conn, task) -> None:
    conn.execute(
        """INSERT INTO dim_task(task_id, platform, name, keywords, sort, pages, detail_top,
                               detail_order, price_min, price_max, exclude_words, enabled, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,1,datetime('now'))
           ON CONFLICT(task_id) DO UPDATE SET
             platform=excluded.platform, name=excluded.name, keywords=excluded.keywords,
             sort=excluded.sort, pages=excluded.pages, detail_top=excluded.detail_top,
             detail_order=excluded.detail_order,
             price_min=excluded.price_min, price_max=excluded.price_max,
             exclude_words=excluded.exclude_words""",
        (
            task.task_id,
            getattr(task, "platform", "tmall"),
            task.name,
            json.dumps(task.keywords, ensure_ascii=False),
            task.sort,
            task.pages,
            task.detail_top,
            task.detail_order,
            task.price_min,
            task.price_max,
            json.dumps(task.exclude_words, ensure_ascii=False),
        ),
    )
    conn.commit()


def get_batch(conn, batch: str, task_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM batch_run WHERE batch=? AND task_id=?", (batch, task_id)
    ).fetchone()
    return dict(row) if row else None


def ensure_batch(conn, batch: str, task_id: str) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO batch_run(batch, task_id, status, stage, started_at)
           VALUES(?, ?, 'running', 'list', datetime('now'))""",
        (batch, task_id),
    )
    conn.commit()


def set_batch(conn, batch: str, task_id: str, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(
        f"UPDATE batch_run SET {sets} WHERE batch=? AND task_id=?",
        (*fields.values(), batch, task_id),
    )
    conn.commit()


def upsert_snapshot(
    conn,
    task_id: str,
    item_id: str,
    batch: str,
    captured_at: str,
    rank: int | None,
    price_text: str | None,
    sold_text: str | None,
    sold_lb: int | None,
    cart_text: str | None,
    cart_lb: int | None,
    *,
    title: str | None = None,
    image_url: str | None = None,
    shop_name: str | None = None,
    page_no: int | None = None,
    platform: str = "tmall",
) -> None:
    conn.execute(
        """INSERT INTO fact_snapshot(task_id, platform, item_id, batch, captured_at, rank,
                                     price_text, sold_text, sold_lb, cart_text, cart_lb,
                                     title, image_url, shop_name, page_no, is_detail)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
           ON CONFLICT(task_id, item_id, batch) DO UPDATE SET
             platform=excluded.platform,
             captured_at=excluded.captured_at, rank=excluded.rank,
             price_text=excluded.price_text, sold_text=excluded.sold_text,
             sold_lb=excluded.sold_lb, cart_text=excluded.cart_text,
             cart_lb=excluded.cart_lb,
             title=excluded.title, image_url=excluded.image_url, shop_name=excluded.shop_name,
             page_no=excluded.page_no""",
        (
            task_id, platform, item_id, batch, captured_at, rank,
            price_text, sold_text, sold_lb, cart_text, cart_lb,
            title, image_url, shop_name, page_no,
        ),
    )


def set_snapshot_detail(conn, task_id: str, item_id: str, batch: str, price_final: float | None) -> None:
    conn.execute(
        """UPDATE fact_snapshot SET price_final=?, is_detail=1
           WHERE task_id=? AND item_id=? AND batch=?""",
        (price_final, task_id, item_id, batch),
    )
    conn.commit()


def upsert_product(conn, rec: dict) -> None:
    fields = [
        "platform", "item_id", "title", "shop_name", "shop_rating", "positive_rate",
        "sku_names", "params_json", "image_url", "video_url", "product_url",
        "first_seen_at", "updated_at",
    ]
    values = [rec.get(f) or ("tmall" if f == "platform" else None) for f in fields]
    update_cols = [f for f in fields if f not in ("item_id", "platform")]
    sets = ", ".join(f"{c}=excluded.{c}" for c in update_cols if c != "first_seen_at")
    sets += ", first_seen_at=COALESCE(product.first_seen_at, excluded.first_seen_at)"
    conn.execute(
        f"""INSERT INTO product({', '.join(fields)})
            VALUES({', '.join('?' * len(fields))})
            ON CONFLICT(platform, item_id) DO UPDATE SET {sets}""",
        values,
    )
    conn.commit()


def list_candidates(
    conn, task_id: str, batch: str, limit: int, order: str = "rank"
) -> list[dict]:
    order_sql = "rank ASC" if order == "rank" else "COALESCE(sold_lb, -1) DESC, rank ASC"
    rows = conn.execute(
        f"""SELECT * FROM fact_snapshot
            WHERE task_id=? AND batch=?
            ORDER BY {order_sql}
            LIMIT ?""",
        (task_id, batch, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def distinct_batches(conn, task_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT batch FROM fact_snapshot WHERE task_id=? ORDER BY batch DESC",
        (task_id,),
    ).fetchall()
    return [r["batch"] for r in rows]


def reset_batch(conn, batch: str, task_id: str) -> None:
    conn.execute("DELETE FROM batch_run WHERE batch=? AND task_id=?", (batch, task_id))
    conn.commit()


def reset_batch_progress(conn, batch: str, task_id: str) -> None:
    """重新开始一个批次（非续采场景）：清空进度，允许重跑。"""
    conn.execute(
        """UPDATE batch_run
           SET status='running', stage='list', page_done=0, detail_done=0,
               items_count=0, error=NULL, started_at=datetime('now'), finished_at=NULL
           WHERE batch=? AND task_id=?""",
        (batch, task_id),
    )
    conn.commit()


def save_hotwords(conn, keyword: str, words: list[str]) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    for rank, word in enumerate(words, 1):
        conn.execute(
            """INSERT INTO hot_word(keyword, word, rank, created_at)
               VALUES(?,?,?,?)
               ON CONFLICT(keyword, word) DO UPDATE SET
                 rank=excluded.rank, created_at=excluded.created_at""",
            (keyword, word, rank, now),
        )
    conn.commit()


def list_hotwords(conn, keyword: str | None = None, limit: int = 50) -> list[dict]:
    if keyword:
        rows = conn.execute(
            "SELECT * FROM hot_word WHERE keyword=? ORDER BY rank ASC LIMIT ?",
            (keyword, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM hot_word ORDER BY created_at DESC, rank ASC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def hotword_keywords(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT keyword, MAX(created_at) AS last_at, COUNT(*) AS count
           FROM hot_word GROUP BY keyword ORDER BY last_at DESC"""
    ).fetchall()
    return [dict(r) for r in rows]