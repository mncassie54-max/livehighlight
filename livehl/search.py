"""채팅 키워드 검색 → 구간 찾기.

점수 기반 자동 검출은 "반응이 튀었는지" 만 본다. 무엇에 대한 반응이었는지는
모른다. 그래서 "그때 그 게임 얘기한 데가 어디였지" 같은 건 못 찾는다.

이 모듈은 반대로 간다. 채팅 원문에서 키워드를 찾고, 그 히트가 몰린 자리를
구간으로 묶는다. 한 번 나온 단어는 구간이 아니다 — 몰려 있어야 구간이다.

시간축 주의: chat_events.json 의 `t` 는 **VOD 시간**이고, 후보 구간은
**로컬 녹화 시간**이다. 여기서 offset 을 더해 로컬 시간으로 돌려준다
(pipeline.detect 가 채팅을 뽑을 때 하는 것과 같은 변환).
"""

import re
from typing import Any, Dict, List, Optional

# 검색어에서 따옴표로 묶은 구절과 낱말을 뽑는다.
_TOKEN_RE = re.compile(r'"([^"]+)"|\'([^\']+)\'|([^\s,]+)')

# 한 구간으로 묶을 최대 간격(초). 이보다 벌어지면 다른 장면으로 본다.
DEFAULT_GAP = 30.0
# 첫 히트 앞뒤로 붙이는 여유. 반응은 사건보다 늦게 오므로 앞을 더 준다.
DEFAULT_PRE = 15.0
DEFAULT_POST = 10.0


def parse_query(query: str) -> List[str]:
    """검색어를 낱말 목록으로. 따옴표로 묶으면 띄어쓰기까지 포함해 한 덩이로 본다.

        롤 발로란트        → ["롤", "발로란트"]      (둘 중 하나라도 있으면 히트)
        "그 게임" 롤       → ["그 게임", "롤"]
    """
    out: List[str] = []
    for m in _TOKEN_RE.finditer(query or ""):
        word = (m.group(1) or m.group(2) or m.group(3) or "").strip().lower()
        if word and word not in out:
            out.append(word)
    return out


def _hits(events: List[Dict[str, Any]], words: List[str]) -> List[Dict[str, Any]]:
    """키워드가 들어간 채팅만 골라낸다. 어느 낱말에 걸렸는지도 같이 남긴다."""
    found = []
    for e in events:
        text = (e.get("text") or "")
        low = text.lower()
        matched = [w for w in words if w in low]
        if matched:
            found.append({"event": e, "matched": matched})
    return found


def _cluster(hits: List[Dict[str, Any]], gap: float) -> List[List[Dict[str, Any]]]:
    """시간이 붙어 있는 히트끼리 묶는다."""
    groups: List[List[Dict[str, Any]]] = []
    for h in sorted(hits, key=lambda h: h["event"]["t"]):
        t = h["event"]["t"]
        if groups and t - groups[-1][-1]["event"]["t"] <= gap:
            groups[-1].append(h)
        else:
            groups.append([h])
    return groups


def find(
    events: List[Dict[str, Any]],
    query: str,
    offset: float = 0.0,
    duration: Optional[float] = None,
    gap: float = DEFAULT_GAP,
    pre: float = DEFAULT_PRE,
    post: float = DEFAULT_POST,
    min_hits: int = 1,
    limit: int = 50,
    samples: int = 5,
) -> Dict[str, Any]:
    """키워드가 몰린 구간을 찾는다.

    반환 시간은 전부 **로컬 녹화 시간**이라 후보 구간과 그대로 비교·병합할 수 있다.
    구간은 히트 수가 많은 순으로 자르고, 돌려줄 때는 시간 순으로 되돌린다.
    """
    words = parse_query(query)
    if not words:
        return {"query": [], "total": 0, "groups": []}

    hits = _hits(events, words)
    groups = []
    for cluster in _cluster(hits, gap):
        if len(cluster) < min_hits:
            continue
        times = [h["event"]["t"] for h in cluster]
        start = max(0.0, times[0] + offset - pre)
        end = times[-1] + offset + post
        if duration:
            end = min(end, float(duration))
            if start >= end:
                continue

        per_word: Dict[str, int] = {}
        for h in cluster:
            for w in h["matched"]:
                per_word[w] = per_word.get(w, 0) + 1

        groups.append({
            "start": round(start, 1),
            "end": round(end, 1),
            "dur": round(end - start, 1),
            # 가장 촘촘한 자리를 대표 시점으로 쓴다 (미리보기·숏츠가 여기를 중심으로 잘린다)
            "peak": round(_densest(times) + offset, 1),
            "hits": len(cluster),
            "matched": per_word,
            "messages": _samples(cluster, offset, samples),
            "sources": _sources(cluster),
        })

    groups.sort(key=lambda g: (-g["hits"], g["start"]))
    groups = groups[:limit]
    groups.sort(key=lambda g: g["start"])
    return {"query": words, "total": len(hits), "groups": groups}


