"""로컬 웹 GUI 서버 (표준 라이브러리만 사용).

브라우저의 파일 선택창은 절대경로를 주지 않으므로, 서버측 파일 브라우저
(/api/fs) 로 영상 파일을 고르게 한다.
"""

import json
import mimetypes
import os
import posixpath
import re
import subprocess
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import config, ffmpeg_tools, jobs, pipeline, store

Handler = Callable[["Req"], Any]
_ROUTES: List[Tuple[str, "re.Pattern", Handler]] = []


class Req:
    def __init__(self, handler: "App", method: str, path: str, query: Dict[str, List[str]], match, body: Any):
        self.h = handler
        self.method = method
        self.path = path
        self.query = query
        self.m = match
        self.body = body or {}

    def q(self, name: str, default=None):
        v = self.query.get(name)
        return v[0] if v else default


def route(method: str, pattern: str):
    def deco(fn: Handler):
        _ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn

    return deco


class ApiError(Exception):
    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- API


@route("GET", "/api/doctor")
def _doctor(r: Req):
    d = ffmpeg_tools.doctor()
    d["data_dir"] = config.DATA_DIR
    d["home"] = os.path.expanduser("~")
    return d


@route("GET", "/api/fs")
def _fs(r: Req):
    path = r.q("path") or os.path.expanduser("~")
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        path = os.path.dirname(path) or "/"
    dirs, files = [], []
    try:
        for name in sorted(os.listdir(path), key=lambda s: s.lower()):
            if name.startswith("."):
                continue
            full = os.path.join(path, name)
            try:
                if os.path.isdir(full):
                    dirs.append({"name": name, "path": full})
                elif os.path.splitext(name)[1].lower() in config.VIDEO_EXTS:
                    st = os.stat(full)
                    files.append({"name": name, "path": full, "size": st.st_size, "mtime": st.st_mtime})
            except OSError:
                continue
    except PermissionError:
        raise ApiError("접근 권한이 없는 폴더입니다: %s" % path, 403)
    shortcuts = [
        {"name": "홈", "path": os.path.expanduser("~")},
        {"name": "다운로드", "path": os.path.expanduser("~/Downloads")},
        {"name": "동영상", "path": os.path.expanduser("~/Movies")},
        {"name": "데스크탑", "path": os.path.expanduser("~/Desktop")},
    ]
    return {
        "path": path,
        "parent": os.path.dirname(path) if path != "/" else None,
        "dirs": dirs,
        "files": files,
        "shortcuts": [s for s in shortcuts if os.path.isdir(s["path"])],
    }


@route("GET", "/api/projects")
def _projects(r: Req):
    return {"projects": store.list_projects()}


@route("POST", "/api/projects")
def _create(r: Req):
    b = r.body
    video = os.path.abspath(os.path.expanduser((b.get("video_path") or "").strip()))
    if not video or not os.path.isfile(video):
        raise ApiError("영상 파일을 찾을 수 없습니다: %s" % video)
    name = (b.get("name") or os.path.splitext(os.path.basename(video))[0]).strip()
    proj = store.new_project(name, video, (b.get("youtube_url") or "").strip(),
                             (b.get("chzzk_url") or "").strip())
    try:
        pipeline.probe_project(proj)
        proj = store.load(proj["id"])
    except Exception as e:  # noqa: BLE001
        proj["probe_error"] = str(e)
        store.save(proj)
    return {"project": proj}


@route("GET", r"/api/projects/([\w가-힣-]+)")
def _get(r: Req):
    pid = r.m.group(1)
    if not store.exists(pid):
        raise ApiError("프로젝트가 없습니다", 404)
    proj = store.load(pid)
    proj["has_signals"] = os.path.exists(pipeline.signals_path(pid))
    return {"project": proj}


@route("POST", r"/api/projects/([\w가-힣-]+)/delete")
def _delete(r: Req):
    store.delete(r.m.group(1))
    return {"ok": True}


