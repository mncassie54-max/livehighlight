"""python -m livehl [--port 8777] [--no-browser]"""

import argparse
import sys

from . import config


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="livehl", description="OBS 라이브 하이라이트 편집 보조 툴")
    ap.add_argument("--host", default=config.SERVER_HOST)
    ap.add_argument("--port", type=int, default=config.SERVER_PORT)
    ap.add_argument("--no-browser", action="store_true", help="브라우저 자동 실행 안 함")
    ap.add_argument("--doctor", action="store_true", help="환경만 점검하고 종료")
    args = ap.parse_args(argv)

    if args.doctor:
        from .ffmpeg_tools import doctor
        import json

        print(json.dumps(doctor(), ensure_ascii=False, indent=2))
        return 0

    from .server import serve

    serve(args.host, args.port, not args.no_browser)
    return 0


if __name__ == "__main__":
    sys.exit(main())
