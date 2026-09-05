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

MODEL = "claude-haiku-4-5"

SYSTEM_PROMPT = """You write crude, trash-talking one-liners for a private \
fantasy football league's "Hall of Shame" website. The tone is deliberately \
vulgar and mean -- these are close friends who fully expect to get clowned. \
Keep it playful-mean, not genuinely hateful: no slurs, nothing about \
protected traits (race, religion, gender, disability, etc). Just fantasy \
football incompetence, roasted the way it deserves.

You will be given one league member's real career stats. Write exactly 3 \
short, distinct one-line roasts based on those specific stats -- reference \
actual numbers (their record, their titles, their best/worst season, their \
points against) so each roast feels earned, not generic. Vary sentence \
structure and angle of attack between the 3 lines.

Respond with ONLY the 3 roast lines, one per line, nothing else -- no \
numbering, no quotes, no preamble, no explanation."""


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


def team_display_name(team):
    name = (team.get("name") or "").strip()
    if name:
        return name
    parts = [p for p in [team.get("location"), team.get("nickname")] if p]
    return " ".join(parts).strip() or f"Team {team.get('id')}"


def resolve_owner_name(team, season, owners):
    team_name = team_display_name(team)
    current = owners.get("currentTeamNames", {})
    if team_name in current:
        return current[team_name]

    overrides = owners.get("memberNameOverrides", {})
    for member_id in team.get("owners", []):
        if member_id in overrides:
            return overrides[member_id]

    member_ids = set(team.get("owners", []))
    for member in season.get("members", []):
        if member.get("id") in member_ids and member.get("displayName"):
            return member["displayName"]

    return f"{team_name} (unmapped)"


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
    lines = [line.strip(" -•\"'") for line in text.splitlines() if line.strip()]
    return lines[:3]


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

    output = {
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "model": MODEL,
        "roasts": roasts,
    }
    (data_dir / "ai_roasts.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\nDone. Wrote AI roasts for {len(roasts)} owner(s).")


if __name__ == "__main__":
    main()
