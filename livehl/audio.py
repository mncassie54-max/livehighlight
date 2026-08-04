"""마이크 오디오 기반 흥분도 / 웃음 분석.

ffmpeg 로 지정 오디오 트랙만 8kHz 모노 PCM 으로 뽑아 스트리밍 수신하고,
20ms RMS 엔벨로프(50Hz)를 만들어 두 가지 신호를 산출한다.

  mic_excite : 롤링 베이스라인(1분 중앙값) 대비 음량 상승분 (dB)
  mic_laugh  : 엔벨로프의 3.5~8Hz 진폭 진동 세기
               — "하하하/ㅋㅋㅋ" 처럼 빠르게 끊기는 발성의 특징

시간축은 **로컬 녹화 파일 시간**이다.
"""

import subprocess
from typing import Any, Callable, Dict, Optional

import numpy as np

from . import config
from .ffmpeg_tools import ffmpeg

Progress = Optional[Callable[[float, str], None]]

SR = config.AUDIO_SR
HOP_SEC = config.AUDIO_HOP
HOP = int(round(SR * HOP_SEC))          # 160 samples
ENV_RATE = 1.0 / HOP_SEC                # 50 Hz


def envelope(
    video: str, stream_index: int, duration: Optional[float] = None, progress: Progress = None
) -> np.ndarray:
    """20ms RMS 엔벨로프를 반환 (float32, 50Hz)."""
    args = [
        ffmpeg(), "-hide_banner", "-v", "error", "-nostdin",
        "-i", video,
        "-map", "0:%d" % stream_index,
        "-vn", "-sn", "-dn",
        "-ac", "1", "-ar", str(SR),
        "-f", "s16le", "-",
    ]
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    chunk_bytes = SR * 2 * 30  # 30초 단위
    leftover = b""
    parts = []
    frames = 0
    try:
        while True:
            data = proc.stdout.read(chunk_bytes)
            if not data:
                break
            buf = leftover + data
            n_samples = len(buf) // 2
            usable = (n_samples // HOP) * HOP
            leftover = buf[usable * 2:]
            if usable == 0:
                continue
            x = np.frombuffer(buf[: usable * 2], dtype="<i2").astype(np.float32) / 32768.0
            x = x.reshape(-1, HOP)
            parts.append(np.sqrt((x * x).mean(axis=1)).astype(np.float32))
            frames += parts[-1].shape[0]
            if progress and duration:
                done = frames * HOP_SEC
                progress(min(0.98, done / max(duration, 1.0)), "오디오 분석 %s / %s" % (_hms(done), _hms(duration)))
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        err = b""
        try:
            err = proc.stderr.read() or b""
        finally:
            proc.stderr.close()
        rc = proc.wait()
    if not parts:
        raise RuntimeError("오디오를 읽지 못했습니다 (트랙 %d).\n%s" % (stream_index, err.decode("utf-8", "replace")[-1500:]))
    if rc not in (0, None) and frames == 0:
        raise RuntimeError("ffmpeg 오류:\n" + err.decode("utf-8", "replace")[-1500:])
    return np.concatenate(parts)


def _hms(s: float) -> str:
    s = int(s)
    return "%d:%02d:%02d" % (s // 3600, (s % 3600) // 60, s % 60)


def _rolling_median(x: np.ndarray, win_frames: int) -> np.ndarray:
    """긴 배열에서도 빠른 근사 롤링 중앙값 (블록 중앙값 → 중앙값 필터 → 보간)."""
    n = len(x)
    block = max(1, win_frames // 12)
    m = n // block
    if m < 3:
        return np.full(n, float(np.median(x)), dtype=np.float32)
    bm = np.median(x[: m * block].reshape(m, block), axis=1)
    k = max(3, int(round(win_frames / block)))
    if k % 2 == 0:
        k += 1
    k = min(k, m if m % 2 == 1 else m - 1)
    pad = k // 2
    bp = np.pad(bm, pad, mode="edge")
    sw = np.lib.stride_tricks.sliding_window_view(bp, k)
    med = np.median(sw, axis=1)
    centers = (np.arange(m) + 0.5) * block
    return np.interp(np.arange(n), centers, med).astype(np.float32)


def features(env: np.ndarray, length_sec: int, progress: Progress = None) -> Dict[str, np.ndarray]:
    """엔벨로프 → 1Hz 신호 (mic_excite, mic_laugh, mic_voiced)."""
    n_sec = max(int(length_sec) + 1, 1)
    db = 20.0 * np.log10(env + 1e-5)
    baseline = _rolling_median(db, int(60 * ENV_RATE))
    rel = db - baseline

    fps = int(round(ENV_RATE))  # 초당 엔벨로프 프레임 수
    usable = (len(rel) // fps) * fps
    if usable == 0:
        z = np.zeros(n_sec, dtype=np.float32)
        return {"mic_excite": z, "mic_laugh": z.copy(), "mic_voiced": z.copy()}
    r2 = rel[:usable].reshape(-1, fps)
    per_sec_mean = r2.mean(axis=1)
    per_sec_max = r2.max(axis=1)
    excite = (0.5 * per_sec_mean + 0.5 * per_sec_max).astype(np.float32)
    voiced = (r2 > 3.0).mean(axis=1).astype(np.float32)

    # ---- 웃음: 엔벨로프의 3.5~8Hz 진동 세기
    if progress:
        progress(0.99, "웃음 진동 분석 중…")
    win = int(2 * ENV_RATE)  # 2초 창
    nrows = r2.shape[0]
    lin = env[:usable]
    freqs = np.fft.rfftfreq(win, d=HOP_SEC)
    band = (freqs >= 3.5) & (freqs <= 8.0)
    ref = (freqs >= 0.5) & (freqs <= 15.0)
    laugh = np.zeros(nrows, dtype=np.float32)
    half = win // 2
    hann = np.hanning(win).astype(np.float32)
    for i in range(nrows):
        c = i * fps + fps // 2
        a, b = c - half, c - half + win
        if a < 0 or b > len(lin):
            continue
        seg = lin[a:b]
        seg = (seg - seg.mean()) * hann
        p = np.abs(np.fft.rfft(seg)) ** 2
        tot = float(p[ref].sum()) + 1e-12
        laugh[i] = float(p[band].sum()) / tot
    # 조용한 구간의 잡음 비율은 웃음이 아니다 → 음량 상승분으로 게이팅
    gate = np.clip(excite / 6.0, 0.0, 1.5)
    laugh = (laugh * gate).astype(np.float32)

    out = {}
    for k, v in (("mic_excite", excite), ("mic_laugh", laugh), ("mic_voiced", voiced)):
        out[k] = _fit(v, n_sec)
    return out


def _fit(v: np.ndarray, n: int) -> np.ndarray:
    if len(v) == n:
        return v.astype(np.float32)
    if len(v) > n:
        return v[:n].astype(np.float32)
    return np.concatenate([v, np.zeros(n - len(v), dtype=np.float32)]).astype(np.float32)


def analyze(
    video: str, stream_index: int, duration: float, progress: Progress = None
) -> Dict[str, Any]:
    env = envelope(video, stream_index, duration, progress)
    feats = features(env, int(duration), progress)
    db = 20.0 * np.log10(env + 1e-5)
    stats = {
        "frames": int(len(env)),
        "seconds": round(len(env) * HOP_SEC, 1),
        "median_db": round(float(np.median(db)), 1),
        "p95_db": round(float(np.percentile(db, 95)), 1),
        "silence_ratio": round(float((db < np.median(db) - 12).mean()), 3),
        "voiced_ratio": round(float(feats["mic_voiced"].mean()), 3),
    }
    return {"features": feats, "stats": stats}
