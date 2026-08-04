"""ffmpeg / ffprobe 탐색 및 미디어 정보 추출.

Homebrew 가 없는 환경도 지원한다:
  1) 환경변수 LIVEHL_FFMPEG / LIVEHL_FFPROBE
  2) PATH
  3) /opt/homebrew/bin, /usr/local/bin, /usr/bin
  4) pip 패키지 imageio-ffmpeg 에 번들된 정적 바이너리 (ffmpeg 전용)

ffprobe 가 없으면 `ffmpeg -i` 의 stderr 를 파싱해서 같은 정보를 얻는다.
"""

import functools
import json
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional

_SEARCH_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", os.path.expanduser("~/bin"))


class FFmpegMissing(RuntimeError):
    pass


@functools.lru_cache(maxsize=None)
def find_bin(name: str) -> Optional[str]:
    env = os.environ.get("LIVEHL_" + name.upper())
    if env and os.path.exists(env):
        return env
    p = shutil.which(name)
    if p:
        return p
    for d in _SEARCH_DIRS:
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    if name == "ffmpeg":
        try:
            import imageio_ffmpeg  # type: ignore

            cand = imageio_ffmpeg.get_ffmpeg_exe()
            if cand and os.path.exists(cand):
                return cand
        except Exception:
            pass
    return None


def ffmpeg() -> str:
    p = find_bin("ffmpeg")
    if not p:
        raise FFmpegMissing(
            "ffmpeg 를 찾을 수 없습니다. `brew install ffmpeg` 또는 "
            "`.venv/bin/pip install imageio-ffmpeg` 로 설치하세요."
        )
    return p


def ffprobe() -> Optional[str]:
    return find_bin("ffprobe")


def doctor() -> Dict[str, Any]:
    """GUI 의 환경 점검 패널용."""
    fm, fp = find_bin("ffmpeg"), find_bin("ffprobe")
    ver = None
    if fm:
        try:
            out = subprocess.run([fm, "-version"], capture_output=True, text=True, timeout=20).stdout
            ver = out.splitlines()[0] if out else None
        except Exception:
            pass
    try:
        import yt_dlp  # type: ignore

        ytdlp = yt_dlp.version.__version__
    except Exception:
        ytdlp = None
    return {
        "ffmpeg": fm,
        "ffmpeg_version": ver,
        "ffprobe": fp,
        "ffprobe_note": None if fp else "ffprobe 없음 → ffmpeg 출력 파싱으로 대체 (정상 동작)",
        "yt_dlp": ytdlp,
        "ok": bool(fm),
    }


