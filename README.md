# Craftmine Football Predictor

A transparent, betting-style football match predictor. It pulls each team's
recent match stats, aggregates their performance **for and against** (goals,
corners, shots, possession, …), models the match as a set of **Poisson
processes**, and prints **fair odds** for every market so you can compare
against a bookmaker.

It predicts two ways from the *same* model:

- an **analytical Poisson** model (closed-form 1X2, Over/Under, BTTS, corners), and
- a **Monte Carlo simulation** that adds exact-score distributions, Asian
  handicaps, clean sheets, win-to-nil, per-team corner lines, and a full custom
  **bet builder**.

**Data sources are chosen automatically per team:**

| Team type | Source | Notes |
|-----------|--------|-------|
| **Club** (Arsenal, Real Madrid, …) | **WhoScored** | drives a real headless Chrome (SeleniumBase UC mode); no public API |
| **National** (Brazil, France, …) | **Flashscore** | free backend feed via `curl_cffi`; **no browser needed** |
| National (fallback) | **API-Football** | optional; only if Flashscore fails *and* you supply a free key |

> ⚠️ For personal use. WhoScored/Flashscore have no public APIs, so be polite —
> data is cached on disk. The model is a **baseline**, not a crystal ball.
> Gamble responsibly.

---

## Poisson *vs.* Monte Carlo — they're the same model

A natural question: *"is it Poisson or Monte Carlo?"* **Both** — Poisson is the
model; Monte Carlo is just one way to compute it.

- **Poisson** is the assumption: each team's goals (corners, shots, …) follow a
  Poisson distribution with a mean **λ** derived from recent form.
- The **analytical** engine ([footy/predict.py](footy/predict.py)) plugs λ
  straight into the Poisson formula — it builds the home×away score-probability
  matrix and sums cells for 1X2 / O-U / BTTS. Exact, but only for markets with a
  closed form.
- The **Monte Carlo** engine ([footy/simulate.py](footy/simulate.py)) instead
  *draws* tens of thousands of random outcomes from those **same** Poisson
  distributions and counts how often each result happens. Approximate (it
  converges as iterations rise), but it can price **any** market — exact scores,
  handicaps, win-to-nil, player props.

Both engines share the exact same λ values
(`footy.predict.expected_values`), so they agree on common markets —
[selftest.py](selftest.py) even asserts they match within ~2%. In short:
**Poisson is the dice; Monte Carlo rolls them many times instead of doing the
algebra.**

---

## Two ways to run it

### A) Prebuilt Windows app (no Python needed)

Download **[`dist/FootballPredictor.exe`](dist/FootballPredictor.exe)** and
double-click it. A console window prints a local address (e.g.
`http://127.0.0.1:5000`) — open that in your browser.

- **Windows 64-bit**, needs an **internet** connection.
- Install **Google Chrome** for *club*-team predictions (national teams work
  without it).
- First launch is slow (it unpacks itself). Windows SmartScreen may warn because
  the app is unsigned → **More info → Run anyway**.

### B) From source (Python)

```powershell
git clone https://github.com/karl-craftmine/craftmine-predictor.git
cd craftmine-predictor
python -m pip install -r requirements.txt
python selftest.py        # offline sanity check (no network/browser)
python server.py          # then open the address it prints
```

Needs **Python 3.10+** and (for club teams) **Google Chrome**.

---

## Web app + bet builder (recommended)

```powershell
python server.py     # or just run the .exe
```

Open the address it prints — **http://127.0.0.1:5000** (or the next free port
like `:5001`). Club scraping runs **hidden** (headless Chrome — nothing pops up).

1. Start typing a team — an **autocomplete** dropdown suggests matches. Or use
   the **📅 Upcoming Fixtures** panel to pick a real fixture.
2. **Load match** (national teams are quick via Flashscore; a club's first
   lookup is ~1–2 min via WhoScored, then cached).
3. **Build your bet slip**: pick a market from the dropdown — it expands to the
   right fields (team / line / over-under / player) — and click **+ Add**. Stack
   as many as you like.
4. **Simulate** → one Monte Carlo run evaluates your whole slip and prints each
   bet's probability and fair odds, plus form and player tables for reference.

Leave the slip empty and **Simulate** just gives the standard markets (1X2,
Over/Under 2.5, BTTS).

**Markets you can build:**

- Match result (1X2), both teams to score, team to score
- **Result + BTTS**, **win to nil**, **result + a team's goal count**
- Over/Under (total or per-team) for **goals, corners, shots, shots on target,
  fouls, offsides, yellow/red cards** (clubs add first-half goals, tackles, aerials)
- **Player** over/under (shots / shots on target / goals) and **player to score
  (anytime)** — *club teams only; Flashscore national-team data has no player props*

The bet builder only offers markets it actually has data for, so the national-team
menu is slightly shorter than the club menu.

### Following & upcoming fixtures

The **📅 Upcoming Fixtures** panel shows a calendar for the teams and
competitions you follow. Click any fixture to drop both teams into the predictor.

