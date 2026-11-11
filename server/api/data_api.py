"""控制层：数据接口（任务/批次、商品列表与详情、CSV 导出、删除）。"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from server.services import data_service
from server.utils import image_referer, is_allowed_image_url

router = APIRouter()


def _csv_response(content: str, filename: str) -> StreamingResponse:
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/tasks")
def tasks():
    return data_service.list_tasks()


@router.get("/products")
def products(
    task_id: str | None = Query(default=None),
    batch: str = Query(...),
    only_detail: int = Query(default=1),
    proxy_images: int = Query(default=1),
):
    """task_id 省略时返回整个批次（跨平台合并）的商品。"""
    return data_service.list_products(
        batch,
        task_id=task_id,
        only_detail=bool(only_detail),
        proxy_images=bool(proxy_images),
    )


@router.get("/products/{item_id}")
def product(item_id: str, platform: str = Query(default="tmall")):
    detail = data_service.product_detail(item_id, platform)
    if not detail:
        raise HTTPException(status_code=404, detail="商品不存在")
    return detail


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str):
    if not data_service.delete_task(task_id):
        raise HTTPException(status_code=404, detail="品类不存在")
    return {"ok": True}


@router.delete("/batches")
def delete_batch(task_id: str, batch: str):
    if not data_service.delete_batch(task_id, batch):
        raise HTTPException(status_code=404, detail="批次不存在")
    return {"ok": True}


@router.get("/image")
def image_proxy(url: str = Query(...)):
    """本地图片代理：1688 / 拼多多 CDN 防盗链。"""
    if not is_allowed_image_url(url):
        raise HTTPException(status_code=400, detail="不允许的图片地址")
    try:
        import requests

        resp = requests.get(
            url,
            headers={
                "Referer": image_referer(url),
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
            },
            timeout=20,
            stream=True,
        )
        resp.raise_for_status()
        return StreamingResponse(
            resp.iter_content(64 * 1024),
            media_type=resp.headers.get("Content-Type", "image/jpeg"),
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"图片代理失败：{exc}") from exc


@router.get("/export")
def export(task_id: str, batch: str, only_detail: int = 1):
    content, filename = data_service.export_single(task_id, batch, bool(only_detail))
    return _csv_response(content, filename)


@router.get("/report")
def report(batch: str, only_detail: int = 0):
    """一次搜索的跨平台合并报告（CSV）。"""
    result = data_service.export_merged(batch, bool(only_detail))
    if not result:
        raise HTTPException(status_code=404, detail="该批次没有可导出的商品数据")
    content, filename = result
    return _csv_response(content, filename)


@router.get("/report/meta")
def report_meta(batch: str):
    meta = data_service.report_meta(batch)
    if not meta:
        raise HTTPException(status_code=404, detail="该批次没有可导出的平台数据")
    return meta
