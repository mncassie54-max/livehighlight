"""신호 융합 → 하이라이트 후보 구간 검출.

모든 계산은 **로컬 녹화 시간축(1Hz)** 에서 이뤄진다.
채팅 곡선은 offset 으로 옮겨서 합친다.

정규화는 구간별 롤링 베이스라인을 쓴다. 5시간 방송이면 시청자 수가 크게
변하기 때문에, 절대 채팅 수가 아니라 "그 시점 기준 평소보다 얼마나 튀었는지"
를 봐야 한다.
"""

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .audio import _rolling_median as rolling_median
from .align import shift_curve, smooth

SIGNALS = (
    "chat_rate",
    "chat_laugh",
    "chat_hype",
    "chat_clip",
    "chat_paid",
    "mic_excite",
    "mic_laugh",
)

LABELS = {
    "chat_clip": ("✂️", "클립 요청"),
    "chat_laugh": ("😂", "채팅 폭소"),
    "mic_laugh": ("🤣", "본인 폭소"),
    "chat_hype": ("🔥", "채팅 흥분"),
    "chat_rate": ("💬", "채팅 폭주"),
    "chat_paid": ("💸", "슈퍼챗"),
    "mic_excite": ("📢", "리액션 큼"),
}

# 라벨을 고를 때: 같은 세기라면 "무슨 일이 있었는지" 를 더 잘 설명하는 신호를 우선한다.
# (채팅이 많다/소리가 크다 는 결과이고, 클립 요청·웃음은 원인에 가깝다)
SPECIFICITY = {
    "chat_clip": 1.45,
    "chat_paid": 1.30,
    "chat_laugh": 1.20,
    "mic_laugh": 1.20,
    "chat_hype": 1.08,
    "chat_rate": 1.0,
    "mic_excite": 1.0,
}


def normalize(x: np.ndarray, smooth_sec: int = 10, base_sec: int = 300) -> np.ndarray:
    """롤링 베이스라인 대비 robust z-score."""
    x = np.asarray(x, dtype=np.float64)
    if len(x) < 10 or not np.any(x):
        return np.zeros(len(x), dtype=np.float32)
    s = smooth(x, max(2, smooth_sec))
    base = rolling_median(s.astype(np.float32), max(30, base_sec)).astype(np.float64)
    resid = s - base
    mad = float(np.median(np.abs(resid))) * 1.4826
    scale = mad if mad > 1e-6 else float(np.std(resid))
    if scale < 1e-6:
        return np.zeros(len(x), dtype=np.float32)
    return np.clip(resid / scale, -3.0, 12.0).astype(np.float32)


def build(
    signals: Dict[str, np.ndarray],
    offset_sec: float,
    n_local: int,
    weights: Dict[str, float],
) -> Dict[str, Any]:
    """정규화된 개별 신호(z)와 융합 점수를 로컬 시간축으로 반환."""
    z: Dict[str, np.ndarray] = {}
    for name in SIGNALS:
        cur = signals.get(name)
        if cur is None or len(cur) == 0:
            continue
        arr = np.asarray(cur)
        if name.startswith("chat_"):
            arr = shift_curve(arr, offset_sec, n_local)
        else:
            arr = arr[:n_local] if len(arr) >= n_local else np.pad(arr, (0, n_local - len(arr)))
        z[name] = normalize(arr)

    total_w = sum(abs(weights.get(k, 0.0)) for k in z) or 1.0
    score = np.zeros(n_local, dtype=np.float32)
    for name, arr in z.items():
        score += float(weights.get(name, 0.0)) * arr
    score /= total_w
    return {"z": z, "score": score}


def _local_maxima(x: np.ndarray) -> np.ndarray:
    if len(x) < 3:
        return np.array([], dtype=np.int64)
    return np.where((x[1:-1] >= x[:-2]) & (x[1:-1] > x[2:]))[0] + 1


