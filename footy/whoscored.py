"""Scrape WhoScored match data via SeleniumBase UC mode (clears Cloudflare).

WhoScored has no public API. Each match page assigns a big JSON object to
``require.config.params['args']`` whose ``matchCentreData`` holds full per-team
stats (corners, shots, possession, ...). We read it straight out of the page's
JS context, so there's no brittle HTML parsing. Match-centre data is cached to
disk per match id, so re-runs are instant and we stay polite to the site.
"""

from __future__ import annotations

import datetime
import json
import re
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

BASE = "https://www.whoscored.com"

# Leagues scraped once to seed the team-name autocomplete index.
COMPETITIONS = {
    "Premier League": f"{BASE}/Regions/252/Tournaments/2/England-Premier-League",
    "La Liga": f"{BASE}/Regions/206/Tournaments/4/Spain-LaLiga",
    "Serie A": f"{BASE}/Regions/108/Tournaments/5/Italy-Serie-A",
    "Bundesliga": f"{BASE}/Regions/81/Tournaments/3/Germany-Bundesliga",
    "Ligue 1": f"{BASE}/Regions/74/Tournaments/22/France-Ligue-1",
    "Champions League": f"{BASE}/Regions/250/Tournaments/12/Europe-Champions-League",
}


def _prettify(slug: str) -> str:
    return slug.replace("-", " ").strip().title()

# WhoScored stat name -> our key, for "count" stats (sum the minute-keyed dict).
WS_COUNT_STATS = {
    "corners": "cornersTotal",
    "shots": "shotsTotal",
    "shots_on_target": "shotsOnTarget",
    "fouls": "foulsCommited",      # (WhoScored's spelling)
    "offsides": "offsidesCaught",
    "aerials_won": "aerialsWon",
    "tackles": "tacklesTotal",
}

# Read matchCentreData straight from the page's JS context as clean JSON.
_JS_GET_MCD = """
try {
  if (typeof require !== 'undefined' && require.config && require.config.params
      && require.config.params['args']) {
    var a = require.config.params['args'];
    if (a && a.matchCentreData) return JSON.stringify(a.matchCentreData);
  }
} catch (e) { return 'ERR:' + e; }
return null;
"""


class WhoScoredError(RuntimeError):
    pass


_DEAD_BROWSER_SIGNS = (
    "refused", "maxretryerror", "newconnectionerror", "no such window",
    "target window already closed", "disconnected", "invalid session id",
    "chrome not reachable", "failed to establish a new connection",
)


def _looks_like_dead_browser(exc: BaseException) -> bool:
    """True if the exception chain looks like the browser/driver was closed."""
    seen: set[int] = set()
    e: Optional[BaseException] = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if any(s in str(e).lower() for s in _DEAD_BROWSER_SIGNS):
            return True
        e = e.__cause__ or e.__context__
    return False


def _sum_series(d: Any) -> Optional[float]:
    """Sum a WhoScored {minute: value} stat dict (a match total)."""
    if not isinstance(d, dict) or not d:
        return None
    try:
        return float(sum(float(v) for v in d.values()))
    except (TypeError, ValueError):
        return None


def _final_series(d: Any) -> Optional[float]:
    """Value at the latest minute of a {minute: value} series (e.g. possession)."""
    if not isinstance(d, dict) or not d:
        return None
    try:
        last_key = max(d, key=lambda k: int(k))
        return float(d[last_key])
    except (TypeError, ValueError):
        return None


def _event_name(e: dict[str, Any], field: str = "type") -> str:
    t = e.get(field)
    return t.get("displayName", "") if isinstance(t, dict) else str(t or "")


def _half_goals(mc: dict[str, Any], is_home: bool) -> tuple[Optional[int], Optional[int]]:
    m = re.match(r"\s*(\d+)\s*:\s*(\d+)", str(mc.get("htScore") or ""))
    if not m:
        return None, None
    h, a = int(m.group(1)), int(m.group(2))
    return (h, a) if is_home else (a, h)


def _team_cards(mc: dict[str, Any]) -> dict[Any, dict[str, int]]:
    """{teamId: {'yellow_cards': n, 'red_cards': n}} parsed from card events."""
    out: dict[Any, dict[str, int]] = {}
    for e in mc.get("events", []):
        if _event_name(e) != "Card":
            continue
        name = _event_name(e, "cardType").lower()
        rec = out.setdefault(e.get("teamId"), {"yellow_cards": 0, "red_cards": 0})
        if "second" in name:          # second yellow = a booking AND a sending-off
            rec["yellow_cards"] += 1
            rec["red_cards"] += 1
        elif "red" in name:
            rec["red_cards"] += 1
        elif "yellow" in name:
            rec["yellow_cards"] += 1
    return out


