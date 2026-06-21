"""footy — scrape WhoScored match data and predict match chances.

Public API:
    from footy import WhoScoredScraper, build_form, predict_match
"""

from .whoscored import WhoScoredScraper, WhoScoredError
from . import apifootball
from . import flashscore
from .form import TeamForm, build_form, aggregate_players, apply_recency_weights
from .predict import predict_match, MatchPrediction, DIXON_COLES_RHO
from .simulate import (simulate_match, SimResult, run_simulation, Simulation,
                       evaluate_bet, top_scorelines)

__all__ = [
    "WhoScoredScraper",
    "WhoScoredError",
    "apifootball",
    "flashscore",
    "TeamForm",
    "build_form",
    "aggregate_players",
    "apply_recency_weights",
    "predict_match",
    "MatchPrediction",
    "DIXON_COLES_RHO",
    "simulate_match",
    "SimResult",
    "run_simulation",
    "Simulation",
    "evaluate_bet",
    "top_scorelines",
]

__version__ = "0.1.0"
