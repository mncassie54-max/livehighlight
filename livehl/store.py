"""프로젝트(=방송 1회) 상태 저장/로드."""

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from . import config


def _slug(name: str) -> str:
    s = re.sub(r"[^0-9A-Za-z가-힣_-]+", "-", name).strip("-").lower()
    return s[:60] or "project"


def projects_root() -> str:
    os.makedirs(config.DATA_DIR, exist_ok=True)
    return config.DATA_DIR


def project_dir(pid: str) -> str:
    return os.path.join(projects_root(), pid)


def path_in(pid: str, *parts: str) -> str:
    p = os.path.join(project_dir(pid), *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def new_project(name: str, video_path: str, youtube_url: str = "",
                chzzk_url: str = "") -> Dict[str, Any]:
    base = _slug(name)
    pid = base
    n = 2
    while os.path.exists(project_dir(pid)):
        pid = "%s-%d" % (base, n)
        n += 1
    proj: Dict[str, Any] = {
        "id": pid,
        "name": name,
        "video_path": video_path,
        "youtube_url": youtube_url,
        "chzzk_url": chzzk_url,      # 동시송출 시 치지직 다시보기 주소
        "chzzk_offset_sec": 0.0,     # 두 플랫폼 다시보기 시작점 차이 보정
        "created": time.time(),
        "media": None,            # probe 결과
        "mic_stream": None,       # 마이크 오디오 스트림 index (절대 index)
        "mix_stream": None,       # 익스포트에 쓸 오디오 스트림 index
        "video_start_utc": None,  # OBS 녹화 시작 epoch
        "stream_start_utc": None, # 유튜브 방송 시작 epoch
        "offset_sec": 0.0,        # local_t = vod_t + offset_sec
        "offset_source": None,
        "chat_stats": None,
        "audio_stats": None,
        "weights": dict(config.DEFAULT_WEIGHTS),
        "detect": dict(config.DEFAULT_DETECT),
        "export": dict(config.DEFAULT_EXPORT),
        "segments": [],
        "log": [],
    }
    os.makedirs(project_dir(pid), exist_ok=True)
    save(proj)
    return proj


def save(proj: Dict[str, Any]) -> None:
    p = os.path.join(project_dir(proj["id"]), "project.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(proj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def load(pid: str) -> Dict[str, Any]:
    p = os.path.join(project_dir(pid), "project.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def exists(pid: str) -> bool:
    return os.path.exists(os.path.join(project_dir(pid), "project.json"))


def list_projects() -> List[Dict[str, Any]]:
    out = []
    root = projects_root()
    for name in sorted(os.listdir(root)):
        pj = os.path.join(root, name, "project.json")
        if os.path.exists(pj):
            try:
                with open(pj, encoding="utf-8") as f:
                    p = json.load(f)
                out.append(
                    {
                        "id": p["id"],
                        "name": p.get("name"),
                        "video_path": p.get("video_path"),
                        "created": p.get("created"),
                        "duration": (p.get("media") or {}).get("duration"),
                        "n_segments": len(p.get("segments") or []),
                        "has_signals": os.path.exists(os.path.join(root, name, "signals.npz")),
                    }
                )
            except Exception:
                continue
    out.sort(key=lambda x: x.get("created") or 0, reverse=True)
    return out


def delete(pid: str) -> None:
    import shutil

    d = project_dir(pid)
    if os.path.isdir(d):
        shutil.rmtree(d)


def log(proj: Dict[str, Any], msg: str) -> None:
    proj.setdefault("log", []).append({"t": time.time(), "msg": msg})
    proj["log"] = proj["log"][-200:]
