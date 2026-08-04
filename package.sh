#!/usr/bin/env bash
# 다른 Mac 으로 옮길 zip 파일을 만든다.
# 가상환경(.venv)과 분석 데이터(data)는 제외하므로 용량이 작다 (수백 KB).
#
#   ./package.sh                    → ~/Desktop/livehl-YYYYMMDD.zip
#   ./package.sh /path/to/out.zip   → 지정한 경로로
set -euo pipefail
cd "$(dirname "$0")"

OUT="${1:-$HOME/Desktop/livehl-$(date +%Y%m%d).zip}"
OUT_DIR="$(dirname "$OUT")"
mkdir -p "$OUT_DIR"
rm -f "$OUT"

zip -r -q "$OUT" \
  livehl web README.md 사용법.md 설치.md requirements.txt start.sh package.sh \
  -x '*__pycache__*' '*.pyc' '*.DS_Store'

echo "만들었습니다: $OUT"
echo "크기: $(du -h "$OUT" | cut -f1)"
echo
echo "다른 Mac 으로 옮기는 방법:"
echo "  1) 에어드롭 / USB / 클라우드로 이 zip 을 옮긴다"
echo "  2) 압축을 푼다 (더블클릭)"
echo "  3) 터미널에서:  cd <풀린폴더> && ./start.sh"
echo "     (첫 실행 때 필요한 것들을 자동으로 내려받는다 — 인터넷 연결 필요)"
