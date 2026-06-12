from __future__ import annotations

import os
import sys
from pathlib import Path


SCAN_DIRS = (Path("frontend/dist"), Path("backend/app/static"))
MARKERS = ("BOT_TOKEN", "bot_token", "WEBHOOK_SECRET", "OWNER_USER_ID")


def _iter_files(scan_dir: Path):
    for path in scan_dir.rglob("*"):
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
    existing_dirs = [scan_dir for scan_dir in SCAN_DIRS if scan_dir.exists()]
    if not existing_dirs:
        print("frontend/dist/ and backend/app/static/ not found - skipping SC-5 check")
        return 0

    bot_token = os.environ.get("BOT_TOKEN", "")
    webhook_secret = os.environ.get("WEBHOOK_SECRET", "")
    owner_user_id = os.environ.get("OWNER_USER_ID", "")
    markers = MARKERS + tuple(
        value for value in (bot_token, webhook_secret, owner_user_id) if value
    )

    for scan_dir in existing_dirs:
        for path in _iter_files(scan_dir):
            marker = _contains_marker(path, markers)
            if marker is not None:
                print(f"SC-5 FAIL: secret marker {marker!r} found in {path}")
                return 1

    scanned = ", ".join(str(path) for path in existing_dirs)
    print(f"SC-5 PASS: no bot token or webhook secret in built assets ({scanned})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
