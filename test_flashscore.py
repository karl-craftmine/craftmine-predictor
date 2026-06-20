"""Smoke test: full Flashscore national-team flow (search -> matches -> stats)."""

import sys

from footy import flashscore
from footy.form import build_form

TEAM = sys.argv[1] if len(sys.argv) > 1 else "Brazil"

print(f"Testing Flashscore for '{TEAM}'...")
try:
    team, matches = flashscore.load_team(TEAM, matches_limit=10)
    print(f"  team: {team['name']} (id {team['id']}, source {team['source']})")
    print(f"  found {len(matches)} finished matches")
    for m in matches[:5]:
        f = m["for"]
        print(f"   {m['date']}  {m['venue']} vs {m['opponent']:<16} "
              f"goals={f.get('goals')} corners={f.get('corners')} "
              f"shots={f.get('shots')} poss={f.get('possession')}")

    # Confirm the data actually feeds the model.
    form = build_form(team["name"], matches)
    print(f"\n  TeamForm over {form.matches} matches:")
    for key in ("goals", "corners", "shots", "shots_on_target", "possession"):
        af, aa = form.avg_for(key), form.avg_against(key)
        if af is not None:
            print(f"    {key:<16} for={af:.2f}  against={aa:.2f}")
except flashscore.FlashscoreError as e:
    print(f"FlashscoreError: {e}")
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
