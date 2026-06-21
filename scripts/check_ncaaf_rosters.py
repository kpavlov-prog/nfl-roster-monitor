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


def roster_url(team_id):
    return (
        "https://site.api.espn.com/apis/site/v2/sports/"
        f"football/college-football/teams/{team_id}/roster"
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


def fetch_roster(team):
    url = roster_url(team["id"])
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    data = response.json()

    players = extract_players(data)

    team_name = data.get("team", {}).get("displayName") or team["name"]

    return {
        "code": team["code"],
        "team": team_name,
        "teamId": team["id"],
        "logo": team.get("logo", ""),
        "source": "ESPN College Football API",
        "url": url,
        "checkedAt": now_iso(),
        "players": players,
    }


def player_map(roster):
    return {
        player["name"].lower(): player
        for player in roster.get("players", [])
    }


def make_change(roster, change_type, player, old_value="", new_value=""):
    severity = "low"

    if change_type in ["removed", "status changed"]:
        severity = "medium"

    return {
        "timestamp": now_iso(),
        "teamCode": roster["code"],
        "teamName": roster["team"],
        "type": change_type,
        "playerName": player.get("name", ""),
        "position": player.get("position", ""),
        "oldValue": old_value or "",
        "newValue": new_value or "",
        "source": "ESPN College Football API",
        "severity": severity,
    }


def diff_rosters(old_roster, new_roster):
    if old_roster is None:
        return []

    old_players = player_map(old_roster)
    new_players = player_map(new_roster)

    changes = []

    for key, new_player in new_players.items():
        old_player = old_players.get(key)

        if old_player is None:
            changes.append(make_change(new_roster, "added", new_player, "", new_player.get("status", "")))
            continue

        if old_player.get("status", "") != new_player.get("status", ""):
            changes.append(make_change(new_roster, "status changed", new_player, old_player.get("status", ""), new_player.get("status", "")))

        if old_player.get("position", "") != new_player.get("position", ""):
            changes.append(make_change(new_roster, "position changed", new_player, old_player.get("position", ""), new_player.get("position", "")))

        if old_player.get("number", "") != new_player.get("number", ""):
            changes.append(make_change(new_roster, "number changed", new_player, old_player.get("number", ""), new_player.get("number", "")))

    for key, old_player in old_players.items():
        if key not in new_players:
            changes.append(make_change(old_roster, "removed", old_player, old_player.get("status", ""), ""))

    return changes


def append_json_log(path, new_items):
    if not new_items:
        return

    existing = load_json(path) or []
    save_json(path, new_items + existing)


def append_team_log(team_code, changes):
    append_json_log(LOGS / f"{team_code}.json", changes)


def append_all_log(changes):
    append_json_log(LOGS / "all_changes.json", changes)


def run():
    ensure_dirs()

    teams = fetch_teams()
    valid_codes = {team["code"] for team in teams}
    cleanup_old_files(valid_codes)

    statuses = []
    all_changes = []

    print(f"Discovered {len(teams)} FBS NCAAF teams from ESPN")

    for team in teams:
        code = team["code"]

        try:
            current_roster = fetch_roster(team)

            state_path = STATE / f"{code}.json"
            old_roster = load_json(state_path)

            changes = diff_rosters(old_roster, current_roster)

            save_json(state_path, current_roster)
            save_json(LATEST / f"{code}.json", current_roster)

            if changes:
                append_team_log(code, changes)
                all_changes.extend(changes)

            statuses.append({
                "teamCode": code,
                "teamName": current_roster["team"],
                "teamId": team["id"],
                "logo": team.get("logo", ""),
                "checkedAt": current_roster["checkedAt"],
                "playerCount": len(current_roster["players"]),
                "lastChangeCount": len(changes),
                "lastError": "",
                "source": "ESPN College Football API",
            })

            print(f"{code}: {len(current_roster['players'])} players, {len(changes)} changes")

        except Exception as error:
            statuses.append({
                "teamCode": code,
                "teamName": team["name"],
                "teamId": team["id"],
                "logo": team.get("logo", ""),
                "checkedAt": now_iso(),
                "playerCount": 0,
                "lastChangeCount": 0,
                "lastError": str(error),
                "source": "ESPN College Football API",
            })

            print(f"{code}: ERROR {error}")

        time.sleep(0.25)

    append_all_log(all_changes)

    summary = {
        "lastRunFinished": now_iso(),
        "teamsChecked": len(teams),
        "teamsOk": sum(1 for item in statuses if not item["lastError"]),
        "teamsErrored": sum(1 for item in statuses if item["lastError"]),
        "totalChanges": len(all_changes),
        "source": "ESPN College Football API",
    }

    save_json(DATA / "status.json", statuses)
    save_json(DATA / "summary.json", summary)

    print("Done:", summary)


if __name__ == "__main__":
    run()
