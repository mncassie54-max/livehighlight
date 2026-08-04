#!/bin/bash
# Live Highlight 실행 런처 (macOS) — Finder에서 이 파일을 더블클릭하면 됩니다.
#
# 처음 실행할 때 이 폴더 안에 .venv 를 만들고 필요한 패키지를 설치한 뒤
# 브라우저에 작업 화면을 띄웁니다. (첫 실행만 몇 분 걸립니다.)

set -e
cd "$(dirname "$0")"

say() { printf '%s\n' "$1"; }
fail() {
  say ""
  say "❌ $1"
  say ""
  say "이 창은 닫아도 됩니다. (문제가 계속되면 위 메시지를 그대로 전달해 주세요.)"
  exit 1
}

# ---------------------------------------------------------------- 파이썬 찾기
find_python() {
  for cand in \
    python3.14 python3.13 python3.12 python3.11 \
    /opt/homebrew/bin/python3 /usr/local/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
    python3 /usr/bin/python3
  do
    path="$(command -v "$cand" 2>/dev/null || true)"
    [ -n "$path" ] || continue
    "$path" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null || continue
    printf '%s\n' "$path"
    return 0
  done
  return 1
}

PYTHON="$(find_python)" || fail "Python 3.9 이상을 찾을 수 없습니다.
  터미널에서 다음을 실행해 개발자 도구(파이썬 포함)를 설치하세요:
    xcode-select --install"

# ------------------------------------------------------------- 가상환경 준비
if [ ! -x .venv/bin/python ]; then
  say "▶ 처음 실행이라 준비를 합니다 (몇 분 걸립니다)…"
  say "  사용할 파이썬: $PYTHON ($("$PYTHON" -V 2>&1))"
  rm -rf .venv
  "$PYTHON" -m venv .venv || fail "가상환경(.venv) 생성에 실패했습니다."
fi

# requirements.txt 가 바뀌면 다시 설치한다
STAMP=".venv/.livehl-deps"
WANT="$(shasum requirements.txt | awk '{print $1}')"
HAVE="$(cat "$STAMP" 2>/dev/null || true)"
if [ "$WANT" != "$HAVE" ]; then
  say "▶ 필요한 패키지를 설치합니다…"
  .venv/bin/python -m pip install -q --upgrade pip \
    || fail "pip 업그레이드에 실패했습니다. 인터넷 연결을 확인하세요."
  .venv/bin/python -m pip install -q -r requirements.txt \
    || fail "패키지 설치에 실패했습니다. 인터넷 연결을 확인하세요."
  printf '%s\n' "$WANT" > "$STAMP"
fi

# ---------------------------------------------- 채팅 수집기(yt-dlp) 최신 유지
# yt-dlp 는 유튜브가 내부 구조를 바꿀 때마다 깨진다(몇 주 단위로 일어난다).
# 낡은 채로 두면 "채팅을 받지 못했습니다" 만 보이고 원인을 알 수 없으므로
# 하루에 한 번 최신으로 맞춘다. 실패하면 지금 버전으로 그냥 진행한다.
YT_STAMP=".venv/.ytdlp-checked"
TODAY="$(date +%Y%m%d)"
if [ "$(cat "$YT_STAMP" 2>/dev/null || true)" != "$TODAY" ]; then
  say "▶ 채팅 수집기를 최신으로 맞춥니다 (하루에 한 번만 확인합니다)…"
  # 인터넷이 없으면 pip 은 "이미 설치됨" 으로 판단해 성공(0)을 반환한다.
  # 그러면 갱신하지도 못한 채 확인했다고 표시되므로, 먼저 연결을 직접 확인한다.
  # pip 출력은 로그로 돌려 두고 실패했을 때만 보여준다(경고문이 겁을 주지 않게).
  YT_LOG="$(mktemp)"
  if curl -fsS --max-time 8 -o /dev/null "https://pypi.org/simple/yt-dlp/" 2>/dev/null \
     && .venv/bin/python -m pip install -q --upgrade --timeout 10 --retries 1 yt-dlp \
        > "$YT_LOG" 2>&1; then
    printf '%s\n' "$TODAY" > "$YT_STAMP"
    say "  ✓ 준비됨 (yt-dlp $(.venv/bin/python -c 'import yt_dlp; print(yt_dlp.version.__version__)' 2>/dev/null || echo '버전 확인 실패'))"
  else
    say "  ⚠️  최신 확인을 건너뜁니다 (인터넷 연결을 확인해 주세요)."
    say "     지금 갖고 있는 버전으로 계속합니다. 채팅 수집이 안 되면 이 창을 다시 실행해 보세요."
  fi
  rm -f "$YT_LOG"
fi

# ---------------------------------------------------------------------- 실행
say ""
say "▶ Live Highlight 실행 — 잠시 뒤 브라우저에 작업 화면이 열립니다."
say "  (끝내려면 이 창에서 Control + C 를 누르거나 창을 닫으세요.)"
say ""
exec .venv/bin/python -m livehl "$@"
