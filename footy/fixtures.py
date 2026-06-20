"""Follow list + upcoming-fixtures aggregation via TheSportsDB (fast HTTP).

A small JSON file holds what you follow:
    {"teams": ["Brazil", ...], "competitions": ["4429", "4328", ...]}
(competitions are TheSportsDB league ids; teams are names.)

build_fixtures() asks TheSportsDB for each followed league's and team's next
matches and keeps those within `days` days. No browser, so it's near-instant.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from . import sportsdb


def load_follows(path: str | Path) -> dict[str, list[str]]:
    p = Path(path)
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8-sig"))  # tolerate a BOM
            return {"teams": d.get("teams", []), "competitions": d.get("competitions", [])}
        except json.JSONDecodeError:
            pass
    return {"teams": [], "competitions": []}


def save_follows(path: str | Path, follows: dict) -> None:
    Path(path).write_text(json.dumps({
        "teams": sorted(set(follows.get("teams", []))),
        "competitions": sorted(set(str(c) for c in follows.get("competitions", []))),
    }), encoding="utf-8")


def build_fixtures(follows: dict, days: int = 7) -> list[dict[str, Any]]:
    """Upcoming fixtures (next `days` days) for followed leagues + teams."""
    league_ids = {str(c) for c in follows.get("competitions", [])}
    team_names = follows.get("teams", [])
    today = datetime.date.today()
    cutoff = today + datetime.timedelta(days=days)

    raw: list[dict[str, Any]] = []
    # Competitions: sweep each day (per-league 'next' feed is sparse). Filter by
    # league id, so we only fetch what's followed out of each day's events.
    if league_ids:
        for i in range(days):
            day = (today + datetime.timedelta(days=i)).isoformat()
            for e in sportsdb.events_on_day(day):
                if str(e.get("idLeague") or "") in league_ids:
                    raw.append(e)
    # Teams: each followed team's own upcoming-match feed.
    for name in team_names:
        tid = sportsdb.search_team_id(name)
        if tid:
            raw += sportsdb.team_next(tid)

    seen, merged = set(), []
    for e in raw:
        f = sportsdb.normalize(e)
        if not (f["date"] and f["home"] and f["away"]):
            continue
        try:
            d = datetime.date.fromisoformat(f["date"])
        except ValueError:
            continue
        if not (today <= d <= cutoff):
            continue
        key = (f["date"], f["home"].lower(), f["away"].lower())
        if key in seen:
            continue
        seen.add(key)
        merged.append(f)

    merged.sort(key=lambda x: (x["date"], x.get("time") or "", x["home"]))
    return merged


def refresh_cache(follows_path: str | Path, cache_path: str | Path) -> dict:
    """Build fixtures for the saved follows and write them to the cache file."""
    fixtures = build_fixtures(load_follows(follows_path))
    payload = {"updated": datetime.datetime.now().isoformat(timespec="seconds"),
               "fixtures": fixtures}
    Path(cache_path).write_text(json.dumps(payload), encoding="utf-8")
    return payload
