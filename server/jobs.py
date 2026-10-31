"""后台任务：登录前置检查 + 并行采集 worker + SSE 事件。"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime

from bkr import db
from bkr.auth import is_authorized, login_site
from bkr.config import ROOT, Task, profile_dir_for, state_path_for


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
            "phase": "auth",
            "stage": "start",
            "page_done": 0,
            "pages": 0,
            "items_count": 0,
            "detail_done": 0,
            "detail_total": 0,
            "auth_statuses": [],
            "platforms_status": [],
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

    # ---------- 采集（登录前置） ----------

    def start_collect(self, task: Task, batch: str, resume: bool = False) -> dict:
        return self.start_run([task], batch, resume=resume)

    def start_collect_many(self, tasks: list[Task], batch: str, resume: bool = False) -> dict:
        return self.start_run(tasks, batch, resume=resume)

    def start_run(
        self,
        tasks: list[Task],
        batch: str,
        resume: bool = False,
        auth_first: str | None = None,
    ) -> dict:
        """创建一次运行：先登录检查，全部授权后才进入采集。"""
        if not tasks:
            raise BusyError("没有可采集的平台任务")
        if not self._run_lock.acquire(blocking=False):
            raise BusyError("已有一个采集/登录任务在运行，请等待完成")
        first = tasks[0]
        job = self._new_job(
            type="collect",
            phase="auth",
            task_id=first.task_id,
            task_name=first.name,
            task_platform=first.platform,
            batch=batch,
            platforms=[t.platform for t in tasks],
            auth_statuses=[],
            platforms_status=[
                {
                    "task_id": t.task_id, "name": t.name, "platform": t.platform,
                    "status": "waiting", "stage": "waiting",
                    "page_done": 0, "pages": t.pages, "items_count": 0,
                    "detail_done": 0, "detail_total": t.detail_top, "error": None,
                }
                for t in tasks
            ],
            task_index=1,
            task_total=len(tasks),
            pages=first.pages,
            detail_total=first.detail_top,
            message="登录检查中…",
        )
        job["_task_ids"] = [t.task_id for t in tasks]
        self._publish(job)
        threading.Thread(
            target=self._run_job,
            args=(job, tasks, batch, resume, auth_first),
            daemon=True,
        ).start()
        return job

    def _auth_statuses(self, tasks: list[Task]) -> list[dict]:
        out = []
        for t in tasks:
            ok = is_authorized(t.platform)
            out.append({
                "task_id": t.task_id,
                "name": t.name,
                "platform": t.platform,
                "status": "authorized" if ok else "waiting",
                "reason": None,
            })
        return out

    def _set_auth(self, job: dict, platform: str, status: str, reason: str | None = None) -> None:
        for s in job.get("auth_statuses") or []:
            if s["platform"] == platform:
                s["status"] = status
                s["reason"] = reason
                label = s["name"].split(" · ")[-1] if " · " in s["name"] else s["name"]
                if status == "logging_in":
                    job["message"] = f"正在打开{label}登录窗口，请扫码…"
                elif status == "authorized":
                    job["message"] = f"{label} 已授权"
                elif status == "unauthorized":
                    job["message"] = f"{label} 未授权：{reason or '请重新登录'}"
        self._publish(job)

    def _login_platform(self, platform: str) -> tuple[bool, str]:
        """打开单平台登录窗口，等待登录完成（成功/关闭/超时）。"""
        from bkr.browser import browser_runtime, close_context, launch_context, wait_for_login

        try:
            with browser_runtime() as p:
                ctx = launch_context(
                    p, profile_dir=profile_dir_for(platform), kill_stale=True
                )
                try:
                    ok, reason = wait_for_login(ctx, login_site(platform))
                    if ok:
                        try:
                            ctx.storage_state(path=str(state_path_for(platform)))
                        except Exception:
                            pass
                    return ok, reason
                finally:
                    close_context(ctx)
                    # 给浏览器进程一点时间释放 profile 与落盘 cookie
                    time.sleep(1.5)
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"

    def _ensure_state(self, platform: str) -> None:
        """已登录但缺少 storage_state 时，headless 打开一次导出（不发网络请求）。"""
        path = state_path_for(platform)
        if path.exists() or not is_authorized(platform):
            return
        try:
            from bkr.browser import browser_runtime, close_context, launch_context

            with browser_runtime() as p:
                ctx = launch_context(
                    p, headless=True, profile_dir=profile_dir_for(platform), kill_stale=True
                )
                try:
                    ctx.storage_state(path=str(path))
                finally:
                    close_context(ctx)
            print(f"[job] 已为 {platform} 导出登录态：{path.name}")
        except Exception as exc:  # noqa: BLE001
            print(f"[job] 导出 {platform} 登录态失败：{exc}")

    def _run_job(
        self,
        job: dict,
        tasks: list[Task],
        batch: str,
        resume: bool,
        auth_first: str | None,
    ) -> None:
        try:
            job["auth_statuses"] = self._auth_statuses(tasks)
            self._publish(job)

            for task in tasks:
                self._ensure_state(task.platform)

            missing = [t for t in tasks if not is_authorized(t.platform)]
            if auth_first:
                missing.sort(key=lambda t: 0 if t.platform == auth_first else 1)

            for task in missing:
                if is_authorized(task.platform):
                    self._set_auth(job, task.platform, "authorized")
                    continue
                self._set_auth(job, task.platform, "logging_in")
                ok, reason = self._login_platform(task.platform)
                if ok:
                    self._set_auth(job, task.platform, "authorized")
                else:
                    msg = {"closed": "浏览器窗口被关闭", "timeout": "登录超时"}.get(
                        reason, reason
                    )
                    self._set_auth(job, task.platform, "unauthorized", msg)
                    break

            job["auth_statuses"] = self._auth_statuses(tasks)
            if not all(is_authorized(t.platform) for t in tasks):
                pending = [
                    s["name"].split(" · ")[-1]
                    for s in job["auth_statuses"]
                    if s["status"] != "authorized"
                ]
                job.update(
                    phase="auth",
                    status="auth_required",
                    message="请完成登录后再继续：" + "、".join(pending),
                )
                self._publish(job)
                return

            job.update(phase="collect", status="running", message="登录完成，开始采集…")
            self._publish(job)
            self._collect_phase(job, tasks, batch, resume)
        finally:
            job["finished_at"] = _now()
            self._publish(job)
            self._run_lock.release()

    def _collect_phase(
        self, job: dict, tasks: list[Task], batch: str, resume: bool = False
    ) -> None:
        """并行 worker 采集阶段（调用方负责锁与 finished_at）。"""
        job["phase"] = "collect"
        stop = threading.Event()

        def snapshot() -> list[dict]:
            conn = db.connect()
            try:
                out = []
                for t in tasks:
                    row = db.get_batch(conn, batch, t.task_id) or {}
                    status = row.get("status") or "waiting"
                    if status not in ("running", "paused_captcha", "failed", "done"):
                        status = "waiting"
                    out.append({
                        "task_id": t.task_id,
                        "name": t.name,
                        "platform": t.platform,
                        "status": status,
                        "stage": row.get("stage") or "waiting",
                        "page_done": int(row.get("page_done") or 0),
                        "pages": t.pages,
                        "items_count": int(row.get("items_count") or 0),
                        "detail_done": int(row.get("detail_done") or 0),
                        "detail_total": t.detail_top,
                        "error": row.get("error"),
                    })
                return out
            finally:
                conn.close()

        def aggregate(statuses: list[dict]) -> str:
            states = [s["status"] for s in statuses]
            if any(s == "paused_captcha" for s in states):
                return "paused_captcha"
            if any(s in ("running", "waiting") for s in states):
                return "running"
            if any(s == "failed" for s in states):
                return "failed"
            if states and all(s == "done" for s in states):
                return "done"
            return "running"

        def message(statuses: list[dict]) -> str:
            parts = []
            for s in statuses:
                label = s["name"].split(" · ")[-1] if " · " in s["name"] else s["name"]
                st = s["status"]
                if st == "running" and s["stage"] == "detail":
                    parts.append(f"{label} 详情 {s['detail_done']}/{s['detail_total']}")
                elif st == "running":
                    parts.append(f"{label} 列表 {s['page_done']}/{s['pages']}")
                else:
                    parts.append(f"{label} " + {
                        "waiting": "等待", "paused_captcha": "待人工处理",
                        "failed": "失败", "done": "已完成",
                    }.get(st, st))
            return "；".join(parts)

        def apply_statuses(statuses: list[dict]) -> None:
            job["platforms_status"] = statuses
            job["status"] = aggregate(statuses)
            job["error"] = next((s["error"] for s in statuses if s["error"]), None)
            job["message"] = message(statuses)
            current = next(
                (s for s in statuses if s["status"] in ("running", "paused_captcha")),
                statuses[0] if statuses else None,
            )
            if current:
                job.update(
                    task_id=current["task_id"],
                    task_name=current["name"],
                    task_platform=current["platform"],
                )
            self._publish(job)

        def monitor() -> None:
            last = None
            while not stop.wait(2.0):
                statuses = snapshot()
                if statuses != last:
                    last = statuses
                    apply_statuses(statuses)

        mt = threading.Thread(target=monitor, daemon=True)
        mt.start()
        procs: list[subprocess.Popen] = []
        try:
            stagger = float(os.environ.get("BK_PARALLEL_STAGGER", "75"))
            for idx, task in enumerate(tasks):
                if idx > 0 and stagger > 0:
                    print(f"[job] 错峰等待 {stagger:.0f}s 后启动 {task.name}")
                    time.sleep(stagger)
                env = os.environ.copy()
                env["BK_PROFILE"] = task.platform
                cmd = [
                    sys.executable, "-m", "bkr.worker",
                    "--task-id", task.task_id, "--batch", batch,
                ]
                if resume:
                    cmd.append("--resume")
                print(f"[job] 启动 worker：{task.name}（profile={task.platform}）")
                procs.append(subprocess.Popen(cmd, cwd=str(ROOT), env=env))
            for proc in procs:
                proc.wait()
        except Exception as exc:  # noqa: BLE001
            job["status"] = "failed"
            job["error"] = f"{type(exc).__name__}: {exc}"
            job["message"] = job["error"]
        finally:
            stop.set()
            mt.join(timeout=3)
            statuses = snapshot()
            apply_statuses(statuses)
            if job["status"] == "running":
                job["status"] = "failed"
                job["error"] = job.get("error") or "worker 提前退出"
                job["message"] = job["error"]
            self._publish(job)

    # ---------- 登录（账号管理 / 页面右上角按钮） ----------

    def start_login(self, profile: str | None = None, site: str = "taobao") -> dict:
        if not self._run_lock.acquire(blocking=False):
            raise BusyError("已有一个采集/登录任务在运行，请等待完成")
        job = self._new_job(
            type="login",
            phase="login",
            status="waiting",
            profile=profile,
            site=site,
            message="请在打开的浏览器里扫码登录",
        )
        self._publish(job)
        threading.Thread(target=self._run_login, args=(job,), daemon=True).start()
        return job

    def _run_login(self, job: dict) -> None:
        try:
            from bkr.browser import browser_runtime, close_context, launch_context, wait_for_login

            profile = job.get("profile")
            site = job.get("site") or "taobao"
            pdir = profile_dir_for(profile)

            with browser_runtime() as p:
                ctx = launch_context(p, profile_dir=pdir, kill_stale=True)
                try:
                    ok, reason = wait_for_login(ctx, site)
                    if ok:
                        try:
                            ctx.storage_state(path=str(state_path_for(profile)))
                        except Exception:
                            pass
                        job["status"] = "done"
                        job["message"] = f"{site} 登录成功"
                    else:
                        job["status"] = "timeout" if reason == "timeout" else "failed"
                        job["message"] = {
                            "closed": f"{site} 登录窗口被关闭",
                            "timeout": f"{site} 登录超时，请重试",
                        }.get(reason, f"登录失败：{reason}")
                finally:
                    close_context(ctx)
        except Exception as exc:  # noqa: BLE001
            job["status"] = "failed"
            job["error"] = f"{type(exc).__name__}: {exc}"
            job["message"] = job["error"]
        finally:
            job["finished_at"] = _now()
            self._publish(job)
            self._run_lock.release()


manager = JobManager()