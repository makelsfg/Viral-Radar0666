"""商品快照表 fact_snapshot 的数据访问（含与 product 的联合查询）。"""
from __future__ import annotations

JOINED_COLUMNS = """
    s.item_id, s.platform,
    COALESCE(p.title, s.title) AS title,
    COALESCE(p.image_url, s.image_url) AS image_url,
    COALESCE(p.shop_name, s.shop_name) AS shop_name,
    s.price_final, s.price_text, s.sold_text, s.sold_lb,
    s.cart_text, s.cart_lb, s.rank, s.captured_at, s.is_detail,
    p.shop_rating, p.positive_rate, p.product_url, p.params_json, p.sku_names
"""

JOINED_FROM = """
    FROM fact_snapshot s
    LEFT JOIN product p ON p.item_id = s.item_id AND p.platform = s.platform
"""


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


def set_detail(
    conn,
    task_id: str,
    item_id: str,
    batch: str,
    price_final: float | None,
    *,
    sold_text: str | None = None,
    sold_lb: int | None = None,
    cart_text: str | None = None,
    cart_lb: int | None = None,
) -> None:
    conn.execute(
        """UPDATE fact_snapshot SET price_final=?, is_detail=1,
             sold_text=COALESCE(sold_text, ?), sold_lb=COALESCE(sold_lb, ?),
             cart_text=COALESCE(?, cart_text), cart_lb=COALESCE(?, cart_lb)
           WHERE task_id=? AND item_id=? AND batch=?""",
        (price_final, sold_text, sold_lb, cart_text, cart_lb, task_id, item_id, batch),
    )
    conn.commit()


def list_candidates(conn, task_id: str, batch: str, limit: int, order: str = "rank") -> list[dict]:
    order_sql = "rank ASC" if order == "rank" else "COALESCE(sold_lb, -1) DESC, rank ASC"
    rows = conn.execute(
        f"""SELECT * FROM fact_snapshot
            WHERE task_id=? AND batch=?
            ORDER BY {order_sql}
            LIMIT ?""",
        (task_id, batch, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def list_batches(conn, task_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT batch FROM fact_snapshot WHERE task_id=? ORDER BY batch DESC",
        (task_id,),
    ).fetchall()
    return [r["batch"] for r in rows]


def list_products(
    conn,
    batch: str,
    task_id: str | None = None,
    only_detail: bool = True,
) -> list[dict]:
    """批次商品列表（跨平台合并；task_id 省略时返回整批）。"""
    where = "s.batch=?"
    args: list = [batch]
    if task_id:
        where += " AND s.task_id=?"
        args.append(task_id)
    if only_detail:
        where += " AND s.is_detail=1"
    rows = conn.execute(
        f"""SELECT {JOINED_COLUMNS} {JOINED_FROM}
            WHERE {where}
            ORDER BY COALESCE(s.sold_lb, -1) DESC, s.rank ASC""",
        args,
    ).fetchall()
    return [dict(r) for r in rows]


def list_task_products(conn, task_id: str, batch: str) -> list[dict]:
    """单平台任务批次商品（分析/证据包口径）。"""
    rows = conn.execute(
        f"""SELECT {JOINED_COLUMNS} {JOINED_FROM}
            WHERE s.task_id=? AND s.batch=?""",
        (task_id, batch),
    ).fetchall()
    return [dict(r) for r in rows]


def count_batch(conn, task_id: str, batch: str) -> tuple[int, int]:
    row = conn.execute(
        """SELECT COUNT(*) AS n,
                  SUM(CASE WHEN is_detail=1 THEN 1 ELSE 0 END) AS details
           FROM fact_snapshot WHERE task_id=? AND batch=?""",
        (task_id, batch),
    ).fetchone()
    return int(row["n"] or 0), int(row["details"] or 0)


def count_detail_today(conn, date_prefix: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM fact_snapshot WHERE is_detail=1 AND captured_at LIKE ?",
        (date_prefix + "%",),
    ).fetchone()
    return int(row[0] or 0)


def product_history(conn, platform: str, item_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT batch, price_final, sold_lb, cart_lb, captured_at
           FROM fact_snapshot WHERE platform=? AND item_id=? ORDER BY batch""",
        (platform, item_id),
    ).fetchall()
    return [dict(r) for r in rows]


def delete_by_task(conn, task_id: str) -> None:
    conn.execute("DELETE FROM fact_snapshot WHERE task_id=?", (task_id,))


def delete_by_batch(conn, task_id: str, batch: str) -> int:
    cur = conn.execute(
        "DELETE FROM fact_snapshot WHERE task_id=? AND batch=?", (task_id, batch)
    )
    return cur.rowcount


def delete_by_batch_tasks(conn, batch: str, task_ids: list[str]) -> None:
    marks = ",".join("?" for _ in task_ids)
    conn.execute(
        f"DELETE FROM fact_snapshot WHERE batch=? AND task_id IN ({marks})",
        (batch, *task_ids),
    )


def batch_has_rows(conn, batch: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM fact_snapshot WHERE batch=? LIMIT 1", (batch,)
    ).fetchone()
    return row is not None
