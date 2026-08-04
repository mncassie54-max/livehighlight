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

  # 프로그램 파일만 덮어쓴다. .venv 와 data 는 tar 안에 없으므로 그대로 남는다.
  if ! cp -R "$src/." "$target/"; then
    fail "$label 파일을 복사하지 못했습니다."
  fi

  chmod +x "$target"/*.command 2>/dev/null

  # 인터넷에서 받은 표시를 지운다 → 받는 분이 "확인되지 않은 개발자" 경고를 안 본다
  xattr -dr com.apple.quarantine "$target" 2>/dev/null

  say "  ✓ $label 준비 완료"
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
hr
say "  ✅ 설치가 끝났습니다"
hr
say ""
say "폴더를 열어 드립니다. 안에 프로그램 폴더 두 개가 있습니다."
say ""
say "  ✂️  AutoCut        — 영상의 조용한 부분 잘라내기"
say "  🔍 LiveHighlight  — 방송에서 재밌는 구간 찾기"
say ""
say "쓰고 싶은 폴더에 들어가서 [ 업데이트.command ] 를 더블클릭하면 실행됩니다."
say "그 파일이 실행 버튼입니다. 다음부터도 항상 그 파일만 누르시면 됩니다."
say ""
say "이 창은 이제 닫으셔도 됩니다."
say ""

open "$DEST" 2>/dev/null
