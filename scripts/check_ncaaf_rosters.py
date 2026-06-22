import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]

DATA = ROOT / "data" / "ncaaf"
LATEST = DATA / "latest"
STATE = DATA / "state"
LOGS = ROOT / "logs" / "ncaaf"

TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams?limit=500"

FBS_TEAM_CODES = {
    "AF","AKR","ALA","APP","ARIZ","ARK","ARST","ARMY","ASU","AUB","BALL","BAY","BC","BGSU","BOIS","BUFF","BYU",
    "CAL","CCU","CHAR","CIN","CLEM","CLT","CMU","COLO","CONN","CSU","DUKE","ECU","EMU","FAU","FIU","FLA","FLST",
    "FRES","GASO","GAST","GT","HAW","HOU","IU","ILL","IOWA","ISU","JMU","KAN","KENT","KSU","KU","LIB","LT","LOU",
    "LSU","MAR","MD","MEM","MIA","M-OH","MICH","MINN","MISS","MIZ","MSST","MTU","NAVY","NCST","NEB","NEV","NIU",
    "NMST","NMSU","NORTH","NU","NW","ODU","OHIO","OKLA","OKST","ORE","ORST","PITT","PSU","PUR","RICE","RUTG",
    "SAM","SDSU","SJSU","SMU","SOAL","SOFL","STAN","SYR","TCU","TEM","TENN","TEX","TLSA","TOL","TROY","TTU",
    "TULN","UAB","UCF","UCLA","UGA","ULL","ULM","UMASS","UNC","UNLV","UNT","USC","USF","USM","UTAH","UTEP",
    "UTSA","UVA","VAN","VT","WAKE","WASH","WIS","WKU","WMU","WVU","WYO"
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs():
    LATEST.mkdir(parents=True, exist_ok=True)
    STATE.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)


def safe_code(value):
    value = str(value or "").upper()
    value = re.sub(r"[^A-Z0-9]+", "_", value)
    return value.strip("_")


