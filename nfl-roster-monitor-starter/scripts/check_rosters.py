import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LATEST = DATA / "latest"
TEAM_LOGS = DATA / "team-logs"
STATUS_FILE = DATA / "status.json"
ALL_ACTIVITY = DATA / "all-activity.csv"

TEAMS = [
    ("ARI", "Arizona Cardinals", "arizona-cardinals", "ari"),
    ("ATL", "Atlanta Falcons", "atlanta-falcons", "atl"),
    ("BAL", "Baltimore Ravens", "baltimore-ravens", "bal"),
    ("BUF", "Buffalo Bills", "buffalo-bills", "buf"),
    ("CAR", "Carolina Panthers", "carolina-panthers", "car"),
    ("CHI", "Chicago Bears", "chicago-bears", "chi"),
    ("CIN", "Cincinnati Bengals", "cincinnati-bengals", "cin"),
    ("CLE", "Cleveland Browns", "cleveland-browns", "cle"),
    ("DAL", "Dallas Cowboys", "dallas-cowboys", "dal"),
    ("DEN", "Denver Broncos", "denver-broncos", "den"),
    ("DET", "Detroit Lions", "detroit-lions", "det"),
    ("GB", "Green Bay Packers", "green-bay-packers", "gb"),
    ("HOU", "Houston Texans", "houston-texans", "hou"),
    ("IND", "Indianapolis Colts", "indianapolis-colts", "ind"),
    ("JAX", "Jacksonville Jaguars", "jacksonville-jaguars", "jax"),
    ("KC", "Kansas City Chiefs", "kansas-city-chiefs", "kc"),
    ("LV", "Las Vegas Raiders", "las-vegas-raiders", "lv"),
    ("LAC", "Los Angeles Chargers", "los-angeles-chargers", "lac"),
    ("LAR", "Los Angeles Rams", "los-angeles-rams", "lar"),
    ("MIA", "Miami Dolphins", "miami-dolphins", "mia"),
    ("MIN", "Minnesota Vikings", "minnesota-vikings", "min"),
    ("NE", "New England Patriots", "new-england-patriots", "ne"),
    ("NO", "New Orleans Saints", "new-orleans-saints", "no"),
    ("NYG", "New York Giants", "new-york-giants", "nyg"),
    ("NYJ", "New York Jets", "new-york-jets", "nyj"),
    ("PHI", "Philadelphia Eagles", "philadelphia-eagles", "phi"),
    ("PIT", "Pittsburgh Steelers", "pittsburgh-steelers", "pit"),
    ("SF", "San Francisco 49ers", "san-francisco-49ers", "sf"),
    ("SEA", "Seattle Seahawks", "seattle-seahawks", "sea"),
    ("TB", "Tampa Bay Buccaneers", "tampa-bay-buccaneers", "tb"),
    ("TEN", "Tennessee Titans", "tennessee-titans", "ten"),
    ("WAS", "Washington Commanders", "washington-commanders", "wsh"),
]

IMPORTANT_POSITIONS = {"QB", "RB", "WR", "TE", "K", "PK", "CB", "DB", "S", "FS", "SS", "DE", "EDGE", "OT", "LT"}
POSITIONS_RE = r"QB|RB|FB|WR|TE|C|G|OG|OT|OL|DE|DT|DL|LB|OLB|MLB|CB|S|FS|SS|DB|PK|K|P|LS|EDGE"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; RosterActivityMonitor/1.0)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def official_url(slug: str) -> str:
    return f"https://www.nfl.com/teams/{slug}/roster"


def espn_url(espn: str) -> str:
    return f"https://www.espn.com/nfl/team/roster/_/name/{espn}"


def fetch_html(url: str, attempts: int = 2) -> str:
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            if len(response.text) < 500:
                raise ValueError("response too short")
            return response.text
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 + attempt)
    raise RuntimeError(f"failed to fetch {url}: {last_error}")