def _player_goals(mc: dict[str, Any]) -> dict[Any, int]:
    """{playerId: goals} from goal events, excluding own goals."""
    counts: dict[Any, int] = {}
    for e in mc.get("events", []):
        if _event_name(e) != "Goal":
            continue
        if any(_event_name(q, "type") == "OwnGoal" for q in e.get("qualifiers", [])):
            continue
        pid = e.get("playerId")
        if pid is not None:
            counts[pid] = counts.get(pid, 0) + 1
    return counts


def _player_stats(side: dict[str, Any], goals_by_pid: dict[Any, int]) -> list[dict[str, Any]]:
    """Per-player [{name, position, rating, shots, sot, goals, started}]."""
    out = []
    for p in side.get("players", []) or []:
        st = p.get("stats") or {}
        rating = _final_series(st.get("ratings"))
        if rating is None:
            continue
        out.append({
            "name": p.get("name"),
            "position": p.get("position"),
            "rating": round(rating, 2),
            "shots": _sum_series(st.get("shotsTotal")) or 0.0,
            "sot": _sum_series(st.get("shotsOnTarget")) or 0.0,
            "goals": float(goals_by_pid.get(p.get("playerId"), 0)),
            "started": bool(p.get("isFirstEleven")),
        })
    return out


def _side_stats(stats: dict[str, Any], goals: Optional[int]) -> dict[str, Optional[float]]:
    # NOTE: possession is filled in by the caller from both teams' pass shares,
    # because WhoScored's per-minute possession series doesn't decode cleanly.
    out: dict[str, Optional[float]] = {"goals": float(goals) if goals is not None else None}
    for key, ws_key in WS_COUNT_STATS.items():
        out[key] = _sum_series(stats.get(ws_key))
    pt = _sum_series(stats.get("passesTotal"))
    pa = _sum_series(stats.get("passesAccurate"))
    out["pass_acc"] = round(pa / pt * 100, 1) if pt else None
    return out


