import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]

DATA = ROOT / "data" / "mlb"
LATEST = DATA / "latest"
STATE = DATA / "state"

LOGS = ROOT / "logs" / "mlb"

ROSTER_TYPE = "active"

TEAMS = [
    {"code": "ARI", "name": "Arizona Diamondbacks", "id": 109},
    {"code": "ATL", "name": "Atlanta Braves", "id": 144},
    {"code": "BAL", "name": "Baltimore Orioles", "id": 110},
    {"code": "BOS", "name": "Boston Red Sox", "id": 111},
    {"code": "CHC", "name": "Chicago Cubs", "id": 112},
    {"code": "CHW", "name": "Chicago White Sox", "id": 145},
    {"code": "CIN", "name": "Cincinnati Reds", "id": 113},
    {"code": "CLE", "name": "Cleveland Guardians", "id": 114},
    {"code": "COL", "name": "Colorado Rockies", "id": 115},
    {"code": "DET", "name": "Detroit Tigers", "id": 116},
    {"code": "HOU", "name": "Houston Astros", "id": 117},
    {"code": "KC", "name": "Kansas City Royals", "id": 118},
    {"code": "LAA", "name": "Los Angeles Angels", "id": 108},
    {"code": "LAD", "name": "Los Angeles Dodgers", "id": 119},
    {"code": "MIA", "name": "Miami Marlins", "id": 146},
    {"code": "MIL", "name": "Milwaukee Brewers", "id": 158},
    {"code": "MIN", "name": "Minnesota Twins", "id": 142},
    {"code": "NYM", "name": "New York Mets", "id": 121},
    {"code": "NYY", "name": "New York Yankees", "id": 147},
    {"code": "ATH", "name": "Athletics", "id": 133},
    {"code": "PHI", "name": "Philadelphia Phillies", "id": 143},
    {"code": "PIT", "name": "Pittsburgh Pirates", "id": 134},
    {"code": "SD", "name": "San Diego Padres", "id": 135},
    {"code": "SF", "name": "San Francisco Giants", "id": 137},
    {"code": "SEA", "name": "Seattle Mariners", "id": 136},
    {"code": "STL", "name": "St. Louis Cardinals", "id": 138},
    {"code": "TB", "name": "Tampa Bay Rays", "id": 139},
    {"code": "TEX", "name": "Texas Rangers", "id": 140},
    {"code": "TOR", "name": "Toronto Blue Jays", "id": 141},
    {"code": "WSH", "name": "Washington Nationals", "id": 120},
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs():
    LATEST.mkdir(parents=True, exist_ok=True)
    STATE.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)


def load_json(path):
    if not path.exists():
        return None

    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def roster_url(team_id):
    return (
        f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster"
        f"?rosterType={ROSTER_TYPE}"
    )


def fetch_roster(team):
    url = roster_url(team["id"])

    response = requests.get(url, timeout=25)
    response.raise_for_status()

    data = response.json()

    players = []

    for item in data.get("roster", []):
        person = item.get("person", {})
        position = item.get("position", {})

        players.append({
            "name": person.get("fullName", ""),
            "position": position.get("abbreviation", ""),
            "number": item.get("jerseyNumber", ""),
            "status": item.get("status", {}).get("code", "ACTIVE"),
            "source": "MLB Stats API",
        })

    players = sorted(players, key=lambda p: p["name"])

    return {
        "code": team["code"],
        "team": team["name"],
        "teamId": team["id"],
        "source": "MLB Stats API",
        "rosterType": ROSTER_TYPE,
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

    if change_type == "removed":
        severity = "medium"

    if change_type == "status changed":
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
        "source": "MLB Stats API",
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
            changes.append(
                make_change(
                    new_roster,
                    "added",
                    new_player,
                    "",
                    new_player.get("status", "")
                )
            )
            continue

        if old_player.get("status", "") != new_player.get("status", ""):
            changes.append(
                make_change(
                    new_roster,
                    "status changed",
                    new_player,
                    old_player.get("status", ""),
                    new_player.get("status", "")
                )
            )

        if old_player.get("position", "") != new_player.get("position", ""):
            changes.append(
                make_change(
                    new_roster,
                    "position changed",
                    new_player,
                    old_player.get("position", ""),
                    new_player.get("position", "")
                )
            )

        if old_player.get("number", "") != new_player.get("number", ""):
            changes.append(
                make_change(
                    new_roster,
                    "number changed",
                    new_player,
                    old_player.get("number", ""),
                    new_player.get("number", "")
                )
            )

    for key, old_player in old_players.items():
        if key not in new_players:
            changes.append(
                make_change(
                    old_roster,
                    "removed",
                    old_player,
                    old_player.get("status", ""),
                    ""
                )
            )

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

    statuses = []
    all_changes = []

    for team in TEAMS:
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
                "teamName": team["name"],
                "checkedAt": current_roster["checkedAt"],
                "playerCount": len(current_roster["players"]),
                "lastChangeCount": len(changes),
                "lastError": "",
                "source": "MLB Stats API",
            })

            print(f"{code}: {len(current_roster['players'])} players, {len(changes)} changes")

        except Exception as error:
            statuses.append({
                "teamCode": code,
                "teamName": team["name"],
                "checkedAt": now_iso(),
                "playerCount": 0,
                "lastChangeCount": 0,
                "lastError": str(error),
                "source": "MLB Stats API",
            })

            print(f"{code}: ERROR {error}")

        time.sleep(0.5)

    append_all_log(all_changes)

    summary = {
        "lastRunFinished": now_iso(),
        "teamsChecked": len(TEAMS),
        "teamsOk": sum(1 for item in statuses if not item["lastError"]),
        "teamsErrored": sum(1 for item in statuses if item["lastError"]),
        "totalChanges": len(all_changes),
        "source": "MLB Stats API",
        "rosterType": ROSTER_TYPE,
    }

    save_json(DATA / "status.json", statuses)
    save_json(DATA / "summary.json", summary)

    print("Done:", summary)


if __name__ == "__main__":
    run()