@route("POST", r"/api/projects/([\w가-힣-]+)/settings")
def _settings(r: Req):
    pid = r.m.group(1)
    proj = store.load(pid)
    b = r.body
    for key in ("name", "youtube_url", "chzzk_url"):
        if b.get(key) is not None:
            proj[key] = b[key]
    for key in ("mic_stream", "mix_stream"):
        if b.get(key) is not None:
            proj[key] = int(b[key])
    if b.get("chzzk_offset_sec") is not None:
        proj["chzzk_offset_sec"] = float(b["chzzk_offset_sec"])
        proj["chzzk_offset_auto"] = False        # 손으로 정했으면 자동계산이 덮지 않는다
        proj["chzzk_offset_source"] = "수동 지정"
    if b.get("offset_sec") is not None:
        proj["offset_sec"] = float(b["offset_sec"])
        proj["offset_source"] = "수동 지정"
    for key in ("weights", "detect", "export"):
        if isinstance(b.get(key), dict):
            proj[key].update(b[key])
    store.save(proj)
    return {"project": proj}


@route("POST", r"/api/projects/([\w가-힣-]+)/analyze")
def _analyze(r: Req):
    pid = r.m.group(1)
    proj = store.load(pid)
    b = r.body
    if b.get("mic_stream") is not None:
        proj["mic_stream"] = int(b["mic_stream"])
    if b.get("mix_stream") is not None:
        proj["mix_stream"] = int(b["mix_stream"])
    if b.get("youtube_url") is not None:
        proj["youtube_url"] = b["youtube_url"].strip()
    if b.get("chzzk_url") is not None:
        proj["chzzk_url"] = b["chzzk_url"].strip()
    store.save(proj)
    do_chat = bool(b.get("do_chat", True)) and bool(
        proj.get("youtube_url") or proj.get("chzzk_url"))
    do_audio = bool(b.get("do_audio", True))
    job = jobs.spawn(
        "분석",
        lambda p: pipeline.analyze(pid, do_chat, do_audio, bool(b.get("auto_align", True)), p),
        pid,
    )
    return {"job": _job_view(job)}


@route("POST", r"/api/projects/([\w가-힣-]+)/refine")
def _refine(r: Req):
    return {"refine": pipeline.refine(r.m.group(1))}


@route("POST", r"/api/projects/([\w가-힣-]+)/detect")
def _detect(r: Req):
    pid = r.m.group(1)
    segs = pipeline.detect(pid, r.body.get("detect"), r.body.get("weights"))
    return {"segments": segs}


@route("GET", r"/api/projects/([\w가-힣-]+)/curves")
def _curves(r: Req):
    width = int(r.q("width", 1600) or 1600)
    return pipeline.curves_for_plot(r.m.group(1), max(200, min(width, 6000)))


@route("POST", r"/api/projects/([\w가-힣-]+)/segments")
def _segments(r: Req):
    pid = r.m.group(1)
    proj = store.load(pid)
    updates = {int(u["id"]): u for u in (r.body.get("segments") or [])}
    for s in proj.get("segments") or []:
        u = updates.get(s["id"])
        if not u:
            continue
        if "selected" in u:
            s["selected"] = bool(u["selected"])
        if "title" in u:
            s["title"] = str(u["title"])[:80]
        for k in ("start", "end"):
            if k in u:
                s[k] = max(0.0, float(u[k]))
        s["dur"] = round(s["end"] - s["start"], 1)
    store.save(proj)
    return {"ok": True, "segments": proj["segments"]}


@route("POST", r"/api/projects/([\w가-힣-]+)/export")
def _export(r: Req):
    pid = r.m.group(1)
    kind = r.body.get("kind")
    if kind == "xml":
        mode = r.body.get("mode", "markers")
        job = jobs.spawn("XML(%s)" % mode,
                         lambda p: pipeline.export_xml(pid, mode, bool(r.body.get("remux", True)), p), pid)
    elif kind == "previews":
        ids = r.body.get("seg_ids")
        job = jobs.spawn("미리보기 클립", lambda p: pipeline.export_previews(pid, ids, p), pid)
    elif kind == "shorts":
        ids = r.body.get("seg_ids")
        job = jobs.spawn("숏츠", lambda p: pipeline.export_shorts(pid, ids, p), pid)
    else:
        raise ApiError("알 수 없는 익스포트 종류: %s" % kind)
    return {"job": _job_view(job)}


@route("GET", "/api/jobs")
def _jobs(r: Req):
    return {"jobs": [_job_view(j) for j in jobs.list_for(r.q("project"))]}


@route("GET", r"/api/jobs/(\w+)")
def _job(r: Req):
    j = jobs.get(r.m.group(1))
    if not j:
        raise ApiError("작업이 없습니다", 404)
    return {"job": _job_view(j)}


