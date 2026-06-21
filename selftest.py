"""Offline sanity check — runs both engines on fake form, no network/browser.

    python selftest.py

Confirms the math works and that the Monte Carlo simulation agrees with the
analytical model on shared markets — both with and without the Dixon-Coles
low-score correction — before you pull live WhoScored data.
"""

from footy import build_form, predict_match, simulate_match, DIXON_COLES_RHO


def fake(team, gf, ga, cf, ca, n=8):
    matches = [{
        "for": {"goals": gf, "corners": cf},
        "against": {"goals": ga, "corners": ca},
        "opponent": "X", "venue": "H", "players": [],
    } for _ in range(n)]
    return build_form(team, matches)


def main() -> int:
    home = fake("Home FC", 2.0, 0.8, 6.5, 3.5)
    away = fake("Away Utd", 1.1, 1.4, 4.0, 5.0)

    pred = predict_match(home, away)
    assert abs(pred.prob_home + pred.prob_draw + pred.prob_away - 1.0) < 1e-6
    assert pred.prob_home > pred.prob_away, "home should be favoured"

    res = simulate_match(home, away, iterations=60000, seed=7)
    assert abs(res.prob_home + res.prob_draw + res.prob_away - 1.0) < 1e-9

    # Monte Carlo should agree with the analytical model on shared markets.
    assert abs(res.prob_home - pred.prob_home) < 0.02, (res.prob_home, pred.prob_home)
    assert abs(res.prob_over_goals - pred.prob_over_goals) < 0.02
    assert abs(res.prob_corners_over - pred.prob_corners_over) < 0.02

    # With Dixon-Coles on, the corrected MC (sampling from the DC score matrix)
    # must still match the corrected analytical model, and the correction must
    # lift draws above the independent-Poisson baseline.
    pred_dc = predict_match(home, away, rho=DIXON_COLES_RHO)
    res_dc = simulate_match(home, away, iterations=60000, seed=7, rho=DIXON_COLES_RHO)
    assert abs(res_dc.prob_home - pred_dc.prob_home) < 0.02, (res_dc.prob_home, pred_dc.prob_home)
    assert abs(res_dc.prob_draw - pred_dc.prob_draw) < 0.02, (res_dc.prob_draw, pred_dc.prob_draw)
    assert pred_dc.prob_draw > pred.prob_draw, "Dixon-Coles should boost draws"

    print("Self-test passed.\n")
    print(f"  Analytical  H/D/A: {pred.prob_home:.0%} / {pred.prob_draw:.0%} / {pred.prob_away:.0%}"
          f"   O{pred.goals_line}: {pred.prob_over_goals:.0%}"
          f"   O{pred.corners_line} corners: {pred.prob_corners_over:.0%}")
    print(f"  MonteCarlo  H/D/A: {res.prob_home:.0%} / {res.prob_draw:.0%} / {res.prob_away:.0%}"
          f"   O{res.goals_line}: {res.prob_over_goals:.0%}"
          f"   O{res.corners_line} corners: {res.prob_corners_over:.0%}")
    print(f"  MC extras -> home clean sheet {res.clean_sheet_home:.0%}, "
          f"home -1.5 {dict(res.handicaps)['Home -1.5']:.0%}, "
          f"home win-to-nil {res.win_to_nil_home:.0%}")
    print(f"  Dixon-Coles -> draw {pred.prob_draw:.1%} (Poisson) -> "
          f"{pred_dc.prob_draw:.1%} (analytical) ~ {res_dc.prob_draw:.1%} (MC)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
