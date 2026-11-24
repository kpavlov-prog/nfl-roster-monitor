"""Build data/ncaab/d1_teams.json - the NCAA Division I men's basketball teams the checker monitors.

Run by hand when the Division I membership changes (normally once before a season):
    python scripts/build_ncaab_d1_master.py

Source: ESPN only.
- Division I membership: the core API's Division I group (50) for the season, through its
  conferences. A team counts when it is in a Division I conference and ESPN marks it active
  (in 2026-27 this leaves out Hartford, id 42: inactive, in no conference).
- Cross-check: the site API's team list must contain exactly the same ids, or nothing is written.
  (The site API's /groups endpoint is not used: it lists only part of Division I.)

The file is reviewed and committed like code; check_ncaab_rosters.py validates it on every run.
"""
import json
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "ncaab" / "d1_teams.json"

SEASON = 2027  # ESPN season year of the 2026-27 season
CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball"
SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball"
DIVISION_I_GROUP = 50


def get_json(url):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    time.sleep(0.2)
    return response.json()


def safe_code(value):
    return re.sub(r"[^A-Z0-9]+", "_", str(value or "").upper()).strip("_")


def ref_id(ref):
    return ref.split("/teams/")[1].split("?")[0]


def with_limit(url, limit=500):
    return url + ("&" if "?" in url else "?") + f"limit={limit}"


def build():
    conferences = get_json(with_limit(f"{CORE}/seasons/{SEASON}/types/2/groups/{DIVISION_I_GROUP}/children", 100))
    conference_of = {}
    for item in conferences.get("items", []):
        group = get_json(item["$ref"])
        if not group.get("isConference"):
            continue
        members = get_json(with_limit(group["teams"]["$ref"], 100))
        for member in members.get("items", []):
            team_id = ref_id(member["$ref"])
            if team_id in conference_of:
                raise SystemExit(f"team {team_id} is in two conferences: {conference_of[team_id]} and {group['name']}")
            conference_of[team_id] = {"conference": group.get("name", ""), "conferenceId": str(group.get("id", ""))}

    site_teams = {t["team"]["id"]: t["team"] for t in
                  get_json(f"{SITE}/teams?limit=1000")["sports"][0]["leagues"][0]["teams"]}

    teams = []
    for team_id, conference in conference_of.items():
        site = site_teams.get(team_id)
        if site is None:
            raise SystemExit(f"Division I team {team_id} ({conference['conference']}) is missing from the site team list")
        if site.get("isActive") is False:
            continue
        logos = site.get("logos") or []
        teams.append({
            "id": team_id,
            "code": safe_code(site.get("abbreviation")),
            "abbreviation": site.get("abbreviation", ""),
            "name": site.get("displayName", ""),
            "conference": conference["conference"],
            "conferenceId": conference["conferenceId"],
            "logo": logos[0]["href"] if logos else f"https://a.espncdn.com/i/teamlogos/ncaa/500/{team_id}.png",
        })

    extra = sorted(set(site_teams) - set(conference_of))
    if extra:
        raise SystemExit(f"site team list has teams outside the Division I conferences: {extra}; nothing written")
    codes = [t["code"] for t in teams]
    if len(set(codes)) != len(codes) or len({t["id"] for t in teams}) != len(teams):
        raise SystemExit("duplicate team ids or codes; nothing written")
    return sorted(teams, key=lambda t: t["code"])


if __name__ == "__main__":
    teams = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(teams, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(teams)} Division I teams in "
          f"{len({t['conference'] for t in teams})} conferences")
    sys.exit(0)
