from __future__ import annotations

import os
import sys
from pathlib import Path


DIST_DIR = Path("frontend/dist")
MARKERS = ("BOT_TOKEN", "bot_token", "WEBHOOK_SECRET")


def _iter_dist_files():
    for path in DIST_DIR.rglob("*"):
        if path.is_file():
            yield path


def _contains_marker(path: Path, markers: tuple[str, ...]) -> str | None:
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    for marker in markers:
        if marker and marker in content:
            return marker
    return None


def main() -> int:
    if not DIST_DIR.exists():
        print("frontend/dist/ not found - skipping SC-5 check")
        return 0

    bot_token = os.environ.get("BOT_TOKEN", "")
    markers = MARKERS + ((bot_token,) if bot_token else ())

    for path in _iter_dist_files():
        marker = _contains_marker(path, markers)
        if marker is not None:
            print(f"SC-5 FAIL: secret marker {marker!r} found in {path}")
            return 1

    print("SC-5 PASS: no bot token in frontend bundle")
    return 0


if __name__ == "__main__":
    sys.exit(main())
