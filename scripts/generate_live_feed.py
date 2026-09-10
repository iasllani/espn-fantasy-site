#!/usr/bin/env python3
"""
Generate a feed of individual notable player performances -- across every
owner's roster, not grouped by matchup -- for whichever week is currently
in progress. Three kinds of events:

  - "over"    a starter beat their own week's projection by a lot
  - "under"   a starter missed their own week's projection by a lot
  - "benched" a player put up a big score while sitting on the bench --
              roast the owner for not starting them

Meant to run several times a week, shortly after each night's real games
wrap (see .github/workflows/live-feed.yml) -- separate from and lighter
than the main fetch_espn_data.py pipeline, since this only needs fresh
per-player box scores for the current week, not the full season history.

Writes docs/data/live_feed.json. Always overwrites with the latest
snapshot for the current week.

Requires ESPN_SWID/ESPN_S2. ANTHROPIC_API_KEY is optional -- without it,
this writes the plain factual stat lines with no AI commentary.

Usage:
    ESPN_SWID='{...}' ESPN_S2='...' ANTHROPIC_API_KEY='sk-ant-...' \
        python3 scripts/generate_live_feed.py --league-id 703243
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
BENCH_SLOT_ID = 20   # stable/standard ESPN lineup slot id
IR_SLOT_ID = 21
OVERPERFORM_THRESHOLD = 8.0
UNDERPERFORM_THRESHOLD = -8.0
BENCH_NOTABLE_THRESHOLD = 15.0
MAX_EVENTS = 10

SYSTEM_PROMPT = f"""You write one short, explicit, cursing reaction to a \
single fantasy football player's performance this week, for a private \
league's "Live" feed. {TONE_GUARDRAIL}

You'll be given the player's name, position, real NFL team, the fantasy \
owner who rosters them, their actual points this week, their projection, \
and whether they started or were benched. Write exactly one or two \
sentences reacting to it -- if they crushed their projection, hype it up \
while ribbing the owner for maybe not trusting them enough; if they \
bombed, roast the owner for starting them; if they were benched while \
scoring big, roast the owner hard for the bench decision specifically.

