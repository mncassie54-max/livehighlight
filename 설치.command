#!/bin/bash
# 영상 편집 도구 설치 — 이 파일 하나만 더블클릭하면 됩니다.
#
# AutoCut 과 Live Highlight 를 인터넷에서 받아 한 폴더에 정리합니다.
# GitHub 계정도, 별도 앱도 필요 없습니다. 맥에 기본으로 있는 것만 씁니다.
#
# 이미 설치되어 있으면 최신 버전으로 갱신합니다.
# 이때 이미 받아둔 패키지(.venv)와 분석 결과(data)는 건드리지 않습니다.

cd "$(dirname "$0")"

OWNER="mncassie54-max"
DEST="$HOME/Documents/영상편집도구"

say() { printf '%s\n' "$1"; }
hr()  { say "────────────────────────────────────────"; }

fail() {
  say ""
  say "❌ $1"
  say ""
  say "인터넷 연결을 확인하고 다시 시도해 주세요."
  say "계속 안 되면 이 창의 내용을 그대로 전달해 주시면 됩니다."
  say ""
  say "이 창은 닫으셔도 됩니다."
  exit 1
}

# 한 개 받아서 풀기:  가져오기 <저장소이름> <설치할폴더이름> <보여줄이름>
가져오기() {
  repo="$1"; dir="$2"; label="$3"
  target="$DEST/$dir"

  say ""
  say "▶ $label 내려받는 중…"

  tmp="$(mktemp -d)" || fail "임시 폴더를 만들지 못했습니다."
  trap 'rm -rf "$tmp"' RETURN

  if ! curl -fL --progress-bar \
      "https://codeload.github.com/$OWNER/$repo/tar.gz/refs/heads/main" \
      -o "$tmp/src.tgz"; then
    fail "$label 을 내려받지 못했습니다."
  fi

  if ! tar -xzf "$tmp/src.tgz" -C "$tmp"; then
    fail "$label 압축을 푸는 데 실패했습니다."
  fi

  # 압축을 풀면 <저장소이름>-main 폴더 하나가 나온다
  src="$(find "$tmp" -maxdepth 1 -type d -name "$repo-*" | head -1)"
  [ -n "$src" ] || fail "$label 내려받은 파일이 예상과 다릅니다."

  mkdir -p "$target" || fail "설치 폴더를 만들지 못했습니다: $target"

  # 저장소에는 개발용 파일(README·tests·docs 등)도 들어 있다.
  # 받는 분 폴더가 어지럽지 않게 배포목록.txt 에 적힌 것만 가져온다.
  # 목록 파일이 없는 옛 버전이면 전체를 복사한다.
  if [ -f "$src/배포목록.txt" ]; then
    while IFS= read -r item; do
      case "$item" in ""|\#*) continue;; esac
      [ -e "$src/$item" ] || continue
      cp -R "$src/$item" "$target/" || fail "$label 파일을 복사하지 못했습니다."
    done < "$src/배포목록.txt"
  elif ! cp -R "$src/." "$target/"; then
    fail "$label 파일을 복사하지 못했습니다."
  fi

  chmod +x "$target"/*.command 2>/dev/null

  # 인터넷에서 받은 표시를 지운다 → 받는 분이 "확인되지 않은 개발자" 경고를 안 본다
  xattr -dr com.apple.quarantine "$target" 2>/dev/null

  say "  ✓ $label 준비 완료"
}

# ── Dock 에 고정할 수 있는 앱 런처 만들기 ─────────────────────────────
# .command 파일은 Dock 앱 자리에 고정할 수 없다. 그래서 macOS 에 기본으로 있는
# osacompile 로 진짜 .app 을 만든다. 아이콘도 이때 붙일 수 있다.
# 이 맥에서 직접 만든 파일이라 인터넷 격리 표시가 없고, 따라서
# "확인되지 않은 개발자" 경고도 나오지 않는다.
앱만들기() {
  dir="$1"; appname="$2"; iconname="$3"
  target="$DEST/$appname.app"
  launcher="$DEST/$dir/업데이트.command"

  if ! command -v osacompile >/dev/null 2>&1; then
    return 1
  fi

  work="$(mktemp -d)" || return 1
  # Terminal 을 직접 조종(AppleEvent)하면 macOS 가 자동화 권한을 묻는다.
  # open 으로 열면 그 창이 뜨지 않는다.
  cat > "$work/launch.applescript" <<EOF
on run
	do shell script "open " & quoted form of "$launcher"
end run
EOF

  rm -rf "$target"
  if ! osacompile -o "$target" "$work/launch.applescript" 2>/dev/null; then
    rm -rf "$work"; return 1
  fi

  icon="$DEST/$dir/icons/$iconname"
  if [ -f "$icon" ]; then
    cp "$icon" "$target/Contents/Resources/applet.icns" 2>/dev/null
    touch "$target" 2>/dev/null      # Finder 가 아이콘을 다시 읽게 한다
  fi

  rm -rf "$work"
  return 0
}

hr
say "  영상 편집 도구 설치"
hr
say ""
say "AutoCut 과 Live Highlight 를 아래 폴더에 설치합니다."
say "  $DEST"
say ""
say "잠시 걸립니다 (보통 10~30초). 창을 닫지 말고 기다려 주세요."

mkdir -p "$DEST" || fail "폴더를 만들지 못했습니다: $DEST"

가져오기 "autocut"       "AutoCut"       "✂️  AutoCut"
가져오기 "livehighlight" "LiveHighlight" "🔍 Live Highlight"

say ""
say "▶ 실행 아이콘을 만듭니다…"
앱만들기 "AutoCut"       "AutoCut"        "AutoCut.icns"       && APP_AC=1
앱만들기 "LiveHighlight" "Live Highlight" "LiveHighlight.icns" && APP_LH=1
if [ "${APP_AC:-}" = "1" ] || [ "${APP_LH:-}" = "1" ]; then
  say "  ✓ 아이콘 준비 완료"
else
  say "  ⚠️  아이콘을 만들지 못했습니다. 폴더 안 [업데이트.command] 로도 실행됩니다."
fi

say ""
hr
say "  ✅ 설치가 끝났습니다"
hr
say ""
say "폴더를 열어 드립니다. 아이콘 두 개가 보입니다."
say ""
say "  ✂️  AutoCut          — 영상의 조용한 부분 잘라내기"
say "  🔍 Live Highlight   — 방송에서 재밌는 구간 찾기"
say ""
say "이 아이콘을 더블클릭하면 실행됩니다."
say ""
say "💡 아이콘을 화면 아래 Dock 으로 끌어다 놓으세요."
say "   그러면 다음부터는 폴더를 열지 않고 Dock 에서 바로 켤 수 있습니다."
say ""
say "   (아이콘 대신 프로그램 폴더 안의 [업데이트.command] 를 눌러도 똑같습니다)"
say ""
say "이 창은 이제 닫으셔도 됩니다."
say ""

open "$DEST" 2>/dev/null