Fixtures come from **TheSportsDB** (a free fixtures API — no signup), so the
calendar is **instant** and shows real dates and kickoff times for the next 7
days. (Match *simulations* still use Flashscore/WhoScored — that's where the
detailed stats live.)

- **Follow** → add teams and competitions, both with search autocomplete. Your
  follows are saved in `cache/follows.json`.
- **↻ Refresh** rebuilds the fixtures into `cache/fixtures.json`, which the page
  reads instantly.

The page shows cached fixtures instantly, then refreshes in the background if the
cache is more than ~6 hours old. You can also refresh by hand anytime:
`python refresh_fixtures.py`.

---

## Command line

Home team first. The browser runs hidden by default.

```powershell
python app.py "Arsenal" "Chelsea"                 # analytical Poisson model
python app.py "Arsenal" "Chelsea" --sim           # Monte Carlo (more markets)
python app.py "Brazil" "Argentina" --sim          # national teams (Flashscore)
python app.py "Liverpool" "Everton" -m 12 --sim --players
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
| `--players` | show each team's top players by average rating (club teams) |
| `--show-browser` | show the Chrome window (default: hidden/headless) |
| `--no-cache` | ignore cached match data |
| `-v, --verbose` | print each sampled match as it's read |

Match data is cached under `cache/` (6 h for feeds, longer for final match stats),
so re-runs are fast and polite.

---

## Optional: API-Football fallback key

National teams use **Flashscore** (free, no key). If you also want the
**API-Football** fallback (used only if Flashscore can't find a national team),
get a free key at <https://www.api-football.com/> and either:

- set the `APIFOOTBALL_KEY` environment variable, **or**
- drop the key into `cache/apifootball_key.txt` (next to the `.exe`, or in the
  repo's `cache/` when running from source).

This file is **git-ignored** and is **not** bundled into the published `.exe`, so
your key stays private.

---

## What it pulls per match (for **and** against)

- **Flashscore** (national): goals · corners · shots · shots on target ·
  possession · fouls · offsides · yellow/red cards
- **WhoScored** (clubs): all of the above **plus** first-half goals, aerials won,
  tackles, pass accuracy, and **player ratings/shots** (so clubs get player props)

Because each match page/feed carries *both* teams' stats, "for" and "against"
come from the same fetch — no cross-referencing needed.

---

## How it works

```
footy/
  whoscored.py   UC-mode Chrome scraper for CLUB matches → matchCentreData,
                 parsed into per-match for/against stats (+ player ratings). Cached.
  flashscore.py  Flashscore backend-feed reader for NATIONAL teams (curl_cffi,
                 no browser): search → recent results → per-match statistics. Cached.
  apifootball.py API-Football client — optional national-team fallback (needs key).
  form.py        TeamForm: per-game for/against averages over the sample.
  predict.py     Analytical Poisson model → 1X2, O/U goals, BTTS, O/U corners.
  simulate.py    Monte Carlo engine (numpy) → everything above + handicaps,
                 clean sheets, win-to-nil, score distribution, per-team corners.
  fixtures.py    follow list + upcoming-fixtures aggregation
  sportsdb.py    TheSportsDB client (fast fixtures API for the calendar)
  paths.py       resolves cache/resource dirs (works from source AND a frozen exe)
app.py            command-line interface
server.py         local web app (Flask) — search, simulate, follow, fixtures
index.html        the web app's single page
refresh_fixtures.py  rescrape followed fixtures
selftest.py       offline check (confirms MC agrees with the analytical model)
predictor.spec    PyInstaller build recipe for the Windows .exe
```

**The blend.** For each metric, the home expectation blends the home team's
output with the away team's concession (and vice versa):

```
exp_home = (home.for + away.against) / 2
exp_away = (away.for + home.against) / 2
```

Goals get a home-advantage multiplier, then goals and corners are each modelled
as Poisson processes. The analytical and Monte Carlo engines share the exact
same expected values (`footy.predict.expected_values`), so they agree on common
markets while the simulation adds the harder-to-derive ones.

---

## Building the .exe yourself

```powershell
python -m pip install pyinstaller
python -m PyInstaller predictor.spec --noconfirm --clean
```

The result is `dist/FootballPredictor.exe`. The build does **not** embed the
API-Football key (see above), and cache is written next to the `.exe` at runtime.

---

## Troubleshooting

- **Cloudflare blocks a club lookup** → update the bypass (`python -m pip install
  -U seleniumbase`), or run `python app.py ... --show-browser` to watch and clear
  a checkbox if one appears.
- **Flashscore feeds change** → their endpoint/field codes occasionally rotate;
  the format details are documented in [footy/flashscore.py](footy/flashscore.py).

## Ideas to extend

- Weight recent matches more heavily (form decay).
- Split home/away venue form instead of overall.
- Add correlation between goals and corners in the simulation.
- Swap Poisson for Dixon–Coles (corrects low-score bias).
