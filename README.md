# footy — WhoScored match predictor

A Python betting-style predictor. It scrapes recent match data from
**WhoScored**, aggregates each team's chances **for and against** (corners,
shots, goals, possession, …), and predicts a match two ways:

- an **analytical Poisson** model, and
- a **Monte Carlo simulation** (`--sim`) that also gives exact-score
  distributions, Asian handicaps, clean sheets, win-to-nil, and corner lines.

It prints **fair odds** for every market so you can compare against a bookmaker.

> ⚠️ WhoScored has no public API and sits behind Cloudflare. This drives a real
> Chrome browser (SeleniumBase **UC mode**) to read the data the page already
> loads. For personal use only — don't hammer it. The model is a **baseline**;
> gamble responsibly.

---

## Setup

**1. Install Python** (if you haven't): <https://www.python.org/downloads/> —
tick *"Add python.exe to PATH"*. You also need **Google Chrome** installed.

**2. Install dependencies:**

```powershell
cd C:\Users\pried\OneDrive\Documents\predictor
python -m pip install -r requirements.txt
```

**3. Check the math works (offline, no browser):**

```powershell
python selftest.py
```

---

## Web app + bet builder (no commands, no pop-ups) — recommended

```powershell
python server.py
```

Then open the address it prints — **http://127.0.0.1:5000** (or the next free
port like `:5001` if 5000 is busy). The scraping runs **hidden in the
background** (headless Chrome — nothing pops up).

1. Start typing a team — an **autocomplete** dropdown suggests matches (top
   leagues, ~100 teams; the index builds itself on first run). Or click
   **📅 Upcoming fixtures** to list the home team's next matches and pick one
   (populates during the season; empty in the off-season).
2. **Load match** (first lookup ~1–2 min, cached after).
3. **Build your bet slip**: pick a market from the dropdown — it expands to the
   right fields (team / line / over-under / player) — and click **+ Add**.
   Stack as many as you want.
4. **Simulate** → one Monte Carlo run evaluates your whole slip and prints each
   bet's probability and fair odds, plus form and player tables for reference.

Markets you can build:

- Match result (1X2), both teams to score, team to score
- **Result + BTTS**, **win to nil** (result + opponent no goal), **result + a
  team's goal count**
- Over/Under (total or per-team) for **goals, first-half goals, corners, shots,
  shots on target, fouls, offsides, yellow/red cards, tackles, aerials**
- **Player** over/under (shots / shots on target / goals) and **player to score
  (anytime)** — any player from either lineup

Leave the slip empty and Simulate gives you the standard markets (1X2, O/U 2.5,
BTTS).

### Following & upcoming fixtures

The **📅 Following** panel at the top shows a calendar of upcoming fixtures for
the teams and competitions you follow. Click any fixture to drop both teams into
the predictor and simulate.

