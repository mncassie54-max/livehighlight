"""후보 구간을 실제 파일로 뽑아내기 — 미리보기 mp4 / 숏츠 9:16."""

import os
import re
import subprocess
from typing import Any, Callable, Dict, List, Optional

from .ffmpeg_tools import ffmpeg

Progress = Optional[Callable[[float, str], None]]

_OUT_TIME_RE = re.compile(r"out_time_ms=(\d+)")


def _run(args: List[str], total_sec: float, progress: Progress, label: str) -> None:
    """-progress pipe:1 로 진행률을 읽으며 ffmpeg 실행."""
    full = args[:1] + ["-hide_banner", "-nostdin", "-v", "error", "-progress", "pipe:1", "-nostats"] + args[1:]
    proc = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    for line in proc.stdout:
        m = _OUT_TIME_RE.search(line)
        if m and progress and total_sec > 0:
            done = int(m.group(1)) / 1e6
            progress(min(0.99, done / total_sec), "%s %.0f%%" % (label, 100 * done / total_sec))
    err = proc.stderr.read() if proc.stderr else ""
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError("ffmpeg 실패 (%s):\n%s" % (label, (err or "")[-1500:]))


def _seek_args(video: str, start: float, dur: float) -> List[str]:
    # 큰 파일에서 빠르게 이동하려면 -ss 를 -i 앞에 둔다 (재인코딩이므로 정확도는 유지된다)
    lead = min(start, 12.0)  # 키프레임 확보용 여유
    return [
        "-ss", "%.3f" % max(0.0, start - lead),
        "-i", video,
        "-ss", "%.3f" % (lead if start >= lead else start),
        "-t", "%.3f" % dur,
    ]


def _amap(mix_stream: Optional[int]) -> List[str]:
    return ["-map", "0:%d" % mix_stream] if mix_stream is not None else ["-map", "0:a:0?"]


def preview(
    video: str,
    start: float,
    end: float,
    out_path: str,
    mix_stream: Optional[int] = None,
    height: int = 540,
    progress: Progress = None,
) -> str:
    dur = max(0.2, end - start)
    args = (
        [ffmpeg(), "-y"]
        + _seek_args(video, start, dur)
        + ["-map", "0:v:0"]
        + _amap(mix_stream)
        + [
            "-vf", "scale=-2:%d" % height,
            "-c:v", "libx264", "-crf", "24", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ac", "2",
            "-movflags", "+faststart",
            out_path,
        ]
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    _run(args, dur, progress, "미리보기")
    return out_path


def shorts(
    video: str,
    start: float,
    end: float,
    out_path: str,
    mix_stream: Optional[int] = None,
    layout: str = "blur",
    focus_x: float = 0.5,
    srt_path: Optional[str] = None,
    progress: Progress = None,
) -> str:
    """9:16 1080x1920 숏츠. layout=crop(꽉 채움) | blur(원본 유지 + 블러 배경)"""
    dur = max(0.2, end - start)
    fx = max(0.0, min(1.0, float(focus_x)))

    sub = ""
    if srt_path and os.path.exists(srt_path):
        style = "FontSize=16,Outline=2,BorderStyle=1,Alignment=2,MarginV=180"
        sub = ",subtitles=filename='%s':force_style='%s'" % (srt_path.replace("'", "\\'"), style)

    if layout == "crop":
        chain = (
            "[0:v]crop=w='min(iw\\,ih*9/16)':h=ih:x='(iw-min(iw\\,ih*9/16))*%.3f':y=0,"
            "scale=1080:1920:flags=lanczos,setsar=1%s[vout]" % (fx, sub)
        )
    else:
        chain = (
            "[0:v]split=2[bg][fg];"
            "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
            "gblur=sigma=28[bgb];"
            "[fg]scale=1080:-2:flags=lanczos[fgs];"
            "[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1%s[vout]" % sub
        )

    args = (
        [ffmpeg(), "-y"]
        + _seek_args(video, start, dur)
        + ["-filter_complex", chain, "-map", "[vout]"]
        + _amap(mix_stream)
        + [
            "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-r", "30",
            "-c:a", "aac", "-b:a", "192k", "-ac", "2",
            "-movflags", "+faststart",
            out_path,
        ]
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    _run(args, dur, progress, "숏츠")
    return out_path


def thumbnail(video: str, at: float, out_path: str, width: int = 480) -> str:
    args = [
        ffmpeg(), "-y", "-ss", "%.3f" % max(0.0, at), "-i", video,
        "-frames:v", "1", "-vf", "scale=%d:-2" % width, "-q:v", "4", out_path,
    ]
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cp = subprocess.run(args + [], capture_output=True, text=True)
    if cp.returncode != 0:
        raise RuntimeError("썸네일 실패:\n" + (cp.stderr or "")[-800:])
    return out_path


def safe_name(s: Dict[str, Any]) -> str:
    t = s.get("title") or s.get("label") or "clip"
    t = re.sub(r"[^0-9A-Za-z가-힣 _-]+", "", t).strip().replace(" ", "_")
    return "%02d_%s_%s" % (s.get("rank", 0), _tag(s["start"]), t[:30] or "clip")


def _tag(sec: float) -> str:
    sec = int(sec)
    return "%02dh%02dm%02ds" % (sec // 3600, (sec % 3600) // 60, sec % 60)
