import json
import re
from html import unescape
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "ncaaf" / "fbs_teams.json"

ESPN_TEAMS_PAGE = "https://www.espn.com/college-football/teams"

def safe_code(value):
    return re.sub(r"[^A-Z0-9]+", "_", str(value or "").upper()).strip("_")

def clean_html(value):
    value = re.sub(r"<[^>]+>", "", value)
    return unescape(value).strip()

def main():
    html = requests.get(ESPN_TEAMS_PAGE, timeout=30).text

    pattern = re.compile(
        r'<a[^>]+href="[^"]*/college-football/team/_/id/(\d+)/[^"]*"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL
    )

    teams = {}

    for team_id, raw_name in pattern.findall(html):
        name = clean_html(raw_name)

        if not name:
            continue

        # Skip accidental non-team links if ESPN markup changes
        if name in {"Statistics", "Schedule", "Roster", "Tickets"}:
            continue

        code = safe_code(name.split()[0])

        teams[team_id] = {
            "id": str(team_id),
            "code": code,
            "name": name,
            "logo": f"https://a.espncdn.com/i/teamlogos/ncaa/500/{team_id}.png"
        }

    result = sorted(teams.values(), key=lambda x: x["name"])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"Wrote {len(result)} teams to {OUT}")

if __name__ == "__main__":
    main()