def load_json(path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def cleanup_old_files(valid_codes):
    valid_files = {f"{code}.json" for code in valid_codes}

    for folder in [LATEST, STATE]:
        if folder.exists():
            for path in folder.glob("*.json"):
                if path.name not in valid_files:
                    path.unlink()

    if LOGS.exists():
        for path in LOGS.glob("*.json"):
            if path.name == "all_changes.json":
                continue
            if path.name not in valid_files:
                path.unlink()

    all_changes_path = LOGS / "all_changes.json"
    if all_changes_path.exists():
        existing = load_json(all_changes_path) or []
        filtered = [item for item in existing if item.get("teamCode") in valid_codes]
        save_json(all_changes_path, filtered)


def fetch_teams():
    response = requests.get(TEAMS_URL, timeout=30)
    response.raise_for_status()
    data = response.json()

    teams = []

    for sport in data.get("sports", []):
        for league in sport.get("leagues", []):
            for item in league.get("teams", []):
                team = item.get("team", {})

                team_id = team.get("id")
                name = team.get("displayName") or team.get("name")
                abbreviation = (
                    team.get("abbreviation")
                    or team.get("shortDisplayName")
                    or team.get("slug")
                    or team_id
                )

                if not team_id or not name:
                    continue

                code = safe_code(abbreviation)

                if code not in FBS_TEAM_CODES:
                    continue

                logos = team.get("logos") or []
                logo = logos[0].get("href", "") if logos and isinstance(logos, list) else ""

                teams.append({
                    "id": str(team_id),
                    "code": code,
                    "name": name,
                    "logo": logo or f"https://a.espncdn.com/i/teamlogos/ncaa/500/{team_id}.png",
                })

    unique = {}
    for team in teams:
        unique[team["id"]] = team

    return sorted(unique.values(), key=lambda t: t["name"])


def roster_url(team_id, page=1):
    return (
        "https://site.api.espn.com/apis/site/v2/sports/"
        f"football/college-football/teams/{team_id}/roster?limit=100&page={page}"
    )


def extract_players(data):
    players = []

    def add_player(athlete, fallback_position=""):
        if not isinstance(athlete, dict):
            return

        position = athlete.get("position") or fallback_position or ""

        if isinstance(position, str):
            position_value = position
        elif isinstance(position, dict):
            position_value = (
                position.get("abbreviation")
                or position.get("displayName")
                or position.get("name")
                or ""
            )
        else:
            position_value = ""

        name = (
            athlete.get("displayName")
            or athlete.get("fullName")
            or athlete.get("name")
            or athlete.get("shortName")
            or ""
        )

        if not name:
            return

        players.append({
            "name": name,
            "position": position_value,
            "number": str(athlete.get("jersey") or athlete.get("number") or ""),
            "status": "ACTIVE",
            "source": "ESPN College Football API",
        })

    for item in data.get("athletes", []):
        if isinstance(item, dict) and "items" in item:
            fallback_position = item.get("position") or item.get("name") or item.get("displayName") or ""
            for athlete in item.get("items", []):
                add_player(athlete, fallback_position)
        elif isinstance(item, dict) and "athlete" in item:
            add_player(item.get("athlete"))
        else:
            add_player(item)

    for group in data.get("groups", []):
        fallback_position = group.get("position") or group.get("name") or group.get("displayName") or ""
        for athlete in group.get("athletes", []):
            add_player(athlete, fallback_position)
        for athlete in group.get("items", []):
            add_player(athlete, fallback_position)

    seen = {}
    for player in players:
        key = player["name"].lower()
        seen[key] = player

    return sorted(seen.values(), key=lambda p: p["name"])


def core_team_athletes_url(team_id, page=1):
    return (
        "https://sports.core.api.espn.com/v2/sports/football/leagues/"
        f"college-football/seasons/2026/teams/{team_id}/athletes"
        f"?limit=300&page={page}"
    )


def get_json(url):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


def player_from_core_athlete(item):
    if "$ref" in item:
        item = get_json(item["$ref"])

    position = item.get("position") or {}

    if isinstance(position, dict):
        position_value = (
            position.get("abbreviation")
            or position.get("displayName")
            or position.get("name")
            or ""
        )
    else:
        position_value = str(position or "")

    name = (
        item.get("displayName")
        or item.get("fullName")
        or item.get("name")
        or item.get("shortName")
        or ""
    )

    if not name:
        return None

    return {
        "name": name,
        "position": position_value,
        "number": str(item.get("jersey") or item.get("number") or ""),
        "status": "ACTIVE",
        "source": "ESPN College Football Core API",
    }


def fetch_core_roster(team):
    all_players = []

    for page in range(1, 10):
        data = get_json(core_team_athletes_url(team["id"], page))
        items = data.get("items", [])

        if not items:
            break

        for item in items:
            player = player_from_core_athlete(item)
            if player:
                all_players.append(player)

        if len(items) < 300:
            break

    by_name = {}
    for player in all_players:
        by_name[player["name"].lower()] = player

    return sorted(by_name.values(), key=lambda p: p["name"])


def fetch_roster(team):

    url = roster_url(team["id"], 1)
    response = requests.get(url, timeout=30)
    response.raise_for_status()

    data = response.json()
    site_players = extract_players(data)

    core_players = []

    if len(site_players) >= 100:
        try:
            core_players = fetch_core_roster(team)
        except Exception as error:
            print(f"{team['code']}: core fallback failed: {error}")

    players = core_players if len(core_players) > len(site_players) else site_players

    team_name = data.get("team", {}).get("displayName") or team["name"]

    return {
        "code": team["code"],
        "team": team_name,
        "teamId": team["id"],
        "logo": team.get("logo", ""),
        "source": "ESPN College Football Core API" if len(core_players) > len(site_players) else "ESPN College Football API",
        "url": core_team_athletes_url(team["id"], 1) if len(core_players) > len(site_players) else url,
        "checkedAt": now_iso(),
        "players": players,
    }
