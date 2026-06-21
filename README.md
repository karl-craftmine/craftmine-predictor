# Craftmine Football Predictor

A transparent, betting-style football match predictor. It pulls each team's
recent match stats, aggregates their performance **for and against** (goals,
corners, shots, possession, …) with **recent matches weighted more heavily**,
models goals as **Poisson processes** with a **Dixon-Coles low-score
correction**, and prints **fair odds** for every market so you can compare
against a bookmaker.

It predicts two ways from the *same* model:

- an **analytical** engine (closed-form 1X2, Over/Under, BTTS, corners), and
- a **Monte Carlo simulation** that adds exact-score distributions, Asian
  handicaps, clean sheets, win-to-nil, per-team corner lines, and a full custom
  **bet builder**.

Both engines apply the same Dixon-Coles correction and recency weighting, so the
web app, the `.exe`, and the command line all price markets the same way.

**Data sources are chosen automatically per team:**

| Team type | Source | Notes |
|-----------|--------|-------|
| **Club** (Arsenal, Real Madrid, …) | **WhoScored** | drives a real headless Chrome (SeleniumBase UC mode); no public API |
| Club (fallback) | **Flashscore** | when WhoScored can't cover a club (e.g. Russian leagues) or no browser is available |
| **National** (Brazil, France, …) | **Flashscore** | free backend feed via `curl_cffi`; **no browser needed**. Obvious typos are auto-corrected ("Spein" → Spain) |
| National (fallback) | **API-Football** | optional; only if Flashscore fails *and* you supply a free key |

> ⚠️ For personal use. WhoScored/Flashscore have no public APIs, so be polite —
> data is cached on disk. The model is a **baseline**, not a crystal ball.
> Gamble responsibly.

---

## Poisson *vs.* Monte Carlo — they're the same model

A natural question: *"is it Poisson or Monte Carlo?"* **Both** — Poisson is the
model; Monte Carlo is just one way to compute it.

- **Poisson** is the assumption: each team's goals (corners, shots, …) follow a
  Poisson distribution with a mean **λ** derived from recent (recency-weighted)
  form.
- **Dixon-Coles** then fixes the well-known flaw in independent Poisson: it
  under-rates draws (0-0, 1-1) and over-rates 1-0 / 0-1. We re-weight those four
  low-score cells (ρ = −0.13) so draws get their fair share.
- The **analytical** engine ([footy/predict.py](footy/predict.py)) plugs λ
  straight into the formula — it builds the home×away score-probability matrix,
  applies the Dixon-Coles weights, and sums cells for 1X2 / O-U / BTTS. Exact,
  but only for markets with a closed form.
- The **Monte Carlo** engine ([footy/simulate.py](footy/simulate.py)) instead
  *draws* tens of thousands of random scorelines from that **same** Dixon-Coles
  score matrix and counts how often each result happens. Approximate (it
  converges as iterations rise), but it can price **any** market — exact scores,
  handicaps, win-to-nil, player props.

Both engines share the same λ values and the same Dixon-Coles correction, so
they agree on common markets — [selftest.py](selftest.py) asserts they match
within ~2%, both with and without the correction. In short: **Poisson is the
dice, Dixon-Coles shaves them toward draws, and Monte Carlo rolls them many
times instead of doing the algebra.**

---

## Two ways to run it

### A) Prebuilt Windows app (no Python needed)

Download **[`dist/FootballPredictor.exe`](dist/FootballPredictor.exe)** and
double-click it. A console window prints a local address (e.g.
`http://127.0.0.1:5000`) — open that in your browser.

- **Windows 64-bit**, needs an **internet** connection.
- Install **Google Chrome** for the richest *club* stats. Without it, clubs fall
  back to Flashscore (covers many leagues); national teams never need a browser.
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
  (anytime)** — clubs get all of these; **national teams** get **goals / to-score**
  props (from Flashscore per-player ratings + goals) but **not** shots / SoT,
  which Flashscore doesn't publish for internationals

The bet builder only offers markets it actually has data for, so the national-team
menu is slightly shorter than the club menu (no per-player shots/SoT).

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
| `--no-dixon-coles` | disable the Dixon-Coles low-score correction |
| `--no-recency` | weight all sampled matches equally (no recency decay) |
| `--recency-half-life N` | recency half-life in days (default 90) |
| `--players` | show each team's top players by average rating (clubs and national teams) |
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
  possession · fouls · offsides · yellow/red cards, **plus per-player ratings +
  goals** from the lineup feed (so national teams get goals / to-score props —
  but no per-player shots/SoT)
- **WhoScored** (clubs): all of the team stats above **plus** first-half goals,
  aerials won, tackles, pass accuracy, and **full per-player shots + ratings**
  (so clubs also get shots/SoT player props)

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
  predict.py     Analytical model (Poisson + Dixon-Coles) → 1X2, O/U goals, BTTS, O/U corners.
  simulate.py    Monte Carlo engine (numpy) → everything above + handicaps, clean
                 sheets, win-to-nil, score distribution, per-team corners
                 (goals drawn from the Dixon-Coles score matrix).
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

Each sampled match also carries a recency weight (recent games count more) that
feeds the averages above. Goals get a home-advantage multiplier, then goals are
modelled as Poisson with the Dixon-Coles low-score correction (corners stay
plain Poisson). The analytical and Monte Carlo engines share the exact same
expected values (`footy.predict.expected_values`) and correction, so they agree
on common markets while the simulation adds the harder-to-derive ones.

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

- Split home/away venue form instead of overall.
- Add correlation between goals and corners in the simulation.
- Fit ρ and the recency half-life per league instead of using fixed defaults.
