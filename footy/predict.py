"""Transparent Poisson prediction model built on aggregated team form.

For each metric we estimate home/away expected values by blending the home
team's "attack" with the away team's "defence" (and vice versa):

    exp_home = (home.for + away.against) / 2
    exp_away = (away.for + home.against) / 2

Goals get a home-advantage multiplier. Goals and corners are then modelled as
independent Poisson processes, which yields:
  * 1X2 (home / draw / away) from the goal score matrix
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


def _blend(home: TeamForm, away: TeamForm, key: str) -> tuple[Optional[float], Optional[float]]:
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


def predict_match(
    home: TeamForm,
    away: TeamForm,
    home_advantage: float = 1.10,
    goals_line: float = 2.5,
    corners_line: float = 9.5,
) -> MatchPrediction:
    # --- goals ---
    eh, ea, ch, ca = expected_values(home, away, home_advantage)

    matrix = _goal_matrix(eh, ea)
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
