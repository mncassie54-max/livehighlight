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

# ---------------------------------------------------------------------- 실행
say ""
say "▶ Live Highlight 실행 — 잠시 뒤 브라우저에 작업 화면이 열립니다."
say "  (끝내려면 이 창에서 Control + C 를 누르거나 창을 닫으세요.)"
say ""
exec .venv/bin/python -m livehl "$@"
