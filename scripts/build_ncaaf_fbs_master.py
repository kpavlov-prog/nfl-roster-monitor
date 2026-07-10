import json
import re
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "ncaaf" / "fbs_teams.json"

TEAMS_API = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams?limit=500"

FBS_TEAM_NAMES = {
    "Tulane Green Wave",
    "SMU Mustangs",
    "Sam Houston Bearkats",
    "UTEP Miners",
    "UTSA Roadrunners",
    "USC Trojans",
    "Massachusetts Minutemen",
    "UMass Minutemen",
}

def safe_code(value):
    return re.sub(r"[^A-Z0-9]+", "_", str(value or "").upper()).strip("_")

def main():
    data = requests.get(TEAMS_API, timeout=30).json()
    found = {}

    for sport in data.get("sports", []):
        for league in sport.get("leagues", []):
            for item in league.get("teams", []):
                team = item.get("team", {})
                name = team.get("displayName") or team.get("name")
                if name not in FBS_TEAM_NAMES:
                    continue

                team_id = str(team.get("id"))
                code = safe_code(team.get("abbreviation") or team.get("shortDisplayName") or team_id)
                logo = f"https://a.espncdn.com/i/teamlogos/ncaa/500/{team_id}.png"

                found[name] = {
                    "id": team_id,
                    "code": code,
                    "name": name,
                    "logo": logo,
                }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(sorted(found.values(), key=lambda x: x["name"]), indent=2), encoding="utf-8")

    print(f"Wrote {len(found)} teams to {OUT}")
    print("Missing:")
    for name in sorted(FBS_TEAM_NAMES - set(found)):
        print(name)

if __name__ == "__main__":
    main()
