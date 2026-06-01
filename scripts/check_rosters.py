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

IMPORTANT_POSITIONS = {"QB", "RB", "WR", "TE", "K", "CB", "DB", "S", "FS", "SS", "DE", "EDGE", "OT", "LT"}

HEADERS = {
    "User-Agent": "Mozilla/5.0 NFLRosterMonitor/1.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def official_url(slug):
    return f"https://www.nfl.com/teams/{slug}/roster"

def ensure_dirs():
    for folder in [DATA, LATEST, STATE, LOGS]:
        folder.mkdir(parents=True, exist_ok=True)

def fetch_html(url):
    last_error = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            r.raise_for_status()
            if len(r.text) < 500:
                raise ValueError("Response too short")
            return r.text
        except Exception as e:
            last_error = e
            time.sleep(2 + attempt * 2)
    raise RuntimeError(str(last_error))

def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()

def parse_roster(html, code, name, slug):
    soup = BeautifulSoup(html, "html.parser")
    players = {}
    positions = r"QB|RB|FB|WR|TE|C|G|OG|OT|OL|DE|DT|DL|LB|OLB|MLB|CB|S|FS|SS|DB|K|PK|P|LS|EDGE"

    rows = soup.find_all("tr")
    for row in rows:
        text = clean(row.get_text(" "))
        if not text:
            continue

        pos_match = re.search(rf"\b({positions})\b", text)
        if not pos_match:
            continue

        links = row.find_all("a")
        possible_names = [clean(a.get_text(" ")) for a in links]
        possible_names = [
            n for n in possible_names
            if re.match(r"^[A-Z][A-Za-z.'-]+(?: [A-Z][A-Za-z.'-]+){1,3}$", n)
        ]

        if not possible_names:
            name_match = re.search(r"([A-Z][A-Za-z.'-]+(?: [A-Z][A-Za-z.'-]+){1,3})", text)
            if not name_match:
                continue
            player_name = clean(name_match.group(1))
        else:
            player_name = possible_names[0]

        position = pos_match.group(1)
        if position == "PK":
            position = "K"

        number_match = re.search(r"\b([0-9]{1,2})\b", text)
        status_match = re.search(r"\b(ACT|RES|IR|PUP|NFI|SUS|EXE|RFA|UFA|UDF|NON)\b", text)

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
        "team": name,
        "source": "NFL.com official roster",
        "url": official_url(slug),
        "checkedAt": now_iso(),
        "players": sorted(players.values(), key=lambda x: x["name"]),
    }

def load_json(path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))

def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def player_map(roster):
    return {p["name"].lower(): p for p in roster.get("players", [])}

def severity(change):
    score = 0
    pos = change.get("position", "")
    text = f"{change['type']} {change.get('oldValue','')} {change.get('newValue','')}".lower()

    if pos == "QB":
        score += 50
    elif pos in {"RB", "WR", "TE", "K"}:
        score += 25
    elif pos in {"CB", "DB", "S", "FS", "SS", "DE", "EDGE", "OT", "LT"}:
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
    item = {
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
    item["severity"] = severity(item)
    return item

def diff_rosters(old, new):
    if old is None:
        return []

    old_map = player_map(old)
    new_map = player_map(new)
    changes = []

    for key, new_player in new_map.items():
        old_player = old_map.get(key)
        if not old_player:
            changes.append(make_change(new, "added", new_player, "", new_player.get("status", "")))
            continue

        if old_player.get("status", "") != new_player.get("status", ""):
            changes.append(make_change(new, "status changed", new_player, old_player.get("status", ""), new_player.get("status", "")))

        if old_player.get("position", "") != new_player.get("position", ""):
            changes.append(make_change(new, "position changed", new_player, old_player.get("position", ""), new_player.get("position", "")))

        if old_player.get("number", "") != new_player.get("number", ""):
            changes.append(make_change(new, "number changed", new_player, old_player.get("number", ""), new_player.get("number", "")))

    for key, old_player in old_map.items():
        if key not in new_map:
            changes.append(make_change(old, "removed", old_player, old_player.get("status", ""), ""))

    return changes

def append_team_log(code, changes):
    if not changes:
        return

    path = LOGS / f"{code}.json"
    existing = load_json(path) or []
    save_json(path, changes + existing)

def append_all_log(changes):
    if not changes:
        return

    path = LOGS / "all_changes.json"
    existing = load_json(path) or []
    save_json(path, changes + existing)

def run():
    ensure_dirs()

    statuses = []
    all_changes = []

    for code, team_name, slug in TEAMS:
        try:
            html = fetch_html(official_url(slug))
            roster = parse_roster(html, code, team_name, slug)

    old_roster = load_json(STATE / f"{code}.json")

    if code == "ARI":
        print("DEBUG STATE PATH:", STATE / f"{code}.json")
        if old_roster:
            names = [p.get("name", "") for p in old_roster.get("players", [])]
            print("DEBUG ARI TEST FOUND:", any("TEST" in n for n in names))
        else:
            print("DEBUG ARI OLD ROSTER: None")

    changes = diff_rosters(old_roster, roster)

            save_json(STATE / f"{code}.json", roster)
            save_json(LATEST / f"{code}.json", roster)

            append_team_log(code, changes)
            all_changes.extend(changes)

            statuses.append({
                "teamCode": code,
                "teamName": team_name,
                "checkedAt": roster["checkedAt"],
                "playerCount": len(roster["players"]),
                "lastChangeCount": len(changes),
                "lastError": "",
                "source": "NFL.com",
            })

            print(f"{code}: {len(roster['players'])} players, {len(changes)} changes")

        except Exception as e:
            statuses.append({
                "teamCode": code,
                "teamName": team_name,
                "checkedAt": now_iso(),
                "playerCount": 0,
                "lastChangeCount": 0,
                "lastError": str(e),
                "source": "NFL.com",
            })
            print(f"{code}: ERROR {e}")

        time.sleep(1.5)

    append_all_log(all_changes)
    
    for change in all_changes:
        append_team_log(change["teamCode"], [change])

        team_log_path = LOGS / f"{team_code}.json"

        existing_team_log = []
        if team_log_path.exists():
            existing_team_log = json.loads(
                team_log_path.read_text(encoding="utf-8")
            )

        existing_team_log.insert(0, change)

        team_log_path.write_text(
            json.dumps(existing_team_log, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

    summary = {
        "lastRunFinished": now_iso(),
        "teamsChecked": len(TEAMS),
        "teamsOk": sum(1 for s in statuses if not s["lastError"]),
        "teamsErrored": sum(1 for s in statuses if s["lastError"]),
        "totalChanges": len(all_changes),
        "source": "NFL.com official roster only",
    }

    save_json(DATA / "status.json", statuses)
    save_json(DATA / "summary.json", summary)

    print("Done:", summary)

if __name__ == "__main__":
    run()
