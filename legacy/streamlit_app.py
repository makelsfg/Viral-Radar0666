"""爆款雷达 · Streamlit 展示页：一行一商品 + 行详情。

运行：streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "radar.db"

st.set_page_config(page_title="爆款雷达", page_icon="🔭", layout="wide")


def q(sql: str, params: tuple = ()) -> pd.DataFrame:
    conn = sqlite3.connect(DB)
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


@st.cache_data(ttl=10)
def load_tasks() -> pd.DataFrame:
    return q("SELECT task_id, name FROM dim_task ORDER BY created_at")


@st.cache_data(ttl=10)
def load_batches(task_id: str) -> list[str]:
    df = q(
        "SELECT DISTINCT batch FROM fact_snapshot WHERE task_id=? ORDER BY batch DESC",
        (task_id,),
    )
    return df["batch"].tolist()


@st.cache_data(ttl=5)
def load_rows(task_id: str, batch: str) -> pd.DataFrame:
    return q(
        """SELECT s.item_id, p.title, p.image_url, s.price_final, s.sold_lb, s.sold_text,
                  s.cart_lb, s.cart_text, p.shop_name, p.shop_rating, p.positive_rate,
                  p.sku_names, p.params_json, s.captured_at, p.product_url, s.rank, s.is_detail
           FROM fact_snapshot s LEFT JOIN product p ON p.item_id = s.item_id
           WHERE s.task_id=? AND s.batch=?
           ORDER BY COALESCE(s.sold_lb, -1) DESC, s.rank ASC""",
        (task_id, batch),
    )


@st.cache_data(ttl=5)
def load_history(item_id: str) -> pd.DataFrame:
    return q(
        """SELECT batch, price_final, sold_lb, cart_lb, captured_at
           FROM fact_snapshot WHERE item_id=? ORDER BY batch""",
        (item_id,),
    )


def main() -> None:
    st.title("爆款雷达 · 天猫品类爆款")
    st.caption(
        "口径：已售/加购为页面区间文本的下限；价格只存到手价；"
        "覆盖范围 = 天猫 tab 搜索前 2 页 + 详情 Top20"
    )

    if not DB.exists():
        st.info(
            "还没有数据库。先运行 `python -m bkr.cli seed-demo` 生成演示数据，"
            "或 `python -m bkr.cli collect --task kids-bottle` 开始采集。"
        )
        return

    tasks = load_tasks()
    if tasks.empty:
        st.info("数据库为空：先运行 `python -m bkr.cli seed-demo` 或 `collect`。")
        return

    task_map = {f"{r.name}（{r.task_id}）": r.task_id for r in tasks.itertuples()}
    with st.sidebar:
        st.header("筛选")
        label = st.selectbox("品类", list(task_map))
        task_id = task_map[label]
        batches = load_batches(task_id)
        if not batches:
            st.warning("该品类还没有批次数据，请先采集")
            return
        batch = st.selectbox("批次", batches)
        order = st.radio(
            "排序", ["已售降序", "到手价升序", "到手价降序", "榜单名次"], horizontal=False
        )

    df = load_rows(task_id, batch)
    if df.empty:
        st.warning("当前批次没有数据")
        return

    if order == "已售降序":
        df = df.sort_values("sold_lb", ascending=False, na_position="last")
    elif order == "到手价升序":
        df = df.sort_values("price_final", ascending=True, na_position="last")
    elif order == "到手价降序":
        df = df.sort_values("price_final", ascending=False, na_position="last")
    else:
        df = df.sort_values("rank", ascending=True, na_position="last")

    c1, c2, c3 = st.columns(3)
    c1.metric("本批商品", len(df))
    c2.metric("采到详情", int(df["is_detail"].fillna(0).sum()))
    c3.metric("批次", batch)

    def sku_count(s) -> int:
        try:
            return len(json.loads(s or "[]"))
        except (TypeError, ValueError):
            return 0

    view = pd.DataFrame({
        "主图": df["image_url"].fillna(""),
        "标题": df["title"].fillna(""),
        "到手价": pd.to_numeric(df["price_final"], errors="coerce"),
        "已售": df["sold_text"].fillna(""),
        "加购": df["cart_text"].fillna(""),
        "店铺": df["shop_name"].fillna(""),
        "评分": pd.to_numeric(df["shop_rating"], errors="coerce"),
        "好评率": df["positive_rate"].fillna(""),
        "SKU数": df["sku_names"].fillna("[]").map(sku_count),
        "采集时间": df["captured_at"].fillna(""),
        "链接": df["product_url"].fillna(""),
    })

    st.dataframe(
        view,
        use_container_width=True,
        hide_index=True,
        height=420,
        column_config={
            "主图": st.column_config.ImageColumn("主图", width="small"),
            "到手价": st.column_config.NumberColumn("到手价", format="¥%.2f"),
            "链接": st.column_config.LinkColumn("链接", display_text="打开"),
        },
    )

    st.download_button(
        "导出 CSV",
        view.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{task_id}_{batch}.csv",
        mime="text/csv",
    )

    st.divider()
    st.subheader("商品详情")
    dfr = df.reset_index(drop=True)
    sel = st.selectbox(
        "选择商品",
        options=list(dfr.index),
        format_func=lambda i: f"{str(dfr.loc[i, 'title'])[:48]}（{dfr.loc[i, 'item_id']}）",
    )
    row = dfr.loc[sel]
    col_img, col_info = st.columns([1, 2])
    with col_img:
        img = row.get("image_url")
        if isinstance(img, str) and img:
            st.image(img, width=280)
        if isinstance(row.get("product_url"), str) and row.get("product_url"):
            st.markdown(f"[打开天猫商品页]({row['product_url']})")
    with col_info:
        try:
            params = json.loads(row.get("params_json") or "{}")
        except ValueError:
            params = {}
        if params:
            st.table(pd.DataFrame(list(params.items()), columns=["参数", "值"]))
        else:
            st.caption("暂无参数（详情页未采到或解析失败，可查 data/raw/ 原始文件）")
        try:
            skus = json.loads(row.get("sku_names") or "[]")
        except ValueError:
            skus = []
        if skus:
            st.markdown("**SKU**：" + " ｜ ".join(skus))
        hist = load_history(row["item_id"])
        if len(hist) > 1:
            st.markdown("**历史批次**")
            st.dataframe(hist, use_container_width=True, hide_index=True)


main()