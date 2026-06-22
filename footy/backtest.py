"""Leakage-safe backtest of the prediction model on national-team results.

For every match between two teams in a pool, we rebuild each side's goals form
from ONLY their matches *before* that match's date, predict the 1X2 outcome, and
compare to what actually happened. Nothing from the match (or later) leaks into
its own prediction.

It scores two goals models side by side (DC + recency held fixed, so the only
difference is how expected goals are formed):
  * average blend (old)   — (team attack + opponent defence) / 2
  * multiplicative (new)  — λ = μ · attack · defence (mismatches compound)

and reports a 3x3 confusion matrix plus proper scoring rules (accuracy, Brier,
log-loss) and a calibration table, against naive baselines.

Run:
    python -m footy.backtest
    python -m footy.backtest --k 12 --half-life 540 --pages 3
"""

from __future__ import annotations

import argparse
import math
from collections import defaultdict

from . import predict
from .flashscore import FlashscoreScraper, FlashscoreError
from .form import build_form
from .predict import predict_match, DIXON_COLES_RHO

# National teams that play each other often (dense intra-pool fixtures: UEFA
# Nations League / Euro qualifiers / friendlies, CONMEBOL qualifiers & Copa).
DEFAULT_POOL = [
    "England", "France", "Spain", "Germany", "Italy", "Portugal", "Netherlands",
    "Belgium", "Croatia", "Denmark", "Switzerland", "Poland", "Serbia", "Austria",
    "Scotland", "Wales", "Czech Republic", "Hungary", "Turkey", "Sweden",
    "Brazil", "Argentina", "Uruguay", "Colombia",
]

_LABELS = ("H", "D", "A")   # outcome classes, in index order


def _outcome(hg: float, ag: float) -> int:
    return 0 if hg > ag else (2 if hg < ag else 1)


# --- data gathering -------------------------------------------------------

def gather(pool, pages, verbose=True):
    """{team_id: {'name', 'history'}} for each resolvable pool team (cached)."""
    scraper = FlashscoreScraper()
    teams: dict[str, dict] = {}
    for name in pool:
        try:
            t = scraper.search_team(name)
            if not t:
                if verbose:
                    print(f"  ! no Flashscore team for '{name}'")
                continue
            hist = scraper.team_history(t, pages=pages)
            teams[t["id"]] = {"name": t["name"], "history": hist}
            if verbose:
                print(f"  {t['name']:<16} {len(hist)} matches")
        except FlashscoreError as e:
            if verbose:
                print(f"  ! {name}: {e}")
    return teams


def canonical_matches(teams):
    """De-duplicated matches where BOTH teams are in the pool.

    Each is (ts, home_id, away_id, home_goals, away_goals).
    """
    seen: dict[str, tuple] = {}
    for tid, t in teams.items():
        for h in t["history"]:
            opp = h["opponent_id"]
            if opp not in teams:
                continue                       # need the opponent's history too
            mid = h["match_id"]
            if mid in seen:
                continue
            if h["venue"] == "H":
                seen[mid] = (h["ts"], tid, opp, h["goals_for"], h["goals_against"])
            else:
                seen[mid] = (h["ts"], opp, tid, h["goals_against"], h["goals_for"])
    return sorted(seen.values())               # chronological


# --- as-of-date form (leakage-safe) ---------------------------------------

def _asof_form(name, history, cutoff_ts, k, half_life):
    """Form from a team's most-recent `k` matches strictly before `cutoff_ts`."""
    prior = [h for h in history if h["ts"] < cutoff_ts][:k]   # history is newest-first
    if not prior:
        return None, 0
    matches = []
    for h in prior:
        m = {"for": {"goals": h["goals_for"]},
             "against": {"goals": h["goals_against"]}, "venue": h["venue"]}
        if half_life:
            age_days = (cutoff_ts - h["ts"]) / 86400.0
            m["weight"] = 0.5 ** (age_days / half_life)
        matches.append(m)
    return build_form(name, matches), len(prior)


# --- metrics --------------------------------------------------------------

def _metrics(records):
    """records: list of (probs(H,D,A), actual_idx) -> dict of metrics."""
    n = len(records)
    confusion = [[0, 0, 0] for _ in range(3)]   # [actual][predicted]
    correct = brier = logloss = 0.0
    for probs, actual in records:
        pred = max(range(3), key=lambda c: probs[c])
        confusion[actual][pred] += 1
        if pred == actual:
            correct += 1
        brier += sum((probs[c] - (1.0 if c == actual else 0.0)) ** 2 for c in range(3))
        logloss += -math.log(max(probs[actual], 1e-15))
    return {
        "n": n, "confusion": confusion,
        "accuracy": correct / n if n else 0.0,
        "brier": brier / n if n else 0.0,
        "logloss": logloss / n if n else 0.0,
    }


def _baselines(records):
    """No-skill references: always-home accuracy, and base-rate Brier/log-loss."""
    n = len(records)
    base = [0, 0, 0]
    for _, actual in records:
        base[actual] += 1
    rates = [b / n for b in base] if n else [0, 0, 0]
    brier = logloss = 0.0
    for _, actual in records:
        brier += sum((rates[c] - (1.0 if c == actual else 0.0)) ** 2 for c in range(3))
        logloss += -math.log(max(rates[actual], 1e-15))
    return {
        "outcome_rates": rates,
        "always_home_acc": rates[0],
        "base_rate_brier": brier / n if n else 0.0,
        "base_rate_logloss": logloss / n if n else 0.0,
    }


