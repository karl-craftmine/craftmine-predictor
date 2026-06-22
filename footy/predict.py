"""Transparent Poisson prediction model built on aggregated team form.

For each metric we estimate home/away expected values by blending the home
team's "attack" with the away team's "defence" (and vice versa):

    exp_home = (home.for + away.against) / 2
    exp_away = (away.for + home.against) / 2

Goals get a home-advantage multiplier. Goals are modelled as Poisson with an
optional Dixon-Coles low-score correction (``rho``); corners stay independent
Poisson. This yields:
  * 1X2 (home / draw / away) from the (corrected) goal score matrix
  * Over/Under and BTTS for goals
  * Over/Under for total corners

A baseline, not a crystal ball — compare the probabilities to bookmaker odds.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .form import TeamForm

MAX_GOALS = 12  # truncation point for the Poisson score matrix


def poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * lam ** k / math.factorial(k)


def poisson_sf_over(line: float, lam: float) -> float:
    """P(X > line) for Poisson(lam). Use half-lines (e.g. 9.5) to avoid pushes."""
    threshold = math.floor(line) + 1
    cdf_below = sum(poisson_pmf(k, lam) for k in range(threshold))
    return max(0.0, 1.0 - cdf_below)


# --- goals model ----------------------------------------------------------
# Expected goals can be computed two ways:
#   "average"        — legacy: (team attack + opponent defence) / 2.
#   "multiplicative" — λ = μ · attack · defence, where attack/defence are each
#                      team's goals-for / goals-against relative to a league
#                      baseline μ. A strong attack meeting a weak defence now
#                      *compounds* instead of averaging out, so lopsided games
#                      (e.g. Spain vs a minnow) aren't pulled toward the middle.
# Switchable so footy/backtest.py can score the two head-to-head.
GOALS_MODEL = "multiplicative"

LEAGUE_AVG_GOALS = 1.35     # reference goals per team per game (the baseline μ)
_RATING_SHRINK = 5.0        # pseudo-matches pulling a thin sample toward average
_RATING_CLAMP = 2.2         # cap on a single attack/defence multiplier
_MAX_EXP_GOALS = 4.0        # ceiling on expected goals (tames small-sample blow-ups)


def _rating(value: Optional[float], samples: int, mu: float) -> float:
    """A team's goal strength relative to baseline μ, shrunk for small samples.

    1.0 = average; >1 scores/concedes more than average. With few matches the
    estimate is pulled toward 1.0 (regression to the mean) and clamped, so a
    fluky 5-0 in a 3-game sample can't explode the prediction.
    """
    if value is None or mu <= 0:
        return 1.0
    raw = value / mu
    shrunk = 1.0 + (raw - 1.0) * (samples / (samples + _RATING_SHRINK)) if samples else 1.0
    return min(_RATING_CLAMP, max(1.0 / _RATING_CLAMP, shrunk))


def _expected_goals(home: TeamForm, away: TeamForm,
                    mu: float = LEAGUE_AVG_GOALS) -> tuple[float, float]:
    """Multiplicative attack×defence expected goals (pre home-advantage)."""
    att_h = _rating(home.avg_for("goals"), home.samples("goals"), mu)
    def_a = _rating(away.avg_against("goals"), away.samples("goals"), mu)
    att_a = _rating(away.avg_for("goals"), away.samples("goals"), mu)
    def_h = _rating(home.avg_against("goals"), home.samples("goals"), mu)
    return (min(_MAX_EXP_GOALS, mu * att_h * def_a),
            min(_MAX_EXP_GOALS, mu * att_a * def_h))


def _blend(home: TeamForm, away: TeamForm, key: str) -> tuple[Optional[float], Optional[float]]:
    if key == "goals" and GOALS_MODEL == "multiplicative":
        return _expected_goals(home, away)
    hf, ha = home.avg_for(key), home.avg_against(key)
    af, aa = away.avg_for(key), away.avg_against(key)
    exp_home = (hf + aa) / 2 if hf is not None and aa is not None else hf
    exp_away = (af + ha) / 2 if af is not None and ha is not None else af
    return exp_home, exp_away


def expected_values(
    home: TeamForm, away: TeamForm, home_advantage: float = 1.10
) -> tuple[float, float, Optional[float], Optional[float]]:
    """Shared expected goals/corners used by BOTH the analytical and MC engines.

    Returns (exp_home_goals, exp_away_goals, exp_home_corners, exp_away_corners).
    Falls back to league-ish priors when a team has no data for a metric.
    """
    eh, ea = _blend(home, away, "goals")
    eh = (eh if eh is not None else 1.3) * home_advantage
    ea = ea if ea is not None else 1.1
    ch, ca = _blend(home, away, "corners")
    return eh, ea, ch, ca


@dataclass
class MatchPrediction:
    home_name: str
    away_name: str
    exp_home_goals: float
    exp_away_goals: float
    prob_home: float
    prob_draw: float
    prob_away: float
    prob_over_goals: float
    prob_btts: float
    exp_home_corners: Optional[float] = None
    exp_away_corners: Optional[float] = None
    corners_line: float = 9.5
    prob_corners_over: Optional[float] = None
    goals_line: float = 2.5
    scorelines: list[tuple[str, float]] = field(default_factory=list)


def _goal_matrix(lam_home: float, lam_away: float):
    home_p = [poisson_pmf(i, lam_home) for i in range(MAX_GOALS + 1)]
    away_p = [poisson_pmf(j, lam_away) for j in range(MAX_GOALS + 1)]
    return [[home_p[i] * away_p[j] for j in range(MAX_GOALS + 1)]
            for i in range(MAX_GOALS + 1)]


# Dixon–Coles low-score dependence correction. Independent Poisson under-predicts
# draws (0-0, 1-1) and over-predicts 1-0/0-1; this re-weights those four cells.
# rho < 0 boosts draws; a commonly fitted value for football is around -0.13.
DIXON_COLES_RHO = -0.13


def _dc_tau(i: int, j: int, lam: float, mu: float, rho: float) -> float:
    if i == 0 and j == 0:
        return 1.0 - lam * mu * rho
    if i == 0 and j == 1:
        return 1.0 + lam * rho
    if i == 1 and j == 0:
        return 1.0 + mu * rho
    if i == 1 and j == 1:
        return 1.0 - rho
    return 1.0


def _apply_dixon_coles(matrix, lam: float, mu: float, rho: float):
    """Re-weight the four low-score cells and renormalise to a valid distribution."""
    adj = [row[:] for row in matrix]
    for i in (0, 1):
        for j in (0, 1):
            adj[i][j] = max(0.0, matrix[i][j] * _dc_tau(i, j, lam, mu, rho))
    total = sum(sum(row) for row in adj)
    return [[c / total for c in row] for row in adj] if total > 0 else adj


def predict_match(
    home: TeamForm,
    away: TeamForm,
    home_advantage: float = 1.10,
    goals_line: float = 2.5,
    corners_line: float = 9.5,
    rho: float = 0.0,
) -> MatchPrediction:
    # --- goals ---
    eh, ea, ch, ca = expected_values(home, away, home_advantage)

    matrix = _goal_matrix(eh, ea)
    if rho:                                  # Dixon–Coles low-score correction
        matrix = _apply_dixon_coles(matrix, eh, ea, rho)
    p_home = p_draw = p_away = p_btts = 0.0
    for i in range(MAX_GOALS + 1):
        for j in range(MAX_GOALS + 1):
            p = matrix[i][j]
            if i > j:
                p_home += p
            elif i == j:
                p_draw += p
            else:
                p_away += p
            if i > 0 and j > 0:
                p_btts += p

    threshold = math.floor(goals_line) + 1
    p_over_goals = sum(
        matrix[i][j]
        for i in range(MAX_GOALS + 1)
        for j in range(MAX_GOALS + 1)
        if i + j >= threshold
    )

    flat = [
        (f"{i}-{j}", matrix[i][j])
        for i in range(min(MAX_GOALS, 6) + 1)
        for j in range(min(MAX_GOALS, 6) + 1)
    ]
    flat.sort(key=lambda x: x[1], reverse=True)

    # --- corners ---
    prob_corners_over = (
        poisson_sf_over(corners_line, ch + ca)
        if ch is not None and ca is not None else None
    )

    return MatchPrediction(
        home_name=home.team_name,
        away_name=away.team_name,
        exp_home_goals=round(eh, 2),
        exp_away_goals=round(ea, 2),
        prob_home=p_home,
        prob_draw=p_draw,
        prob_away=p_away,
        prob_over_goals=p_over_goals,
        prob_btts=p_btts,
        exp_home_corners=round(ch, 2) if ch is not None else None,
        exp_away_corners=round(ca, 2) if ca is not None else None,
        corners_line=corners_line,
        prob_corners_over=prob_corners_over,
        goals_line=goals_line,
        scorelines=flat[:5],
    )


def implied_odds(prob: float) -> Optional[float]:
    """Fair decimal odds for a probability (no bookmaker margin)."""
    return round(1.0 / prob, 2) if prob and prob > 0 else None
