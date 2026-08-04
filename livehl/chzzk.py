"""치지직(CHZZK) 다시보기 채팅 수집.

유튜브와 동시송출하는 경우 유튜브 채팅만 보면 시청자 반응의 절반만 보게 된다.
치지직 다시보기 채팅을 따로 받아 chat.py 와 같은 형식으로 만들어 합친다.

시간축은 `playerMessageTime`(다시보기 재생 위치, ms)을 그대로 쓴다.
유튜브 VOD 초와 마찬가지로 **다시보기 0초 기준**이므로, 두 플랫폼의 다시보기
시작점이 같다면 그대로 합칠 수 있다. 어긋나면 프로젝트의 오프셋으로 보정한다.

주의: 치지직은 공개 API 문서가 없다. 응답 형태가 바뀔 수 있으므로 파싱은
모두 방어적으로 한다. 실패해도 분석 자체는 계속 진행하되(유튜브 채팅만으로),
왜 못 받았는지는 `error` 에 담아 화면에 보여준다 — 조용히 비면 원인을 알 수 없다.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

Progress = Optional[Callable[[float, str], None]]

API = "https://api.chzzk.naver.com"
VIDEO_RE = re.compile(r"chzzk\.naver\.com/video/(\d+)")

# 브라우저처럼 보이지 않으면 거절당한다.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://chzzk.naver.com/",
}

# 한 번에 받아오는 채팅 수 (API 상한이 있어 그 이상은 무시된다)
PAGE = 100


def video_no(url: str) -> Optional[str]:
    """치지직 다시보기 URL 에서 영상 번호를 뽑는다. 아니면 None."""
    if not url:
        return None
    m = VIDEO_RE.search(url.strip())
    if m:
        return m.group(1)
    # 번호만 붙여넣은 경우도 받아준다
    s = url.strip()
    return s if s.isdigit() else None


def _get(path: str, timeout: float = 15.0):
    """(내용, 오류메시지) 를 돌려준다. 성공하면 오류는 None.

    조용히 실패하면 "치지직 채팅이 왜 안 들어왔는지" 알 수 없으므로,
    치지직이 돌려준 한글 메시지를 그대로 위로 넘긴다.
    """
    req = urllib.request.Request(API + path, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8", "replace")).get("message")
        except Exception:  # noqa: BLE001
            msg = None
        return None, msg or ("치지직 응답 오류 (HTTP %s)" % e.code)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return None, "치지직에 연결하지 못했습니다 (%s)" % type(e).__name__
    try:
        data = json.loads(body)
    except ValueError:
        return None, "치지직 응답을 이해하지 못했습니다."
    if not isinstance(data, dict):
        return None, "치지직 응답 형식이 예상과 다릅니다."
    content = data.get("content")
    return (content if isinstance(content, dict) else data), None


def meta(no: str) -> Dict[str, Any]:
    """영상 길이·제목 등. 실패하면 error 에 이유가 담긴다."""
    c, err = _get("/service/v2/videos/%s" % no)
    if c is None:
        c2, err2 = _get("/service/v1/videos/%s" % no)
        if c2 is not None:
            c, err = c2, None
        else:
            err = err or err2
    c = c or {}
    return {
        "video_no": no,
        "title": c.get("videoTitle") or c.get("title"),
        "duration": c.get("duration"),
        "publish_date": c.get("publishDate") or c.get("liveOpenDate"),
        "error": err,
    }


def _nickname(chat: Dict[str, Any]) -> str:
    p = chat.get("profile")
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except ValueError:
            return ""
    if isinstance(p, dict):
        return p.get("nickname") or ""
    return ""


def _amount(chat: Dict[str, Any]) -> float:
    """후원 금액. 후원이 아니면 0."""
    e = chat.get("extras")
    if isinstance(e, str):
        try:
            e = json.loads(e)
        except ValueError:
            return 0.0
    if isinstance(e, dict):
        for k in ("payAmount", "payedAmount", "donationAmount"):
            try:
                v = float(e.get(k) or 0)
            except (TypeError, ValueError):
                continue
            if v > 0:
                return v
    return 0.0


def fetch(url: str, out_dir: str, progress: Progress = None) -> Dict[str, Any]:
    """다시보기 채팅을 전부 받아 JSONL 로 저장한다.

    반환: {video_no, title, duration, chat_file, count}
    받지 못하면 chat_file 이 None 이다.
    """
    no = video_no(url)
    if not no:
        return {"chat_file": None, "count": 0, "error": "치지직 다시보기 주소가 아닙니다."}

    os.makedirs(out_dir, exist_ok=True)
    if progress:
        progress(0.05, "치지직 영상 정보 조회 중…")

    info = meta(no)
    if info.get("error"):
        info["chat_file"] = None
        info["count"] = 0
        if progress:
            progress(1.0, "치지직: %s" % info["error"])
        return info
    total_ms = 0
    try:
        total_ms = int(float(info.get("duration") or 0) * 1000)
    except (TypeError, ValueError):
        total_ms = 0

    path = os.path.join(out_dir, "chzzk_chat.jsonl")
    seen = set()
    count = 0
    t_ms = 0
    stall = 0

    with open(path, "w", encoding="utf-8") as f:
        while True:
            # 이 API 는 지정한 시각부터 "영상 끝까지" 를 돌려준다 (한 번에 오는 양에 상한이 있다).
            # previousVideoChatSize 는 50 이외의 값을 주면 거절하므로 아예 넣지 않는다.
            data, err = _get(
                "/service/v1/videos/%s/chats?playerMessageTime=%d" % (no, t_ms)
            )
            if err:
                if count == 0:
                    info["error"] = err      # 한 건도 못 받았으면 이유를 남긴다
                break
            chats = (data or {}).get("videoChats")
            if not isinstance(chats, list) or not chats:
                break                        # 앞으로 더 없으면 끝

            newest = t_ms
            added = 0
            for c in chats:
                if not isinstance(c, dict):
                    continue
                try:
                    ms = int(c.get("playerMessageTime") or 0)
                except (TypeError, ValueError):
                    continue
                key = (ms, c.get("content") or "", _nickname(c))
                if key in seen:
                    continue
                seen.add(key)
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
                count += 1
                added += 1
                newest = max(newest, ms)

            if progress:
                if total_ms > 0:
                    progress(min(0.95, newest / float(total_ms)),
                             "치지직 채팅 %d개 수신 중…" % count)
                else:
                    progress(0.5, "치지직 채팅 %d개 수신 중…" % count)

            # 새로 받은 게 없거나 시간이 앞으로 안 가면 다 받은 것이다.
            # (같은 시각에 메시지가 몰려 상한에 걸리는 경우를 대비해 1ms 씩 민다)
            if added == 0:
                break
            if newest <= t_ms:
                stall += 1
                if stall >= 3:
                    break
                t_ms += 1
            else:
                stall = 0
                t_ms = newest + 1

            time.sleep(0.05)  # 너무 빠르게 두드리지 않는다

    info["chat_file"] = path if count else None
    info["count"] = count
    if progress:
        progress(1.0, "치지직 채팅 %d개" % count)
    return info


def parse(chat_file: Optional[str]) -> List[Dict[str, Any]]:
    """chat.parse 와 같은 형식으로 만든다: [{t, text, author, kind, amount}]"""
    events: List[Dict[str, Any]] = []
    if not chat_file or not os.path.exists(chat_file):
        return events

    with open(chat_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                c = json.loads(line)
            except ValueError:
                continue
            try:
                t = int(c.get("playerMessageTime") or 0) / 1000.0
            except (TypeError, ValueError):
                continue
            amount = _amount(c)
            events.append({
                "t": t,
                "text": c.get("content") or "",
                "author": _nickname(c),
                "kind": "paid" if amount > 0 else "text",
                "amount": amount,
                "source": "chzzk",
            })

    events.sort(key=lambda e: e["t"])
    return events