Fixtures come from **TheSportsDB** (a free fixtures API — no signup), so the
calendar is **instant** (~0.5s) and shows real dates *and* kickoff times for the
**next 7 days**, including the World Cup. (Match *simulations* still use
WhoScored — that's where the detailed corner/shot stats live.)

- **Manage** → follow teams and competitions, both with **search autocomplete**
  (**Browse** lists the supported competitions). Your follows are saved in
  `cache/follows.json`.
- **↻ Refresh** rebuilds the fixtures (now ~0.5s) into `cache/fixtures.json`,
  which the page reads instantly.
- Followed **teams** and **competitions** both show their next matches over the
  next 7 days. Quiet in the off-season; great for tournaments that are on.

> TheSportsDB's free key is shared and rate-limited, so a refresh fired right
> after many requests can come back thin — just hit ↻ Refresh again. Normal use
> (a refresh or two a day) is well within limits.

#### Staying fresh

The page shows the cached fixtures **instantly**, then — if the cache is **more
than ~6 hours old or from a previous day** — refreshes from the API in the
background (now ~0.5s) and swaps in fresh fixtures. So it's current whenever you
open it, no matter the time; it does **not** flip at midnight on its own. (Tune
the window via `STALE_HOURS` in `index.html`.)

As a backstop, a Windows scheduled task **`FootyFixturesRefresh`** also runs
`refresh_fixtures.py` every day at **08:00** so the cache is usually already
warm. Manage it:

```powershell
Get-ScheduledTask FootyFixturesRefresh                 # check it
Start-ScheduledTask FootyFixturesRefresh               # run it now
Set-ScheduledTask FootyFixturesRefresh -Trigger (New-ScheduledTaskTrigger -Daily -At 7:00am)   # change time
Unregister-ScheduledTask FootyFixturesRefresh -Confirm:$false                                   # remove it
```

You can also refresh by hand anytime: `python refresh_fixtures.py`.

---

## Command line

Home team first. The browser runs hidden by default (no window).

```powershell
python app.py "Arsenal" "Chelsea"                 # analytical model
python app.py "Arsenal" "Chelsea" --sim           # Monte Carlo (more markets)
python app.py "Liverpool" "Everton" -m 12 --sim --players
python app.py 13 15 --sim                          # WhoScored team IDs also work
```

### Flags

| flag | meaning |
|------|---------|
| `-m, --matches N` | finished matches to sample per team (default 10) |
| `--sim` | use the Monte Carlo engine (adds handicaps, clean sheets, etc.) |
| `--iterations N` | Monte Carlo iterations (default 50000) |
| `--goals-line X` | over/under goals line (default 2.5) |
| `--corners-line X` | over/under corners line (default 9.5) |
| `--home-advantage X` | home goal multiplier (default 1.10) |
| `--players` | show each team's top players by average rating |
| `--show-browser` | show the Chrome window (default: hidden/headless) |
| `--no-cache` | ignore cached match data |
| `-v, --verbose` | print each sampled match as it's read |

Match data is cached under `cache/` for 6 hours, so re-runs are fast and polite.

---

## What it pulls per match (for **and** against)

goals · **corners** · shots · shots on target · possession · fouls · offsides ·
aerials won · tackles · pass accuracy · player ratings.

Because each WhoScored match page carries *both* teams' stats, "for" and
"against" come from the same fetch — no cross-referencing needed.

---

## How it works

```
footy/
  whoscored.py   UC-mode scraper: search team → recent matches → matchCentreData,
                 parsed into per-match for/against stats (+ player ratings). Cached.
  form.py        TeamForm: per-game for/against averages over the sample.
  predict.py     Analytical Poisson model → 1X2, O/U goals, BTTS, O/U corners.
  simulate.py    Monte Carlo engine (numpy) → everything above + handicaps,
                 clean sheets, win-to-nil, score distribution, per-team corners.
  fixtures.py    follow list + upcoming-fixtures aggregation
  sportsdb.py    TheSportsDB client (fast fixtures API for the calendar)
app.py           command-line interface
server.py        local web app (Flask) — search, simulate, follow, fixtures
index.html       the web app's single page
refresh_fixtures.py  rescrape followed fixtures (run by the daily task)
selftest.py      offline check (confirms MC agrees with the analytical model)
```

**The model.** For each metric, the home expectation blends the home team's
output with the away team's concession (and vice versa):

```
exp_home = (home.for + away.against) / 2
exp_away = (away.for + home.against) / 2
```

Goals get a home-advantage multiplier, then goals and corners are each modelled
as Poisson processes. The analytical and Monte Carlo engines share the exact
same expected values (`footy.predict.expected_values`), so they agree on common
markets while the simulation adds the harder-to-derive ones.

### When it can't get in

If Cloudflare blocks the run, update the bypass — SeleniumBase maintains it:

```powershell
python -m pip install -U seleniumbase
```

If it still won't get in, run `python app.py ... --show-browser` to watch what's
happening (and solve a verification checkbox if one ever appears).

---

## Ideas to extend

- Weight recent matches more heavily (form decay).
- Split home/away venue form instead of overall.
- Add correlation between goals and corners in the simulation.
- Swap Poisson for Dixon–Coles (corrects low-score bias).
- Cache team-name → ID lookups to skip the search step.
