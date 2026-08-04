"""백그라운드 작업 관리 (GUI 진행률 폴링용)."""

import threading
import time
import traceback
import uuid
from typing import Any, Callable, Dict, List, Optional

_LOCK = threading.Lock()
_JOBS: Dict[str, Dict[str, Any]] = {}


def create(name: str, project: Optional[str] = None) -> Dict[str, Any]:
    jid = uuid.uuid4().hex[:12]
    job = {
        "id": jid,
        "name": name,
        "project": project,
        "status": "running",
        "progress": 0.0,
        "message": "시작…",
        "result": None,
        "error": None,
        "created": time.time(),
        "finished": None,
        "log": [],
    }
    with _LOCK:
        _JOBS[jid] = job
        # 오래된 작업 정리
        if len(_JOBS) > 60:
            old = sorted(_JOBS.values(), key=lambda j: j["created"])[:20]
            for o in old:
                if o["status"] in ("done", "error"):
                    _JOBS.pop(o["id"], None)
    return job


def get(jid: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        return _JOBS.get(jid)


def list_for(project: Optional[str] = None) -> List[Dict[str, Any]]:
    with _LOCK:
        jobs = list(_JOBS.values())
    if project:
        jobs = [j for j in jobs if j.get("project") == project]
    return sorted(jobs, key=lambda j: -j["created"])


def spawn(name: str, fn: Callable[[Callable[[float, str], None]], Any], project: Optional[str] = None) -> Dict[str, Any]:
    """fn(progress) 을 스레드에서 실행. progress(frac, message)."""
    job = create(name, project)

    def report(frac: float, message: str = "") -> None:
        job["progress"] = max(0.0, min(1.0, float(frac)))
        if message:
            job["message"] = message
            if not job["log"] or job["log"][-1] != message:
                job["log"].append(message)
                job["log"] = job["log"][-60:]

    def runner() -> None:
        try:
            job["result"] = fn(report)
            job["progress"] = 1.0
            job["status"] = "done"
            job["message"] = "완료"
        except Exception as e:  # noqa: BLE001
            job["status"] = "error"
            job["error"] = "%s: %s" % (type(e).__name__, e)
            job["message"] = job["error"]
            job["traceback"] = traceback.format_exc()
        finally:
            job["finished"] = time.time()

    t = threading.Thread(target=runner, name="livehl-" + name, daemon=True)
    t.start()
    return job
