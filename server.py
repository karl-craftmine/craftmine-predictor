"""Local web app for the WhoScored predictor — with a bet-builder.

    python server.py
    # then open http://127.0.0.1:5000

Flow: POST /api/load (scrape + return form & player lists) then POST
/api/simulate (run one simulation, evaluate the bet slip). Scrapes run hidden
(headless), one at a time, and are cached so repeat lookups are instant.
"""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

from flask import Flask, jsonify, request

from footy import (WhoScoredScraper, build_form, aggregate_players,
                   apply_recency_weights, run_simulation, evaluate_bet,
                   top_scorelines, sportsdb, DIXON_COLES_RHO)
from footy import apifootball, flashscore, nations
from footy.whoscored import WhoScoredError
from footy.form import METRIC_LABELS, COUNT_METRICS
from footy.fixtures import load_follows, save_follows, refresh_cache
from footy.paths import data_dir, resource_dir

_CACHE = data_dir() / "cache"   # writable; persists next to the .exe when frozen
_CACHE.mkdir(parents=True, exist_ok=True)
FOLLOWS_FILE = _CACHE / "follows.json"
FIXTURES_FILE = _CACHE / "fixtures.json"

app = Flask(__name__)
_scrape_lock = threading.Lock()
_LOADED: dict[str, dict] = {}   # in-memory cache of loaded matchups


def _clamp(v, lo, hi):
    return max(lo, min(int(v), hi))


def _key(home, away, matches):
    return f"{home.lower()}|{away.lower()}|{matches}"


def _national_name(name) -> str | None:
    """Country to treat `name` as a national team, or None.

    Exact country match, or a close typo — so 'Spein' routes to Spain
    (Flashscore) instead of dead-ending on a WhoScored club search.
    """
    if nations.is_national(name):
        return name
    return nations.closest_country(name, cutoff=0.8)


def _team_error(name) -> str:
    """A helpful 'couldn't load' message, suggesting a country if it's a typo."""
    guess = nations.closest_country(name, cutoff=0.6)
    if guess:
        return f"Couldn't find '{name}'. Did you mean {guess.title()}?"
    return (f"Couldn't find enough finished matches for '{name}' — check the "
            "spelling? Some lower-division or less-covered leagues aren't available.")


def _get_data(home, away, matches):
    k = _key(home, away, matches)
    if k in _LOADED:
        return _LOADED[k]
    with _scrape_lock:
        if k in _LOADED:
            return _LOADED[k]

        ht, hm, at, am = None, None, None, None
        hsrc = asrc = None      # which provider supplied each side (for the UI)

        # Flashscore first for BOTH clubs and national teams: one fast HTTP source
        # (no browser/Cloudflare) covering ~all of world football. For nationals
        # use the resolved country name so an obvious typo ("Spein" -> Spain) still
        # routes; for clubs the raw typed name is what Flashscore searches.
        home_nat, away_nat = _national_name(home), _national_name(away)
        try:
            ht, hm = flashscore.load_team(home_nat or home, matches); hsrc = "flashscore"
        except flashscore.FlashscoreError:
            pass
        try:
            at, am = flashscore.load_team(away_nat or away, matches); asrc = "flashscore"
        except flashscore.FlashscoreError:
            pass

        # API-Football fallback for national teams Flashscore lacks (needs a key).
        if not hm and home_nat and apifootball.has_key():
            t, m = apifootball.load_team(home_nat, matches)
            if m: ht, hm, hsrc = t, m, "apifootball"
        if not am and away_nat and apifootball.has_key():
            t, m = apifootball.load_team(away_nat, matches)
            if m: at, am, asrc = t, m, "apifootball"

        # WhoScored last resort for any club Flashscore couldn't find — richer
        # per-player shot stats, but slow (headless browser + Cloudflare) and only
        # the major leagues. Tolerate a browser that won't start (e.g. no Chrome).
        if not hm or not am:
            try:
                with WhoScoredScraper(headless=True) as ws:
                    if not hm:
                        try:
                            ht = ws.search_team(home)
                            hm = ws.team_recent_matches(ht, matches)
                            if hm: hsrc = "whoscored"
                        except WhoScoredError:
                            pass
                    if not am:
                        try:
                            at = ws.search_team(away)
                            am = ws.team_recent_matches(at, matches)
                            if am: asrc = "whoscored"
                        except WhoScoredError:
                            pass
            except Exception:
                pass   # browser unavailable — nothing more to try

        if not hm or not am:
            raise WhoScoredError(_team_error(home if not hm else away))
        # Weight recent matches more heavily before aggregating (matches the CLI).
        hm = apply_recency_weights(hm)
        am = apply_recency_weights(am)
        data = {
            "home_team": ht, "away_team": at,
            "home_form": build_form(ht["name"], hm),
            "away_form": build_form(at["name"], am),
            "home_players": aggregate_players(hm),
            "away_players": aggregate_players(am),
            "home_matches": len(hm), "away_matches": len(am),
            "home_source": hsrc, "away_source": asrc,
        }
        _LOADED[k] = data
        return data


def _player_list(players):
    return [{"name": p["name"], "position": p["position"], "games": p["games"],
             "shots_avg": p["shots_avg"], "sot_avg": p["sot_avg"],
             "goals_avg": p["goals_avg"], "rating_avg": p["rating_avg"]}
            for p in players if p["games"] >= 1]


