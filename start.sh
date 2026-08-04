#!/usr/bin/env bash
# livehl 실행 스크립트 — 처음 실행하면 가상환경과 의존성을 자동으로 준비한다.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "가상환경 생성 중…"
  python3 -m venv .venv
fi

if ! .venv/bin/python -c "import numpy, yt_dlp" >/dev/null 2>&1; then
  echo "의존성 설치 중…"
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -r requirements.txt
fi

exec .venv/bin/python -m livehl "$@"
