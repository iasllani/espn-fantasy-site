#!/usr/bin/env python3
"""
Generate fresh, AI-written roast lines for every league member, using their
real career stats, and write them to docs/data/ai_roasts.json for the site
to read.

This is optional and additive: if ANTHROPIC_API_KEY isn't set, or a call for
a given owner fails, this script just skips that owner. docs/app.js falls
back to its own template-based roast lines for anyone missing from
ai_roasts.json, so the site always works even without this step.

Usage:
    ANTHROPIC_API_KEY='sk-ant-...' python3 scripts/generate_ai_roasts.py
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import anthropic

from owner_resolution import resolve_owner_name
from ai_tone import TONE_GUARDRAIL, looks_like_refusal

MODEL = "claude-haiku-4-5"

SYSTEM_PROMPT = f"""You write crude, trash-talking one-liners for a private \
fantasy football league's "Hall of Shame" website. {TONE_GUARDRAIL}

You will be given one league member's real career stats. Write exactly 3 \
short, distinct one-line roasts based on those specific stats -- reference \
actual numbers (their record, their titles, their best/worst season, their \
points against) so each roast feels earned, not generic. Vary sentence \
structure and angle of attack between the 3 lines.

Respond with ONLY the 3 roast lines, one per line, nothing else -- no \
numbering, no quotes, no preamble, no explanation."""

ROSTER_SYSTEM_PROMPT = f"""You write one short, crude roast about an entire \
fantasy football roster, for a private league's website. {TONE_GUARDRAIL}

You will be given one team's full roster with each player's position, NFL \
team, injury status, and points scored. Write exactly 2-3 sentences \
roasting the roster as a whole -- bad draft picks, wasted bench spots, \
injury-prone guys, boring lineup choices, whatever stands out. Reference \
specific players by name.

