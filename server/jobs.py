"""后台任务管理：采集/登录线程、单会话锁、SSE 事件总线。"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from datetime import datetime

from bkr import db
from bkr.collector import run_task
from bkr.config import Task


class BusyError(RuntimeError):
    """已有采集/登录任务占用浏览器会话。"""


class EventBus:
    """线程安全的事件总线：工作线程 publish，SSE 连接消费。"""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subs: set[asyncio.Queue] = set()
        self._lock = threading.Lock()

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, payload: dict) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        data = json.dumps(payload, ensure_ascii=False, default=str)
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                loop.call_soon_threadsafe(q.put_nowait, data)
            except RuntimeError:
                pass


bus = EventBus()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self._registry_lock = threading.Lock()
        self._run_lock = threading.Lock()

    def get(self, job_id: str) -> dict | None:
        return self.jobs.get(job_id)

    def active(self) -> bool:
        return self._run_lock.locked()

    def _new_job(self, **kw) -> dict:
        job = {
            "id": uuid.uuid4().hex[:10],
            "status": "running",
            "stage": "start",
            "page_done": 0,
            "pages": 0,
            "items_count": 0,
            "detail_done": 0,
            "detail_total": 0,
            "message": "",
            "error": None,
            "started_at": _now(),
            "finished_at": None,
        }
        job.update(kw)
        with self._registry_lock:
            self.jobs[job["id"]] = job
        return job

    def _publish(self, job: dict) -> None:
        bus.publish({"type": "job", "job": dict(job)})

    # ---------- 采集 ----------

    def start_collect(self, task: Task, batch: str, resume: bool = False) -> dict:
        return self.start_collect_many([task], batch, resume=resume)

    def start_collect_many(self, tasks: list[Task], batch: str, resume: bool = False) -> dict:
        if not tasks:
            raise BusyError("没有可采集的平台任务")
        if not self._run_lock.acquire(blocking=False):
            raise BusyError("已有一个采集/登录任务在运行，请等待完成")
        first = tasks[0]
        job = self._new_job(
            type="collect",
            task_id=first.task_id,
            task_name=first.name,
            task_platform=first.platform,
            batch=batch,
            platforms=[t.platform for t in tasks],
            task_index=1,
            task_total=len(tasks),
            pages=first.pages,
            detail_total=first.detail_top,
            message="正在启动浏览器…",
        )
        job["_task_ids"] = [t.task_id for t in tasks]
        self._publish(job)
        threading.Thread(
            target=self._run_collect_many, args=(job, tasks, batch, resume), daemon=True
        ).start()
        return job

    def _run_collect_many(self, job: dict, tasks: list[Task], batch: str, resume: bool) -> None:
        stop = threading.Event()

        def monitor() -> None:
            last = None
            while not stop.wait(2.0):
                task_id = job.get("_current_task_id")
                if not task_id:
                    continue
                snap = None
                try:
                    conn = db.connect()
                    try:
                        row = db.get_batch(conn, batch, task_id)
                    finally:
                        conn.close()
                    if row:
                        status = row.get("status")
                        if status not in ("running", "paused_captcha", "failed", "done"):
                            status = "running"
                        snap = {
                            "status": status,
                            "stage": row.get("stage") or "start",
                            "page_done": int(row.get("page_done") or 0),
                            "items_count": int(row.get("items_count") or 0),
                            "detail_done": int(row.get("detail_done") or 0),
                            "error": row.get("error"),
                        }
                except Exception:
                    snap = None
                if snap and snap != last:
                    last = snap
                    job.update(snap)
                    job["message"] = _progress_message(job)
                    self._publish(job)

        mt = threading.Thread(target=monitor, daemon=True)
        mt.start()
        try:
            for idx, task in enumerate(tasks, 1):
                job.update(
                    task_id=task.task_id,
                    task_name=task.name,
                    task_platform=task.platform,
                    task_index=idx,
                    task_total=len(tasks),
                    pages=task.pages,
                    detail_total=task.detail_top,
                    page_done=0,
                    detail_done=0,
                    items_count=0,
                    stage="start",
                    status="running",
                    error=None,
                    message=f"正在启动 {task.name} …",
                )
                job["_current_task_id"] = task.task_id
                self._publish(job)
                run_task(task, batch=batch, resume=resume)
            job.update(status="done", stage="done", message="采集完成")
        except SystemExit:
            task_id = job.get("_current_task_id")
            row = {}
            try:
                conn = db.connect()
                try:
                    row = db.get_batch(conn, batch, task_id) or {}
                finally:
                    conn.close()
            except Exception:
                pass
            job["status"] = row.get("status") or "failed"
            job["error"] = row.get("error")
            job["message"] = row.get("error") or "采集中断"
        except Exception as exc:  # noqa: BLE001
            job["status"] = "failed"
            job["error"] = f"{type(exc).__name__}: {exc}"
            job["message"] = job["error"]
        finally:
            stop.set()
            mt.join(timeout=3)
            job["finished_at"] = _now()
            self._publish(job)
            self._run_lock.release()

    def _run_collect(self, job: dict, task: Task, batch: str, resume: bool) -> None:
        stop = threading.Event()

        def monitor() -> None:
            last = None
            while not stop.wait(2.0):
                snap = None
                try:
                    conn = db.connect()
                    try:
                        row = db.get_batch(conn, batch, task.task_id)
                    finally:
                        conn.close()
                    if row:
                        status = row.get("status")
                        if status not in ("running", "paused_captcha", "failed", "done"):
                            status = "running"
                        snap = {
                            "status": status,
                            "stage": row.get("stage") or "start",
                            "page_done": int(row.get("page_done") or 0),
                            "items_count": int(row.get("items_count") or 0),
                            "detail_done": int(row.get("detail_done") or 0),
                            "error": row.get("error"),
                        }
                except Exception:
                    snap = None
                if snap and snap != last:
                    last = snap
                    job.update(snap)
                    job["message"] = _progress_message(job)
                    self._publish(job)

        mt = threading.Thread(target=monitor, daemon=True)
        mt.start()
        try:
            run_task(task, batch=batch, resume=resume)
            job["status"] = "done"
            job["stage"] = "done"
            job["message"] = "采集完成"
        except SystemExit:
            row = {}
            try:
                conn = db.connect()
                try:
                    row = db.get_batch(conn, batch, task.task_id) or {}
                finally:
                    conn.close()
            except Exception:
                pass
            job["status"] = row.get("status") or "failed"
            job["error"] = row.get("error")
            job["message"] = row.get("error") or "采集中断"
        except Exception as exc:  # noqa: BLE001
            job["status"] = "failed"
            job["error"] = f"{type(exc).__name__}: {exc}"
            job["message"] = job["error"]
        finally:
            stop.set()
            mt.join(timeout=3)
            job["finished_at"] = _now()
            self._publish(job)
            self._run_lock.release()

    # ---------- 登录 ----------

    def start_login(self) -> dict:
        if not self._run_lock.acquire(blocking=False):
            raise BusyError("已有一个采集/登录任务在运行，请等待完成")
        job = self._new_job(type="login", status="waiting", message="请在打开的浏览器里扫码登录")
        self._publish(job)
        threading.Thread(target=self._run_login, args=(job,), daemon=True).start()
        return job

    def _run_login(self, job: dict) -> None:
        try:
            from bkr.browser import browser_runtime, close_context, launch_context
            from bkr.config import STATE_PATH

            with browser_runtime() as p:
                ctx = launch_context(p)
                page = ctx.new_page()
                try:
                    page.goto("https://www.taobao.com", wait_until="domcontentloaded")
                    deadline = time.time() + 300
                    logged = False
                    hits = 0
                    while time.time() < deadline:
                        try:
                            names = {c.get("name") for c in ctx.cookies()}
                        except Exception:
                            break
                        from bkr.browser import has_login_cookies

                        if has_login_cookies(names):
                            hits += 1
                            if hits >= 2:  # 连续两次确认，避免半登录
                                logged = True
                                break
                        else:
                            hits = 0
                        time.sleep(2)
                    if logged:
                        time.sleep(2)  # 等 cookie 落盘
                        try:
                            ctx.storage_state(path=str(STATE_PATH))
                        except Exception:
                            pass
                        job["status"] = "done"
                        job["message"] = "登录成功"
                    else:
                        job["status"] = "timeout"
                        job["message"] = "5 分钟内未检测到完整登录（需 cookie2 + unb），请重试"
                finally:
                    try:
                        page.close()
                    except Exception:
                        pass
                    close_context(ctx)
        except Exception as exc:  # noqa: BLE001
            job["status"] = "failed"
            job["error"] = f"{type(exc).__name__}: {exc}"
            job["message"] = job["error"]
        finally:
            job["finished_at"] = _now()
            self._publish(job)
            self._run_lock.release()


def _progress_message(job: dict) -> str:
    status = job.get("status")
    prefix = ""
    if int(job.get("task_total") or 1) > 1 and job.get("task_platform"):
        prefix = f"[{job.get('task_platform')}] "
    if status == "paused_captcha":
        return prefix + "触发验证或登录失效：请在浏览器处理后点击「继续采集」"
    if status == "failed":
        return prefix + (job.get("error") or "采集失败")
    if status == "done":
        return prefix + "采集完成"
    stage = job.get("stage")
    if stage == "list":
        return prefix + f"正在采集搜索结果（第 {job.get('page_done')}/{job.get('pages')} 页）"
    if stage == "detail":
        return prefix + f"正在采集商品详情（{job.get('detail_done')}/{job.get('detail_total')}）"
    return prefix + "采集中…"


manager = JobManager()