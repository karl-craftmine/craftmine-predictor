"""API-Football (api-sports.io) — match stats for national teams.

WhoScored barely tracks internationals; API-Football does (corners, shots, cards
for the World Cup etc.). This is used as a fallback for teams WhoScored can't
cover. It needs a FREE key (https://www.api-football.com/ → dashboard), set via
the APIFOOTBALL_KEY env var or a `cache/apifootball_key.txt` file. Free tier is
~100 requests/day, so results are cached on disk to conserve it.

Returns matches in the SAME shape as the WhoScored scraper, so build_form()
and the simulator work unchanged.

NOTE: Free tier cannot use fixtures?last= — we pull finished games by season
(2024→2022) instead. Verify against your plan if stats look sparse.
"""

from __future__ import annotations

import difflib
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import requests

from .paths import data_dir, resource_dir

BASE = "https://v3.football.api-sports.io"
_CACHE = data_dir() / "cache"   # writable; persists next to the .exe when frozen

# API-Football statistic label -> our metric key.
STAT_MAP = {
    "Corner Kicks": "corners",
    "Total Shots": "shots",
    "Shots on Goal": "shots_on_target",
    "Fouls": "fouls",
    "Offsides": "offsides",
    "Yellow Cards": "yellow_cards",
    "Red Cards": "red_cards",
    "Ball Possession": "possession",   # "55%" -> 55
}


def get_key() -> Optional[str]:
    key = os.environ.get("APIFOOTBALL_KEY")
    if key:
        return key.strip()
    # Look next to the exe/repo first (lets a user drop their own key there),
    # then in the copy bundled into the build (resource_dir == _MEIPASS when frozen).
    for base in (_CACHE, resource_dir() / "cache"):
        f = base / "apifootball_key.txt"
        if f.exists():
            k = f.read_text(encoding="utf-8").strip()
            if k:
                return k
    return None


def has_key() -> bool:
    return bool(get_key())


# Footballing nations — if a team name is one of these, treat it as a national
# team and go straight to API-Football (WhoScored barely covers internationals).
COUNTRIES = {
    "argentina", "australia", "austria", "belgium", "bolivia", "brazil", "cameroon",
    "canada", "cape verde", "chile", "china", "colombia", "costa rica", "croatia",
    "curacao", "curaçao", "czech republic", "denmark", "ecuador", "egypt", "england",
    "france", "germany", "ghana", "greece", "haiti", "honduras", "hungary", "iceland",
    "iran", "iraq", "ireland", "israel", "italy", "ivory coast", "jamaica", "japan",
    "jordan", "mexico", "morocco", "netherlands", "new zealand", "nigeria", "norway",
    "panama", "paraguay", "peru", "poland", "portugal", "qatar", "saudi arabia",
    "scotland", "senegal", "serbia", "slovakia", "slovenia", "south africa",
    "south korea", "spain", "sweden", "switzerland", "tunisia", "turkey", "ukraine",
    "uruguay", "usa", "united states", "venezuela", "wales",
}


def is_national(name: str) -> bool:
    return (name or "").strip().lower() in COUNTRIES


def closest_country(name: str, cutoff: float = 0.8) -> Optional[str]:
    """Best-matching country for a (possibly misspelled) name, or None.

    Used to (a) auto-route obvious country typos to Flashscore ('Spein' ->
    'spain') and (b) suggest a correction in 'team not found' errors (looser
    cutoff). Returns the lower-cased country key from COUNTRIES.
    """
    matches = difflib.get_close_matches(
        (name or "").strip().lower(), list(COUNTRIES), n=1, cutoff=cutoff)
    return matches[0] if matches else None


_last = 0.0


