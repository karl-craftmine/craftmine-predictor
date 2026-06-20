"""footy — scrape WhoScored match data and predict match chances.

Public API:
    from footy import WhoScoredScraper, build_form, predict_match
"""

from .whoscored import WhoScoredScraper, WhoScoredError
from . import apifootball
from . import flashscore
from .form import TeamForm, build_form, aggregate_players
from .predict import predict_match, MatchPrediction
from .simulate import (simulate_match, SimResult, run_simulation, Simulation,
                       evaluate_bet)

__all__ = [
    "WhoScoredScraper",
    "WhoScoredError",
    "apifootball",
    "flashscore",
    "TeamForm",
    "build_form",
    "aggregate_players",
    "predict_match",
    "MatchPrediction",
    "simulate_match",
    "SimResult",
    "run_simulation",
    "Simulation",
    "evaluate_bet",
]

__version__ = "0.1.0"
