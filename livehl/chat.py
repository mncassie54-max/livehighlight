"""유튜브 라이브 채팅 리플레이 수집 및 신호화.

yt-dlp 로 `*.live_chat.json` (JSONL) 을 받아서 초당 신호 곡선으로 만든다.
곡선의 시간축은 **VOD 시간**(다시보기 0초 기준)이다.
"""

import glob
import json
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

Progress = Optional[Callable[[float, str], None]]


# --------------------------------------------------------------------------- 다운로드


def fetch(url: str, out_dir: str, progress: Progress = None) -> Dict[str, Any]:
    """채팅 리플레이 + 방송 메타데이터를 가져온다."""
    import yt_dlp

    os.makedirs(out_dir, exist_ok=True)
    if progress:
        progress(0.05, "유튜브 메타데이터 조회 중…")

    def hook(d):
        if progress and d.get("status") == "downloading":
            progress(0.3, "채팅 리플레이 수신 중…")

    opts = {
        "skip_download": True,
        "writesubtitles": True,
        "subtitleslangs": ["live_chat"],
        "outtmpl": {"default": os.path.join(out_dir, "chat.%(ext)s")},
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [hook],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    files = sorted(glob.glob(os.path.join(out_dir, "*live_chat.json")))
    meta = {
        "video_id": info.get("id"),
        "title": info.get("title"),
        "duration": info.get("duration"),
        "release_timestamp": info.get("release_timestamp"),
        "timestamp": info.get("timestamp"),
        "was_live": info.get("was_live"),
        "chat_file": files[-1] if files else None,
    }
    if progress:
        progress(0.6, "채팅 파일 %s" % ("확보" if files else "없음(리플레이 비공개일 수 있음)"))
    return meta


# --------------------------------------------------------------------------- 파싱

_RENDERERS = (
    "liveChatTextMessageRenderer",
    "liveChatPaidMessageRenderer",
    "liveChatPaidStickerRenderer",
    "liveChatMembershipItemRenderer",
    "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer",
)

# ㅋㅋ 같은 의성어. ㅋ 하나짜리는 추임새에 가까워 2개부터 본다.
LAUGH_RE = re.compile(r"(ㅋ{2,}|ㅎ{2,}|ㅋㅎ|ㅎㅋ|크크|킼|lo+l|lmao|rofl|😂|🤣|😹|ㅋ_ㅋ)", re.I)

# "웃기네" "웃겨" "존잼" 처럼 말로 웃음을 표현한 것.
# ㅋㅋ 를 치는 것보다 품이 들어 수가 적게 잡히므로, 놓치면 웃음 구간을 통째로 놓친다.
LAUGH_WORD_RE = re.compile(
    r"(웃[기겨긴김겼을]|빵\s*터|빵터|터졌|터진다|뿜었|[존꿀핵개]\s*잼|잼있|재밌|"
    r"미치겠|배\s*아프|숨넘어|숨\s*막혀)",
    re.I,
)

# "안 웃겨" "노잼" 은 웃은 게 아니다. 이게 걸리면 웃음으로 세지 않는다.
NOT_LAUGH_RE = re.compile(r"(노\s*잼|안\s*웃|못\s*웃|재미없|웃기지\s*도|웃기지도)", re.I)


def laugh_score(txt: str) -> float:
    """이 채팅이 얼마나 웃은 것인지. 0 이면 웃음이 아니다."""
    if not txt or NOT_LAUGH_RE.search(txt):
        return 0.0
    if LAUGH_RE.search(txt):
        # ㅋ 개수가 많으면 더 크게 웃은 것으로 본다 (최대 3배)
        k = min(len(re.findall(r"[ㅋㅎ]", txt)), 12) / 4.0
        return 1.0 + k
    if LAUGH_WORD_RE.search(txt):
        # 의성어만큼 강하지는 않게 본다.
        return 1.0
    return 0.0
HYPE_RE = re.compile(
    r"(ㄷㄷ+|ㅁㅊ|미친|대박|레전드|렛츠고|ㄴㅇㄱ|헐+|우와+|와+아*|ㅗㅜㅑ|소름|개[웃쩐잘미]|"
    r"실화냐|어질|와우|ㅇㅁㅇ|😱|🔥|👏|💀|😭|😮)",
    re.I,
)
CLIP_RE = re.compile(r"(클립|박제|편집|숏[츠쓰]|shorts|짤|타임스탬프|다시보기|하이라이트|여기다|지금이야)", re.I)


def _runs_to_text(msg: Dict[str, Any]) -> str:
    out = []
    for r in (msg or {}).get("runs", []) or []:
        if "text" in r:
            out.append(r["text"])
        elif "emoji" in r:
            e = r["emoji"]
            eid = e.get("emojiId") or ""
            if eid and len(eid) <= 4:
                out.append(eid)
            else:
                sc = e.get("shortcuts") or []
                out.append(sc[0] if sc else "")
    return "".join(out)


def parse(chat_file: str, progress: Progress = None) -> List[Dict[str, Any]]:
    """[{t, text, author, kind, amount}] (t = VOD 초)"""
    events: List[Dict[str, Any]] = []
    if not chat_file or not os.path.exists(chat_file):
        return events
    total = max(os.path.getsize(chat_file), 1)
    read = 0
    first_us: Optional[int] = None
    with open(chat_file, encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f):
            read += len(line)
            if progress and lineno % 5000 == 0:
                progress(min(0.95, read / total), "채팅 파싱 %d줄" % lineno)
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            action = obj.get("replayChatItemAction") or {}
            offset_ms = action.get("videoOffsetTimeMsec")
            for act in action.get("actions", []) or []:
                item = ((act.get("addChatItemAction") or {}).get("item")) or {}
                for rname in _RENDERERS:
                    r = item.get(rname)
                    if not r:
                        continue
                    ts_us = r.get("timestampUsec")
                    if first_us is None and ts_us:
                        try:
                            first_us = int(ts_us)
                        except Exception:
                            pass
                    if offset_ms is not None:
                        t = int(offset_ms) / 1000.0
                    elif ts_us and first_us:
                        t = (int(ts_us) - first_us) / 1e6
                    else:
                        continue
                    text = _runs_to_text(r.get("message") or {})
                    if not text and r.get("headerSubtext"):
                        text = _runs_to_text(r["headerSubtext"])
                    amount = None
                    if r.get("purchaseAmountText"):
                        amount = (r["purchaseAmountText"] or {}).get("simpleText")
                    kind = "text"
                    if "Paid" in rname:
                        kind = "paid"
                    elif "Membership" in rname or "Sponsorships" in rname:
                        kind = "member"
                    events.append(
                        {
                            "t": round(t, 2),
                            "text": text,
                            "author": ((r.get("authorName") or {}).get("simpleText") or ""),
                            "kind": kind,
                            "amount": amount,
                        }
                    )
                    break
    events.sort(key=lambda e: e["t"])
    if progress:
        progress(1.0, "채팅 %d개 파싱 완료" % len(events))
    return events


# --------------------------------------------------------------------------- 신호화

CURVES = ("chat_rate", "chat_laugh", "chat_hype", "chat_clip", "chat_paid")


def curves(events: List[Dict[str, Any]], length_sec: int) -> Dict[str, np.ndarray]:
    """1Hz 격자 위의 채팅 신호들 (VOD 시간축)."""
    n = max(int(length_sec) + 1, 1)
    out = {k: np.zeros(n, dtype=np.float32) for k in CURVES}
    for e in events:
        i = int(e["t"])
        if i < 0 or i >= n:
            continue
        txt = e["text"] or ""
        out["chat_rate"][i] += 1.0
        lw = laugh_score(txt)
        if lw:
            out["chat_laugh"][i] += lw
        if HYPE_RE.search(txt):
            out["chat_hype"][i] += 1.0
        if CLIP_RE.search(txt):
            out["chat_clip"][i] += 1.0
        if e["kind"] in ("paid", "member"):
            out["chat_paid"][i] += 1.0
    return out


def stats(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not events:
        return {"count": 0}
    laugh = sum(1 for e in events if laugh_score(e["text"] or ""))
    clip = sum(1 for e in events if CLIP_RE.search(e["text"] or ""))
    paid = sum(1 for e in events if e["kind"] in ("paid", "member"))
    span = events[-1]["t"] - events[0]["t"]
    return {
        "count": len(events),
        "laugh": laugh,
        "clip_requests": clip,
        "paid": paid,
        "first_t": events[0]["t"],
        "last_t": events[-1]["t"],
        "span_sec": span,
        "per_min": round(len(events) / (span / 60.0), 1) if span > 60 else None,
        "unique_authors": len({e["author"] for e in events if e["author"]}),
    }


def sample_messages(
    events: List[Dict[str, Any]], t0: float, t1: float, limit: int = 10
) -> List[Dict[str, Any]]:
    """구간 안에서 대표 채팅을 골라 반환 (웃음/클립요청/슈퍼챗 우선)."""
    lo, hi = _bisect(events, t0), _bisect(events, t1)
    window = events[lo:hi]
    if not window:
        return []

    def prio(e):
        txt = e["text"] or ""
        s = 0
        if e["kind"] in ("paid", "member"):
            s += 3
        if CLIP_RE.search(txt):
            s += 3
        if laugh_score(txt):
            s += 2
        if HYPE_RE.search(txt):
            s += 1
        s += min(len(txt), 40) / 100.0
        return -s

    picked = sorted(window, key=prio)[: limit * 3]
    seen = set()
    out = []
    for e in sorted(picked, key=lambda e: e["t"]):
        key = (e["text"] or "")[:24]
        if key in seen:
            continue
        seen.add(key)
        out.append({"t": e["t"], "text": (e["text"] or "")[:120], "author": e["author"][:20], "kind": e["kind"]})
        if len(out) >= limit:
            break
    return out


def _bisect(events: List[Dict[str, Any]], t: float) -> int:
    import bisect

    keys = _KeyView(events)
    return bisect.bisect_left(keys, t)


class _KeyView:
    """events 리스트를 t 기준으로 이진탐색하기 위한 뷰."""

    def __init__(self, data):
        self.data = data

    def __len__(self):
        return len(self.data)

    def __getitem__(self, i):
        return self.data[i]["t"]
