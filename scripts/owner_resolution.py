"""Shared owner-name resolution logic, used by every script that needs to
turn an ESPN team object into a real person's name. Mirrors the same
resolution order as docs/app.js's resolveOwnerName -- keep both in sync."""


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