def make_player(name: str, position: str, status: str, source_status: str) -> dict:
    position = "K" if position == "PK" else position
    return {
        "name": clean(name),
        "position": position,
        "status": status,
        "sourceStatus": source_status,
        "important": position in IMPORTANT_POSITIONS,
    }


def parse_official(html: str) -> List[dict]:
    soup = BeautifulSoup(html, "lxml")
    text = clean(soup.get_text(" "))
    players: Dict[str, dict] = {}

    pattern = re.compile(rf"\b([A-Z][A-Za-z.'-]+(?: [A-Z][A-Za-z.'-]+){{1,3}})\s+(?:#?\d{{1,2}}\s+)?({POSITIONS_RE})\b")
    for name, position in pattern.findall(text):
        name = clean(name)
        key = name.lower()
        if len(name) > 45 or key in players:
            continue
        if any(skip in name.lower() for skip in ["official", "roster", "tickets", "news"]):
            continue
        players[key] = make_player(name, position, "LISTED", "official")

    return sorted(players.values(), key=lambda p: p["name"])


def parse_espn(html: str) -> List[dict]:
    soup = BeautifulSoup(html, "lxml")
    text = clean(soup.get_text(" "))
    players: Dict[str, dict] = {}

    pattern = re.compile(rf"\b([A-Z][A-Za-z.'-]+(?: [A-Z][A-Za-z.'-]+){{1,3}})\s+({POSITIONS_RE})\s+\d{{1,2}}\b")
    for name, position in pattern.findall(text):
        name = clean(name)
        key = name.lower()
        if len(name) > 45 or key in players:
            continue
        players[key] = make_player(name, position, "LISTED", "espn")

    return sorted(players.values(), key=lambda p: p["name"])


def merge_rosters(official_players: List[dict], espn_players: List[dict]) -> List[dict]:
    official = {p["name"].lower(): p for p in official_players}
    espn = {p["name"].lower(): p for p in espn_players}
    names = sorted(set(official) | set(espn))
    merged = []
    for key in names:
        o = official.get(key)
        e = espn.get(key)
        base = dict(o or e)
        if o and e:
            base["status"] = "CONFIRMED"
            base["sourceStatus"] = "confirmed-both"
        elif o:
            base["status"] = "OFFICIAL_ONLY"
            base["sourceStatus"] = "official-only"
        else:
            base["status"] = "ESPN_ONLY"
            base["sourceStatus"] = "espn-only"
        base["important"] = bool((o and o.get("important")) or (e and e.get("important")))
        merged.append(base)
    return merged


def load_snapshot(team_code: str) -> Optional[dict]:
    path = LATEST / f"{team_code}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_snapshot(team_code: str, snapshot: dict) -> None:
    LATEST.mkdir(parents=True, exist_ok=True)
    (LATEST / f"{team_code}.json").write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")


def player_map(snapshot: dict) -> Dict[str, dict]:
    return {p["name"].lower(): p for p in snapshot.get("players", [])}


def score_change(change: dict) -> str:
    score = 0
    pos = change.get("position", "")
    text = f"{change.get('type','')} {change.get('oldValue','')} {change.get('newValue','')}".lower()
    if pos == "QB":
        score += 50
    if pos in {"RB", "WR", "TE", "K"}:
        score += 25
    if pos in {"CB", "DB", "S", "FS", "SS", "DE", "EDGE", "OT"}:
        score += 15
    if change["type"] == "removed":
        score += 45
    if change["type"] == "added":
        score += 15
    if change["type"] == "status changed":
        score += 35
    if re.search(r"ir|pup|nfi|sus|res|out|inj", text):
        score += 40
    if score >= 80:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def make_change(snapshot: dict, change_type: str, player: dict, old_value: str, new_value: str) -> dict:
    change = {
        "timestamp": now_iso(),
        "teamCode": snapshot["code"],
        "teamName": snapshot["team"],
        "severity": "low",
        "type": change_type,
        "playerName": player.get("name", ""),
        "position": player.get("position", ""),
        "oldValue": old_value or "",
        "newValue": new_value or "",
        "sourceStatus": player.get("sourceStatus", ""),
    }
    change["severity"] = score_change(change)
    return change