def detect(
    fused: Dict[str, Any],
    params: Dict[str, Any],
    duration: float,
    weights: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    score = fused["score"]
    z = fused["z"]
    p = params
    sm = smooth(score, max(2, int(p.get("smooth", 8)))).astype(np.float32)
    thr = float(p.get("threshold", 1.8))
    min_gap = int(p.get("min_gap", 45))
    pre = int(p.get("pre_roll", 25))
    post = int(p.get("post_roll", 20))
    max_len = int(p.get("max_len", 180))
    merge_gap = int(p.get("merge_gap", 8))
    top_n = int(p.get("top_n", 40))
    n = len(sm)

    cands = _local_maxima(sm)
    cands = cands[sm[cands] >= thr]
    if len(cands) == 0:
        return []
    order = cands[np.argsort(-sm[cands])]

    # non-max suppression
    keep: List[int] = []
    for i in order:
        if all(abs(int(i) - k) >= min_gap for k in keep):
            keep.append(int(i))
        if len(keep) >= top_n * 3:
            break

    raw: List[Tuple[int, int, int]] = []
    for pk in keep:
        lo = pk
        limit = int(max_len * 0.6)
        while lo > 0 and sm[lo] > thr * 0.4 and pk - lo < limit:
            lo -= 1
        hi = pk
        while hi < n - 1 and sm[hi] > thr * 0.4 and hi - pk < limit:
            hi += 1
        start = max(0, min(lo, pk - pre))
        end = min(n - 1, max(hi, pk + post))
        if end - start > max_len:
            half = max_len // 2
            start = max(0, pk - half)
            end = min(n - 1, start + max_len)
        raw.append((start, end, pk))

    raw.sort()
    merged: List[Tuple[int, int, List[int]]] = []
    for start, end, pk in raw:
        if merged and start - merged[-1][1] <= merge_gap and (end - merged[-1][0]) <= max_len:
            ps, pe, pks = merged[-1]
            merged[-1] = (ps, max(pe, end), pks + [pk])
        else:
            merged.append((start, end, [pk]))

    segs: List[Dict[str, Any]] = []
    for idx, (start, end, pks) in enumerate(merged):
        pk = max(pks, key=lambda i: sm[i])
        win = slice(start, end + 1)
        contrib = {}
        for name, arr in z.items():
            contrib[name] = round(float(np.percentile(arr[win], 92)), 2)
        w = weights or {}
        ranked = sorted(
            contrib.items(),
            key=lambda kv: -(kv[1] * float(w.get(kv[0], 1.0)) * SPECIFICITY.get(kv[0], 1.0)),
        )
        top = [kv for kv in ranked if kv[1] > 0.3] or ranked[:1]
        emoji, word = LABELS.get(top[0][0], ("⭐", "하이라이트")) if top else ("⭐", "하이라이트")
        segs.append(
            {
                "id": idx,
                "start": float(start),
                "end": float(min(end, duration)),
                "peak": float(pk),
                "dur": round(float(min(end, duration) - start), 1),
                "score": round(float(sm[pk]), 2),
                "area": round(float(np.clip(sm[win], 0, None).sum()), 1),
                "contrib": contrib,
                "label": "%s %s" % (emoji, word),
                "reason": ", ".join(
                    "%s %s%.1f" % (LABELS.get(k, ("", k))[1], "+" if v >= 0 else "", v)
                    for k, v in top[:3]
                ),
                "selected": True,
                "title": "",
            }
        )

    segs.sort(key=lambda s: -s["score"])
    segs = segs[:top_n]
    segs.sort(key=lambda s: s["start"])
    for i, s in enumerate(segs):
        s["id"] = i
        s["rank"] = 0
    for rank, s in enumerate(sorted(segs, key=lambda s: -s["score"]), start=1):
        s["rank"] = rank
    return segs


def downsample_for_plot(x: np.ndarray, width: int = 1600) -> List[float]:
    """캔버스용: 구간 최대값으로 줄인다 (피크를 잃지 않게)."""
    x = np.asarray(x, dtype=np.float32)
    if len(x) == 0:
        return []
    if len(x) <= width:
        return [round(float(v), 3) for v in x]
    step = int(np.ceil(len(x) / width))
    usable = (len(x) // step) * step
    b = x[:usable].reshape(-1, step).max(axis=1)
    return [round(float(v), 3) for v in b]
