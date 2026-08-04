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

    base = {
        "skip_download": True,
        "writesubtitles": True,
        "subtitleslangs": ["live_chat"],
        "outtmpl": {"default": os.path.join(out_dir, "chat.%(ext)s")},
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [hook],
    }

    # 유튜브가 기본 클라이언트를 거절하는 일이 잦다("The page needs to be reloaded").
    # 클라이언트를 바꿔가며 되는 것을 쓴다. 기본값(None)부터 시도한다.
    info = None
    last_err: Optional[Exception] = None
    for client in (None, "android", "web_safari", "tv", "mweb"):
        opts = dict(base)
        if client:
            opts["extractor_args"] = {"youtube": {"player_client": [client]}}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            break
        except Exception as e:  # noqa: BLE001  yt-dlp 는 여러 예외를 던진다
            last_err = e
            if progress and client:
                progress(0.1, "유튜브 접속 재시도 중(%s)…" % client)
    if info is None:
        # 원인은 대개 셋 중 하나다. 사용자가 스스로 판단할 수 있게 순서대로 적는다.
        # 유튜브가 내부 구조를 바꾸면 yt-dlp 가 낡아 실패하므로 그 경우를 먼저 알린다.
        raise RuntimeError(
            "유튜브에서 채팅을 받지 못했습니다. 아래를 차례로 확인해 주세요.\n"
            "  1) [업데이트.command] 를 다시 실행해 주세요. "
            "유튜브가 방식을 바꾸면 채팅 수집기가 낡아 실패하는데, 실행할 때 최신으로 맞춰집니다.\n"
            "  2) 주소가 그 방송의 '다시보기' 주소인지 확인해 주세요.\n"
            "  3) 그 방송에 채팅 다시보기가 남아 있는지 확인해 주세요"
            "(비공개이거나 삭제되면 받을 수 없습니다).\n"
            "\n현재 수집기 버전: %s\n원래 오류: %s"
            % (getattr(yt_dlp.version, "__version__", "알 수 없음"), last_err)
        )

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


HYPE_RE = re.compile(
    r"(ㄷㄷ+|ㅁㅊ|미친|대박|레전드|렛츠고|ㄴㅇㄱ|헐+|우와+|와+아*|ㅗㅜㅑ|소름|개[웃쩐잘미]|"
    r"실화냐|어질|와우|ㅇㅁㅇ|😱|🔥|👏|💀|😭|😮)",
    re.I,
)
# "이 구간 편집해줘" 처럼 시청자가 직접 지목한 것만 잡는다. 가중치가 가장 높은 신호라
# 오탐이 섞이면 상위 후보를 통째로 밀어낸다.
#
# 실제 방송 채팅 2,753개로 확인한 결과 아래 두 개는 반드시 빼야 했다.
#   · `다시보기` — "다시보기 보니까 잘 노시던데요" 처럼 그냥 VOD 얘기
#   · 맨 `편집`  — "혼자 편집하고 그러니까ㅋㅋ" 처럼 편집이라는 화제
# 둘이 오탐의 대부분(8건 중 6건)이었다. `편집` 은 요청형일 때만 받는다.
CLIP_RE = re.compile(
    r"(클립|박제|숏[츠쓰]|shorts|타임스탬프|명장면|하이라이트|"
    r"짤(?![리렸려란근])|"                        # "짤렸어요"(잘렸다) 는 제외
    r"편집\s*(?:해|좀|각|하자|부탁|필요|하세)|"
    r"(?:이거|저거|요거|여기|지금)\s*(?:따|각))",
    re.I,
)


# --------------------------------------------------------------- 치지직 이모티콘
#
# 치지직 채팅은 이모티콘이 `{:d_65:}` 같은 코드로 들어와서 글자만 봐서는 뜻을 알 수 없다.
# 채팅의 extras.emojis 가 코드 → 이미지 URL 을 알려주므로, 기본 이모티콘 이미지를
# 실제로 받아 눈으로 확인해서 아래 표를 만들었다. (코드 → 파일 규칙: d_N → b_(N-40), c_(N-72))
EMOJI_RE = re.compile(r"\{:([a-zA-Z0-9_]+):\}")

