"""商品主数据表 product 的数据访问。"""
from __future__ import annotations

FIELDS = [
    "platform", "item_id", "title", "shop_name", "shop_rating", "positive_rate",
    "sku_names", "params_json", "image_url", "video_url", "product_url",
    "first_seen_at", "updated_at",
]


def upsert_product(conn, rec: dict) -> None:
    values = [rec.get(f) or ("tmall" if f == "platform" else None) for f in FIELDS]
    update_cols = [f for f in FIELDS if f not in ("item_id", "platform", "first_seen_at")]
    sets = ", ".join(f"{c}=excluded.{c}" for c in update_cols)
    sets += ", first_seen_at=COALESCE(product.first_seen_at, excluded.first_seen_at)"
    conn.execute(
        f"""INSERT INTO product({', '.join(FIELDS)})
            VALUES({', '.join('?' * len(FIELDS))})
            ON CONFLICT(platform, item_id) DO UPDATE SET {sets}""",
        values,
    )
    conn.commit()


def get_product(conn, platform: str, item_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM product WHERE platform=? AND item_id=?", (platform, item_id)
    ).fetchone()
    return dict(row) if row else None


def delete_orphans(conn) -> None:
    """删除不再被任何快照引用的商品行。"""
    conn.execute(
        """DELETE FROM product
           WHERE NOT EXISTS (
             SELECT 1 FROM fact_snapshot s
             WHERE s.item_id = product.item_id AND s.platform = product.platform
           )"""
    )