def _get(path: str, ttl: float = 30 * 24 * 3600) -> dict[str, Any]:
    """GET an endpoint with on-disk caching (quota is precious)."""
    key = get_key()
    if not key:
        return {}
    cache_file = _CACHE / ("apif_" + str(abs(hash(path))) + ".json")
    if cache_file.exists() and time.time() - cache_file.stat().st_mtime < ttl:
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    global _last
    wait = 1.0 - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    try:
        r = requests.get(f"{BASE}/{path}", headers={"x-apisports-key": key}, timeout=20)
        _last = time.time()
        data = r.json() if r.status_code == 200 else {}
    except Exception:
        return {}
    if data.get("response"):
        _CACHE.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data), encoding="utf-8")
    return data


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value).rstrip("%"))
    except ValueError:
        return None


def resolve_team(name: str) -> Optional[dict[str, Any]]:
    """API-Football team record as {id, name}, or None."""
    data = _get(f"teams?search={requests.utils.quote(name)}")
    resp = data.get("response") or []
    if not resp:
        return None
    t = resp[0]["team"]
    return {"id": t["id"], "name": t["name"]}


def _search_team_id(name: str) -> Optional[int]:
    team = resolve_team(name)
    return team["id"] if team else None


def load_team(name: str, num_matches: int) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch a national team's recent matches. Returns (team, matches) or (None, [])."""
    if not has_key():
        return None, []
    team = resolve_team(name)
    if not team:
        return None, []
    matches = team_matches(name, num_matches, team_id=team["id"])
    return (team, matches) if matches else (None, [])


# Free plan only exposes these seasons (no fixtures?last= or ?next=).
_FREE_SEASONS = (2024, 2023, 2022)


def _recent_fixtures(team_id: int, num_matches: int) -> list[dict[str, Any]]:
    """Most recent finished fixtures for a team (free-tier compatible)."""
    by_id: dict[int, dict[str, Any]] = {}
    for season in _FREE_SEASONS:
        for fx in (_get(f"fixtures?team={team_id}&season={season}").get("response") or []):
            if fx.get("fixture", {}).get("status", {}).get("short") != "FT":
                continue
            fid = fx["fixture"]["id"]
            by_id[fid] = fx
        if len(by_id) >= num_matches:
            break
    fixtures = sorted(by_id.values(), key=lambda f: f["fixture"]["date"], reverse=True)
    return fixtures[:num_matches]


def _side_stats(stats_items: list[dict], goals: Optional[int], half: Optional[int]) -> dict:
    out: dict[str, Optional[float]] = {"goals": float(goals) if goals is not None else None,
                                       "half_goals": float(half) if half is not None else None}
    for item in stats_items or []:
        key = STAT_MAP.get(item.get("type"))
        if key:
            out[key] = _num(item.get("value"))
    return out


def team_matches(name: str, num_matches: int = 6,
                 team_id: Optional[int] = None) -> list[dict[str, Any]]:
    """A team's last `num_matches` finished matches as for/against dicts."""
    tid = team_id or _search_team_id(name)
    if not tid:
        return []
    fixtures = _recent_fixtures(tid, num_matches)
    out = []
    for fx in fixtures:
        fid = fx.get("fixture", {}).get("id")
        teams = fx.get("teams", {})
        is_home = teams.get("home", {}).get("id") == tid
        goals = fx.get("goals", {})
        ht = (fx.get("score", {}) or {}).get("halftime", {}) or {}
        gf = goals.get("home") if is_home else goals.get("away")
        ga = goals.get("away") if is_home else goals.get("home")
        hf = ht.get("home") if is_home else ht.get("away")
        ha = ht.get("away") if is_home else ht.get("home")

        sdata = (_get(f"fixtures/statistics?fixture={fid}").get("response") or [])
        mine = next((s for s in sdata if s.get("team", {}).get("id") == tid), {})
        theirs = next((s for s in sdata if s.get("team", {}).get("id") != tid), {})
        opp = teams.get("away" if is_home else "home", {})
        out.append({
            "match_id": fid,
            "date": (fx.get("fixture", {}).get("date") or "")[:10],
            "venue": "H" if is_home else "A",
            "opponent": opp.get("name"),
            "for": _side_stats(mine.get("statistics"), gf, hf),
            "against": _side_stats(theirs.get("statistics"), ga, ha),
            "players": [],   # player props not pulled from API-Football (quota)
        })
    return out