Respond with ONLY the roast text, nothing else -- no preamble, no labels."""


def owner_stats(name, agg):
    decisions = agg["wins"] + agg["losses"] + agg["ties"]
    win_pct = ((agg["wins"] + agg["ties"] * 0.5) / decisions * 100) if decisions else 0.0
    record = f"{agg['wins']}-{agg['losses']}" + (f"-{agg['ties']}" if agg["ties"] else "")

    played = [s for s in agg["seasons"] if s["wins"] + s["losses"] + s["ties"] > 0]
    best = max(played, key=lambda s: (s["wins"] + s["ties"] * 0.5) / (s["wins"] + s["losses"] + s["ties"]), default=None)
    worst = min(played, key=lambda s: (s["wins"] + s["ties"] * 0.5) / (s["wins"] + s["losses"] + s["ties"]), default=None)

    lines = [
        f"Name: {name}",
        f"Career record: {record} ({win_pct:.1f}% win rate) across {len(agg['seasons'])} seasons",
        f"Championships: {agg['championships']}",
        f"Career points for: {agg['pointsFor']:.1f}, points against: {agg['pointsAgainst']:.1f}",
    ]
    if best:
        best_record = f"{best['wins']}-{best['losses']}" + (f"-{best['ties']}" if best["ties"] else "")
        lines.append(f"Best season: {best['year']}, went {best_record}")
    if worst:
        worst_record = f"{worst['wins']}-{worst['losses']}" + (f"-{worst['ties']}" if worst["ties"] else "")
        lines.append(f"Worst season: {worst['year']}, went {worst_record}")
    return "\n".join(lines)


def build_owner_aggregates(seasons, owners):
    by_owner = {}
    for season in seasons:
        for team in season.get("teams", []):
            name = resolve_owner_name(team, season, owners)
            agg = by_owner.setdefault(name, {
                "wins": 0, "losses": 0, "ties": 0,
                "pointsFor": 0.0, "pointsAgainst": 0.0,
                "championships": 0, "seasons": [],
            })
            w, l, t = team.get("wins") or 0, team.get("losses") or 0, team.get("ties") or 0
            agg["wins"] += w
            agg["losses"] += l
            agg["ties"] += t
            agg["pointsFor"] += team.get("points_for") or 0
            agg["pointsAgainst"] += team.get("points_against") or 0
            if season.get("champion") is not None and team.get("id") == season["champion"]:
                agg["championships"] += 1
            agg["seasons"].append({"year": season.get("seasonId"), "wins": w, "losses": l, "ties": t})
    return by_owner


def generate_roasts_for_owner(client, name, agg):
    response = client.messages.create(
        model=MODEL,
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": owner_stats(name, agg)}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    if response.stop_reason == "refusal" or looks_like_refusal(text):
        return []  # docs/app.js falls back to its own template lines for this owner
    lines = [line.strip(" -•\"'") for line in text.splitlines() if line.strip()]
    return lines[:3]


def current_rosters_by_owner(seasons, owners):
    if not seasons:
        return {}
    latest = seasons[-1]
    return {
        resolve_owner_name(team, latest, owners): team.get("roster", [])
        for team in latest.get("teams", [])
    }


def roster_summary_for_prompt(roster):
    lines = []
    for p in roster:
        cur_pts = (p.get("currentSeason") or {}).get("points") or 0
        prior = p.get("priorSeason") or {}
        prior_pts = prior.get("points")
        status = f" ({p['injuryStatus']})" if p.get("injuryStatus") and p["injuryStatus"] != "ACTIVE" else ""
        prior_str = f", {prior_pts:.1f} pts last season" if prior_pts is not None else ", no games last season"
        lines.append(f"- {p['name']} ({p['position']}, {p['proTeam']}){status}: {cur_pts:.1f} pts this season{prior_str}")
    return "\n".join(lines)


def generate_roster_joke(client, owner_name, roster):
    if not roster:
        return None
    response = client.messages.create(
        model=MODEL,
        max_tokens=300,
        system=ROSTER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Owner: {owner_name}\n\nRoster:\n{roster_summary_for_prompt(roster)}"}],
    )
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if response.stop_reason == "refusal" or looks_like_refusal(text):
        return None
    return text or None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=str(Path(__file__).resolve().parent.parent / "docs" / "data"))
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY not set -- skipping AI roast generation (site will use template fallback).")
        sys.exit(0)

    data_dir = Path(args.data_dir)
    owners = json.loads((data_dir / "owners.json").read_text(encoding="utf-8"))
    meta = json.loads((data_dir / "league_meta.json").read_text(encoding="utf-8"))

    seasons = []
    for year in meta.get("years", []):
        season_path = data_dir / "seasons" / f"season_{year}.json"
        if season_path.exists():
            seasons.append(json.loads(season_path.read_text(encoding="utf-8")))

    aggregates = build_owner_aggregates(seasons, owners)

    client = anthropic.Anthropic(api_key=api_key)
    roasts = {}
    for name, agg in aggregates.items():
        if "(unmapped)" in name:
            continue  # don't waste a call roasting an ESPN handle nobody recognizes
        try:
            lines = generate_roasts_for_owner(client, name, agg)
            if lines:
                roasts[name] = lines
                print(f"  {name}: generated {len(lines)} line(s)")
        except anthropic.APIStatusError as e:
            print(f"  {name}: API error ({e.status_code}), skipping -- site will use template fallback", file=sys.stderr)
        except anthropic.APIConnectionError as e:
            print(f"  {name}: connection error ({e}), skipping -- site will use template fallback", file=sys.stderr)

    rosters = current_rosters_by_owner(seasons, owners)
    roster_jokes = {}
    for name, roster in rosters.items():
        if "(unmapped)" in name:
            continue
        try:
            joke = generate_roster_joke(client, name, roster)
            if joke:
                roster_jokes[name] = joke
                print(f"  {name}: roster joke generated")
        except anthropic.APIStatusError as e:
            print(f"  {name}: roster joke API error ({e.status_code}), skipping", file=sys.stderr)
        except anthropic.APIConnectionError as e:
            print(f"  {name}: roster joke connection error ({e}), skipping", file=sys.stderr)

    output = {
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "model": MODEL,
        "roasts": roasts,
        "rosterJokes": roster_jokes,
    }
    (data_dir / "ai_roasts.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\nDone. Wrote AI roasts for {len(roasts)} owner(s), roster jokes for {len(roster_jokes)} owner(s).")


if __name__ == "__main__":
    main()
