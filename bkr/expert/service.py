"""大佬分析服务：创建运行、后台执行、取消与查询。"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime

from .. import db
from . import store
from .graph import ExpertCancelled, build_graph


class ExpertBusy(RuntimeError):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ExpertService:
    def __init__(self) -> None:
        self._cancels: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    # ---------- 对外 ----------

    def create(
        self,
        title: str,
        keyword: str,
        sources: list[dict],
        max_rounds: int = 3,
        pass_score: int = 85,
    ) -> dict:
        clean = self._validate_sources(sources)
        if not clean:
            raise ValueError("请至少选择一个批次，或上传报告/填写笔记")
        run_id = "ex-" + uuid.uuid4().hex[:10]
        run = store.create_run(
            run_id, title.strip() or "大佬分析", keyword.strip(),
            clean, int(max_rounds), int(pass_score),
        )
        event = threading.Event()
        with self._lock:
            self._cancels[run_id] = event
        thread = threading.Thread(
            target=self._execute, args=(run_id, event), daemon=True
        )
        with self._lock:
            self._threads[run_id] = thread
        thread.start()
        self._publish(run_id, "stage", {"stage": "queued", "message": "任务已创建"})
        return run

    def cancel(self, run_id: str) -> bool:
        run = store.get_run(run_id, with_rounds=False)
        if not run:
            return False
        if run.get("status") in ("done", "needs_human", "failed", "cancelled"):
            return False
        event = self._cancels.get(run_id)
        if event is not None:
            event.set()
        store.update_run(run_id, status="cancelling")
        self._publish(run_id, "stage", {"stage": "cancelling", "message": "正在取消"})
        return True

    def get(self, run_id: str) -> dict | None:
        return store.get_run(run_id)

    def list(self, limit: int = 20) -> list[dict]:
        return store.list_runs(limit)

    def delete(self, run_id: str) -> bool:
        event = self._cancels.get(run_id)
        if event is not None:
            event.set()
        return store.delete_run(run_id)

    # ---------- 内部 ----------

    def _validate_sources(self, sources: list[dict]) -> list[dict]:
        out = []
        batch_count = 0
        chars = 0
        for src in sources or []:
            kind = str(src.get("type") or "").strip()
            if kind == "batch":
                task_id = str(src.get("task_id") or "").strip()
                batch = str(src.get("batch") or "").strip()
                if not task_id or not batch:
                    continue
                batch_count += 1
                if batch_count > 10:
                    raise ValueError("批次最多选择 10 个")
                out.append({"type": "batch", "task_id": task_id, "batch": batch})
            elif kind in ("report", "note"):
                content = str(src.get("content") or "").strip()
                if not content:
                    continue
                if len(content) > 200_000:
                    raise ValueError("单个报告/笔记不能超过 20 万字符")
                chars += len(content)
                if chars > 200_000:
                    raise ValueError("报告与笔记合计不能超过 20 万字符")
                out.append({
                    "type": kind,
                    "name": str(src.get("name") or ("上传报告" if kind == "report" else "手动笔记"))[:120],
                    "content": content,
                })
        return out

    def _execute(self, run_id: str, cancel_event: threading.Event) -> None:
        run = store.get_run(run_id, with_rounds=False)
        if not run:
            return
        try:
            graph = build_graph(run_id, lambda e, p: self._publish(run_id, e, p), cancel_event)
            state = {
                "run_id": run_id,
                "title": run["title"],
                "keyword": run.get("keyword") or "",
                "sources": run.get("sources") or [],
                "max_rounds": int(run.get("max_rounds") or 3),
                "pass_score": int(run.get("pass_score") or 85),
                "iteration": 1,
                "history": [],
                "best_score": -1,
                "best_draft_md": "",
                "draft_md": "",
                "review": None,
                "status": "running",
                "error": None,
            }
            result = graph.invoke(state, config={"recursion_limit": 30})
            final = store.get_run(run_id, with_rounds=False) or {}
            if final.get("status") == "running":
                status = "done" if result.get("status") == "done" else "failed"
                store.update_run(
                    run_id, status=status,
                    final_report_md=final.get("final_report_md") or result.get("draft_md"),
                )
        except ExpertCancelled:
            store.update_run(run_id, status="cancelled", error="用户已取消")
            self._publish(run_id, "done", {"status": "cancelled", "message": "已取消"})
        except Exception as exc:  # noqa: BLE001
            msg = self._humanize(exc)
            store.update_run(run_id, status="failed", error=msg)
            self._publish(run_id, "error", {"status": "failed", "message": msg})
        finally:
            store.update_run(run_id, finished_at=_now())
            with self._lock:
                self._threads.pop(run_id, None)
                self._cancels.pop(run_id, None)
            self._publish(run_id, "stage", {"stage": "finished"})

    def _humanize(self, exc: Exception) -> str:
        msg = f"{type(exc).__name__}: {exc}"
        if "API Key" in msg or "api_key" in msg or "未配置" in msg:
            return msg if "settings.yaml" in msg else msg + "（到 config/settings.yaml 的 expert.writer / expert.reviewer 配置）"
        if "langchain" in msg or "langgraph" in msg:
            return msg + "（先执行 pip install -r requirements.txt）"
        if "connection" in msg.lower() or "timeout" in msg.lower():
            return msg + "（检查模型服务地址与网络）"
        return msg

    def _publish(self, run_id: str, event: str, payload: dict) -> None:
        from server.jobs import bus

        run = store.get_run(run_id, with_rounds=False) or {}
        summary = {
            "id": run_id,
            "title": run.get("title"),
            "keyword": run.get("keyword"),
            "status": run.get("status"),
            "iteration": run.get("iterations") or 0,
            "max_rounds": run.get("max_rounds"),
            "pass_score": run.get("pass_score"),
            "error": run.get("error"),
        }
        bus.publish({"type": "expert", "run": summary, "event": event, **payload})


service = ExpertService()