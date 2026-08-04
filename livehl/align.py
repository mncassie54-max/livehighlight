"""OBS 녹화 시간축 ↔ 유튜브 VOD 시간축 정렬.

정의:  local_t = vod_t + offset_sec

1) 1차 추정: OBS 파일명에 박힌 녹화 시작시각(또는 파일 생성시각)과
   유튜브 방송 시작시각(release_timestamp)의 차이.
2) 미세보정: 채팅 곡선과 마이크 흥분도 곡선의 FFT 교차상관으로 최적 지연을 찾는다.
   (시청자 반응은 보통 0.5~3초 늦으므로 그만큼 음수 보정이 잡히는 게 정상)
"""

import os
import re
import time
from typing import Any, Dict, Optional, Tuple

import numpy as np

# OBS 기본 파일명: "2026-08-04 22-58-31.mkv", "2026-08-04_22-58-31.mkv" 등
_TS_RE = re.compile(r"(\d{4})[-_.](\d{2})[-_.](\d{2})[ _T](\d{2})[-_.:](\d{2})[-_.:](\d{2})")


def parse_obs_filename_time(path: str) -> Optional[float]:
    m = _TS_RE.search(os.path.basename(path))
    if not m:
        return None
    y, mo, d, h, mi, s = (int(g) for g in m.groups())
    try:
        return time.mktime((y, mo, d, h, mi, s, 0, 0, -1))
    except Exception:
        return None


def file_start_time(path: str, duration: Optional[float] = None) -> Tuple[Optional[float], str]:
    """녹화 시작 epoch 과 그 근거를 반환."""
    t = parse_obs_filename_time(path)
    if t:
        return t, "파일명 타임스탬프"
    try:
        st = os.stat(path)
        bt = getattr(st, "st_birthtime", None)
        if bt:
            return float(bt), "파일 생성시각(birthtime)"
        if duration:
            return float(st.st_mtime) - float(duration), "수정시각 - 길이"
        return float(st.st_mtime), "수정시각"
    except Exception:
        return None, "알 수 없음"


def initial_offset(
    video_path: str, video_duration: Optional[float], stream_start_utc: Optional[float]
) -> Dict[str, Any]:
    vstart, src = file_start_time(video_path, video_duration)
    if vstart is None or not stream_start_utc:
        return {
            "offset_sec": 0.0,
            "video_start_utc": vstart,
            "source": "추정 불가 → 0초 (수동 보정 필요)",
        }
    off = float(stream_start_utc) - float(vstart)
    return {
        "offset_sec": round(off, 2),
        "video_start_utc": vstart,
        "source": "%s ↔ 유튜브 방송 시작시각" % src,
    }


# --------------------------------------------------------------------------- 미세보정


def _norm(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    x = x - x.mean()
    s = x.std()
    return x / s if s > 1e-9 else x


def smooth(x: np.ndarray, win: int) -> np.ndarray:
    if win <= 1:
        return np.asarray(x, dtype=np.float64)
    k = np.hanning(win)
    k = k / k.sum()
    return np.convolve(np.asarray(x, dtype=np.float64), k, mode="same")


def shift_curve(curve: np.ndarray, offset: float, out_len: int) -> np.ndarray:
    """VOD 시간축 곡선을 로컬 시간축(길이 out_len)으로 옮긴다."""
    src_idx = np.arange(out_len, dtype=np.float64) - float(offset)
    out = np.zeros(out_len, dtype=np.float32)
    valid = (src_idx >= 0) & (src_idx < len(curve))
    if valid.any():
        out[valid] = np.asarray(curve, dtype=np.float32)[src_idx[valid].astype(np.int64)]
    return out


def refine_offset(
    chat_curve: np.ndarray,
    mic_curve: np.ndarray,
    current_offset: float,
    max_shift: int = 600,
    smooth_win: int = 15,
) -> Dict[str, Any]:
    """교차상관으로 offset 미세보정. 반환: {offset_sec, delta, confidence, curve}"""
    n = len(mic_curve)
    if n < 120 or len(chat_curve) < 120:
        return {"offset_sec": float(current_offset), "delta": 0.0, "confidence": 0.0, "note": "데이터 부족"}

    a = _norm(smooth(mic_curve, smooth_win))                                  # 로컬 시간축
    b = _norm(smooth(shift_curve(chat_curve, current_offset, n), smooth_win))  # 로컬 시간축으로 옮긴 채팅

    N = 1
    while N < len(a) + len(b):
        N *= 2
    A = np.fft.rfft(a, N)
    B = np.fft.rfft(b, N)
    cc = np.fft.irfft(A * np.conj(B), N) / max(n, 1)

    max_shift = int(min(max_shift, n // 3))
    lags = np.arange(-max_shift, max_shift + 1)
    vals = np.concatenate([cc[N - max_shift:], cc[: max_shift + 1]])
    best = int(np.argmax(vals))
    delta = float(lags[best])
    peak = float(vals[best])
    noise = float(np.std(vals))
    conf = round(peak / (noise + 1e-9), 2)
    return {
        "offset_sec": round(float(current_offset) + delta, 2),
        "delta": delta,
        "peak": round(peak, 4),
        "confidence": conf,
        "lags": lags.tolist()[:: max(1, len(lags) // 400)],
        "corr": [round(float(v), 4) for v in vals[:: max(1, len(vals) // 400)]],
        "note": "신뢰도 %.1f (4 이상이면 신뢰 가능)" % conf,
    }
