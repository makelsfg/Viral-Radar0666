"""FastAPI 入口：API + 前端静态托管 + 一键启动。

运行：python -m server.main
"""
from __future__ import annotations

import asyncio
import os
import threading
import webbrowser
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from bkr.config import ROOT
from server.jobs import bus
from server.routes import analysis, collect, data, env, events, hotwords


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bus.attach_loop(asyncio.get_running_loop())
    yield


app = FastAPI(title="爆款雷达", version="0.8.0", lifespan=lifespan)

app.include_router(events.router, prefix="/api")
app.include_router(env.router, prefix="/api")
app.include_router(data.router, prefix="/api")
app.include_router(collect.router, prefix="/api")
app.include_router(analysis.router, prefix="/api")
app.include_router(hotwords.router, prefix="/api")


DIST = ROOT / "web" / "dist"
if DIST.exists():
    assets = DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")


@app.get("/", response_class=HTMLResponse)
def index():
    index_file = DIST / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return HTMLResponse(
        "<div style=\"font-family:system-ui,-apple-system,'Microsoft YaHei';"
        "max-width:620px;margin:80px auto;line-height:1.8;color:#2b2622\">"
        "<h2>前端尚未构建</h2>"
        "<p>首次使用请在项目根目录双击 <b>安装环境.bat</b>，或在 web 目录执行：</p>"
        "<pre style=\"background:#f5f3ef;padding:12px;border-radius:8px\">"
        "npm install\nnpm run build</pre>"
        "<p>完成后刷新本页。</p></div>"
    )


def run() -> None:
    import uvicorn

    port = int(os.environ.get("BK_PORT", "8765"))
    threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    uvicorn.run("server.main:app", host="127.0.0.1", port=port)


if __name__ == "__main__":
    run()