"""Betting-style match predictor backed by WhoScored data.

Examples
--------
    python app.py "Arsenal" "Chelsea"
    python app.py "Liverpool" "Everton" --matches 12 --corners-line 10.5 --players
    python app.py 13 32                       # team IDs work too (Arsenal, Man Utd)

Home team first. The tool opens a real browser (SeleniumBase UC mode) to clear
Cloudflare, samples each team's recent finished matches from WhoScored,
aggregates for/against averages (corners, shots, goals, ...), and prints a
prediction with fair odds.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict

from footy import WhoScoredScraper, build_form, predict_match, simulate_match
from footy import apifootball, flashscore
from footy.whoscored import WhoScoredError
from footy.form import METRIC_LABELS
from footy.predict import implied_odds


def _pct(p) -> str:
    return f"{p * 100:5.1f}%" if p is not None else "  n/a"


def _form_table(form) -> None:
    data = form.as_dict()
    print(f"\n  {form.team_name}  —  last {form.matches} matches")
    print(f"  {'metric':<14}{'for':>8}{'against':>10}{'(n)':>6}")
    print("  " + "-" * 38)
    for key, label in METRIC_LABELS.items():
        if key in data:
            row = data[key]
            f = f"{row['for']:.2f}" if row["for"] is not None else "—"
            a = f"{row['against']:.2f}" if row["against"] is not None else "—"
            print(f"  {label:<14}{f:>8}{a:>10}{row['samples']:>6}")


def _top_players(matches) -> list[dict]:
    agg = defaultdict(lambda: {"sum": 0.0, "games": 0})
    for m in matches:
        for p in m.get("players", []):
            if p.get("name") and p.get("rating"):
                agg[p["name"]]["sum"] += p["rating"]
                agg[p["name"]]["games"] += 1
                agg[p["name"]]["position"] = p.get("position")
    players = [
        {"name": n, "avg": round(v["sum"] / v["games"], 2),
         "games": v["games"], "position": v.get("position")}
        for n, v in agg.items() if v["games"]
    ]
    players.sort(key=lambda x: x["avg"], reverse=True)
    return players


def _maybe_players(args, home_team, home_matches, away_team, away_matches) -> None:
    if not args.players:
        return
    for team, matches in ((home_team, home_matches), (away_team, away_matches)):
        print(f"\n  Top performers — {team['name']} (sampled matches):")
        for pl in _top_players(matches)[:6]:
            print(f"    {pl['avg']:>4}  {pl['name']:<22} "
                  f"{pl['position'] or '?':<4} ({pl['games']} gms)")


def _print_sim(res) -> None:
    def row(label, p):
        print(f"  {label:<26}{_pct(p)}   {implied_odds(p)}")

    print("\n" + "=" * 52)
    print(f"  MONTE CARLO ({res.iterations:,} sims):  "
          f"{res.home_name} vs {res.away_name}")
    print("=" * 52)
    print(f"  Expected goals:    {res.exp_home_goals:.2f}  -  {res.exp_away_goals:.2f}")
    print(f"  Most likely score: {res.scorelines[0][0]} "
          f"({res.scorelines[0][1] * 100:.1f}%)")

    print("\n  Match result                prob    fair odds")
    print("  " + "-" * 46)
    row(f"{res.home_name} win", res.prob_home)
    row("Draw", res.prob_draw)
    row(f"{res.away_name} win", res.prob_away)

    print("\n  Goals / BTTS                prob    fair odds")
    print("  " + "-" * 46)
    row(f"Over {res.goals_line} goals", res.prob_over_goals)
    row(f"Under {res.goals_line} goals", 1 - res.prob_over_goals)
    row("Both teams to score", res.prob_btts)
    row(f"{res.home_name} clean sheet", res.clean_sheet_home)
    row(f"{res.away_name} clean sheet", res.clean_sheet_away)
    row(f"{res.home_name} win to nil", res.win_to_nil_home)
    row(f"{res.away_name} win to nil", res.win_to_nil_away)

    print("\n  Handicaps                   prob    fair odds")
    print("  " + "-" * 46)
    for label, p in res.handicaps:
        row(label, p)

    if res.prob_corners_over is not None:
        print("\n  Corners                     prob    fair odds")
        print("  " + "-" * 46)
        row(f"Over {res.corners_line} total", res.prob_corners_over)
        row(f"Under {res.corners_line} total", 1 - res.prob_corners_over)
        row(f"{res.home_name} over {res.team_corners_line}", res.prob_home_corners_over)
        row(f"{res.away_name} over {res.team_corners_line}", res.prob_away_corners_over)
        total = res.exp_home_corners + res.exp_away_corners
        print(f"  Expected corners: {res.exp_home_corners:.1f} - "
              f"{res.exp_away_corners:.1f} (total {total:.1f})")

    print("\n  Total goals distribution:")
    for g, p in res.total_goals_dist:
        bar = "█" * round(p * 40)
        print(f"    {g:>2}: {p * 100:5.1f}%  {bar}")

    print("\n  Top 6 scorelines:")
    for sc, p in res.scorelines:
        print(f"    {sc}   {p * 100:4.1f}%")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="WhoScored match predictor")
    ap.add_argument("home", help="home team name or WhoScored id")
    ap.add_argument("away", help="away team name or WhoScored id")
    ap.add_argument("-m", "--matches", type=int, default=10,
                    help="finished matches to sample per team (default 10)")
    ap.add_argument("--goals-line", type=float, default=2.5)
    ap.add_argument("--corners-line", type=float, default=9.5)
    ap.add_argument("--home-advantage", type=float, default=1.10,
                    help="home goal multiplier (default 1.10)")
    ap.add_argument("--sim", action="store_true",
                    help="use the Monte Carlo simulation engine (extra markets)")
    ap.add_argument("--iterations", type=int, default=50000,
                    help="Monte Carlo iterations (default 50000)")
    ap.add_argument("--players", action="store_true",
                    help="show each team's top players by average rating")
    ap.add_argument("--show-browser", action="store_true",
                    help="show the Chrome window (default: run hidden/headless)")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print each sampled match")
    args = ap.parse_args(argv)

    # Player/team names carry accents (Ødegaard, Martínez); make stdout UTF-8.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    home_matches = away_matches = None
    home_team = away_team = None
    try:
        home_api = flashscore.is_national_team(args.home)
        away_api = flashscore.is_national_team(args.away)

        if home_api:
            print("Fetching home team from Flashscore...")
            try:
                home_team, home_matches = flashscore.load_team(args.home, args.matches)
                if home_team:
                    print(f"  home: {home_team['name']} (id {home_team['id']})")
            except flashscore.FlashscoreError:
                print("Flashscore failed, trying API-Football...")
                if apifootball.has_key():
                    home_team, home_matches = apifootball.load_team(args.home, args.matches)
                    if home_team:
                        print(f"  home: {home_team['name']} (id {home_team['id']})")
        if away_api:
            print("Fetching away team from Flashscore...")
            try:
                away_team, away_matches = flashscore.load_team(args.away, args.matches)
                if away_team:
                    print(f"  away: {away_team['name']} (id {away_team['id']})")
            except flashscore.FlashscoreError:
                print("Flashscore failed, trying API-Football...")
                if apifootball.has_key():
                    away_team, away_matches = apifootball.load_team(args.away, args.matches)
                    if away_team:
                        print(f"  away: {away_team['name']} (id {away_team['id']})")

        if not home_matches or not away_matches:
            with WhoScoredScraper(
                cache_ttl=0 if args.no_cache else 6 * 3600,
                headless=not args.show_browser,
            ) as ws:
                print("Fetching from WhoScored (hidden browser, ~20s to clear "
                      "Cloudflare)...")
                if not home_matches:
                    home_team = ws.search_team(args.home)
                    print(f"  home: {home_team['name']} (id {home_team['id']})")
                    print(f"\nSampling last {args.matches} matches for "
                          f"{home_team['name']}...")
                    home_matches = ws.team_recent_matches(
                        home_team, args.matches, args.verbose)
                if not away_matches:
                    away_team = ws.search_team(args.away)
                    print(f"  away: {away_team['name']} (id {away_team['id']})")
                    print(f"Sampling last {args.matches} matches for "
                          f"{away_team['name']}...")
                    away_matches = ws.team_recent_matches(
                        away_team, args.matches, args.verbose)
    except WhoScoredError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1

    if not home_matches or not away_matches:
        print("\nNot enough finished matches found to predict.", file=sys.stderr)
        return 1

    home_form = build_form(home_team["name"], home_matches)
    away_form = build_form(away_team["name"], away_matches)
    _form_table(home_form)
    _form_table(away_form)

    if args.sim:
        res = simulate_match(
            home_form, away_form,
            iterations=args.iterations,
            home_advantage=args.home_advantage,
            goals_line=args.goals_line,
            corners_line=args.corners_line,
        )
        _print_sim(res)
        _maybe_players(args, home_team, home_matches, away_team, away_matches)
        print("\n  Note: a baseline model. Compare to bookmaker odds before "
              "betting. Gamble responsibly.")
        return 0

    pred = predict_match(
        home_form, away_form,
        home_advantage=args.home_advantage,
        goals_line=args.goals_line,
        corners_line=args.corners_line,
    )

    print("\n" + "=" * 50)
    print(f"  PREDICTION:  {pred.home_name}  vs  {pred.away_name}")
    print("=" * 50)
    print(f"  Expected goals:    {pred.exp_home_goals:.2f}  -  {pred.exp_away_goals:.2f}")
    print(f"  Most likely score: {pred.scorelines[0][0]} "
          f"({pred.scorelines[0][1] * 100:.1f}%)")

    print("\n  Match result          prob     fair odds")
    print("  " + "-" * 42)
    for label, p in [(f"{pred.home_name} win", pred.prob_home),
                     ("Draw", pred.prob_draw),
                     (f"{pred.away_name} win", pred.prob_away)]:
        print(f"  {label:<20}{_pct(p)}    {implied_odds(p)}")

    print("\n  Markets               prob     fair odds")
    print("  " + "-" * 42)
    markets = [
        (f"Over {pred.goals_line} goals", pred.prob_over_goals),
        (f"Under {pred.goals_line} goals", 1 - pred.prob_over_goals),
        ("Both teams to score", pred.prob_btts),
    ]
    if pred.prob_corners_over is not None:
        markets += [
            (f"Over {pred.corners_line} corners", pred.prob_corners_over),
            (f"Under {pred.corners_line} corners", 1 - pred.prob_corners_over),
        ]
    for label, p in markets:
        print(f"  {label:<20}{_pct(p)}    {implied_odds(p)}")

    if pred.exp_home_corners is not None:
        total = pred.exp_home_corners + pred.exp_away_corners
        print(f"\n  Expected corners:  {pred.exp_home_corners:.1f}  -  "
              f"{pred.exp_away_corners:.1f}  (total {total:.1f})")

    print("\n  Top 5 likely scorelines:")
    for sc, p in pred.scorelines:
        print(f"    {sc}   {p * 100:4.1f}%")

    _maybe_players(args, home_team, home_matches, away_team, away_matches)

    print("\n  Note: a baseline model. Compare to bookmaker odds before betting. "
          "Gamble responsibly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
