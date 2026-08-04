"""기본 설정값. 프로젝트별로 project.json 에서 덮어쓸 수 있다."""

import os

# 워크스페이스: 프로젝트 데이터(신호 캐시, 클립, 익스포트)가 저장되는 곳
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("LIVEHL_DATA", os.path.join(ROOT, "data"))
WEB_DIR = os.path.join(ROOT, "web")

# 오디오 분석 파라미터
AUDIO_SR = 8000          # 분석용 샘플레이트 (에너지 분석엔 충분)
AUDIO_HOP = 0.02         # 20ms 프레임 → 50Hz 엔벨로프 (웃음 4~8Hz 진동 검출 가능)

# 하이라이트 점수 가중치
DEFAULT_WEIGHTS = {
    "chat_rate": 1.0,      # 채팅 속도 급증
    "chat_laugh": 1.2,     # ㅋㅋ / 😂 밀도
    "chat_hype": 0.8,      # 와, ㄷㄷ, 대박, 레전드 …
    "chat_clip": 2.0,      # "클립", "박제", "편집해줘" → 시청자가 직접 지목한 구간
    "chat_paid": 0.6,      # 슈퍼챗 / 멤버십
    "mic_excite": 1.0,     # 마이크 음량 급상승
    "mic_laugh": 1.3,      # 웃음 특유의 진폭 진동
}

DEFAULT_DETECT = {
    "threshold": 1.8,      # 융합 점수(z-score 합) 최소값
    "min_gap": 45,         # 피크 간 최소 간격(초)
    "pre_roll": 25,        # 피크 앞으로 확보할 시간(초)
    "post_roll": 20,       # 피크 뒤로 확보할 시간(초)
    "max_len": 180,        # 한 후보의 최대 길이(초)
    "merge_gap": 8,        # 이 간격 이하로 붙은 후보는 하나로 병합(초)
    "top_n": 40,           # 상위 N개만 유지
    "smooth": 8,           # 점수 곡선 가우시안 스무딩 폭(초)
}

DEFAULT_EXPORT = {
    "preview_height": 540,
    "shorts_layout": "blur",   # crop | blur
    "shorts_focus_x": 0.5,     # 0.0(왼쪽) ~ 1.0(오른쪽) 크롭 중심
    "shorts_max_len": 59,
}

SERVER_HOST = os.environ.get("LIVEHL_HOST", "127.0.0.1")
SERVER_PORT = int(os.environ.get("LIVEHL_PORT", "8777"))

VIDEO_EXTS = {".mkv", ".mp4", ".mov", ".flv", ".ts", ".m4v", ".webm", ".avi"}
