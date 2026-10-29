"""热词接口：获取淘宝搜索联想词并落库。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from bkr import db
from bkr.hotwords import fetch_hotwords

router = APIRouter()


class HotwordRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=50)
    limit: int = Field(default=20, ge=1, le=50)


@router.post("/hotwords")
def fetch(req: HotwordRequest):
    keyword = req.keyword.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="请输入关键词")
    try:
        words = fetch_hotwords(keyword, req.limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"获取联想词失败：{exc}") from exc
    if not words:
        raise HTTPException(status_code=502, detail="未获取到联想词（可能被限流），请稍后再试")

    conn = db.connect()
    try:
        db.save_hotwords(conn, keyword, words)
    finally:
        conn.close()
    return {"keyword": keyword, "words": words}


@router.get("/hotwords")
def history(keyword: str | None = Query(default=None)):
    conn = db.connect()
    try:
        return db.list_hotwords(conn, keyword=keyword, limit=50)
    finally:
        conn.close()


@router.get("/hotwords/keywords")
def keywords():
    conn = db.connect()
    try:
        return db.hotword_keywords(conn)
    finally:
        conn.close()