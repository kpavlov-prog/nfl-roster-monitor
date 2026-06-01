import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LATEST = DATA / "latest"
STATE = DATA / "state"
LOGS = ROOT / "logs"

TEAMS = [
    ("ARI", "Arizona Cardinals", "arizona-cardinals"),
    ("ATL", "Atlanta Falcons", "atlanta-falcons"),
    ("BAL", "Baltimore Ravens", "baltimore-ravens"),
    ("BUF", "Buffalo Bills", "buffalo-bills"),
    ("CAR", "Carolina Panthers", "carolina-panthers"),
    ("CHI", "Chicago Bears", "chicago-bears"),
    ("CIN", "Cincinnati Bengals", "cincinnati-bengals"),
    ("CLE", "Cleveland Browns", "cleveland-browns"),
    ("DAL", "Dallas Cowboys", "dallas-cowboys"),
    ("DEN", "Denver Broncos", "denver-broncos"),
    ("DET", "Detroit Lions", "detroit-lions"),
    ("GB", "Green Bay Packers", "green-bay-packers"),
    ("HOU", "Houston Texans", "houston-texans"),
    ("IND", "Indianapolis Colts", "indianapolis-colts"),
    ("JAX", "Jacksonville Jaguars", "jacksonville-jaguars"),
    ("KC", "Kansas City Chiefs", "kansas-city-chiefs"),
    ("LV", "Las Vegas Raiders", "las-vegas-raiders"),
    ("LAC", "Los Angeles Chargers", "los-angeles-chargers"),
    ("LAR", "Los Angeles Rams", "los-angeles-rams"),
    ("MIA", "Miami Dolphins", "miami-dolphins"),
    ("MIN", "Minnesota Vikings", "minnesota-vikings"),
    ("NE", "New England Patriots", "new-england-patriots"),
    ("NO", "New Orleans Saints", "new-orleans-saints"),
    ("NYG", "New York Giants", "new-york-giants"),
    ("NYJ", "New York Jets", "new-york-jets"),
    ("PHI", "Philadelphia Eagles", "philadelphia-eagles"),
    ("PIT", "Pittsburgh Steelers", "pittsburgh-steelers"),
    ("SF", "San Francisco 49ers", "san-francisco-49ers"),
    ("SEA", "Seattle Seahawks", "seattle-seahawks"),
    ("TB", "Tampa Bay Buccaneers", "tampa-bay-buccaneers"),
    ("TEN", "Tennessee Titans", "tennessee-titans"),
    ("WAS", "Washington Commanders", "washington-commanders"),
]

IMPORTANT_POSITIONS = {
    "QB", "RB", "WR", "TE", "K", "CB", "DB", "S", "FS", "SS",
    "DE", "EDGE", "OT", "LT"
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 NFLRosterMonitor/1.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def official_url(slug):
    return f"https://www.nfl.com/teams/{slug}/roster"


def ensure_dirs():
    DATA.mkdir(exist_ok=True)
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


def fetch_html(url):
    last_error = None

    for attempt in range(3):
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=25
            )
            response.raise_for_status()

            if len(response.text) < 500:
                raise ValueError("Response too short")

            return response.text

        except Exception as error:
            last_error = error
            time.sleep(2 + attempt * 2)

    raise RuntimeError(str(last_error))


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def parse_roster(html, code, team_name, slug):
    soup = BeautifulSoup(html, "html.parser")

    positions = (
        "QB|RB|FB|WR|TE|C|G|OG|OT|OL|DE|DT|DL|LB|OLB|MLB|"
        "CB|S|FS|SS|DB|K|PK|P|LS|EDGE"
    )

    players = {}

    for row in soup.find_all("tr"):
        text = clean(row.get_text(" "))
        if not text:
            continue

        pos_match = re.search(rf"\b({positions})\b", text)
        if not pos_match:
            continue

        position = pos_match.group(1)
        if position == "PK":
            position = "K"

        links = row.find_all("a")
        possible_names = []

        for link in links:
            link_text = clean(link.get_text(" "))
            if re.match(
                r"^[A-Z][A-Za-z.'-]+(?: [A-Z][A-Za-z.'-]+){1,3}$",
                link_text
            ):
                possible_names.append(link_text)

        if possible_names:
            player_name = possible_names[0]
        else:
            name_match = re.search(
                r"([A-Z][A-Za-z.'-]+(?: [A-Z][A-Za-z.'-]+){1,3})",
                text
            )
            if not name_match:
                continue
            player_name = clean(name_match.group(1))

        number_match = re.search(r"\b([0-9]{1,2})\b", text)
        status_match = re.search(
            r"\b(ACT|RES|IR|PUP|NFI|SUS|EXE|RFA|UFA|UDF|NON)\b",
            text
        )

        players[player_name.lower()] = {
            "name": player_name,
            "position": position,
            "number": number_match.group(1) if number_match else "",
            "status": status_match.group(1) if status_match else "LISTED",
            "important": position in IMPORTANT_POSITIONS,
            "source": "NFL.com",
        }

    return {
        "code": code,
        "team": team_name,
        "source": "NFL.com official roster",
        "url": official_url(slug),
        "checkedAt": now_iso(),
        "players": sorted(players.values(), key=lambda p: p["name"]),
    }


