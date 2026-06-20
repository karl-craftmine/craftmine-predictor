"""Aggregate a team's recent matches into per-game averages (for & against)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# Human labels for the metrics we aggregate, in display order.
METRIC_LABELS = {
    "goals": "goals",
    "half_goals": "1st-half goals",
    "corners": "corners",
    "shots": "shots",
    "shots_on_target": "shots on tgt",
    "possession": "possession %",
    "fouls": "fouls",
    "offsides": "offsides",
    "yellow_cards": "yellow cards",
    "red_cards": "red cards",
    "aerials_won": "aerials won",
    "tackles": "tackles",
    "pass_acc": "pass acc %",
}

# Count metrics that can be modelled as Poisson over/under markets.
COUNT_METRICS = [
    "goals", "half_goals", "corners", "shots", "shots_on_target",
    "fouls", "offsides", "yellow_cards", "red_cards", "tackles", "aerials_won",
]


@dataclass
class TeamForm:
    """Per-game averages over the sampled matches (for & against)."""

    team_name: str
    matches: int = 0
    _sum_for: dict[str, float] = field(default_factory=dict)
    _sum_against: dict[str, float] = field(default_factory=dict)
    _count: dict[str, int] = field(default_factory=dict)
    sampled: list[dict[str, Any]] = field(default_factory=list)

    def add(self, key: str, value_for: Optional[float], value_against: Optional[float]) -> None:
        if value_for is None or value_against is None:
            return
        self._sum_for[key] = self._sum_for.get(key, 0.0) + value_for
        self._sum_against[key] = self._sum_against.get(key, 0.0) + value_against
        self._count[key] = self._count.get(key, 0) + 1

    def avg_for(self, key: str) -> Optional[float]:
        n = self._count.get(key, 0)
        return self._sum_for[key] / n if n else None

    def avg_against(self, key: str) -> Optional[float]:
        n = self._count.get(key, 0)
        return self._sum_against[key] / n if n else None

    def as_dict(self) -> dict[str, dict[str, Optional[float]]]:
        out = {}
        for k in self._count:
            af, aa = self.avg_for(k), self.avg_against(k)
            out[k] = {
                "for": round(af, 2) if af is not None else None,
                "against": round(aa, 2) if aa is not None else None,
                "samples": self._count[k],
            }
        return out


def aggregate_players(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-player per-match averages (shots, sot, goals, rating) over the sample.

    Sorted by average rating. Used to populate player-prop dropdowns and to
    seed per-player Poisson means for player markets.
    """
    agg: dict[str, dict[str, float]] = {}
    for m in matches:
        for p in m.get("players", []):
            name = p.get("name")
            if not name:
                continue
            a = agg.setdefault(name, {"shots": 0.0, "sot": 0.0, "goals": 0.0,
                                      "rating": 0.0, "games": 0,
                                      "position": p.get("position")})
            a["shots"] += p.get("shots", 0) or 0
            a["sot"] += p.get("sot", 0) or 0
            a["goals"] += p.get("goals", 0) or 0
            a["rating"] += p.get("rating", 0) or 0
            a["games"] += 1
    out = []
    for name, a in agg.items():
        g = a["games"]
        out.append({
            "name": name,
            "position": a["position"],
            "games": g,
            "shots_avg": round(a["shots"] / g, 2),
            "sot_avg": round(a["sot"] / g, 2),
            "goals_avg": round(a["goals"] / g, 3),
            "rating_avg": round(a["rating"] / g, 2),
        })
    out.sort(key=lambda x: x["rating_avg"], reverse=True)
    return out


def build_form(team_name: str, matches: list[dict[str, Any]]) -> TeamForm:
    """Build a TeamForm from per-match {'for': {...}, 'against': {...}} dicts."""
    form = TeamForm(team_name=team_name)
    for m in matches:
        f, a = m.get("for", {}), m.get("against", {})
        keys = set(f) | set(a)
        for k in keys:
            form.add(k, f.get(k), a.get(k))
        form.matches += 1
        form.sampled.append(
            {
                "match_id": m.get("match_id"),
                "date": m.get("date"),
                "opponent": m.get("opponent"),
                "venue": m.get("venue"),
                "goals_for": f.get("goals"),
                "goals_against": a.get("goals"),
            }
        )
    return form
