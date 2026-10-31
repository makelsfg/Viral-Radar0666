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
from bkr.config import DATA_DIR, ROOT, Task, profile_dir_for, state_path_for


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


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """结束 worker 及其子进程（Windows 用 taskkill /T 连浏览器一起结束）。"""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=15,
            )
        else:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
    except Exception:
        pass


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self._registry_lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._cancels: dict[str, threading.Event] = {}
        self._procs: dict[str, list[subprocess.Popen]] = {}
        self._active_jobs: set[str] = set()

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
        # 先把任务写入 dim_task：worker 是独立子进程，启动时要按 task_id 从库里加载
        try:
            conn = db.connect()
            try:
                for t in tasks:
                    db.upsert_task(conn, t)
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            print(f"[job] 写入任务配置失败：{exc}")
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
        self._cancels[job["id"]] = threading.Event()
        self._procs[job["id"]] = []
        self._active_jobs.add(job["id"])
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
                    job["message"] = f"正在打开{label}登录窗口，请完成登录…"
                elif status == "authorized":
                    job["message"] = f"{label} 已授权"
                elif status == "unauthorized":
                    job["message"] = f"{label} 未授权：{reason or '请重新登录'}"
        try:
            conn = db.connect()
            try:
                db.set_platform_auth(conn, platform, status == "authorized", reason)
            finally:
                conn.close()
        except Exception:
            pass
        self._publish(job)

    def _login_platform(self, platform: str, cancel_event: threading.Event | None = None) -> tuple[bool, str]:
        """打开单平台登录窗口，等待登录完成（成功/关闭/超时/取消）。"""
        from bkr.browser import browser_runtime, close_context, launch_context, wait_for_login

        if cancel_event is not None and cancel_event.is_set():
            return False, "cancelled"
        if platform == "pdd":
            from bkr.providers import pdd_ddk

            if not pdd_ddk.configured():
                return False, pdd_ddk.config_hint()
            return True, "ok"
        try:
            with browser_runtime() as p:
                ctx = launch_context(
                    p, profile_dir=profile_dir_for(platform), kill_stale=True,
                    platform=platform,
                )
                try:
                    ok, reason = wait_for_login(
                        ctx, login_site(platform), cancel_event=cancel_event
                    )
                    if ok:
                        try:
                            ctx.storage_state(path=str(state_path_for(platform)))
                        except Exception:
                            pass
                finally:
                    close_context(ctx)
                    # 给浏览器进程一点时间释放 profile 与落盘 cookie
                    time.sleep(1.5)
            # 拼多多登录成功后可能关窗/停在 login.html：关窗后以落盘 cookie 为准再判一次
            if cancel_event is not None and cancel_event.is_set():
                return False, "cancelled"
            if not ok and is_authorized(platform):
                ok, reason = True, "ok"
                print(f"[job] {platform} 浏览器已关闭，但检测到有效登录 cookie，按登录成功处理")
            return ok, reason
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"

    def _ensure_state(self, platform: str) -> None:
        """已登录但缺少 storage_state 时，headless 打开一次导出（不发网络请求）。"""
        if platform == "pdd":
            return
        path = state_path_for(platform)
        if path.exists() or not is_authorized(platform):
            return
        try:
            from bkr.browser import browser_runtime, close_context, launch_context

            with browser_runtime() as p:
                ctx = launch_context(
                    p, headless=True, profile_dir=profile_dir_for(platform), kill_stale=True,
                    platform=platform,
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
        cancel = self._cancels.get(job["id"]) or threading.Event()
        try:
            job["auth_statuses"] = self._auth_statuses(tasks)
            for s in job["auth_statuses"]:
                try:
                    conn = db.connect()
                    try:
                        db.set_platform_auth(conn, s["platform"], s["status"] == "authorized")
                    finally:
                        conn.close()
                except Exception:
                    pass
            self._publish(job)

            for task in tasks:
                if cancel.is_set():
                    break
                self._ensure_state(task.platform)

            missing = [t for t in tasks if not is_authorized(t.platform)]
            if auth_first:
                missing.sort(key=lambda t: 0 if t.platform == auth_first else 1)

            for task in missing:
                if cancel.is_set():
                    break
                if is_authorized(task.platform):
                    self._set_auth(job, task.platform, "authorized")
                    continue
                self._set_auth(job, task.platform, "logging_in")
                ok, reason = self._login_platform(task.platform, cancel)
                if ok:
                    self._set_auth(job, task.platform, "authorized")
                elif reason == "cancelled":
                    break
                else:
                    msg = {"closed": "浏览器窗口被关闭", "timeout": "登录超时"}.get(
                        reason, reason
                    )
                    self._set_auth(job, task.platform, "unauthorized", msg)
                    break

            if cancel.is_set():
                job.update(status="cancelled", message="已结束本次采集（用户取消）")
                self._publish(job)
                return

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
            self._cancels.pop(job["id"], None)
            self._procs.pop(job["id"], None)
            self._active_jobs.discard(job["id"])

    def _collect_phase(
        self, job: dict, tasks: list[Task], batch: str, resume: bool = False
    ) -> None:
        """并行 worker 采集阶段（调用方负责锁与 finished_at）。"""
        job["phase"] = "collect"
        stop = threading.Event()
        cancel = self._cancels.get(job["id"]) or threading.Event()

        def snapshot() -> list[dict]:
            conn = db.connect()
            try:
                out = []
                for t in tasks:
                    row = db.get_batch(conn, batch, t.task_id) or {}
                    status = row.get("status") or "waiting"
                    if status not in ("running", "paused_captcha", "failed", "done", "cancelled"):
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
            if cancel.is_set():
                return "cancelled"
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
                        "failed": "失败", "done": "已完成", "cancelled": "已取消",
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
        procs: list[tuple[subprocess.Popen, object, str]] = []
        proc_list: list[subprocess.Popen] = []
        self._procs[job["id"]] = proc_list
        logs_dir = DATA_DIR / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        task_logs: dict[str, str] = {}
        try:
            stagger = float(os.environ.get("BK_PARALLEL_STAGGER", "20"))
            for idx, task in enumerate(tasks):
                if cancel.is_set():
                    break
                if idx > 0 and stagger > 0:
                    print(f"[job] 错峰等待 {stagger:.0f}s 后启动 {task.name}")
                    if cancel.wait(stagger):
                        break
                env = os.environ.copy()
                env["BK_PROFILE"] = task.platform
                env["PYTHONUNBUFFERED"] = "1"
                env["PYTHONIOENCODING"] = "utf-8"
                env["PYTHONUTF8"] = "1"
                cmd = [
                    sys.executable, "-m", "bkr.worker",
                    "--task-id", task.task_id, "--batch", batch,
                ]
                if resume:
                    cmd.append("--resume")
                log_path = logs_dir / f"{batch}_{task.platform}.log"
                log_fh = open(log_path, "w", encoding="utf-8")
                task_logs[task.task_id] = str(log_path)
                print(f"[job] 启动 worker：{task.name}（profile={task.platform}，日志 {log_path}）")
                proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env,
                                        stdout=log_fh, stderr=subprocess.STDOUT)
                procs.append((proc, log_fh, str(log_path)))
                proc_list.append(proc)
            while True:
                alive = [p for p, _, _ in procs if p.poll() is None]
                if not alive:
                    break
                if cancel.is_set():
                    print("[job] 收到取消请求：结束所有采集进程")
                    for proc in alive:
                        _kill_process_tree(proc)
                    break
                time.sleep(0.5)
            for proc, fh, _ in procs:
                try:
                    proc.wait(timeout=5)
                except Exception:
                    _kill_process_tree(proc)
                try:
                    fh.close()
                except Exception:
                    pass
        except Exception as exc:  # noqa: BLE001
            if cancel.is_set():
                job.update(status="cancelled", message="已结束本次采集（用户取消）")
            else:
                job["status"] = "failed"
                job["error"] = f"{type(exc).__name__}: {exc}"
                job["message"] = job["error"]
        finally:
            for _, fh, _ in procs:
                try:
                    fh.close()
                except Exception:
                    pass
            stop.set()
            mt.join(timeout=3)
            statuses = snapshot()
            # 结束时把仍为 running 的任务落状态：取消/失败（带日志路径）
            conn = db.connect()
            try:
                for s in statuses:
                    if s["status"] == "running":
                        log_path = task_logs.get(s["task_id"], "")
                        if cancel.is_set():
                            db.set_batch(
                                conn, batch, s["task_id"], status="cancelled",
                                error="已取消（用户结束任务）",
                            )
                        else:
                            db.set_batch(
                                conn, batch, s["task_id"], status="failed",
                                error=f"worker 异常退出（详见日志 {log_path}）",
                            )
                conn.commit()
            finally:
                conn.close()
            if cancel.is_set():
                # 兜底清理该批次各平台的 Edge 进程，避免取消后浏览器残留
                try:
                    from bkr.browser import kill_profile_browsers
                    from bkr.config import profile_dir_for as _profile_dir_for

                    for t in tasks:
                        kill_profile_browsers(_profile_dir_for(t.platform))
                except Exception:
                    pass
            statuses = snapshot()
            apply_statuses(statuses)
            if cancel.is_set():
                job["status"] = "cancelled"
                job["error"] = None
                job["message"] = "已结束本次采集（用户取消）"
            elif job["status"] == "running":
                job["status"] = "failed"
                job["error"] = job.get("error") or "worker 提前退出"
                job["message"] = job["error"]
            self._publish(job)

    # ---------- 取消 / 丢弃结果 ----------

    def cancel(self, job_id: str) -> tuple[bool, str]:
        job = self.jobs.get(job_id)
        if not job:
            return False, "任务不存在"
        if job.get("status") in ("done", "failed", "cancelled", "timeout"):
            return False, "任务已经结束"
        # 任务线程已经退出（如停在等待登录页）时，没有进程可等，直接置为已取消
        if job_id not in self._active_jobs:
            job.update(status="cancelled", message="已结束本次采集（用户取消）", error=None)
            self._publish(job)
            return True, "已结束"
        event = self._cancels.get(job_id)
        if event is not None:
            event.set()
        job["status"] = "cancelling"
        job["message"] = "正在结束采集并关闭浏览器…"
        self._publish(job)
        # 先结束 worker 进程树（含其启动的浏览器）
        for proc in list(self._procs.get(job_id) or []):
            _kill_process_tree(proc)
        # auth 阶段没有 worker，直接关掉该平台登录窗口，让等待循环退出
        try:
            from bkr.browser import kill_profile_browsers

            platforms = {
                s.get("platform") for s in (job.get("platforms_status") or [])
                if s.get("platform")
            }
            for platform in platforms:
                kill_profile_browsers(profile_dir_for(platform))
        except Exception:
            pass
        return True, "正在结束"

    def discard(self, job_id: str) -> tuple[bool, str]:
        """用户选择“不保存”后删除本次批次的采集结果。"""
        import shutil

        job = self.jobs.get(job_id)
        if not job:
            return False, "任务不存在"
        if job.get("status") not in ("cancelled", "failed"):
            return False, "任务未结束，不能丢弃结果"
        batch = job.get("batch")
        task_ids = job.get("_task_ids") or []
        if not batch or not task_ids:
            return False, "批次信息缺失"
        conn = db.connect()
        try:
            marks = ",".join("?" for _ in task_ids)
            conn.execute(
                f"DELETE FROM fact_snapshot WHERE batch=? AND task_id IN ({marks})",
                (batch, *task_ids),
            )
            conn.execute(
                f"DELETE FROM batch_run WHERE batch=? AND task_id IN ({marks})",
                (batch, *task_ids),
            )
            conn.execute(
                """DELETE FROM product
                   WHERE NOT EXISTS (
                     SELECT 1 FROM fact_snapshot s
                     WHERE s.item_id = product.item_id AND s.platform = product.platform
                   )"""
            )
            conn.commit()
        finally:
            conn.close()
        try:
            remain = db.connect()
            try:
                has = remain.execute(
                    "SELECT 1 FROM fact_snapshot WHERE batch=? LIMIT 1", (batch,)
                ).fetchone()
            finally:
                remain.close()
            if not has:
                shutil.rmtree(DATA_DIR / "raw" / batch, ignore_errors=True)
        except Exception:
            pass
        job["discarded"] = True
        job["message"] = "本次结果已丢弃（未保存）"
        self._publish(job)
        return True, "已丢弃"

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
            message="请在打开的浏览器里完成登录",
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
                ctx = launch_context(p, profile_dir=pdir, kill_stale=True,
                                 platform=(site if site == "pdd" else None))
                try:
                    ok, reason = wait_for_login(ctx, site)
                    if ok:
                        try:
                            ctx.storage_state(path=str(state_path_for(profile)))
                        except Exception:
                            pass
                        job["status"] = "done"
                        job["message"] = f"{site} 登录成功"
                        try:
                            conn = db.connect()
                            try:
                                db.set_platform_auth(conn, profile, True)
                            finally:
                                conn.close()
                        except Exception:
                            pass
                    else:
                        job["status"] = "timeout" if reason == "timeout" else "failed"
                        job["message"] = {
                            "closed": f"{site} 登录窗口被关闭",
                            "timeout": f"{site} 登录超时，请重试",
                        }.get(reason, f"登录失败：{reason}")
                        try:
                            conn = db.connect()
                            try:
                                db.set_platform_auth(conn, profile, False, job["message"])
                            finally:
                                conn.close()
                        except Exception:
                            pass
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