def player_map(roster):
    return {
        player["name"].lower(): player
        for player in roster.get("players", [])
    }


def severity(change):
    score = 0
    position = change.get("position", "")
    text = (
        f"{change.get('type', '')} "
        f"{change.get('oldValue', '')} "
        f"{change.get('newValue', '')}"
    ).lower()

    if position == "QB":
        score += 50
    elif position in {"RB", "WR", "TE", "K"}:
        score += 25
    elif position in {"CB", "DB", "S", "FS", "SS", "DE", "EDGE", "OT", "LT"}:
        score += 15

    if change["type"] == "removed":
        score += 45
    elif change["type"] == "added":
        score += 15
    elif change["type"] == "status changed":
        score += 35

    if re.search(r"ir|pup|nfi|sus|res|out|inj", text):
        score += 40

    if score >= 80:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def make_change(roster, change_type, player, old_value="", new_value=""):
    change = {
        "timestamp": now_iso(),
        "teamCode": roster["code"],
        "teamName": roster["team"],
        "type": change_type,
        "playerName": player.get("name", ""),
        "position": player.get("position", ""),
        "oldValue": old_value or "",
        "newValue": new_value or "",
        "source": "NFL.com",
    }

    change["severity"] = severity(change)
    return change


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

    print("ROOT:", ROOT)
    print("STATE:", STATE)
    print("LATEST:", LATEST)
    print("LOGS:", LOGS)

    for code, team_name, slug in TEAMS:
        try:
            html = fetch_html(official_url(slug))
            current_roster = parse_roster(html, code, team_name, slug)

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
                "teamName": team_name,
                "checkedAt": current_roster["checkedAt"],
                "playerCount": len(current_roster["players"]),
                "lastChangeCount": len(changes),
                "lastError": "",
                "source": "NFL.com",
            })

            print(f"{code}: {len(current_roster['players'])} players, {len(changes)} changes")

        except Exception as error:
            statuses.append({
                "teamCode": code,
                "teamName": team_name,
                "checkedAt": now_iso(),
                "playerCount": 0,
                "lastChangeCount": 0,
                "lastError": str(error),
                "source": "NFL.com",
            })

            print(f"{code}: ERROR {error}")

        time.sleep(1.5)

    append_all_log(all_changes)

    summary = {
        "lastRunFinished": now_iso(),
        "teamsChecked": len(TEAMS),
        "teamsOk": sum(1 for item in statuses if not item["lastError"]),
        "teamsErrored": sum(1 for item in statuses if item["lastError"]),
        "totalChanges": len(all_changes),
        "source": "NFL.com official roster only",
    }

    save_json(DATA / "status.json", statuses)
    save_json(DATA / "summary.json", summary)

    print("Done:", summary)


if __name__ == "__main__":
    run()
