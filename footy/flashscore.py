"""Flashscore scraper for international (national-team) match statistics.

WhoScored barely covers internationals and API-Football needs a key with a
small daily quota. Flashscore exposes the same data for free through its
backend "feed" pipeline, which we read with curl_cffi — a browser TLS
fingerprint clears their anti-bot check, and an ``x-fsign`` header (a constant
baked into their site JS) authorises the feed host.

Flow:
    search(name)              -> participant id + country id
    results feed  (pr_…)      -> recent finished match ids, scores, venue
    statistics feed (df_st_…) -> corners / shots / possession / cards / fouls …

Endpoints/codes here were reverse-engineered from the live site (the feed-name
builder lives in Flashscore's ``teamPage`` JS bundle: a team's results page is
``pr_{sport}_{countryId}_{participantId}_{page}_{tz}_{lang}_{projectType}``).

Returns matches in the SAME shape as the WhoScored and API-Football scrapers,
so build_form() and the simulator work unchanged.
"""

from __future__ import annotations

import datetime
import json
import re
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from curl_cffi import requests

from . import apifootball  # reuse its national-team list so routing agrees
from .paths import data_dir

# Flashscore's data pipeline.
NINJA = "https://global.flashscore.ninja/2/x/feed/"   # feed host (needs x-fsign)
SEARCH = "https://s.flashscore.com/search/"
FSIGN = "SW9D1eZo"        # required x-fsign header (a constant in their site JS)

# Feed separators: ~ between records, ¬ between fields, ÷ between key and value.
_BLOCK, _FIELD, _KV = "~", "¬", "÷"

_CACHE = data_dir() / "cache"   # persists next to the .exe when frozen

# Flashscore "Match"-period stat label -> our metric key. Possession is a
# percentage; the rest are plain counts. We deliberately skip compound stats
# like "Passes 88% (462/525)" that don't reduce to one clean per-match number.
_STAT_MAP = {
    "Ball possession": "possession",
    "Total shots": "shots",
    "Shots on target": "shots_on_target",
    "Shots on goal": "shots_on_target",   # older label, same thing
    "Corner kicks": "corners",
    "Fouls": "fouls",
    "Offsides": "offsides",
    "Yellow cards": "yellow_cards",
    "Red cards": "red_cards",
}


class FlashscoreError(Exception):
    """Raised when Flashscore data can't be fetched/parsed (callers fall back)."""


def _num(value: Optional[str]) -> Optional[float]:
    """First number in a Flashscore stat value ('54%' -> 54.0, '7' -> 7.0)."""
    if value is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(m.group()) if m else None