def _calibration(records, bins=5):
    """Reliability of the home-win probability: predicted vs actual per bin."""
    buckets = defaultdict(lambda: [0, 0.0, 0])   # [count, sum_p_home, home_wins]
    for probs, actual in records:
        b = min(int(probs[0] * bins), bins - 1)
        buckets[b][0] += 1
        buckets[b][1] += probs[0]
        buckets[b][2] += 1 if actual == 0 else 0
    rows = []
    for b in range(bins):
        c, sp, w = buckets[b]
        if c:
            rows.append((sp / c, w / c, c))   # (mean predicted, actual rate, n)
    return rows


# --- reporting ------------------------------------------------------------

def _print_confusion(conf):
    print("    confusion (rows = actual, cols = predicted):")
    print("              pred H   pred D   pred A")
    for i, lab in enumerate(_LABELS):
        print(f"      actual {lab}  {conf[i][0]:6d}   {conf[i][1]:6d}   {conf[i][2]:6d}")


def report(results, base):
    n = next(iter(results.values()))["n"]
    print("\n" + "=" * 64)
    print(f"BACKTEST — {n} matches "
          f"(home wins {base['outcome_rates'][0]:.0%}, draws "
          f"{base['outcome_rates'][1]:.0%}, away {base['outcome_rates'][2]:.0%})")
    print("=" * 64)
    print(f"\n{'model':<22}{'accuracy':>10}{'Brier':>10}{'log-loss':>10}   (lower Brier/log-loss = better)")
    print("-" * 64)
    print(f"{'always home':<22}{base['always_home_acc']:>9.1%}{'—':>10}{'—':>10}")
    print(f"{'base rates (no skill)':<22}{'—':>10}{base['base_rate_brier']:>10.4f}{base['base_rate_logloss']:>10.4f}")
    for name, m in results.items():
        print(f"{name:<22}{m['accuracy']:>9.1%}{m['brier']:>10.4f}{m['logloss']:>10.4f}")

    for name, m in results.items():
        print(f"\n— {name} —")
        _print_confusion(m["confusion"])

    print("\nCalibration of home-win probability (multiplicative / new model):")
    print("    predicted   actual    n")
    for pred, act, c in _calibration_rows:
        print(f"      {pred:6.0%}     {act:6.0%}  {c:4d}")


_calibration_rows = []   # filled in run()


# --- driver ---------------------------------------------------------------

def run(pool=None, k=10, half_life=365.0, pages=2, min_prior=4, rho=DIXON_COLES_RHO,
        home_advantage=1.10, verbose=True):
    pool = pool or DEFAULT_POOL
    print(f"Gathering histories for {len(pool)} teams (cached after first run)…")
    teams = gather(pool, pages, verbose)
    matches = canonical_matches(teams)
    print(f"\n{len(matches)} candidate matches between pool teams.")

    # Hold DC + recency fixed and vary ONLY the goals model, so any difference is
    # attributable to average-blend vs multiplicative attack×defence.
    configs = {
        "average blend (old)":    {"rho": rho, "half_life": half_life, "goals_model": "average"},
        "multiplicative (new)":   {"rho": rho, "half_life": half_life, "goals_model": "multiplicative"},
    }
    records = {name: [] for name in configs}
    used = 0
    for ts, home_id, away_id, hg, ag in matches:
        actual = _outcome(hg, ag)
        ok = True
        per_config = {}
        for name, cfg in configs.items():
            hf, nh = _asof_form(teams[home_id]["name"], teams[home_id]["history"],
                                ts, k, cfg["half_life"])
            af, na = _asof_form(teams[away_id]["name"], teams[away_id]["history"],
                                ts, k, cfg["half_life"])
            if nh < min_prior or na < min_prior:
                ok = False
                break
            predict.GOALS_MODEL = cfg["goals_model"]
            p = predict_match(hf, af, home_advantage=home_advantage, rho=cfg["rho"])
            per_config[name] = ((p.prob_home, p.prob_draw, p.prob_away), actual)
        if not ok:
            continue
        used += 1
        for name in configs:
            records[name].append(per_config[name])
    predict.GOALS_MODEL = "multiplicative"   # restore the app default

    if used < 20:
        print(f"\n⚠ only {used} matches had enough prior history — results are noisy. "
              f"Try --pages 3 or a bigger --pool.")
    if used == 0:
        return

    results = {name: _metrics(recs) for name, recs in records.items()}
    base = _baselines(next(iter(records.values())))
    global _calibration_rows
    _calibration_rows = _calibration(records["multiplicative (new)"])
    report(results, base)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Backtest the football prediction model")
    ap.add_argument("--k", type=int, default=10, help="matches of form per team (default 10)")
    ap.add_argument("--half-life", type=float, default=365.0,
                    help="recency half-life in days for the improved model (default 365)")
    ap.add_argument("--pages", type=int, default=2, help="results-feed pages per team (~40 matches each)")
    ap.add_argument("--min-prior", type=int, default=4, help="min prior matches to predict (default 4)")
    ap.add_argument("--rho", type=float, default=DIXON_COLES_RHO, help="Dixon-Coles rho (default -0.13)")
    ap.add_argument("--home-advantage", type=float, default=1.10)
    args = ap.parse_args(argv)
    run(k=args.k, half_life=args.half_life, pages=args.pages,
        min_prior=args.min_prior, rho=args.rho, home_advantage=args.home_advantage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
