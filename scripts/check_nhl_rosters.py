import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]

DATA = ROOT / "data" / "nhl"
LATEST = DATA / "latest"
STATE = DATA / "state"
LOGS = ROOT / "logs" / "nhl"

TEAMS = [
    {"code": "ANA", "name": "Anaheim Ducks"},
    {"code": "BOS", "name": "Boston Bruins"},
    {"code": "BUF", "name": "Buffalo Sabres"},
    {"code": "CGY", "name": "Calgary Flames"},
    {"code": "CAR", "name": "Carolina Hurricanes"},
    {"code": "CHI", "name": "Chicago Blackhawks"},
    {"code": "COL", "name": "Colorado Avalanche"},
    {"code": "CBJ", "name": "Columbus Blue Jackets"},
    {"code": "DAL", "name": "Dallas Stars"},
    {"code": "DET", "name": "Detroit Red Wings"},
    {"code": "EDM", "name": "Edmonton Oilers"},
    {"code": "FLA", "name": "Florida Panthers"},
    {"code": "LAK", "name": "Los Angeles Kings"},
    {"code": "MIN", "name": "Minnesota Wild"},
    {"code": "MTL", "name": "Montreal Canadiens"},
    {"code": "NSH", "name": "Nashville Predators"},
    {"code": "NJD", "name": "New Jersey Devils"},
    {"code": "NYI", "name": "New York Islanders"},
    {"code": "NYR", "name": "New York Rangers"},
    {"code": "OTT", "name": "Ottawa Senators"},
    {"code": "PHI", "name": "Philadelphia Flyers"},
    {"code": "PIT", "name": "Pittsburgh Penguins"},
    {"code": "SEA", "name": "Seattle Kraken"},
    {"code": "SJS", "name": "San Jose Sharks"},
    {"code": "STL", "name": "St. Louis Blues"},
    {"code": "TBL", "name": "Tampa Bay Lightning"},
    {"code": "TOR", "name": "Toronto Maple Leafs"},
    {"code": "UTA", "name": "Utah Mammoth"},
    {"code": "VAN", "name": "Vancouver Canucks"},
    {"code": "VGK", "name": "Vegas Golden Knights"},
    {"code": "WSH", "name": "Washington Capitals"},
    {"code": "WPG", "name": "Winnipeg Jets"},
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
        encoding="utf-8",
    )


def lang_name(value):
    if isinstance(value, dict):
        return value.get("default") or value.get("en") or ""
    return value or ""


def roster_url(team_code):
    return f"https://api-web.nhle.com/v1/roster/{team_code}/current"


def fetch_roster(team):
    url = roster_url(team["code"])
    response = requests.get(url, timeout=25)
    response.raise_for_status()
    data = response.json()

    players = []

    for group in ["forwards", "defensemen", "goalies"]:
        for item in data.get(group, []):
            first = lang_name(item.get("firstName"))
            last = lang_name(item.get("lastName"))
            full_name = f"{first} {last}".strip()

            players.append({
                "name": full_name,
                "position": item.get("positionCode", ""),
                "number": str(item.get("sweaterNumber", "")),
                "status": "ACTIVE",
                "source": "NHL Web API",
            })

    players = sorted(players, key=lambda p: p["name"])

    return {
        "code": team["code"],
        "team": team["name"],
        "source": "NHL Web API",
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
        "source": "NHL Web API",
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
                    new_player.get("status", ""),
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
                    new_player.get("status", ""),
                )
            )

        if old_player.get("position", "") != new_player.get("position", ""):
            changes.append(
                make_change(
                    new_roster,
                    "position changed",
                    new_player,
                    old_player.get("position", ""),
                    new_player.get("position", ""),
                )
            )

        if old_player.get("number", "") != new_player.get("number", ""):
            changes.append(
                make_change(
                    new_roster,
                    "number changed",
                    new_player,
                    old_player.get("number", ""),
                    new_player.get("number", ""),
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
                    "",
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
                "source": "NHL Web API",
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
                "source": "NHL Web API",
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
        "source": "NHL Web API",
    }

    save_json(DATA / "status.json", statuses)
    save_json(DATA / "summary.json", summary)

    print("Done:", summary)


if __name__ == "__main__":
    run()
