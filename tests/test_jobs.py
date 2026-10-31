"""登录前置编排单测（不启动浏览器）。"""
from bkr.config import Task
import server.jobs as jobs_mod


def _task(platform: str) -> Task:
    label = "天猫" if platform == "tmall" else platform
    return Task(task_id=f"t-{platform}", name=f"测试 · {label}", keywords=["测试"], platform=platform)


def test_auth_statuses_mixed():
    old = jobs_mod.is_authorized
    try:
        jobs_mod.is_authorized = lambda p: p == "tmall"
        mgr = jobs_mod.JobManager()
        statuses = mgr._auth_statuses([_task("tmall"), _task("1688")])
        assert statuses[0]["status"] == "authorized"
        assert statuses[1]["status"] == "waiting"
    finally:
        jobs_mod.is_authorized = old


def test_auth_statuses_all_authorized():
    old = jobs_mod.is_authorized
    try:
        jobs_mod.is_authorized = lambda p: True
        mgr = jobs_mod.JobManager()
        statuses = mgr._auth_statuses([_task("tmall"), _task("1688")])
        assert all(s["status"] == "authorized" for s in statuses)
    finally:
        jobs_mod.is_authorized = old