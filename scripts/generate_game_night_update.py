#!/usr/bin/env python3
"""
Generate a "game night" check-in for whichever fantasy week is currently
in progress: each matchup's live score margin plus the standout starter
performances (best/worst vs their own established average) from games
that have actually been played so far. Meant to run multiple times a
week (see .github/workflows/update-data.yml), right after each night's
NFL games wrap -- not just once the whole week is over.

Writes docs/data/game_night_update.json. Always overwrites with the
latest snapshot for the current week -- it's a live status update, not
a historical log.

Requires ESPN_SWID/ESPN_S2 (does its own fresh ESPN fetch for live box
scores, separate from fetch_espn_data.py's season-total roster data).
ANTHROPIC_API_KEY is optional -- without it, this writes the plain
factual scores with no AI commentary.

Usage:
    ESPN_SWID='{...}' ESPN_S2='...' ANTHROPIC_API_KEY='sk-ant-...' \
        python3 scripts/generate_game_night_update.py --league-id 703243
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import anthropic

import fetch_espn_data as espn
from owner_resolution import resolve_owner_name
from ai_tone import TONE_GUARDRAIL, looks_like_refusal

MODEL = "claude-haiku-4-5"
BENCH_SLOT_IDS = {20, 21}  # bench, IR -- stable/standard ESPN lineup slot ids

SYSTEM_PROMPT = f"""You write short, explicit mid-week check-ins for fantasy \
football matchups that are still in progress, for a private league's \
website. {TONE_GUARDRAIL}

You'll be given one matchup's current score, and the standout starter \
performances so far (a "stud" outperforming their own average, and/or a \
"stinker" underperforming it) for each team. Write 2-3 sentences reacting \
to the state of the matchup right now -- who's getting run over, who \
should be sweating, and call out the standout players by name. This game \
isn't over, so don't declare a final winner, just roast the current state.

