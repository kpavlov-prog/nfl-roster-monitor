import json
import re
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "ncaaf" / "fbs_teams.json"

ESPN_TEAMS_PAGE = "https://www.espn.com/college-football/teams"
CORE_TEAM_URL = "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/seasons/2026/teams/{team_id}?lang=en&region=us"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


def safe_code(value):
    return re.sub(r"[^A-Z0-9]+", "_", str(value or "").upper()).strip("_")


def name_from_slug(slug):
    special = {
        "byu-cougars": "BYU Cougars",
        "smu-mustangs": "SMU Mustangs",
        "uab-blazers": "UAB Blazers",
        "ucf-knights": "UCF Knights",
        "ucla-bruins": "UCLA Bruins",
        "usc-trojans": "USC Trojans",
        "utsa-roadrunners": "UTSA Roadrunners",
        "utep-miners": "UTEP Miners",
        "unlv-rebels": "UNLV Rebels",
        "nc-state-wolfpack": "NC State Wolfpack",
        "miami-oh-redhawks": "Miami (OH) RedHawks",
        "hawaii-rainbow-warriors": "Hawai'i Rainbow Warriors",
        "san-jose-state-spartans": "San José State Spartans",
        "texas-a-m-aggies": "Texas A&M Aggies",
        "ul-monroe-warhawks": "UL Monroe Warhawks",
    }

    if slug in special:
        return special[slug]

    return " ".join(part.capitalize() for part in slug.split("-"))


def get_core_team(team_id):
    try:
        response = requests.get(CORE_TEAM_URL.format(team_id=team_id), headers=HEADERS, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception:
        return {}


def main():
    response = requests.get(ESPN_TEAMS_PAGE, headers=HEADERS, timeout=30)
    response.raise_for_status()

    html = response.text

    matches = re.findall(
        r"/college-football/team/_/id/(\d+)/([a-z0-9-]+)",
        html,
        flags=re.IGNORECASE,
    )

    found = {}

    for team_id, slug in matches:
        team_id = str(team_id)

        if team_id in found:
            continue

        core = get_core_team(team_id)

        name = (
            core.get("displayName")
            or core.get("name")
            or name_from_slug(slug)
        )

        code = safe_code(
            core.get("abbreviation")
            or core.get("shortDisplayName")
            or slug.split("-")[0]
        )

        found[team_id] = {
            "id": team_id,
            "code": code,
            "name": name,
            "logo": f"https://a.espncdn.com/i/teamlogos/ncaa/500/{team_id}.png",
        }

        time.sleep(0.05)

    teams = sorted(found.values(), key=lambda x: x["name"])

    if len(teams) < 130:
        raise RuntimeError(f"Expected around 138 FBS teams, but only found {len(teams)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(teams, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Wrote {len(teams)} teams to {OUT}")


if __name__ == "__main__":
    main()
