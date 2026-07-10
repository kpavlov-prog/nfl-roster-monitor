import json
import re
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "ncaaf" / "fbs_teams.json"

GROUPS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/groups"

FBS_CONFERENCES = {
    "ACC",
    "American",
    "Big 12",
    "Big Ten",
    "CUSA",
    "Conference USA",
    "FBS Indep.",
    "FBS Independents",
    "MAC",
    "Mid-American",
    "Mountain West",
    "Pac-12",
    "SEC",
    "Sun Belt",
}

def safe_code(value):
    return re.sub(r"[^A-Z0-9]+", "_", str(value or "").upper()).strip("_")

def collect_teams(group, active=False):
    name = group.get("name", "")
    abbreviation = group.get("abbreviation", "")
    is_fbs_group = name in FBS_CONFERENCES or abbreviation in FBS_CONFERENCES

    active = active or is_fbs_group

    teams = []

    if active:
        for team in group.get("teams", []):
            team_id = str(team.get("id", "")).strip()
            team_name = team.get("displayName") or team.get("name")
            code = safe_code(team.get("abbreviation") or team.get("shortDisplayName") or team_id)

            if team_id and team_name:
                teams.append({
                    "id": team_id,
                    "code": code,
                    "name": team_name,
                    "logo": f"https://a.espncdn.com/i/teamlogos/ncaa/500/{team_id}.png",
                })

    for child in group.get("children", []):
        teams.extend(collect_teams(child, active))

    return teams

def main():
    data = requests.get(GROUPS_URL, timeout=30).json()

    teams = []

    for group in data.get("groups", []):
        teams.extend(collect_teams(group))

    unique = {}
    for team in teams:
        unique[team["id"]] = team

    result = sorted(unique.values(), key=lambda x: x["name"])

    if len(result) < 120:
        raise RuntimeError(f"Expected FBS team list, but only found {len(result)} teams")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Wrote {len(result)} teams to {OUT}")

if __name__ == "__main__":
    main()