@app.route("/api/load", methods=["POST"])
def api_load():
    d = request.get_json(force=True)
    home = (d.get("home") or "").strip()
    away = (d.get("away") or "").strip()
    if not home or not away:
        return jsonify({"error": "Enter both teams."}), 400
    matches = _clamp(d.get("matches", 10), 3, 20)
    try:
        data = _get_data(home, away, matches)
    except WhoScoredError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Unexpected error: {exc}"}), 500

    hf, af = data["home_form"], data["away_form"]
    fh, fa = hf.as_dict(), af.as_dict()
    available = [m for m in COUNT_METRICS if m in fh or m in fa]
    return jsonify({
        "home": {"name": data["home_team"]["name"], "id": data["home_team"]["id"],
                 "matches": data["home_matches"], "source": data["home_source"]},
        "away": {"name": data["away_team"]["name"], "id": data["away_team"]["id"],
                 "matches": data["away_matches"], "source": data["away_source"]},
        "metric_labels": METRIC_LABELS,
        "available_metrics": available,
        "form_home": fh, "form_away": fa,
        "players_home": _player_list(data["home_players"]),
        "players_away": _player_list(data["away_players"]),
    })


@app.route("/api/simulate", methods=["POST"])
def api_simulate():
    d = request.get_json(force=True)
    home = (d.get("home") or "").strip()
    away = (d.get("away") or "").strip()
    matches = _clamp(d.get("matches", 10), 3, 20)
    iterations = _clamp(d.get("iterations", 50000), 1000, 200000)
    bets = d.get("bets") or []
    try:
        data = _get_data(home, away, matches)
    except WhoScoredError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Unexpected error: {exc}"}), 500

    # Build player Poisson means for any player bets in the slip.
    def lookup(side, name):
        lst = data["home_players"] if side == "home" else data["away_players"]
        return next((p for p in lst if p["name"] == name), None)

    specs, seen = [], set()
    for b in bets:
        if b.get("type") in ("player_ou", "player_to_score"):
            key = b.get("key", "")
            if key in seen:
                continue
            side, _, name = key.partition(":")
            p = lookup(side, name)
            if p:
                specs.append({"key": key, "name": name, "side": side,
                              "shots": p["shots_avg"], "sot": p["sot_avg"],
                              "goals": p["goals_avg"]})
                seen.add(key)

    # International games (both sides national) are at neutral venues — there's no
    # real home team here, just input order — so don't apply a home-advantage tilt.
    neutral = nations.is_national(home) and nations.is_national(away)
    sim = run_simulation(data["home_form"], data["away_form"],
                         iterations=iterations, player_specs=specs,
                         home_advantage=1.0 if neutral else 1.10,
                         rho=DIXON_COLES_RHO)

    if not bets:  # sensible defaults if the slip is empty
        bets = [{"type": "result", "side": "home"},
                {"type": "result", "side": "draw"},
                {"type": "result", "side": "away"},
                {"type": "total_ou", "metric": "goals", "line": 2.5, "ou": "over"},
                {"type": "btts", "value": "yes"}]

    return jsonify({
        "home": data["home_team"]["name"], "away": data["away_team"]["name"],
        "iterations": iterations,
        "exp_home_goals": sim.means.get("goals", (None, None))[0],
        "exp_away_goals": sim.means.get("goals", (None, None))[1],
        "scorelines": top_scorelines(sim, n=6),
        "bets": [evaluate_bet(sim, b) for b in bets],
    })


@app.route("/api/teams")
def api_teams():
    """Team-name autocomplete for the match inputs — clubs and national teams
    worldwide, live from Flashscore (the same source that loads them)."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    return jsonify(flashscore.search_participants(q, limit=10))


@app.route("/api/follows", methods=["GET", "POST"])
def api_follows():
    if request.method == "POST":
        d = request.get_json(force=True)
        save_follows(FOLLOWS_FILE, {"teams": d.get("teams", []),
                                    "competitions": d.get("competitions", [])})
    return jsonify(load_follows(FOLLOWS_FILE))


@app.route("/api/competitions")
def api_competitions():
    """Followable competitions (TheSportsDB league id -> name). ?q= filters."""
    q = (request.args.get("q") or "").strip().lower()
    items = [{"key": k, "name": v} for k, v in sportsdb.LEAGUES.items()
             if not q or q in v.lower()]
    items.sort(key=lambda x: x["name"])
    return jsonify(items)


@app.route("/api/sdteam")
def api_sdteam():
    """Team-name autocomplete for the fixtures follow list (TheSportsDB)."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    return jsonify(sportsdb.search_teams(q))


def _read_fixtures() -> dict:
    if FIXTURES_FILE.exists():
        try:
            return json.loads(FIXTURES_FILE.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            pass
    return {"updated": None, "fixtures": []}


@app.route("/api/fixtures")
def api_fixtures():
    return jsonify(_read_fixtures())


@app.route("/api/fixtures/refresh", methods=["POST"])
def api_fixtures_refresh():
    """force=1 always rescrapes (manual button). Otherwise it's a best-effort
    background refresh: if a scrape is already running, hand back the cache."""
    force = request.args.get("force")
    if not force and not _scrape_lock.acquire(blocking=False):
        return jsonify({**_read_fixtures(), "busy": True})
    if force:
        _scrape_lock.acquire()
    try:
        payload = refresh_cache(FOLLOWS_FILE, FIXTURES_FILE)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    finally:
        _scrape_lock.release()
    return jsonify(payload)


@app.route("/")
def index():
    return (resource_dir() / "index.html").read_text(encoding="utf-8")


def _free_port(start: int = 5000, tries: int = 20) -> int:
    """First free port at/after `start` (so a stuck 5000 never blocks startup)."""
    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start


if __name__ == "__main__":
    import os
    import sys
    # Honour an explicit port (CLI arg or PORT env) so a launcher can pin one;
    # otherwise grab the first free port from 5000 so a stuck port never blocks.
    explicit = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].isdigit() else \
        os.environ.get("PORT", "")
    port = int(explicit) if explicit.isdigit() else _free_port()
    print(f"Craftmine Football Predictor running at  http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, threaded=True)
