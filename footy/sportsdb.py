"""TheSportsDB client — fast, reliable fixtures (free tier, no signup).

Used only for the upcoming-fixtures calendar. Match simulations still use
WhoScored (that's where the detailed corner/shot stats live). Plain HTTP/JSON,
so the calendar is instant instead of minutes of browser scraping.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import requests

BASE = "https://www.thesportsdb.com/api/v1/json/3"   # "3" = free public key

# Curated competitions (idLeague -> display name), all verified.
LEAGUES: dict[str, str] = {
    "4328": "English Premier League",
    "4329": "English League Championship",
    "4396": "English League One",
    "4482": "English FA Cup",
    "4330": "Scottish Premier League",
    "4331": "German Bundesliga",
    "4485": "German DFB-Pokal",
    "4332": "Italian Serie A",
    "4334": "French Ligue 1",
    "4335": "Spanish La Liga",
    "4336": "Greek Super League",
    "4337": "Dutch Eredivisie",
    "4641": "Dutch Eerste Divisie",
    "4338": "Belgian Pro League",
    "4339": "Turkish Super Lig",
    "4340": "Danish Superliga",
    "4344": "Portuguese Primeira Liga",
    "4346": "American Major League Soccer",
    "4521": "American NWSL",
    "4347": "Swedish Allsvenskan",
    "4358": "Norwegian Eliteserien",
    "4350": "Mexican Liga MX",
    "4351": "Brazilian Serie A",
    "4406": "Argentine Primera Division",
    "4354": "Ukrainian Premier League",
    "4355": "Russian Premier League",
    "4422": "Polish Ekstraklasa",
    "4644": "Israeli Premier League",
    "4359": "Chinese Super League",
    "4356": "Australian A-League",
    "4480": "UEFA Champions League",
    "4481": "UEFA Europa League",
    "4502": "UEFA European Championship",
    "4429": "FIFA World Cup",
    "4503": "FIFA Club World Cup",
}

_session = requests.Session()
_session.headers.update({"User-Agent": "footy-predictor/1.0"})
_last = 0.0


def _get(path: str) -> dict[str, Any]:
    global _last
    wait = 0.3 - (time.time() - _last)   # be polite to the free API
    if wait > 0:
        time.sleep(wait)
    try:
        r = _session.get(f"{BASE}/{path}", timeout=15)
        _last = time.time()
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


def search_team_id(name: str) -> Optional[str]:
    data = _get(f"searchteams.php?t={requests.utils.quote(name)}")
    teams = data.get("teams") or []
    for t in teams:                       # prefer a soccer team
        if (t.get("strSport") or "").lower() == "soccer":
            return t.get("idTeam")
    return teams[0].get("idTeam") if teams else None


def search_teams(name: str, limit: int = 8) -> list[dict[str, str]]:
    data = _get(f"searchteams.php?t={requests.utils.quote(name)}")
    out = []
    for t in data.get("teams") or []:
        if (t.get("strSport") or "").lower() == "soccer" and t.get("strTeam"):
            out.append({"name": t["strTeam"], "league": t.get("strLeague") or ""})
    return out[:limit]


def team_next(team_id: str) -> list[dict[str, Any]]:
    return (_get(f"eventsnext.php?id={team_id}") or {}).get("events") or []


def league_next(league_id: str) -> list[dict[str, Any]]:
    return (_get(f"eventsnextleague.php?id={league_id}") or {}).get("events") or []


def events_on_day(day: str) -> list[dict[str, Any]]:
    """All soccer events on a date (YYYY-MM-DD). Used for the multi-day sweep —
    the per-league 'next' feed is sparse, but per-day is complete."""
    return (_get(f"eventsday.php?d={day}&s=Soccer") or {}).get("events") or []


def normalize(e: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": e.get("dateEvent"),
        "time": (e.get("strTime") or "")[:5],
        "home": e.get("strHomeTeam"),
        "away": e.get("strAwayTeam"),
        "competition": e.get("strLeague"),
        "started": False,
    }
