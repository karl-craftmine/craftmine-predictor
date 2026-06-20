"""Monte Carlo match simulator.

Simulates the match `iterations` times, drawing each team's goals and corners
from Poisson distributions whose means come from `expected_values` — the SAME
means the analytical model uses, so the two agree on shared markets while the
simulation also yields things that are awkward to derive analytically:
exact-score distribution, Asian handicaps, clean sheets, win-to-nil, and
per-team / total corner lines.

Uses numpy for speed (vectorised RNG); falls back to pure Python if numpy is
absent (slower, but works).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from .form import TeamForm, COUNT_METRICS, METRIC_LABELS
from .predict import expected_values, _blend, implied_odds

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


@dataclass
class SimResult:
    home_name: str
    away_name: str
    iterations: int
    exp_home_goals: float
    exp_away_goals: float
    prob_home: float
    prob_draw: float
    prob_away: float
    goals_line: float
    prob_over_goals: float
    prob_btts: float
    clean_sheet_home: float
    clean_sheet_away: float
    win_to_nil_home: float
    win_to_nil_away: float
    scorelines: list[tuple[str, float]] = field(default_factory=list)
    total_goals_dist: list[tuple[str, float]] = field(default_factory=list)
    handicaps: list[tuple[str, float]] = field(default_factory=list)
    # corners
    exp_home_corners: Optional[float] = None
    exp_away_corners: Optional[float] = None
    corners_line: float = 9.5
    prob_corners_over: Optional[float] = None
    prob_home_corners_over: Optional[float] = None
    prob_away_corners_over: Optional[float] = None
    team_corners_line: float = 4.5


def simulate_match(
    home: TeamForm,
    away: TeamForm,
    iterations: int = 50000,
    home_advantage: float = 1.10,
    goals_line: float = 2.5,
    corners_line: float = 9.5,
    team_corners_line: float = 4.5,
    seed: Optional[int] = None,
) -> SimResult:
    if np is None:  # pragma: no cover
        raise RuntimeError("numpy is required for simulation (pip install numpy)")

    eh, ea, ch, ca = expected_values(home, away, home_advantage)
    rng = np.random.default_rng(seed)

    hg = rng.poisson(eh, iterations)
    ag = rng.poisson(ea, iterations)
    total = hg + ag
    diff = hg - ag

    def frac(mask) -> float:
        return float(np.mean(mask))

    # exact scores (cap display at 6 each)
    cap = 6
    m = (hg <= cap) & (ag <= cap)
    pair_counts = Counter(zip(hg[m].tolist(), ag[m].tolist()))
    scorelines = sorted(
        ((f"{h}-{a}", c / iterations) for (h, a), c in pair_counts.items()),
        key=lambda x: x[1], reverse=True,
    )[:6]

    # total goals distribution 0..5, 6+
    tg = []
    for g in range(6):
        tg.append((str(g), frac(total == g)))
    tg.append(("6+", frac(total >= 6)))

    # Asian-style handicaps on the home side
    handicaps = [
        ("Home -2.5", frac(diff > 2.5)),
        ("Home -1.5", frac(diff > 1.5)),
        ("Home -0.5 (= win)", frac(diff > 0)),
        ("Home +1.5", frac(diff > -1.5)),
        ("Away -1.5", frac(-diff > 1.5)),
    ]

    res = SimResult(
        home_name=home.team_name,
        away_name=away.team_name,
        iterations=iterations,
        exp_home_goals=round(float(hg.mean()), 2),
        exp_away_goals=round(float(ag.mean()), 2),
        prob_home=frac(hg > ag),
        prob_draw=frac(hg == ag),
        prob_away=frac(hg < ag),
        goals_line=goals_line,
        prob_over_goals=frac(total > goals_line),
        prob_btts=frac((hg > 0) & (ag > 0)),
        clean_sheet_home=frac(ag == 0),
        clean_sheet_away=frac(hg == 0),
        win_to_nil_home=frac((hg > ag) & (ag == 0)),
        win_to_nil_away=frac((ag > hg) & (hg == 0)),
        scorelines=scorelines,
        total_goals_dist=tg,
        handicaps=handicaps,
        corners_line=corners_line,
        team_corners_line=team_corners_line,
    )

    if ch is not None and ca is not None:
        hc = rng.poisson(ch, iterations)
        ac = rng.poisson(ca, iterations)
        res.exp_home_corners = round(float(hc.mean()), 2)
        res.exp_away_corners = round(float(ac.mean()), 2)
        res.prob_corners_over = frac((hc + ac) > corners_line)
        res.prob_home_corners_over = frac(hc > team_corners_line)
        res.prob_away_corners_over = frac(ac > team_corners_line)

    return res


# ---------------------------------------------------------------------------
# Generic engine: simulate once, evaluate any bet (used by the web bet-builder)
# ---------------------------------------------------------------------------

def _metric_means(home, away, metric, home_advantage):
    eh, ea = _blend(home, away, metric)
    if eh is None or ea is None:
        return None
    if metric == "goals":
        eh *= home_advantage
    return max(float(eh), 0.0), max(float(ea), 0.0)


class Simulation:
    """Holds the raw simulated arrays so any market is just a query over them."""

    def __init__(self, home_name: str, away_name: str, iterations: int):
        self.home_name = home_name
        self.away_name = away_name
        self.n = iterations
        self.metrics: dict[str, tuple] = {}   # metric -> (home_arr, away_arr)
        self.means: dict[str, tuple] = {}      # metric -> (exp_home, exp_away)
        self.players: dict[str, dict] = {}     # key -> {name, side, shots, sot, goals}


def run_simulation(home: TeamForm, away: TeamForm, iterations: int = 50000,
                   home_advantage: float = 1.10, player_specs=None,
                   seed=None) -> Simulation:
    if np is None:  # pragma: no cover
        raise RuntimeError("numpy is required (pip install numpy)")
    rng = np.random.default_rng(seed)
    sim = Simulation(home.team_name, away.team_name, iterations)
    for metric in COUNT_METRICS:
        m = _metric_means(home, away, metric, home_advantage)
        if m is None:
            continue
        eh, ea = m
        sim.means[metric] = (round(eh, 2), round(ea, 2))
        sim.metrics[metric] = (rng.poisson(eh, iterations), rng.poisson(ea, iterations))
    for spec in (player_specs or []):
        sim.players[spec["key"]] = {
            "name": spec.get("name"), "side": spec.get("side"),
            "shots": rng.poisson(max(spec.get("shots", 0.0), 0.0), iterations),
            "sot": rng.poisson(max(spec.get("sot", 0.0), 0.0), iterations),
            "goals": rng.poisson(max(spec.get("goals", 0.0), 0.0), iterations),
        }
    return sim


def _ou(arr, line, ou):
    over = float(np.mean(arr > line))
    return over if ou == "over" else 1.0 - over


def evaluate_bet(sim: Simulation, spec: dict) -> dict:
    """Evaluate one bet spec against the simulation → {label, prob, odds}."""
    H, A = sim.home_name, sim.away_name
    t = spec.get("type")
    goals = sim.metrics.get("goals")
    hg, ag = (goals if goals else (None, None))

    def out(label, prob):
        if prob is None:
            return {"label": label, "prob": None, "odds": None}
        prob = float(prob)
        return {"label": label, "prob": round(prob, 4), "odds": implied_odds(prob)}

    def frac(mask):
        return float(np.mean(mask))

    def team(side):
        return H if side == "home" else A

    def mlabel(metric):
        return METRIC_LABELS.get(metric, metric)

    def win_mask(side):
        if side == "home":
            return hg > ag
        if side == "away":
            return hg < ag
        return hg == ag

    if t == "result":
        s = spec["side"]
        lbl = "Draw" if s == "draw" else f"{team(s)} win"
        return out(lbl, frac(win_mask(s)))

    if t == "btts":
        yes = (hg > 0) & (ag > 0)
        if spec.get("value", "yes") == "yes":
            return out("Both teams to score", frac(yes))
        return out("Both teams to score - No", frac(~yes))

    if t == "team_to_score":
        arr = hg if spec["side"] == "home" else ag
        return out(f"{team(spec['side'])} to score", frac(arr > 0))

    if t == "win_to_nil":
        s = spec["side"]
        mask = (hg > ag) & (ag == 0) if s == "home" else (ag > hg) & (hg == 0)
        return out(f"{team(s)} win & opponent no goal", frac(mask))

    if t == "result_btts":
        yes = (hg > 0) & (ag > 0)
        btts = yes if spec.get("btts", "yes") == "yes" else ~yes
        s = spec["side"]
        wl = "Draw" if s == "draw" else f"{team(s)} win"
        suffix = "BTTS" if spec.get("btts", "yes") == "yes" else "no BTTS"
        return out(f"{wl} & {suffix}", frac(win_mask(s) & btts))

    if t == "result_team_ou":
        m = sim.metrics.get("goals")
        if m is None:
            return out("(no data)", None)
        arr = m[0] if spec["team"] == "home" else m[1]
        over = arr > spec["line"]
        cond = over if spec["ou"] == "over" else ~over
        s = spec["side"]
        wl = "Draw" if s == "draw" else f"{team(s)} win"
        return out(f"{wl} & {team(spec['team'])} {spec['ou']} {spec['line']} goals",
                   frac(win_mask(s) & cond))

    if t == "total_ou":
        m = sim.metrics.get(spec["metric"])
        if m is None:
            return out(f"{spec['ou'].title()} {spec['line']} {mlabel(spec['metric'])}", None)
        return out(f"{spec['ou'].title()} {spec['line']} total {mlabel(spec['metric'])}",
                   _ou(m[0] + m[1], spec["line"], spec["ou"]))

    if t == "team_ou":
        m = sim.metrics.get(spec["metric"])
        if m is None:
            return out(f"{team(spec['side'])} {spec['ou']} {spec['line']} {mlabel(spec['metric'])}", None)
        arr = m[0] if spec["side"] == "home" else m[1]
        return out(f"{team(spec['side'])} {spec['ou']} {spec['line']} {mlabel(spec['metric'])}",
                   _ou(arr, spec["line"], spec["ou"]))

    if t == "player_ou":
        pl = sim.players.get(spec["key"])
        name = spec.get("name") or (pl and pl["name"]) or "player"
        prop = {"shots": "shots", "sot": "shots on target", "goals": "goals"}[spec["prop"]]
        if not pl:
            return out(f"{name} {spec['ou']} {spec['line']} {prop}", None)
        return out(f"{name} {spec['ou']} {spec['line']} {prop}",
                   _ou(pl[spec["prop"]], spec["line"], spec["ou"]))

    if t == "player_to_score":
        pl = sim.players.get(spec["key"])
        name = spec.get("name") or (pl and pl["name"]) or "player"
        if not pl:
            return out(f"{name} to score", None)
        return out(f"{name} to score (anytime)", frac(pl["goals"] > 0))

    return out("(unknown market)", None)
