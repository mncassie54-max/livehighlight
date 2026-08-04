#!/bin/bash
# Live Highlight 업데이트 + 실행 런처 (macOS) — Finder에서 이 파일을 더블클릭하면 됩니다.
#
# 최신 버전을 받아온 뒤 Live Highlight 를 실행합니다.
# 인터넷이 안 되거나 갱신이 실패해도, 지금 갖고 있는 버전으로 그냥 실행합니다.
#
# 두 가지 설치 방식을 모두 지원합니다.
#   · 설치.command 로 받은 폴더  → 인터넷에서 최신 파일을 직접 받아 덮어씀
#   · git clone 으로 받은 폴더    → git pull

cd "$(dirname "$0")"

OWNER="mncassie54-max"
REPO="livehighlight"
LAUNCH="./실행.command"

say() { printf '%s\n' "$1"; }

건너뛰고_실행() {
  say ""
  say "⚠️  최신 버전을 받지 못했습니다. ($1)"
  say "   지금 갖고 있는 버전으로 실행합니다."
  say ""
  exec "$LAUNCH" "$@"
}

say "▶ 최신 버전이 있는지 확인합니다…"

# ── git 으로 받은 폴더라면 git 으로 갱신한다 ──────────────────────────
if [ -d .git ]; then
  GIT="$(command -v git || echo /usr/bin/git)"
  if [ ! -x "$GIT" ]; then
    건너뛰고_실행 "git 을 찾을 수 없음"
  fi
  if "$GIT" pull --ff-only; then
    say "▶ 최신 상태입니다."
  else
    say ""
    say "⚠️  받아오지 못했습니다. 폴더 안 파일을 직접 고치셨다면 충돌일 수 있습니다."
    say "   지금 갖고 있는 버전으로 실행합니다."
  fi
  say ""
  exec "$LAUNCH" "$@"
fi

# ── 설치.command 로 받은 폴더라면 파일을 직접 받아 덮어쓴다 ────────────
tmp="$(mktemp -d)" || 건너뛰고_실행 "임시 폴더 생성 실패"

if ! curl -fsSL "https://codeload.github.com/$OWNER/$REPO/tar.gz/refs/heads/main" \
     -o "$tmp/src.tgz"; then
  rm -rf "$tmp"; 건너뛰고_실행 "내려받기 실패"
fi

if ! tar -xzf "$tmp/src.tgz" -C "$tmp"; then
  rm -rf "$tmp"; 건너뛰고_실행 "압축 풀기 실패"
fi

src="$(find "$tmp" -maxdepth 1 -type d -name "$REPO-*" | head -1)"
if [ -z "$src" ]; then
  rm -rf "$tmp"; 건너뛰고_실행 "받은 파일이 예상과 다름"
fi

# 이 스크립트 자신도 덮어쓰기 대상이다. 실행 중인 파일을 그대로 덮으면
# 남은 줄을 잘못 읽을 수 있으므로, 복사와 실행은 임시 폴더의 helper 에게 맡긴다.
cat > "$tmp/apply.sh" <<'HELPER'
#!/bin/bash
src="$1"; dst="$2"; launch="$3"; tmp="$4"; shift 4
# 받는 분 폴더에는 배포목록.txt 에 적힌 것만 둔다(개발용 파일 제외).
# 목록이 없는 옛 버전이면 전체를 복사한다.
if [ -f "$src/배포목록.txt" ]; then
  while IFS= read -r item; do
    case "$item" in ""|\#*) continue;; esac
    [ -e "$src/$item" ] || continue
    cp -R "$src/$item" "$dst/" 2>/dev/null
  done < "$src/배포목록.txt"
else
  cp -R "$src/." "$dst/" 2>/dev/null
fi
chmod +x "$dst"/*.command 2>/dev/null
xattr -dr com.apple.quarantine "$dst" 2>/dev/null
cd "$dst" || exit 1
rm -rf "$tmp"
printf '%s\n' "▶ 최신 상태입니다."
printf '\n'
exec "$launch" "$@"
HELPER

chmod +x "$tmp/apply.sh"
exec "$tmp/apply.sh" "$src" "$PWD" "$LAUNCH" "$tmp" "$@"
