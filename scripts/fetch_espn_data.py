#!/usr/bin/env python3
"""
Fetch ESPN Fantasy Football league history and write it out as JSON files
that the static site (in /docs) reads directly.

Auth: ESPN's fantasy API requires two cookies for private leagues, passed
as environment variables (set as GitHub Actions secrets in production):
    ESPN_SWID   -> the SWID cookie value, including the curly braces
    ESPN_S2     -> the espn_s2 cookie value

Usage:
    ESPN_SWID='{...}' ESPN_S2='...' python3 scripts/fetch_espn_data.py --league-id 703243

Notes on ESPN's API (undocumented, reverse-engineered by the fantasy
football community -- this mirrors what the popular `espn-api` package
does under the hood, without requiring that dependency):

  * Seasons 2018+ live at:
      https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{year}/segments/0/leagues/{league_id}
  * Seasons before 2018 live at the "league history" endpoint:
      https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/leagueHistory/{league_id}?seasonId={year}
    which returns a JSON *array* (usually with one element) instead of
    a single object.
  * Both need the same SWID / espn_s2 cookies for a private league.
  * `view` query params control which slices of data come back. We pull:
      mTeam       -> team names, logos, owners
      mRoster     -> rosters (not heavily used yet, but cheap to keep)
      mSettings   -> league settings (name, scoring, playoff format)
      mMatchupScore / mMatchup -> weekly matchup results
      mStandings  -> final standings / records

This script auto-discovers how far back the league's history goes by
walking backwards from the current season until a season lookup fails.
"""
import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path

import requests

MODERN_BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{year}/segments/0/leagues/{league_id}"
LEGACY_BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/leagueHistory/{league_id}"
MODERN_CUTOFF_YEAR = 2018  # first season served by the "seasons/{year}" shape
EARLIEST_PLAUSIBLE_YEAR = 2000  # ESPN fantasy football predates this; safety floor

VIEWS = ["mTeam", "mRoster", "mSettings", "mMatchupScore", "mStandings"]


def fetch_season(session, league_id, year):
    """Return the raw league JSON for one season, or None if it doesn't exist."""
    params = [("view", v) for v in VIEWS]
    if year >= MODERN_CUTOFF_YEAR:
        url = MODERN_BASE.format(year=year, league_id=league_id)
    else:
        url = LEGACY_BASE.format(league_id=league_id)
        params.append(("seasonId", year))

    resp = session.get(url, params=params, timeout=20)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()

    if year < MODERN_CUTOFF_YEAR:
        # legacy endpoint returns a list; take the (usually only) matching entry
        if isinstance(data, list):
            data = next((d for d in data if d.get("seasonId") == year), data[0] if data else None)
    return data


def summarize_season(raw):
    """Pull out the pieces the site actually needs, in a stable shape."""
    if raw is None:
        return None

    teams = []
    for t in raw.get("teams", []):
        teams.append({
            "id": t.get("id"),
            "name": t.get("name"),
            "location": t.get("location"),
            "nickname": t.get("nickname"),
            "abbrev": t.get("abbrev"),
            "owners": t.get("owners", []),  # list of member SWIDs
            "record": t.get("record", {}),
            "playoffSeed": t.get("playoffSeed"),
            "points_for": (t.get("record", {}).get("overall", {}) or {}).get("pointsFor"),
            "points_against": (t.get("record", {}).get("overall", {}) or {}).get("pointsAgainst"),
            "wins": (t.get("record", {}).get("overall", {}) or {}).get("wins"),
            "losses": (t.get("record", {}).get("overall", {}) or {}).get("losses"),
            "ties": (t.get("record", {}).get("overall", {}) or {}).get("ties"),
        })

    members = []
    for m in raw.get("members", []):
        members.append({
            "id": m.get("id"),
            "displayName": m.get("displayName"),
            "firstName": m.get("firstName"),
            "lastName": m.get("lastName"),
        })

    matchups = []
    for game in raw.get("schedule", []):
        matchups.append({
            "matchupPeriodId": game.get("matchupPeriodId"),
            "playoffTierType": game.get("playoffTierType"),
            "home": {
                "teamId": (game.get("home") or {}).get("teamId"),
                "totalPoints": (game.get("home") or {}).get("totalPoints"),
            },
            "away": {
                "teamId": (game.get("away") or {}).get("teamId"),
                "totalPoints": (game.get("away") or {}).get("totalPoints"),
            },
            "winner": game.get("winner"),
        })

    settings = raw.get("settings", {})

    return {
        "seasonId": raw.get("seasonId"),
        "leagueName": settings.get("name"),
        "teams": teams,
        "members": members,
        "matchups": matchups,
        "champion": find_champion(matchups),
        "status": raw.get("status", {}),
    }


def find_champion(matchups):
    """The championship game is the last WINNERS_BRACKET matchup by matchupPeriodId.
    Returns the winning team's id, or None if the bracket hasn't finished yet."""
    finals_round = [m for m in matchups if m.get("playoffTierType") == "WINNERS_BRACKET"]
    if not finals_round:
        return None
    max_period = max(m["matchupPeriodId"] for m in finals_round)
    championship_games = [m for m in finals_round if m["matchupPeriodId"] == max_period]
    for game in championship_games:
        if game["winner"] == "HOME":
            return game["home"]["teamId"]
        if game["winner"] == "AWAY":
            return game["away"]["teamId"]
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--league-id", required=True, type=int)
    parser.add_argument("--out-dir", default=str(Path(__file__).resolve().parent.parent / "docs" / "data"))
    parser.add_argument("--start-year", type=int, default=datetime.date.today().year)
    args = parser.parse_args()

    swid = os.environ.get("ESPN_SWID")
    espn_s2 = os.environ.get("ESPN_S2")
    if not swid or not espn_s2:
        print("ERROR: set ESPN_SWID and ESPN_S2 environment variables first.", file=sys.stderr)
        sys.exit(1)

    session = requests.Session()
    session.cookies.set("SWID", swid, domain=".espn.com")
    session.cookies.set("espn_s2", espn_s2, domain=".espn.com")
    session.headers.update({"User-Agent": "Mozilla/5.0 (fantasy-history-fetcher)"})

    out_dir = Path(args.out_dir)
    seasons_dir = out_dir / "seasons"
    seasons_dir.mkdir(parents=True, exist_ok=True)

    found_years = []
    consecutive_misses = 0
    year = args.start_year

    while year >= EARLIEST_PLAUSIBLE_YEAR and consecutive_misses < 2:
        try:
            raw = fetch_season(session, args.league_id, year)
        except requests.HTTPError as e:
            print(f"  {year}: HTTP error {e} -- stopping here", file=sys.stderr)
            break

        if raw is None:
            print(f"  {year}: no data (league likely didn't exist yet) -- stopping")
            consecutive_misses += 1
            year -= 1
            continue

        consecutive_misses = 0
        summary = summarize_season(raw)
        season_path = seasons_dir / f"season_{year}.json"
        season_path.write_text(json.dumps(summary, indent=2))
        print(f"  {year}: wrote {season_path} ({len(summary['teams'])} teams, {len(summary['matchups'])} matchups)")
        found_years.append(year)

        year -= 1
        time.sleep(0.5)  # be polite to ESPN's API

    found_years.sort()
    meta = {
        "leagueId": args.league_id,
        "years": found_years,
        "generatedAt": datetime.datetime.utcnow().isoformat() + "Z",
    }
    (out_dir / "league_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"\nDone. Seasons found: {found_years}")


if __name__ == "__main__":
    main()