# 대놓고 웃는 것. ㅋㅋ 를 친 것과 같게 본다.
CHZZK_LAUGH_STRONG = {
    "d_65",    # "ㅋㅋㅋㅋㅋㅋ" 글자 (b_25)
    "d_142",   # "ㄹㅇㅋㅋ" 글자 (c_70)
    "d_117",   # 눈물 흘리며 크게 웃는 얼굴 (c_45)
    "d_110",   # 뒤집어지게 웃는 그림 (c_38)
    "d_44",    # 누워서 ㅋㅋㅋㅋ (b_04)
}
# 웃는 얼굴이지만 폭소까지는 아닌 것. 약하게 센다.
CHZZK_LAUGH_MILD = {
    "d_42",    # 선글라스 쓰고 웃는 얼굴 (b_02)
    "d_46",    # 눈 감고 미소 (b_06)
    "d_54",    # ^^ 웃는 얼굴 (b_14)
    "d_108",   # 혀 내밀고 웃는 얼굴 (c_36) — 이 방송에서 가장 많이 쓰였다
    "d_109",   # 만세 하며 웃는 문어 (c_37)
}
# "이 장면 잘라달라" 에 해당하는 것.
CHZZK_CLIP_EMOJI = {
    "d_144",   # "컷!" + 가위 (c_72)
}


def _emoji_codes(txt: str) -> List[str]:
    return EMOJI_RE.findall(txt or "")


def laugh_score(txt: str) -> float:
    """이 채팅이 얼마나 웃은 것인지. 0 이면 웃음이 아니다."""
    if not txt:
        return 0.0
    if NOT_LAUGH_RE.search(txt):
        return 0.0

    codes = _emoji_codes(txt)
    strong = sum(1 for c in codes if c in CHZZK_LAUGH_STRONG)
    mild = sum(1 for c in codes if c in CHZZK_LAUGH_MILD)

    if LAUGH_RE.search(txt):
        # ㅋ 개수가 많으면 더 크게 웃은 것으로 본다 (최대 3배)
        k = min(len(re.findall(r"[ㅋㅎ]", txt)), 12) / 4.0
        return 1.0 + k + min(strong, 3) * 0.5
    if strong:
        # 이모티콘을 여러 번 붙이는 건 ㅋ 을 늘려 치는 것과 같다 (최대 3개까지 센다)
        return 1.0 + min(strong - 1, 2) * 0.5
    if LAUGH_WORD_RE.search(txt):
        # 의성어만큼 강하지는 않게 본다.
        return 1.0
    if mild:
        return 0.6
    return 0.0


def clip_score(txt: str) -> float:
    """시청자가 이 구간을 잘라달라고 했는지. 0 이면 아니다."""
    if not txt:
        return 0.0
    if CLIP_RE.search(txt):
        return 1.0
    if any(c in CHZZK_CLIP_EMOJI for c in _emoji_codes(txt)):
        return 1.0
    return 0.0



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
        if clip_score(txt):
            out["chat_clip"][i] += 1.0
        if e["kind"] in ("paid", "member"):
            out["chat_paid"][i] += 1.0
    return out


def merge(*event_lists: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """여러 플랫폼의 채팅을 하나로 합친다 (시간순).

    동시송출을 하면 유튜브 채팅만으로는 반응의 절반만 보게 되므로,
    치지직 등 다른 곳의 채팅도 같이 넣어 하나의 신호로 만든다.
    각 이벤트의 `source` 는 그대로 유지되어 통계에서 어디서 왔는지 셀 수 있다.
    """
    out: List[Dict[str, Any]] = []
    for lst in event_lists:
        if lst:
            out.extend(lst)
    out.sort(key=lambda e: e.get("t", 0.0))
    return out


def stats(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not events:
        return {"count": 0}
    laugh = sum(1 for e in events if laugh_score(e["text"] or ""))
    clip = sum(1 for e in events if clip_score(e["text"] or ""))
    paid = sum(1 for e in events if e["kind"] in ("paid", "member"))
    span = events[-1]["t"] - events[0]["t"]
    by_source: Dict[str, int] = {}
    for e in events:
        s = e.get("source") or "youtube"
        by_source[s] = by_source.get(s, 0) + 1
    return {
        "count": len(events),
        "by_source": by_source,
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
        if clip_score(txt):
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
