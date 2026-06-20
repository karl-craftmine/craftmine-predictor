"""Refresh the cached upcoming-fixtures list for your follows.

Run by hand, or on a schedule (see the Windows task set up in the README). It
scrapes today's fixtures for followed competitions + each followed team's next
matches and writes them to cache/fixtures.json, which the web app reads.

    python refresh_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from footy.fixtures import refresh_cache

HERE = Path(__file__).parent
CACHE = HERE / "cache"


def main() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    try:
        payload = refresh_cache(CACHE / "follows.json", CACHE / "fixtures.json")
    except Exception as exc:  # noqa: BLE001
        print(f"refresh failed: {exc}", file=sys.stderr)
        return 1
    print(f"refreshed {len(payload['fixtures'])} fixtures at {payload['updated']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