def _date(ts: Optional[str]) -> str:
    """Unix timestamp -> 'YYYY-MM-DD' (Flashscore start times are unix seconds)."""
    try:
        return datetime.datetime.fromtimestamp(
            int(ts), datetime.timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""


class FlashscoreScraper:
    """Reads Flashscore's feed pipeline for national-team match statistics."""

    def __init__(self, cache_ttl: float = 6 * 3600, min_interval: float = 0.4):
        self.session = requests.Session(impersonate="chrome")
        self.session.headers.update({"x-fsign": FSIGN})
        self.cache_ttl = cache_ttl          # results/search feeds (matches change)
        self.stats_ttl = 30 * 24 * 3600     # final match stats never change
        self.min_interval = min_interval
        self._last = 0.0
        _CACHE.mkdir(parents=True, exist_ok=True)

    # -- low level ----------------------------------------------------------

    def _throttle(self) -> None:
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    def _feed(self, name: str, ttl: Optional[float] = None) -> str:
        """GET a pipe-delimited feed by name, with on-disk caching."""
        ttl = self.cache_ttl if ttl is None else ttl
        cache_file = _CACHE / f"fs_{name}.txt"
        if ttl > 0 and cache_file.exists():
            if time.time() - cache_file.stat().st_mtime < ttl:
                return cache_file.read_text(encoding="utf-8")
        self._throttle()
        try:
            r = self.session.get(NINJA + name, timeout=30)
            r.raise_for_status()
            text = r.text
        except Exception as e:
            raise FlashscoreError(f"feed {name} failed: {e}")
        # "0" or "" mean the feed name was rejected or carries no data — don't
        # cache those (a transient bad fetch shouldn't stick).
        if text and text != "0":
            cache_file.write_text(text, encoding="utf-8")
        return text

    @staticmethod
    def _records(text: str) -> list[dict[str, str]]:
        """Parse a pipe-delimited feed into a list of {code: value} dicts."""
        out: list[dict[str, str]] = []
        if not text or text == "0":
            return out
        for block in text.split(_BLOCK):
            rec: dict[str, str] = {}
            for item in block.split(_FIELD):
                if _KV in item:
                    k, _, v = item.partition(_KV)
                    rec[k] = v
            if rec:
                out.append(rec)
        return out

    # -- endpoints ----------------------------------------------------------

    def search_team(self, name: str) -> Optional[dict[str, str]]:
        """Best national-team match -> {id, country_id, name, url}, or None.

        Flashscore search is JSONP: ``cjs.search.jsonpCallback({...})``.
        """
        cache_file = _CACHE / f"fs_search_{re.sub(r'[^a-z0-9]+', '_', name.lower())}.json"
        if self.cache_ttl > 0 and cache_file.exists():
            if time.time() - cache_file.stat().st_mtime < 30 * 24 * 3600:
                try:
                    return json.loads(cache_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    pass
        self._throttle()
        url = f"{SEARCH}?q={quote(name)}&l=1&s=1&f=1%3B1&pid=2&sid=1"
        try:
            raw = self.session.get(url, timeout=20).text
        except Exception as e:
            raise FlashscoreError(f"search '{name}' failed: {e}")
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            return None

        teams = [r for r in (data.get("results") or [])
                 if r.get("type") == "participants" and r.get("id")]
        if not teams:
            return None
        target = name.strip().lower()
        # Prefer an exact name match (the part before " (Confederation)") so we
        # pick the senior side, not "<Country> U21" / "<Country> W".
        def base(r: dict) -> str:
            return (r.get("title") or "").split(" (")[0].strip().lower()
        best = next((r for r in teams if base(r) == target), teams[0])
        result = {
            "id": best["id"],
            "country_id": str(best.get("flag_id", "")),
            "name": (best.get("title") or name).split(" (")[0].strip(),
            "url": best.get("url", ""),
        }
        cache_file.write_text(json.dumps(result), encoding="utf-8")
        return result

    def get_match_statistics(self, match_id: str) -> dict[str, dict[str, Optional[float]]]:
        """Full-match stats for one match -> {'home': {...}, 'away': {...}}.

        Keys are our metric names; values are per-match numbers. Empty dicts if
        the match has no detailed stats (common for minor friendlies).
        """
        recs = self._records(self._feed(f"df_st_1_{match_id}", ttl=self.stats_ttl))
        home: dict[str, Optional[float]] = {}
        away: dict[str, Optional[float]] = {}
        period = None
        for r in recs:
            if "SE" in r:                 # section: Match / 1st Half / 2nd Half
                period = r["SE"]
            if period != "Match":
                continue
            key = _STAT_MAP.get(r.get("SG", ""))   # SG = stat label
            if key:
                home[key] = _num(r.get("SH"))       # SH = home value
                away[key] = _num(r.get("SI"))       # SI = away value
        return {"home": home, "away": away}

    def _recent_results(self, country_id: str, pid: str, pages: int = 1) -> list[dict[str, str]]:
        """Finished match records from the team's results feed (newest first).

        The "show more results" feed is 0-indexed: page 0 is the most recent
        batch (~40 matches), page 1 the next-older batch, and so on.
        """
        matches: list[dict[str, str]] = []
        for page in range(pages):
            name = f"pr_1_{country_id}_{pid}_{page}_3_en_1"
            for r in self._records(self._feed(name)):
                # A played match carries both team names and both final scores.
                if r.get("AA") and r.get("AE") and r.get("AF") \
                        and "AG" in r and "AH" in r:
                    matches.append(r)
        matches.sort(key=lambda r: int(r.get("AD", 0) or 0), reverse=True)
        return matches

    def team_matches(self, team: dict[str, str], num_matches: int = 10) -> list[dict[str, Any]]:
        """A national team's last `num_matches` finished matches (for/against)."""
        pid, cc = team["id"], team.get("country_id", "")
        recs = self._recent_results(cc, pid, pages=1)
        if len(recs) < num_matches:                 # pull more history if needed
            recs = self._recent_results(cc, pid, pages=2)

        out: list[dict[str, Any]] = []
        for r in recs:
            if len(out) >= num_matches:
                break
            is_home = r.get("PX") == pid             # PX/PY = home/away participant id
            is_away = r.get("PY") == pid
            if not (is_home or is_away):
                continue
            hg, ag = _num(r.get("AG")), _num(r.get("AH"))
            stats = self.get_match_statistics(r["AA"])
            has_stats = bool(stats["home"] or stats["away"])
            if is_home:
                for_stats, against_stats = dict(stats["home"]), dict(stats["away"])
                for_stats["goals"], against_stats["goals"] = hg, ag
                opponent = r.get("AF")
            else:
                for_stats, against_stats = dict(stats["away"]), dict(stats["home"])
                for_stats["goals"], against_stats["goals"] = ag, hg
                opponent = r.get("AE")
            # Flashscore drops the card rows entirely when a match had none, so
            # treat a stats-bearing match with no card row as zero cards (else
            # the average would only cover matches that happened to have cards).
            if has_stats:
                for side in (for_stats, against_stats):
                    side.setdefault("yellow_cards", 0.0)
                    side.setdefault("red_cards", 0.0)
            out.append({
                "match_id": r["AA"],
                "date": _date(r.get("AD")),
                "venue": "H" if is_home else "A",
                "opponent": opponent,
                "for": for_stats,
                "against": against_stats,
                "players": [],   # Flashscore player stats aren't pulled (not needed)
            })
        return out

    def team_history(self, team: dict[str, str], pages: int = 2) -> list[dict[str, Any]]:
        """Goals-only match history (newest first) — cheap: no per-match stats.

        Used by the backtester, which only needs scores/dates/opponent ids and
        would otherwise fire one stats request per historical match.
        """
        pid, cc = team["id"], team.get("country_id", "")
        out: list[dict[str, Any]] = []
        for r in self._recent_results(cc, pid, pages=pages):
            is_home = r.get("PX") == pid
            is_away = r.get("PY") == pid
            if not (is_home or is_away):
                continue
            hg, ag = _num(r.get("AG")), _num(r.get("AH"))
            if hg is None or ag is None:
                continue
            out.append({
                "match_id": r["AA"],
                "date": _date(r.get("AD")),
                "ts": int(r.get("AD", 0) or 0),
                "venue": "H" if is_home else "A",
                "opponent_id": (r.get("PY") if is_home else r.get("PX")) or "",
                "opponent": (r.get("AF") if is_home else r.get("AE")) or "",
                "goals_for": hg if is_home else ag,
                "goals_against": ag if is_home else hg,
            })
        return out


def is_national_team(team_name: str) -> bool:
    """True if this is a national team and should be routed to Flashscore.

    Shares API-Football's country list so the two national-team sources agree.
    """
    return apifootball.is_national(team_name)


def load_team(team_name: str, matches_limit: int = 10
              ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load a national team and its recent finished matches (with stats).

    Returns ``(team_info, matches)``. Raises ``FlashscoreError`` when nothing
    usable is found, so callers can fall back to API-Football.
    """
    scraper = FlashscoreScraper()
    team = scraper.search_team(team_name)
    if not team:
        raise FlashscoreError(f"no Flashscore team found for '{team_name}'")
    matches = scraper.team_matches(team, matches_limit)
    if not matches:
        raise FlashscoreError(f"no finished matches with stats for '{team_name}'")
    team_info = {"name": team["name"], "id": team["id"], "source": "flashscore"}
    return team_info, matches