def diff_snapshots(old: Optional[dict], new: dict) -> List[dict]:
    if old is None:
        return []
    changes: List[dict] = []
    old_map = player_map(old)
    new_map = player_map(new)

    for key, new_player in new_map.items():
        old_player = old_map.get(key)
        if old_player is None:
            changes.append(make_change(new, "added", new_player, "", new_player.get("status", "")))
            continue
        for field, label in [("status", "status changed"), ("position", "position changed"), ("sourceStatus", "source confirmation changed")]:
            if old_player.get(field, "") != new_player.get(field, ""):
                changes.append(make_change(new, label, new_player, old_player.get(field, ""), new_player.get(field, "")))

    for key, old_player in old_map.items():
        if key not in new_map:
            changes.append(make_change(old, "removed", old_player, old_player.get("status", ""), ""))

    return changes


def append_csv(path: Path, fieldnames: List[str], rows: List[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def append_changes(changes: List[dict]) -> None:
    fields = ["timestamp", "teamCode", "teamName", "severity", "type", "playerName", "position", "oldValue", "newValue", "sourceStatus"]
    append_csv(ALL_ACTIVITY, fields, changes)
    by_team: Dict[str, List[dict]] = {}
    for change in changes:
        by_team.setdefault(change["teamCode"], []).append(change)
    for team_code, rows in by_team.items():
        append_csv(TEAM_LOGS / f"{team_code}.csv", fields, rows)


def read_status() -> dict:
    if STATUS_FILE.exists():
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    return {}


def save_status(status: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")


def check_team(team_code: str, name: str, slug: str, espn: str) -> dict:
    errors = []
    official_players: List[dict] = []
    espn_players: List[dict] = []

    try:
        official_players = parse_official(fetch_html(official_url(slug)))
        if not official_players:
            raise ValueError("no official players parsed")
    except Exception as exc:
        errors.append(f"official: {exc}")

    try:
        espn_players = parse_espn(fetch_html(espn_url(espn)))
        if not espn_players:
            raise ValueError("no ESPN players parsed")
    except Exception as exc:
        errors.append(f"espn: {exc}")

    if not official_players and not espn_players:
        return {"teamCode": team_code, "teamName": name, "ok": False, "error": " | ".join(errors), "checkedAt": now_iso()}

    players = merge_rosters(official_players, espn_players)
    snapshot = {
        "team": name,
        "code": team_code,
        "checkedAt": now_iso(),
        "source": "NFL.com + ESPN",
        "officialPlayerCount": len(official_players),
        "espnPlayerCount": len(espn_players),
        "errors": errors,
        "players": players,
    }
    old = load_snapshot(team_code)
    changes = diff_snapshots(old, snapshot)
    save_snapshot(team_code, snapshot)
    append_changes(changes)

    return {
        "teamCode": team_code,
        "teamName": name,
        "ok": True,
        "checkedAt": snapshot["checkedAt"],
        "playerCount": len(players),
        "officialPlayerCount": len(official_players),
        "espnPlayerCount": len(espn_players),
        "changeCount": len(changes),
        "errors": errors,
        "baselineCreated": old is None,
    }


def main() -> None:
    DATA.mkdir(exist_ok=True)
    LATEST.mkdir(parents=True, exist_ok=True)
    TEAM_LOGS.mkdir(parents=True, exist_ok=True)

    status = read_status()
    run_started = now_iso()
    results = []

    for team in TEAMS:
        result = check_team(*team)
        results.append(result)
        status[result["teamCode"]] = result
        save_status(status)
        time.sleep(1.0)

    summary = {
        "lastRunStarted": run_started,
        "lastRunFinished": now_iso(),
        "teamsChecked": len(results),
        "teamsOk": sum(1 for r in results if r.get("ok")),
        "teamsErrored": sum(1 for r in results if not r.get("ok")),
        "totalChanges": sum(r.get("changeCount", 0) for r in results),
    }
    (DATA / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