Respond with ONLY the update text, nothing else -- no preamble, no labels."""


def find_current_period(raw):
    """Delegates to ESPN's own status fields -- see the note on
    espn.current_matchup_period about why score-based detection is wrong."""
    return espn.current_matchup_period(raw)


def build_ppg_lookup(season):
    """player id -> best-known established points-per-game (prior season
    preferred, since it's not self-inclusive of the week being checked)."""
    lookup = {}
    for team in season.get("teams", []):
        for p in team.get("roster", []):
            prior = (p.get("priorSeason") or {}).get("pointsPerGame")
            current = (p.get("currentSeason") or {}).get("pointsPerGame")
            avg = prior if prior is not None else current
            if avg is not None and p.get("id") is not None:
                lookup[p["id"]] = avg
    return lookup


def starter_performances(roster_box, current_period, season_year, ppg_by_player):
    """[(player_name, current_points, delta_from_average_or_None), ...] for
    starters (not bench/IR) whose game has actually produced real stats."""
    out = []
    for entry in (roster_box or {}).get("entries", []):
        if entry.get("lineupSlotId") in BENCH_SLOT_IDS:
            continue
        player = (entry.get("playerPoolEntry") or {}).get("player") or {}
        block = espn.find_stat_block(player.get("stats"), season_year, source_id=0, split_id=1, period_id=current_period)
        if block is None:
            continue  # hasn't played (or game hasn't started) this period
        points = block.get("appliedTotal") or 0.0
        avg = ppg_by_player.get(player.get("id"))
        delta = (points - avg) if avg is not None else None
        out.append((player.get("fullName"), points, delta))
    return out


def pick_standouts(performances):
    with_delta = [p for p in performances if p[2] is not None]
    if not with_delta:
        return None, None
    return max(with_delta, key=lambda p: p[2]), min(with_delta, key=lambda p: p[2])


def describe_standouts(stud, stinker):
    parts = []
    if stud and stud[2] > 3:
        parts.append(f"stud: {stud[0]} with {stud[1]:.1f} pts (+{stud[2]:.1f} vs their average)")
    if stinker and stinker[2] < -3:
        parts.append(f"stinker: {stinker[0]} with {stinker[1]:.1f} pts ({stinker[2]:.1f} vs their average)")
    return "; ".join(parts) if parts else "nothing notable yet"


def matchup_prompt(home_name, home_score, home_stud, home_stinker, away_name, away_score, away_stud, away_stinker):
    return (
        f"{home_name}: {home_score:.1f} pts so far. {describe_standouts(home_stud, home_stinker)}\n"
        f"{away_name}: {away_score:.1f} pts so far. {describe_standouts(away_stud, away_stinker)}\n"
        f"Current margin: {abs(home_score - away_score):.1f}"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--league-id", required=True, type=int)
    parser.add_argument("--data-dir", default=str(Path(__file__).resolve().parent.parent / "docs" / "data"))
    args = parser.parse_args()

    swid = os.environ.get("ESPN_SWID")
    espn_s2 = os.environ.get("ESPN_S2")
    if not swid or not espn_s2:
        print("ESPN_SWID/ESPN_S2 not set -- skipping game night update.")
        sys.exit(0)

    data_dir = Path(args.data_dir)
    owners = json.loads((data_dir / "owners.json").read_text(encoding="utf-8"))
    meta = json.loads((data_dir / "league_meta.json").read_text(encoding="utf-8"))
    if not meta.get("years"):
        print("No seasons found, skipping game night update.")
        sys.exit(0)

    latest_year = max(meta["years"])
    local_season = json.loads((data_dir / "seasons" / f"season_{latest_year}.json").read_text(encoding="utf-8"))
    ppg_lookup = build_ppg_lookup(local_season)

    session = espn.build_session(swid, espn_s2)
    raw = espn.fetch_season(session, args.league_id, latest_year, views=["mTeam", "mMatchupScore", "mBoxscore"])
    if raw is None:
        print(f"Could not fetch live data for {latest_year}, skipping.")
        sys.exit(0)

    teams_by_id = {t["id"]: t for t in raw.get("teams", [])}
    matchups = raw.get("schedule", [])
    current_period = find_current_period(raw)
    if current_period is None:
        print(f"Could not determine the current week in {latest_year}, skipping game night update.")
        sys.exit(0)

    games = [
        m for m in matchups
        if m["matchupPeriodId"] == current_period
        and (m.get("home") or {}).get("teamId") is not None
        and (m.get("away") or {}).get("teamId") is not None
    ]

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=api_key) if api_key else None
    if not client:
        print("ANTHROPIC_API_KEY not set -- writing factual scores only (no AI text).")

    results = []
    for m in games:
        home, away = m["home"], m["away"]
        home_name = resolve_owner_name(teams_by_id[home["teamId"]], raw, owners)
        away_name = resolve_owner_name(teams_by_id[away["teamId"]], raw, owners)
        home_score, away_score = espn.live_total_points(home), espn.live_total_points(away)

        home_perf = starter_performances(home.get("rosterForCurrentScoringPeriod"), current_period, latest_year, ppg_lookup)
        away_perf = starter_performances(away.get("rosterForCurrentScoringPeriod"), current_period, latest_year, ppg_lookup)
        home_stud, home_stinker = pick_standouts(home_perf)
        away_stud, away_stinker = pick_standouts(away_perf)

        update = f"{home_name} {home_score:.1f} - {away_score:.1f} {away_name} (in progress)."
        if client:
            try:
                response = client.messages.create(
                    model=MODEL, max_tokens=300, system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": matchup_prompt(
                        home_name, home_score, home_stud, home_stinker,
                        away_name, away_score, away_stud, away_stinker,
                    )}],
                )
                text = "".join(b.text for b in response.content if b.type == "text").strip()
                if response.stop_reason == "refusal" or looks_like_refusal(text):
                    print(f"  {home_name} vs {away_name}: model declined, using fallback", file=sys.stderr)
                elif text:
                    update = text
            except anthropic.APIStatusError as e:
                print(f"  {home_name} vs {away_name}: API error ({e.status_code}), using fallback", file=sys.stderr)
            except anthropic.APIConnectionError as e:
                print(f"  {home_name} vs {away_name}: connection error ({e}), using fallback", file=sys.stderr)

        results.append({
            "homeOwner": home_name, "awayOwner": away_name,
            "homeScore": home_score, "awayScore": away_score,
            "update": update,
        })
        print(f"  [{current_period}] {home_name} {home_score:.1f} - {away_score:.1f} {away_name}")

    output = {
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "seasonId": latest_year,
        "matchupPeriodId": current_period,
        "matchups": results,
    }
    (data_dir / "game_night_update.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\nDone. Wrote game night update for {latest_year} week {current_period}.")


if __name__ == "__main__":
    main()