def _parse_score(mc: dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
    raw = mc.get("ftScore") or mc.get("score") or ""
    m = re.match(r"\s*(\d+)\s*:\s*(\d+)", str(raw))
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


class WhoScoredScraper:
    def __init__(
        self,
        cache_dir: str | Path = "cache",
        cache_ttl: float = 6 * 3600,
        headless: bool = False,
        reconnect_time: float = 6.0,
        wait: float = 4.0,
        min_interval: float = 0.8,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl = cache_ttl
        self.headless = headless
        self.reconnect_time = reconnect_time
        self.wait = wait
        self.min_interval = min_interval
        self._last = 0.0
        self.sb = None
        self._cm = None
        self._primed = False  # have we cleared Cloudflare yet this session?

    # -- lifecycle ----------------------------------------------------------

    def __enter__(self) -> "WhoScoredScraper":
        from seleniumbase import SB
        self._cm = SB(uc=True, headless=self.headless, locale="en")
        self.sb = self._cm.__enter__()
        self._primed = False
        return self

    def __exit__(self, *exc) -> None:
        if self._cm is not None:
            try:
                self._cm.__exit__(*exc)
            except Exception:
                pass  # browser may already be dead; don't mask the real error
            self._cm = None
            self.sb = None

    # -- low level ----------------------------------------------------------

    def _is_challenge(self) -> bool:
        try:
            return "just a moment" in (self.sb.get_title() or "").lower()
        except Exception:
            return False

    def _open(self, url: str, ready: str | None = None) -> None:
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            self._navigate(url)
        except WhoScoredError:
            raise
        except Exception as exc:
            if _looks_like_dead_browser(exc):
                raise WhoScoredError(
                    "The browser was closed or crashed mid-run. Don't close the "
                    "Chrome window while the tool is working — just let it finish. "
                    "Re-run the same command to resume (fetched matches are cached)."
                ) from exc
            raise
        if ready:
            self._wait_ready(ready, self.wait)   # proceed the moment data appears
        else:
            self.sb.sleep(self.wait)
        self._last = time.time()

    def _wait_ready(self, js: str, timeout: float) -> None:
        """Poll a JS truthiness condition; return as soon as it's true."""
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.sb.execute_script(f"return !!({js});"):
                    return
            except Exception:
                pass
            self.sb.sleep(0.25)

    def _navigate(self, url: str) -> None:
        if not self._primed:
            # First page: do the full reconnect dance to clear Cloudflare.
            self.sb.uc_open_with_reconnect(url, reconnect_time=self.reconnect_time)
            try:
                self.sb.uc_gui_click_captcha()
            except Exception:
                pass
            self._primed = True
        else:
            # Cloudflare already cleared this session: a normal nav is enough.
            # Only redo the heavy clear if we actually get re-challenged.
            self.sb.uc_open(url)
            if self._is_challenge():
                self.sb.uc_open_with_reconnect(url, reconnect_time=self.reconnect_time)
                try:
                    self.sb.uc_gui_click_captcha()
                except Exception:
                    pass

    def _collect_links(self, pattern: str) -> list[str]:
        hrefs = self.sb.execute_script(
            "return Array.from(document.querySelectorAll('a'))"
            ".map(a=>a.getAttribute('href')).filter(Boolean);"
        ) or []
        out, seen = [], set()
        for h in hrefs:
            if re.search(pattern, h, re.I) and h not in seen:
                seen.add(h)
                out.append(h if h.startswith("http") else BASE + h)
        return out

    # -- endpoints ----------------------------------------------------------

    def _team_cache(self) -> dict[str, Any]:
        f = self.cache_dir / "teams.json"
        if f.exists():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        return {}

    def search_team(self, name: str) -> dict[str, Any]:
        """Return {'id', 'name', 'url'} for the best-matching team."""
        if name.isdigit():
            return {"id": int(name), "name": name,
                    "url": f"{BASE}/teams/{name}/show/"}

        key = name.strip().lower()
        if self.cache_ttl > 0:
            cached = self._team_cache()
            if key in cached:
                return cached[key]

        self._open(f"{BASE}/search/?t={quote(name)}",
                   ready="document.querySelectorAll('a[href*=\"/teams/\"]').length > 0")
        links = self.sb.execute_script(
            "return Array.from(document.querySelectorAll('a'))"
            ".filter(a => /\\/teams\\/\\d+\\/show\\//i.test(a.getAttribute('href')||''))"
            ".map(a => [a.getAttribute('href'), a.textContent.trim()]);"
        ) or []
        if not links:
            raise WhoScoredError(f"No team found for '{name}'")
        target = name.lower()
        best = next((l for l in links if l[1] and target in l[1].lower()), links[0])
        href, label = best
        tid = re.search(r"/teams/(\d+)/", href).group(1)
        result = {"id": int(tid), "name": label or name,
                  "url": href if href.startswith("http") else BASE + href}

        if self.cache_ttl > 0:
            cache = self._team_cache()
            cache[key] = result
            (self.cache_dir / "teams.json").write_text(
                json.dumps(cache), encoding="utf-8")
        return result

    def _match_centre(self, match_id: int, match_url: str) -> Optional[dict[str, Any]]:
        cache_file = self.cache_dir / f"match_{match_id}.json"
        if self.cache_ttl > 0 and cache_file.exists():
            if time.time() - cache_file.stat().st_mtime < self.cache_ttl:
                try:
                    return json.loads(cache_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    pass
        # Poll until the page's data object exists (not the full page load), then
        # read it. Many matches (esp. national-team qualifiers/friendlies) only
        # carry basic data, no matchCentreData — for those we bail immediately
        # instead of waiting the full timeout.
        self._open(match_url, ready="require && require.config && "
                   "require.config.params && require.config.params['args']")
        raw = self.sb.execute_script(_JS_GET_MCD)
        if not raw or str(raw).startswith("ERR"):
            return None
        try:
            mc = json.loads(raw)
        except json.JSONDecodeError:
            return None
        cache_file.write_text(json.dumps(mc), encoding="utf-8")
        return mc

    def team_recent_matches(
        self, team: dict[str, Any], num_matches: int = 10, verbose: bool = False
    ) -> list[dict[str, Any]]:
        """Sample a team's most recent finished matches into for/against dicts."""
        team_id = team["id"]
        fixtures_url = team["url"].replace("/show/", "/fixtures/")
        self._open(fixtures_url,
                   ready="document.querySelectorAll('a[href*=\"/matches/\"]').length > 0")
        # Read each fixture row's score, so we only open pages for matches that
        # were actually PLAYED. Loading upcoming fixtures (common for tournament
        # / national teams) just to discover they're unplayed is the big time sink.
        rows = self.sb.execute_script(r"""
          const out=[]; const seen=new Set();
          document.querySelectorAll('a').forEach(a=>{
            const href=a.getAttribute('href')||'';
            const m=href.match(/\/matches\/(\d+)\/(live|show)\//i); if(!m||seen.has(m[1]))return; seen.add(m[1]);
            let n=a,h=0,row=a;
            while(n&&h<5){n=n.parentElement;h++; if(n&&n.innerText&&n.innerText.length>6){row=n;break;}}
            out.push({id:m[1], href, txt:(row?row.innerText:'').replace(/\n/g,' ')});
          });
          return out;""") or []
        played: list[tuple[int, str]] = []
        for r in rows:
            if not re.search(r"\d\s+:\s+\d", r.get("txt", "")):
                continue  # no final score shown → not played yet, skip
            mid = int(r["id"])
            url = re.sub(r"/show/", "/live/", r["href"])
            played.append((mid, url if url.startswith("http") else BASE + url))

        results: list[dict[str, Any]] = []
        # Most recent first; small buffer in case a score read is ambiguous.
        for mid, url in list(reversed(played))[: num_matches + 4]:
            if len(results) >= num_matches:
                break
            try:
                mc = self._match_centre(mid, url)
            except WhoScoredError:
                raise  # dead browser — can't continue
            except Exception:
                continue  # transient issue on one page; skip it
            if not mc or mc.get("statusCode") not in (6, "6"):
                continue  # not a finished match
            home, away = mc.get("home", {}), mc.get("away", {})
            is_home = home.get("teamId") == team_id
            if not is_home and away.get("teamId") != team_id:
                # fall back to name match if teamId missing
                if team["name"].lower() not in (
                    str(home.get("name", "")).lower(), str(away.get("name", "")).lower()
                ):
                    continue
                is_home = team["name"].lower() == str(home.get("name", "")).lower()

            hg, ag = _parse_score(mc)
            mine, theirs = (home, away) if is_home else (away, home)
            my_goals, opp_goals = (hg, ag) if is_home else (ag, hg)
            my_stats, opp_stats = mine.get("stats", {}), theirs.get("stats", {})
            for_stats = _side_stats(my_stats, my_goals)
            against_stats = _side_stats(opp_stats, opp_goals)

            # Possession from share of total passes (≈ real possession, sums ~100).
            mp = _sum_series(my_stats.get("passesTotal"))
            tp = _sum_series(opp_stats.get("passesTotal"))
            if mp and tp:
                for_stats["possession"] = round(mp / (mp + tp) * 100, 1)
                against_stats["possession"] = round(tp / (mp + tp) * 100, 1)

            # First-half goals (from the half-time score).
            fh_for, fh_against = _half_goals(mc, is_home)
            for_stats["half_goals"] = fh_for
            against_stats["half_goals"] = fh_against

            # Yellow/red cards (from card events), keyed by team id.
            cards = _team_cards(mc)
            my_cards = cards.get(mine.get("teamId"), {"yellow_cards": 0, "red_cards": 0})
            opp_cards = cards.get(theirs.get("teamId"), {"yellow_cards": 0, "red_cards": 0})
            for_stats.update(my_cards)
            against_stats.update(opp_cards)

            goals_by_pid = _player_goals(mc)
            match = {
                "match_id": mid,
                "date": str(mc.get("startDate", ""))[:10],
                "venue": "H" if is_home else "A",
                "opponent": theirs.get("name"),
                "for": for_stats,
                "against": against_stats,
                "players": _player_stats(mine, goals_by_pid),
            }
            results.append(match)
            if verbose:
                print(f"  [{len(results):2d}] {match['venue']} vs "
                      f"{match['opponent']:<22} {my_goals}-{opp_goals}  "
                      f"corners {match['for']['corners']}-{match['against']['corners']}")
        return results

    # -- discovery (autocomplete + upcoming fixtures) -----------------------

    def list_competition_teams(self, url: str) -> list[dict[str, Any]]:
        """All teams (id, name, url) linked on a competition page."""
        self._open(url)
        links = self.sb.execute_script(
            "return Array.from(document.querySelectorAll('a'))"
            ".filter(a => /\\/teams\\/\\d+\\/show\\//i.test(a.getAttribute('href')||''))"
            ".map(a => [a.getAttribute('href'), a.textContent.trim()]);") or []
        out: dict[int, dict[str, Any]] = {}
        for href, name in links:
            m = re.search(r"/teams/(\d+)/", href)
            if m and name and len(name) > 1:
                tid = int(m.group(1))
                out[tid] = {"id": tid, "name": name,
                            "url": href if href.startswith("http") else BASE + href}
        return list(out.values())

    def build_team_index(self, comps: dict[str, str] | None = None) -> list[dict[str, Any]]:
        """Scrape several leagues into a de-duplicated team list for autocomplete."""
        index: dict[str, dict[str, Any]] = {}
        for url in (comps or COMPETITIONS).values():
            try:
                for t in self.list_competition_teams(url):
                    index[t["name"]] = t
            except Exception:
                continue
        return sorted(index.values(), key=lambda t: t["name"])

    def livescores_fixtures(self) -> list[dict[str, Any]]:
        """All of today's fixtures across competitions (home, away, competition).

        Each LiveScores row carries two /teams/ links, so names are exact.
        """
        self._open(f"{BASE}/livescores")
        raw = self.sb.execute_script(r"""
          const out=[]; const seen=new Set();
          document.querySelectorAll('a').forEach(a=>{
            const href=a.getAttribute('href')||'';
            const m=href.match(/\/matches\/(\d+)\//i); if(!m||seen.has(m[1]))return; seen.add(m[1]);
            let row=a,n=a,h=0;
            while(n&&h<6){n=n.parentElement;h++;
              if(n&&n.querySelectorAll('a[href*="/teams/"]').length>=2){row=n;break;}}
            const tl=[...row.querySelectorAll('a[href*="/teams/"]')].map(x=>x.textContent.trim()).filter(Boolean);
            if(tl.length<2) return;
            out.push({id:m[1], slug:href.split('/').pop(), home:tl[0], away:tl[1],
                      txt:(row.innerText||'').replace(/\n/g,' ').slice(0,60)});
          });
          return out;""") or []
        out = []
        for r in raw:
            comp = re.sub(r"-\d{4}.*$", "", r["slug"])
            # A score shows as "1 : 0" (spaces); a kickoff time as "20:00" (none).
            started = bool(re.search(r"\d\s+:\s+\d", r["txt"]))
            out.append({
                "match_id": r["id"], "competition": comp,
                "competition_name": _prettify(comp),
                "home": r["home"], "away": r["away"], "started": started,
            })
        return out

    def team_upcoming(self, team: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
        """A team's not-yet-played fixtures as [{date, home, away}]."""
        self._open(team["url"].replace("/show/", "/fixtures/"))
        rows = self.sb.execute_script(r"""
          const out=[]; const seen=new Set();
          document.querySelectorAll('a').forEach(a=>{
            const href=a.getAttribute('href')||'';
            const m=href.match(/\/matches\/(\d+)\//i); if(!m||seen.has(m[1]))return; seen.add(m[1]);
            let n=a,h=0,row=a;
            while(n&&h<5){n=n.parentElement;h++; if(n&&n.innerText&&n.innerText.length>6){row=n;break;}}
            out.push({slug:href.split('/').pop(), txt:(row?row.innerText:'').replace(/\n/g,' ')});
          });
          return out;""") or []
        today = datetime.date.today()
        team_slug = re.sub(r"[^a-z0-9]+", "-", team["name"].lower()).strip("-")
        fixtures = []
        for r in rows:
            d = re.search(r"(\d{2})-(\d{2})-(\d{2})", r["txt"])
            if not d:
                continue
            try:
                dt = datetime.date(2000 + int(d.group(3)), int(d.group(2)), int(d.group(1)))
            except ValueError:
                continue
            if dt < today:
                continue
            seg = re.sub(r"^.*?\d{4}-\d{4}-", "", r["slug"])
            if seg.startswith(team_slug + "-"):
                home, away = team["name"], _prettify(seg[len(team_slug) + 1:])
            elif seg.endswith("-" + team_slug):
                home, away = _prettify(seg[:-len(team_slug) - 1]), team["name"]
            else:
                continue
            fixtures.append({"date": dt.isoformat(), "home": home, "away": away})
            if len(fixtures) >= limit:
                break
        return fixtures