def run(args: List[str], timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


# --------------------------------------------------------------------------- probe

_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")
_STREAM_RE = re.compile(
    r"Stream #0:(\d+)(?:\[[^\]]*\])?(?:\(([^)]*)\))?:\s*(Video|Audio|Subtitle):\s*([^,\n]+)"
)
_RES_RE = re.compile(r"(?<![\d])(\d{2,5})x(\d{2,5})(?![\d])")
_FPS_RE = re.compile(r"([\d.]+)\s*fps")
_HZ_RE = re.compile(r"(\d+)\s*Hz")
_TITLE_RE = re.compile(r"^\s+title\s*:\s*(.+)$")

_LAYOUT_CH = {"mono": 1, "stereo": 2, "2.1": 3, "quad": 4, "5.0": 5, "5.1": 6, "7.1": 8}


def _parse_ffmpeg_stderr(text: str) -> Dict[str, Any]:
    info: Dict[str, Any] = {"duration": None, "fps": None, "width": None, "height": None, "streams": []}
    m = _DUR_RE.search(text)
    if m:
        info["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))

    cur: Optional[Dict[str, Any]] = None
    for line in text.splitlines():
        sm = _STREAM_RE.search(line)
        if sm:
            idx, lang, kind, codec = int(sm.group(1)), sm.group(2), sm.group(3), sm.group(4).strip()
            cur = {
                "index": idx,
                "type": kind.lower(),
                "codec": codec,
                "lang": lang,
                "title": None,
                "channels": None,
                "sample_rate": None,
            }
            if kind == "Video":
                rm = _RES_RE.search(line)
                if rm and info["width"] is None:
                    info["width"], info["height"] = int(rm.group(1)), int(rm.group(2))
                fm2 = _FPS_RE.search(line)
                if fm2 and info["fps"] is None:
                    info["fps"] = float(fm2.group(1))
            else:
                hm = _HZ_RE.search(line)
                if hm:
                    cur["sample_rate"] = int(hm.group(1))
                for name, ch in _LAYOUT_CH.items():
                    if re.search(r"\b" + re.escape(name) + r"\b", line):
                        cur["channels"] = ch
                        break
            info["streams"].append(cur)
            continue
        if cur is not None:
            tm = _TITLE_RE.match(line)
            if tm:
                cur["title"] = tm.group(1).strip()
    return info


def probe(path: str) -> Dict[str, Any]:
    """duration/fps/해상도/오디오 트랙 목록을 반환."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    fp = ffprobe()
    if fp:
        cp = run(
            [fp, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
            timeout=120,
        )
        if cp.returncode == 0 and cp.stdout.strip():
            return _from_ffprobe(json.loads(cp.stdout))
    cp = run([ffmpeg(), "-hide_banner", "-i", path], timeout=120)
    info = _parse_ffmpeg_stderr(cp.stderr or "")
    if info["duration"] is None:
        raise RuntimeError("미디어 정보를 읽지 못했습니다:\n" + (cp.stderr or "")[-2000:])
    info["path"] = path
    info["fps_rational"] = _fps_to_rational(info["fps"])
    info["audio_streams"] = [s for s in info["streams"] if s["type"] == "audio"]
    return info


def _from_ffprobe(data: Dict[str, Any]) -> Dict[str, Any]:
    streams = []
    fps = width = height = None
    for s in data.get("streams", []):
        kind = s.get("codec_type")
        entry = {
            "index": s.get("index"),
            "type": kind,
            "codec": s.get("codec_name"),
            "lang": (s.get("tags") or {}).get("language"),
            "title": (s.get("tags") or {}).get("title"),
            "channels": s.get("channels"),
            "sample_rate": int(s["sample_rate"]) if s.get("sample_rate") else None,
        }
        streams.append(entry)
        if kind == "video" and fps is None:
            width, height = s.get("width"), s.get("height")
            rate = s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/0"
            try:
                num, den = rate.split("/")
                if float(den) > 0 and float(num) > 0:
                    fps = float(num) / float(den)
            except Exception:
                pass
    dur = None
    fmt = data.get("format") or {}
    if fmt.get("duration"):
        dur = float(fmt["duration"])
    if dur is None:
        for s in data.get("streams", []):
            if s.get("duration"):
                dur = float(s["duration"])
                break
    return {
        "duration": dur,
        "fps": fps,
        "width": width,
        "height": height,
        "streams": streams,
        "audio_streams": [s for s in streams if s["type"] == "audio"],
        "fps_rational": _fps_to_rational(fps),
        "container": fmt.get("format_name"),
    }


def _fps_to_rational(fps: Optional[float]) -> Dict[str, Any]:
    """FCP7 XML 은 timebase(정수) + ntsc(1000/1001 여부) 형태를 쓴다."""
    if not fps:
        return {"timebase": 30, "ntsc": False, "exact": 30.0}
    for base in (24, 25, 30, 50, 60, 120):
        if abs(fps - base) < 0.02:
            return {"timebase": base, "ntsc": False, "exact": float(base)}
        if abs(fps - base * 1000.0 / 1001.0) < 0.02:
            return {"timebase": base, "ntsc": True, "exact": base * 1000.0 / 1001.0}
    base = int(round(fps))
    return {"timebase": max(base, 1), "ntsc": False, "exact": float(fps)}


def describe_audio_track(s: Dict[str, Any], n: int) -> str:
    bits = ["트랙 %d" % (n + 1)]
    if s.get("title"):
        bits.append(str(s["title"]))
    if s.get("channels"):
        bits.append({1: "mono", 2: "stereo"}.get(s["channels"], "%dch" % s["channels"]))
    if s.get("codec"):
        bits.append(str(s["codec"]))
    return " · ".join(bits)


# --------------------------------------------------------------------------- utils


def remux_to_mp4(src: str, dst: str, audio_stream: Optional[int] = None) -> str:
    """프리미어는 .mkv 를 못 읽는다. 재인코딩 없이 mp4 로 감싸준다."""
    args = [ffmpeg(), "-hide_banner", "-v", "error", "-y", "-i", src, "-map", "0:v:0"]
    if audio_stream is None:
        args += ["-map", "0:a"]
    else:
        args += ["-map", "0:%d" % audio_stream]
    args += ["-c", "copy", "-movflags", "+faststart", dst]
    cp = run(args, timeout=None)
    if cp.returncode != 0:
        raise RuntimeError("리먹스 실패:\n" + (cp.stderr or "")[-2000:])
    return dst