def _densest(times: List[float], window: float = 20.0) -> float:
    """히트가 가장 촘촘한 20초 창의 가운데. 구간이 길 때 어디가 본론인지 짚는다."""
    if len(times) == 1:
        return times[0]
    best_t, best_n = times[0], 0
    for i, t in enumerate(times):
        n = 0
        for u in times[i:]:
            if u - t > window:
                break
            n += 1
        if n > best_n:
            best_n, best_t = n, t + min(window, times[-1] - t) / 2.0
    return best_t


def _samples(cluster: List[Dict[str, Any]], offset: float, limit: int) -> List[Dict[str, Any]]:
    """구간을 대표할 채팅 몇 줄. 같은 말이 반복되면 하나만 남긴다."""
    out, seen = [], set()
    for h in cluster:
        e = h["event"]
        text = (e.get("text") or "")
        key = text[:24]
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "t": round(e["t"] + offset, 1),
            "text": text[:120],
            "author": (e.get("author") or "")[:20],
            "kind": e.get("kind") or "text",
            "source": e.get("source") or "youtube",
        })
        if len(out) >= limit:
            break
    return out


def _sources(cluster: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for h in cluster:
        s = h["event"].get("source") or "youtube"
        counts[s] = counts.get(s, 0) + 1
    return counts


def to_segments(groups: List[Dict[str, Any]], query_words: List[str]) -> List[Dict[str, Any]]:
    """검색 결과를 후보 구간 모양으로 바꾼다.

    `src="search"` 를 달아둔다. 다시 검출할 때 이 표시가 있는 것만 살려 둔다 —
    검색으로 찾아 둔 구간이 재검출 한 번에 사라지면 쓸 수가 없다.
    """
    label = "🔎 " + " ".join(query_words[:2]) + ("…" if len(query_words) > 2 else "")
    segs = []
    for g in groups:
        top = sorted(g["matched"].items(), key=lambda kv: -kv[1])
        segs.append({
            "start": float(g["start"]),
            "end": float(g["end"]),
            "peak": float(g["peak"]),
            "dur": float(g["dur"]),
            "score": 0.0,
            "area": 0.0,
            "contrib": {},
            "label": label,
            "reason": "채팅 검색 · " + ", ".join("%s %d회" % (w, n) for w, n in top[:3]),
            "chat": g["messages"],
            "selected": True,
            "title": "",
            "src": "search",
            "query": list(query_words),
            "hits": g["hits"],
        })
    return segs


def overlaps(a: Dict[str, Any], b: Dict[str, Any], ratio: float = 0.5) -> bool:
    """두 구간이 절반 넘게 겹치는지. 같은 자리를 두 번 넣지 않으려고 쓴다."""
    lo = max(float(a["start"]), float(b["start"]))
    hi = min(float(a["end"]), float(b["end"]))
    if hi <= lo:
        return False
    shorter = min(float(a["end"]) - float(a["start"]), float(b["end"]) - float(b["start"]))
    return shorter > 0 and (hi - lo) / shorter >= ratio
