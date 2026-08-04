#!/bin/bash
# livehl 업데이트 + 실행 런처 (macOS) — Finder에서 이 파일을 더블클릭하면 됩니다.
#
# 최신 버전을 내려받은 뒤 livehl 을 실행합니다.
# 인터넷이 안 되거나 업데이트가 실패해도, 지금 갖고 있는 버전으로 그냥 실행합니다.

cd "$(dirname "$0")"

GIT="$(command -v git || echo /usr/bin/git)"

say() { printf '%s\n' "$1"; }

say "▶ 최신 버전이 있는지 확인합니다…"

if [ ! -d .git ]; then
  say ""
  say "ℹ️  이 폴더는 GitHub Desktop 으로 받은 폴더가 아닙니다."
  say "   업데이트를 건너뛰고 그대로 실행합니다."
  say ""
elif [ ! -x "$GIT" ]; then
  say ""
  say "ℹ️  git 을 찾을 수 없어 업데이트를 건너뜁니다."
  say "   (GitHub Desktop 앱에서 'Pull origin' 을 눌러 직접 받아도 됩니다.)"
  say ""
elif ! "$GIT" pull --ff-only 2>&1; then
  say ""
  say "⚠️  업데이트를 받지 못했습니다."
  say ""
  say "   흔한 원인:"
  say "     · 인터넷이 안 됨"
  say "     · 이 폴더 안의 파일을 직접 고쳐서 충돌이 남"
  say ""
  say "   GitHub Desktop 앱을 열어 'Pull origin' 을 눌러보세요."
  say "   일단은 지금 갖고 있는 버전으로 실행합니다."
  say ""
else
  say "▶ 최신 상태입니다."
fi

say ""
exec ./실행.command "$@"