@route("POST", "/api/reveal")
def _reveal(r: Req):
    path = os.path.abspath(os.path.expanduser(r.body.get("path") or ""))
    if not os.path.exists(path):
        raise ApiError("경로가 없습니다: %s" % path)
    args = ["open", "-R", path] if os.path.isfile(path) else ["open", path]
    try:
        subprocess.Popen(args)
    except Exception as e:  # noqa: BLE001
        raise ApiError("Finder 열기 실패: %s" % e)
    return {"ok": True}


def _job_view(j: Dict[str, Any]) -> Dict[str, Any]:
    return {k: j.get(k) for k in ("id", "name", "project", "status", "progress", "message", "result", "error", "log")}


# --------------------------------------------------------------------------- HTTP


class App(BaseHTTPRequestHandler):
    server_version = "livehl"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 조용하게
        if os.environ.get("LIVEHL_VERBOSE"):
            super().log_message(fmt, *args)

    # ---- helpers
    def _send(self, code: int, body: bytes, ctype: str, extra: Optional[Dict[str, str]] = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj: Any, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=_default).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _static(self, rel: str):
        rel = posixpath.normpath("/" + rel).lstrip("/")
        full = os.path.join(config.WEB_DIR, rel)
        if not full.startswith(config.WEB_DIR) or not os.path.isfile(full):
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            data = f.read()
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self._send(200, data, ctype)

    def _media(self, pid: str, rel: str):
        base = os.path.abspath(store.project_dir(pid))
        full = os.path.abspath(os.path.join(base, urllib.parse.unquote(rel)))
        if not full.startswith(base) or not os.path.isfile(full):
            return self._send(404, b"not found", "text/plain")
        size = os.path.getsize(full)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        code = 200
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
                if start >= size:
                    return self._send(416, b"", "text/plain", {"Content-Range": "bytes */%d" % size})
                code = 206
        length = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(full, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)

    # ---- dispatch
    def _handle(self, method: str):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)

        if method in ("GET", "HEAD"):
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            m = re.match(r"^/media/([\w가-힣-]+)/(.+)$", path)
            if m:
                return self._media(m.group(1), m.group(2))

        body: Any = None
        if method == "POST":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                body = {}

        for meth, pat, fn in _ROUTES:
            if meth != method:
                continue
            mm = pat.match(path)
            if mm:
                try:
                    return self._json(fn(Req(self, method, path, query, mm, body)))
                except ApiError as e:
                    return self._json({"error": str(e)}, e.code)
                except FileNotFoundError as e:
                    return self._json({"error": "파일 없음: %s" % e}, 404)
                except Exception as e:  # noqa: BLE001
                    import traceback

                    if os.environ.get("LIVEHL_VERBOSE"):
                        traceback.print_exc()
                    return self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)
        self._json({"error": "not found: %s" % path}, 404)

    def do_GET(self):
        self._handle("GET")

    def do_HEAD(self):
        self._handle("HEAD")

    def do_POST(self):
        self._handle("POST")


def _default(o):
    try:
        import numpy as np

        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
    except Exception:
        pass
    return str(o)


def serve(host: Optional[str] = None, port: Optional[int] = None, open_browser: bool = True) -> None:
    host = host or config.SERVER_HOST
    port = int(port or config.SERVER_PORT)
    os.makedirs(config.DATA_DIR, exist_ok=True)
    try:
        httpd = ThreadingHTTPServer((host, port), App)
    except OSError as e:
        if getattr(e, "errno", None) == 48:
            print("포트 %d 가 이미 사용 중입니다. 이미 livehl 이 켜져 있다면 브라우저에서" % port)
            print("  http://%s:%d/ 을 열면 됩니다." % (host, port))
            print("다른 포트로 띄우려면:  ./start.sh --port %d" % (port + 1))
            raise SystemExit(1)
        raise
    url = "http://%s:%d/" % (host, port)
    d = ffmpeg_tools.doctor()
    print("livehl 서버 시작: %s" % url)
    print("  ffmpeg : %s" % (d["ffmpeg"] or "❌ 없음 — brew install ffmpeg"))
    print("  ffprobe: %s" % (d["ffprobe"] or "없음(ffmpeg 파싱으로 대체)"))
    print("  yt-dlp : %s" % (d["yt_dlp"] or "❌ 없음"))
    print("  데이터  : %s" % config.DATA_DIR)
    print("종료: Ctrl+C")
    if open_browser:
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        httpd.server_close()
