"""Leakage-safe backtest for prediction model improvements.

Compares baseline (independent Poisson, equal recency weights) vs improved
(Dixon-Coles low-score correction, recency-weighted form) by walking through
historical matches and predicting each using only prior data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

from footy.flashscore import FlashscoreScraper, is_national_team
from footy.form import TeamForm, build_form
from footy.predict import predict_match, DIXON_COLES_RHO


@dataclass
class BacktestResult:
    """Result of a single match prediction vs actual outcome."""
    match_id: str
    date: str
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    
    # Baseline predictions (rho=0, equal weights)
    baseline_prob_home: float
    baseline_prob_draw: float
    baseline_prob_away: float
    baseline_exp_home: float
    baseline_exp_away: float
    
    # Improved predictions (rho=-0.13, recency weights)
    improved_prob_home: float
    improved_prob_draw: float
    improved_prob_away: float
    improved_exp_home: float
    improved_exp_away: float
    
    # Component test predictions (if enabled)
    dc_only_prob_home: Optional[float] = None
    dc_only_prob_draw: Optional[float] = None
    dc_only_prob_away: Optional[float] = None
    recency_only_prob_home: Optional[float] = None
    recency_only_prob_draw: Optional[float] = None
    recency_only_prob_away: Optional[float] = None


@dataclass
class BacktestSummary:
    """Aggregate scoring metrics across all backtested matches."""
    total_matches: int
    
    # Baseline scores
    baseline_log_loss: float
    baseline_rmse: float
    baseline_correct_direction: int  # predicted winner matched actual winner
    baseline_confidence_avg: float  # average confidence in predicted outcome
    
    # Improved scores
    improved_log_loss: float
    improved_rmse: float
    improved_correct_direction: int
    improved_confidence_avg: float
    
    # Confusion matrices: {predicted: {actual: count}}
    baseline_confusion: dict[str, dict[str, int]]
    improved_confusion: dict[str, dict[str, int]]
    
    # Component test scores (if enabled)
    dc_only_log_loss: Optional[float] = None
    dc_only_rmse: Optional[float] = None
    dc_only_correct_direction: Optional[int] = None
    dc_only_confidence_avg: Optional[float] = None
    recency_only_log_loss: Optional[float] = None
    recency_only_rmse: Optional[float] = None
    recency_only_correct_direction: Optional[int] = None
    recency_only_confidence_avg: Optional[float] = None


def _recency_weight(days_ago: int, half_life: int = 90) -> float:
    """Exponential decay weight based on match recency.
    
    Recent matches get weight ~1.0, older matches decay toward 0.
    half_life: days for weight to halve (default 90 days ~ 3 months)
    """
    if days_ago <= 0:
        return 1.0
    # Clamp very old matches to a minimum weight to avoid complete exclusion
    weight = math.exp(-math.log(2) * days_ago / half_life)
    return max(weight, 0.1)


def _build_form_from_history(
    team_name: str,
    history: list[dict[str, Any]],
    cutoff_ts: int,
    use_recency: bool = False,
) -> TeamForm:
    """Build TeamForm from historical matches, respecting cutoff timestamp.
    
    Args:
        team_name: Team name for the form object
        history: List of historical match dicts from team_history()
        cutoff_ts: Unix timestamp - only use matches before this time
        use_recency: If True, apply exponential recency weights
    
    Returns:
        TeamForm object with aggregated stats
    """
    matches_for_form = []
    for m in history:
        if m["ts"] >= cutoff_ts:
            continue  # Skip matches at or after cutoff (leakage prevention)
        
        weight = 1.0
        if use_recency:
            days_ago = (cutoff_ts - m["ts"]) // 86400  # Convert seconds to days
            weight = _recency_weight(days_ago)
        
        matches_for_form.append({
            "match_id": m["match_id"],
            "date": m["date"],
            "opponent": m["opponent"],
            "venue": m["venue"],
            "for": {"goals": m["goals_for"]},
            "against": {"goals": m["goals_against"]},
            "weight": weight,
        })
    
    return build_form(team_name, matches_for_form)


def backtest_team(
    team_name: str,
    min_matches: int = 10,
    use_recency: bool = True,
    rho: float = DIXON_COLES_RHO,
    test_components: bool = False,
) -> tuple[list[BacktestResult], BacktestSummary]:
    """Backtest predictions for a single team's recent matches.
    
    Walks through the team's match history chronologically, predicting each
    match using only data from before that match (no leakage).
    
    Args:
        team_name: Name of the team to backtest
        min_matches: Minimum historical matches required before backtesting
        use_recency: Whether to use recency weighting for improved model
        rho: Dixon-Coles rho parameter (0 for baseline, -0.13 for improved)
    
    Returns:
        (results, summary) tuple
    """
    scraper = FlashscoreScraper()
    team = scraper.search_team(team_name)
    if not team:
        raise ValueError(f"Team '{team_name}' not found")
    
    # Get full goals-only history (newest first)
    history = scraper.team_history(team, pages=3)
    if len(history) < min_matches:
        raise ValueError(f"Only {len(history)} matches found, need at least {min_matches}")
    
    # Sort oldest to newest for chronological walk
    history = sorted(history, key=lambda m: m["ts"])
    
    results = []
    baseline_log_loss_sum = 0.0
    improved_log_loss_sum = 0.0
    baseline_se_sum = 0.0
    improved_se_sum = 0.0
    baseline_correct = 0
    improved_correct = 0
    baseline_confidence_sum = 0.0
    improved_confidence_sum = 0.0
    
    # Confusion matrices: {predicted: {actual: count}}
    baseline_confusion: dict[str, dict[str, int]] = {"H": {"H": 0, "D": 0, "A": 0}, 
                                                     "D": {"H": 0, "D": 0, "A": 0}, 
                                                     "A": {"H": 0, "D": 0, "A": 0}}
    improved_confusion: dict[str, dict[str, int]] = {"H": {"H": 0, "D": 0, "A": 0}, 
                                                      "D": {"H": 0, "D": 0, "A": 0}, 
                                                      "A": {"H": 0, "D": 0, "A": 0}}
    
    # Component test accumulators
    dc_only_log_loss_sum = 0.0
    dc_only_se_sum = 0.0
    dc_only_correct = 0
    dc_only_confidence_sum = 0.0
    recency_only_log_loss_sum = 0.0
    recency_only_se_sum = 0.0
    recency_only_correct = 0
    recency_only_confidence_sum = 0.0
    
    # Cache opponent team lookups to avoid repeated searches
    opponent_cache: dict[str, dict[str, str]] = {}
    
    # Skip first min_matches as burn-in (need enough history for form)
    for i in range(min_matches, len(history)):
        match = history[i]
        cutoff_ts = match["ts"]
        
        # Build form for both teams using only prior data
        # For the main team, we already have the team dict
        opponent_name = match["opponent"]
        
        # Cache opponent lookup
        if opponent_name not in opponent_cache:
            opponent = scraper.search_team(opponent_name)
            if not opponent:
                print(f"Warning: Opponent '{opponent_name}' not found, skipping match")
                continue
            opponent_cache[opponent_name] = opponent
        
        if match["venue"] == "H":
            home_team_dict = team
            away_team_dict = opponent_cache[opponent_name]
        else:
            away_team_dict = team
            home_team_dict = opponent_cache[opponent_name]
        
        home_history = scraper.team_history(home_team_dict, pages=3)
        away_history = scraper.team_history(away_team_dict, pages=3)
        
        # Check if both teams have enough history before the cutoff
        home_prior = [m for m in home_history if m["ts"] < cutoff_ts]
        away_prior = [m for m in away_history if m["ts"] < cutoff_ts]
        
        if len(home_prior) < 5 or len(away_prior) < 5:
            continue  # Skip if insufficient historical data
        
        if match["venue"] == "H":
            home_name, away_name = team_name, match["opponent"]
            home_goals, away_goals = match["goals_for"], match["goals_against"]
        else:
            home_name, away_name = match["opponent"], team_name
            home_goals, away_goals = match["goals_against"], match["goals_for"]
        
        # Baseline: equal weights, rho=0
        home_form_baseline = _build_form_from_history(home_name, home_history, cutoff_ts, use_recency=False)
        away_form_baseline = _build_form_from_history(away_name, away_history, cutoff_ts, use_recency=False)
        pred_baseline = predict_match(home_form_baseline, away_form_baseline, rho=0.0)
        
        # Improved: recency weights, rho=-0.13
        home_form_improved = _build_form_from_history(home_name, home_history, cutoff_ts, use_recency=use_recency)
        away_form_improved = _build_form_from_history(away_name, away_history, cutoff_ts, use_recency=use_recency)
        pred_improved = predict_match(home_form_improved, away_form_improved, rho=rho)
        
        # Component tests (if enabled)
        pred_dc_only = None
        pred_recency_only = None
        if test_components:
            # Dixon-Coles only (no recency)
            pred_dc_only = predict_match(home_form_baseline, away_form_baseline, rho=rho)
            # Recency only (no Dixon-Coles)
            pred_recency_only = predict_match(home_form_improved, away_form_improved, rho=0.0)
        
        # Actual outcome probabilities for log loss
        if home_goals > away_goals:
            actual_home, actual_draw, actual_away = 1.0, 0.0, 0.0
        elif home_goals < away_goals:
            actual_home, actual_draw, actual_away = 0.0, 0.0, 1.0
        else:
            actual_home, actual_draw, actual_away = 0.0, 1.0, 0.0
        
        # Log loss (add small epsilon to avoid log(0))
        eps = 1e-10
        baseline_ll = -(
            actual_home * math.log(pred_baseline.prob_home + eps) +
            actual_draw * math.log(pred_baseline.prob_draw + eps) +
            actual_away * math.log(pred_baseline.prob_away + eps)
        )
        improved_ll = -(
            actual_home * math.log(pred_improved.prob_home + eps) +
            actual_draw * math.log(pred_improved.prob_draw + eps) +
            actual_away * math.log(pred_improved.prob_away + eps)
        )
        
        dc_only_ll = 0.0
        recency_only_ll = 0.0
        if test_components and pred_dc_only and pred_recency_only:
            dc_only_ll = -(
                actual_home * math.log(pred_dc_only.prob_home + eps) +
                actual_draw * math.log(pred_dc_only.prob_draw + eps) +
                actual_away * math.log(pred_dc_only.prob_away + eps)
            )
            recency_only_ll = -(
                actual_home * math.log(pred_recency_only.prob_home + eps) +
                actual_draw * math.log(pred_recency_only.prob_draw + eps) +
                actual_away * math.log(pred_recency_only.prob_away + eps)
            )
        
        # RMSE for expected goals
        baseline_se = (pred_baseline.exp_home_goals - home_goals) ** 2 + \
                      (pred_baseline.exp_away_goals - away_goals) ** 2
        improved_se = (pred_improved.exp_home_goals - home_goals) ** 2 + \
                      (pred_improved.exp_away_goals - away_goals) ** 2
        
        dc_only_se = 0.0
        recency_only_se = 0.0
        if test_components and pred_dc_only and pred_recency_only:
            dc_only_se = (pred_dc_only.exp_home_goals - home_goals) ** 2 + \
                         (pred_dc_only.exp_away_goals - away_goals) ** 2
            recency_only_se = (pred_recency_only.exp_home_goals - home_goals) ** 2 + \
                             (pred_recency_only.exp_away_goals - away_goals) ** 2
        
        # Correct direction (did we predict the right winner?)
        baseline_winner = "H" if pred_baseline.prob_home > pred_baseline.prob_away else \
                         "A" if pred_baseline.prob_away > pred_baseline.prob_home else "D"
        improved_winner = "H" if pred_improved.prob_home > pred_improved.prob_away else \
                         "A" if pred_improved.prob_away > pred_improved.prob_home else "D"
        actual_winner = "H" if home_goals > away_goals else \
                       "A" if away_goals > home_goals else "D"
        
        # Confidence score: probability assigned to predicted outcome
        baseline_confidence = max(pred_baseline.prob_home, pred_baseline.prob_draw, pred_baseline.prob_away)
        improved_confidence = max(pred_improved.prob_home, pred_improved.prob_draw, pred_improved.prob_away)
        
        baseline_confusion[baseline_winner][actual_winner] += 1
        improved_confusion[improved_winner][actual_winner] += 1
        
        if baseline_winner == actual_winner:
            baseline_correct += 1
        if improved_winner == actual_winner:
            improved_correct += 1
        
        baseline_confidence_sum += baseline_confidence
        improved_confidence_sum += improved_confidence
        
        if test_components and pred_dc_only and pred_recency_only:
            dc_only_winner = "H" if pred_dc_only.prob_home > pred_dc_only.prob_away else \
                            "A" if pred_dc_only.prob_away > pred_dc_only.prob_home else "D"
            recency_only_winner = "H" if pred_recency_only.prob_home > pred_recency_only.prob_away else \
                                 "A" if pred_recency_only.prob_away > pred_recency_only.prob_home else "D"
            dc_only_confidence = max(pred_dc_only.prob_home, pred_dc_only.prob_draw, pred_dc_only.prob_away)
            recency_only_confidence = max(pred_recency_only.prob_home, pred_recency_only.prob_draw, pred_recency_only.prob_away)
            
            if dc_only_winner == actual_winner:
                dc_only_correct += 1
            if recency_only_winner == actual_winner:
                recency_only_correct += 1
            
            dc_only_confidence_sum += dc_only_confidence
            recency_only_confidence_sum += recency_only_confidence
        
        baseline_log_loss_sum += baseline_ll
        improved_log_loss_sum += improved_ll
        baseline_se_sum += baseline_se
        improved_se_sum += improved_se
        
        if test_components and pred_dc_only and pred_recency_only:
            dc_only_log_loss_sum += dc_only_ll
            dc_only_se_sum += dc_only_se
            recency_only_log_loss_sum += recency_only_ll
            recency_only_se_sum += recency_only_se
        
        results.append(BacktestResult(
            match_id=match["match_id"],
            date=match["date"],
            home_team=home_name,
            away_team=away_name,
            home_goals=home_goals,
            away_goals=away_goals,
            baseline_prob_home=pred_baseline.prob_home,
            baseline_prob_draw=pred_baseline.prob_draw,
            baseline_prob_away=pred_baseline.prob_away,
            baseline_exp_home=pred_baseline.exp_home_goals,
            baseline_exp_away=pred_baseline.exp_away_goals,
            improved_prob_home=pred_improved.prob_home,
            improved_prob_draw=pred_improved.prob_draw,
            improved_prob_away=pred_improved.prob_away,
            improved_exp_home=pred_improved.exp_home_goals,
            improved_exp_away=pred_improved.exp_away_goals,
            dc_only_prob_home=pred_dc_only.prob_home if test_components and pred_dc_only else None,
            dc_only_prob_draw=pred_dc_only.prob_draw if test_components and pred_dc_only else None,
            dc_only_prob_away=pred_dc_only.prob_away if test_components and pred_dc_only else None,
            recency_only_prob_home=pred_recency_only.prob_home if test_components and pred_recency_only else None,
            recency_only_prob_draw=pred_recency_only.prob_draw if test_components and pred_recency_only else None,
            recency_only_prob_away=pred_recency_only.prob_away if test_components and pred_recency_only else None,
        ))
    
    n = len(results)
    summary = BacktestSummary(
        total_matches=n,
        baseline_log_loss=baseline_log_loss_sum / n if n else 0,
        baseline_rmse=math.sqrt(baseline_se_sum / (2 * n)) if n else 0,
        baseline_correct_direction=baseline_correct,
        baseline_confidence_avg=baseline_confidence_sum / n if n else 0,
        improved_log_loss=improved_log_loss_sum / n if n else 0,
        improved_rmse=math.sqrt(improved_se_sum / (2 * n)) if n else 0,
        improved_correct_direction=improved_correct,
        improved_confidence_avg=improved_confidence_sum / n if n else 0,
        baseline_confusion=baseline_confusion,
        improved_confusion=improved_confusion,
        dc_only_log_loss=dc_only_log_loss_sum / n if test_components and n else None,
        dc_only_rmse=math.sqrt(dc_only_se_sum / (2 * n)) if test_components and n else None,
        dc_only_correct_direction=dc_only_correct if test_components else None,
        dc_only_confidence_avg=dc_only_confidence_sum / n if test_components and n else None,
        recency_only_log_loss=recency_only_log_loss_sum / n if test_components and n else None,
        recency_only_rmse=math.sqrt(recency_only_se_sum / (2 * n)) if test_components and n else None,
        recency_only_correct_direction=recency_only_correct if test_components else None,
        recency_only_confidence_avg=recency_only_confidence_sum / n if test_components and n else None,
    )
    
    return results, summary


def print_summary(summary: BacktestSummary) -> None:
    """Pretty-print backtest summary comparing baseline vs improved."""
    print("\n" + "=" * 60)
    print("BACKTEST SUMMARY")
    print("=" * 60)
    print(f"Total matches: {summary.total_matches}")
    print()
    print("BASELINE (independent Poisson, equal weights):")
    print(f"  Accuracy: {summary.baseline_correct_direction}/{summary.total_matches} "
          f"({100*summary.baseline_correct_direction/summary.total_matches:.1f}%)")
    print(f"  Log Loss: {summary.baseline_log_loss:.4f}")
    print(f"  RMSE (goals): {summary.baseline_rmse:.4f}")
    print(f"  Avg Confidence: {summary.baseline_confidence_avg:.4f}")
    print()
    print("IMPROVED (Dixon-Coles, recency weights):")
    print(f"  Accuracy: {summary.improved_correct_direction}/{summary.total_matches} "
          f"({100*summary.improved_correct_direction/summary.total_matches:.1f}%)")
    print(f"  Log Loss: {summary.improved_log_loss:.4f}")
    print(f"  RMSE (goals): {summary.improved_rmse:.4f}")
    print(f"  Avg Confidence: {summary.improved_confidence_avg:.4f}")
    
    # Confusion matrices
    print()
    print("BASELINE CONFUSION MATRIX (Predicted vs Actual):")
    print("         Actual H   Actual D   Actual A")
    for pred in ["H", "D", "A"]:
        row = f"Pred {pred}:"
        for actual in ["H", "D", "A"]:
            count = summary.baseline_confusion[pred][actual]
            row += f"    {count:5d}"
        print(row)
    
    print()
    print("IMPROVED CONFUSION MATRIX (Predicted vs Actual):")
    print("         Actual H   Actual D   Actual A")
    for pred in ["H", "D", "A"]:
        row = f"Pred {pred}:"
        for actual in ["H", "D", "A"]:
            count = summary.improved_confusion[pred][actual]
            row += f"    {count:5d}"
        print(row)
    
    # Component test results (if available)
    if summary.dc_only_log_loss is not None:
        print()
        print("COMPONENT TESTS:")
        print("Dixon-Coles only (no recency):")
        print(f"  Accuracy: {summary.dc_only_correct_direction}/{summary.total_matches} "
              f"({100*summary.dc_only_correct_direction/summary.total_matches:.1f}%)")
        print(f"  Log Loss: {summary.dc_only_log_loss:.4f}")
        print(f"  RMSE (goals): {summary.dc_only_rmse:.4f}")
        print(f"  Avg Confidence: {summary.dc_only_confidence_avg:.4f}")
        print()
        print("Recency only (no Dixon-Coles):")
        print(f"  Accuracy: {summary.recency_only_correct_direction}/{summary.total_matches} "
              f"({100*summary.recency_only_correct_direction/summary.total_matches:.1f}%)")
        print(f"  Log Loss: {summary.recency_only_log_loss:.4f}")
        print(f"  RMSE (goals): {summary.recency_only_rmse:.4f}")
        print(f"  Avg Confidence: {summary.recency_only_confidence_avg:.4f}")
    
    print()
    # Improvement metrics
    ll_improvement = summary.baseline_log_loss - summary.improved_log_loss
    rmse_improvement = summary.baseline_rmse - summary.improved_rmse
    dir_improvement = summary.improved_correct_direction - summary.baseline_correct_direction
    conf_improvement = summary.improved_confidence_avg - summary.baseline_confidence_avg
    
    print("IMPROVEMENT (combined):")
    print(f"  Accuracy: {dir_improvement:+d} ({100*dir_improvement/summary.total_matches:+.1f}%) "
          f"{'(better)' if dir_improvement > 0 else '(worse)' if dir_improvement < 0 else '(same)'}")
    print(f"  Log Loss: {ll_improvement:+.4f} {'(better)' if ll_improvement > 0 else '(worse)' if ll_improvement < 0 else '(same)'}")
    print(f"  RMSE: {rmse_improvement:+.4f} {'(better)' if rmse_improvement > 0 else '(worse)' if rmse_improvement < 0 else '(same)'}")
    print(f"  Confidence: {conf_improvement:+.4f} {'(better)' if conf_improvement > 0 else '(worse)' if conf_improvement < 0 else '(same)'}")
    print("=" * 60)


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python backtest.py <team_name> [--components]")
        print("Example: python backtest.py Brazil")
        print("         python backtest.py Brazil --components")
        sys.exit(1)
    
    team_name = sys.argv[1]
    test_components = "--components" in sys.argv
    print(f"Backtesting {team_name}{' (with component testing)' if test_components else ''}...")
    
    try:
        results, summary = backtest_team(team_name, test_components=test_components)
        print_summary(summary)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