Respond with ONLY the reaction text, nothing else -- no preamble, no labels."""


def find_current_period(matchups):
    active_periods = {
        m["matchupPeriodId"] for m in matchups
        if (m.get("home") or {}).get("totalPoints") or (m.get("away") or {}).get("totalPoints")
    }
    return max(active_periods) if active_periods else None


def collect_events(raw, current_period, owners):
    teams_by_id = {t["id"]: t for t in raw.get("teams", [])}
    season_year = raw.get("seasonId")
    events = []

    for m in raw.get("schedule", []):
        if m["matchupPeriodId"] != current_period:
            continue
        for side in ("home", "away"):
            team_box = m.get(side) or {}
            team_id = team_box.get("teamId")
            if team_id is None or team_id not in teams_by_id:
                continue
            owner = resolve_owner_name(teams_by_id[team_id], raw, owners)

            for entry in (team_box.get("rosterForCurrentScoringPeriod") or {}).get("entries", []):
                slot = entry.get("lineupSlotId")
                if slot == IR_SLOT_ID:
                    continue
                player = (entry.get("playerPoolEntry") or {}).get("player") or {}
                stats = player.get("stats")

                actual_block = espn.find_stat_block(stats, season_year, source_id=0, split_id=1, period_id=current_period)
                if actual_block is None:
                    continue  # hasn't played yet this period
                actual = actual_block.get("appliedTotal") or 0.0

                projected_block = espn.find_stat_block(stats, season_year, source_id=1, split_id=1, period_id=current_period)
                projected = projected_block.get("appliedTotal") if projected_block else None
                delta = (actual - projected) if projected is not None else None

                name = player.get("fullName")
                position = espn.POSITION_MAP.get(player.get("defaultPositionId"), "?")

                if slot == BENCH_SLOT_ID:
                    if actual >= BENCH_NOTABLE_THRESHOLD:
                        events.append({
                            "type": "benched", "owner": owner, "player": name, "position": position,
                            "actual": actual, "projected": projected, "magnitude": actual,
                        })
                elif delta is not None:
                    if delta >= OVERPERFORM_THRESHOLD:
                        events.append({
                            "type": "over", "owner": owner, "player": name, "position": position,
                            "actual": actual, "projected": projected, "magnitude": delta,
                        })
                    elif delta <= UNDERPERFORM_THRESHOLD:
                        events.append({
                            "type": "under", "owner": owner, "player": name, "position": position,
                            "actual": actual, "projected": projected, "magnitude": abs(delta),
                        })

    events.sort(key=lambda e: e["magnitude"], reverse=True)
    return events[:MAX_EVENTS]


def fallback_line(event):
    if event["type"] == "benched":
        return f"{event['owner']} benched {event['player']}, who scored {event['actual']:.1f} anyway."
    proj = event.get("projected")
    proj_str = f"{proj:.1f}" if proj is not None else "?"
    verb = "crushed" if event["type"] == "over" else "bombed"
    return f"{event['player']} {verb} his projection: {event['actual']:.1f} actual vs {proj_str} projected, playing for {event['owner']}."


def event_prompt(event):
    proj = event.get("projected")
    proj_str = f"{proj:.1f}" if proj is not None else "unknown"
    status = "benched (did not start)" if event["type"] == "benched" else "started"
    return (
        f"Player: {event['player']} ({event['position']})\n"
        f"Owner: {event['owner']}\n"
        f"Status: {status}\n"
        f"Actual points this week: {event['actual']:.1f}\n"
        f"Projected points: {proj_str}"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--league-id", required=True, type=int)
    parser.add_argument("--data-dir", default=str(Path(__file__).resolve().parent.parent / "docs" / "data"))
    args = parser.parse_args()

    swid = os.environ.get("ESPN_SWID")
    espn_s2 = os.environ.get("ESPN_S2")
    if not swid or not espn_s2:
        print("ESPN_SWID/ESPN_S2 not set -- skipping live feed.")
        sys.exit(0)

    data_dir = Path(args.data_dir)
    owners = json.loads((data_dir / "owners.json").read_text(encoding="utf-8"))
    meta = json.loads((data_dir / "league_meta.json").read_text(encoding="utf-8"))
    if not meta.get("years"):
        print("No seasons found, skipping live feed.")
        sys.exit(0)
    latest_year = max(meta["years"])

    session = espn.build_session(swid, espn_s2)
    raw = espn.fetch_season(session, args.league_id, latest_year, views=["mTeam", "mMatchupScore", "mBoxscore"])
    if raw is None:
        print(f"Could not fetch live data for {latest_year}, skipping.")
        sys.exit(0)

    current_period = find_current_period(raw.get("schedule", []))
    if current_period is None:
        print(f"No games have started yet in {latest_year}, skipping live feed.")
        sys.exit(0)

    events = collect_events(raw, current_period, owners)
    if not events:
        print(f"No notable performances yet in week {current_period}.")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=api_key) if api_key else None
    if not client:
        print("ANTHROPIC_API_KEY not set -- writing factual lines only (no AI text).")

    results = []
    for event in events:
        line = fallback_line(event)
        if client:
            try:
                response = client.messages.create(
                    model=MODEL, max_tokens=200, system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": event_prompt(event)}],
                )
                text = "".join(b.text for b in response.content if b.type == "text").strip()
                if response.stop_reason == "refusal" or looks_like_refusal(text):
                    print(f"  {event['player']}: model declined, using fallback", file=sys.stderr)
                elif text:
                    line = text
            except anthropic.APIStatusError as e:
                print(f"  {event['player']}: API error ({e.status_code}), using fallback", file=sys.stderr)
            except anthropic.APIConnectionError as e:
                print(f"  {event['player']}: connection error ({e}), using fallback", file=sys.stderr)

        results.append({
            "type": event["type"], "owner": event["owner"], "player": event["player"],
            "position": event["position"], "actual": event["actual"], "projected": event["projected"],
            "line": line,
        })
        print(f"  [{event['type']}] {event['player']} ({event['owner']}): {event['actual']:.1f} pts")

    output = {
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "seasonId": latest_year,
        "matchupPeriodId": current_period,
        "events": results,
    }
    (data_dir / "live_feed.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\nDone. Wrote {len(results)} live feed event(s) for {latest_year} week {current_period}.")


if __name__ == "__main__":
    main()
