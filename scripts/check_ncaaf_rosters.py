import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

import roster_safety


ROOT = Path(__file__).resolve().parents[1]

DATA = ROOT / "data" / "ncaaf"
LATEST = DATA / "latest"
STATE = DATA / "state"
LOGS = ROOT / "logs" / "ncaaf"

TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams?limit=500"
FBS_TEAM_NAMES = {
    "Boston College Eagles","California Golden Bears","Clemson Tigers","Duke Blue Devils","Florida State Seminoles","Georgia Tech Yellow Jackets","Louisville Cardinals","Miami Hurricanes","NC State Wolfpack","North Carolina Tar Heels","Pittsburgh Panthers","SMU Mustangs","Stanford Cardinal","Syracuse Orange","Virginia Cavaliers","Virginia Tech Hokies","Wake Forest Demon Deacons",
    "Army Black Knights","Charlotte 49ers","East Carolina Pirates","Florida Atlantic Owls","Memphis Tigers","Navy Midshipmen","North Texas Mean Green","Rice Owls","South Florida Bulls","Temple Owls","Tulane Green Wave","Tulsa Golden Hurricane","UAB Blazers","UTSA Roadrunners",
    "Arizona State Sun Devils","Arizona Wildcats","BYU Cougars","Baylor Bears","Cincinnati Bearcats","Colorado Buffaloes","Houston Cougars","Iowa State Cyclones","Kansas Jayhawks","Kansas State Wildcats","Oklahoma State Cowboys","TCU Horned Frogs","Texas Tech Red Raiders","UCF Knights","Utah Utes","West Virginia Mountaineers",
    "Illinois Fighting Illini","Indiana Hoosiers","Iowa Hawkeyes","Maryland Terrapins","Michigan State Spartans","Michigan Wolverines","Minnesota Golden Gophers","Nebraska Cornhuskers","Northwestern Wildcats","Ohio State Buckeyes","Oregon Ducks","Penn State Nittany Lions","Purdue Boilermakers","Rutgers Scarlet Knights","UCLA Bruins","USC Trojans","Washington Huskies","Wisconsin Badgers",
    "Delaware Blue Hens","Florida International Panthers","Jacksonville State Gamecocks","Kennesaw State Owls","Liberty Flames","Louisiana Tech Bulldogs","Middle Tennessee Blue Raiders","Missouri State Bears","New Mexico State Aggies","Sam Houston Bearkats","Western Kentucky Hilltoppers",
    "Notre Dame Fighting Irish","UConn Huskies",
    "Akron Zips","Ball State Cardinals","Bowling Green Falcons","Buffalo Bulls","Central Michigan Chippewas","Eastern Michigan Eagles","Kent State Golden Flashes","Massachusetts Minutemen","Miami (OH) RedHawks","Ohio Bobcats","Sacramento State Hornets","Toledo Rockets","Western Michigan Broncos",
    "Air Force Falcons","Hawai'i Rainbow Warriors","Nevada Wolf Pack","New Mexico Lobos","North Dakota State Bison","Northern Illinois Huskies","San José State Spartans","UNLV Rebels","UTEP Miners","Wyoming Cowboys",
    "Boise State Broncos","Colorado State Rams","Fresno State Bulldogs","Oregon State Beavers","San Diego State Aztecs","Texas State Bobcats","Utah State Aggies","Washington State Cougars",
    "Alabama Crimson Tide","Arkansas Razorbacks","Auburn Tigers","Florida Gators","Georgia Bulldogs","Kentucky Wildcats","LSU Tigers","Mississippi State Bulldogs","Missouri Tigers","Oklahoma Sooners","Ole Miss Rebels","South Carolina Gamecocks","Tennessee Volunteers","Texas A&M Aggies","Texas Longhorns","Vanderbilt Commodores",
    "App State Mountaineers","Arkansas State Red Wolves","Coastal Carolina Chanticleers","Georgia Southern Eagles","Georgia State Panthers","James Madison Dukes","Louisiana Ragin' Cajuns","Marshall Thundering Herd","Old Dominion Monarchs","South Alabama Jaguars","Southern Miss Golden Eagles","Troy Trojans","UL Monroe Warhawks","Tulane Green Wave",
    "SMU Mustangs",
    "Sam Houston Bearkats",
    "UTEP Miners",
    "UTSA Roadrunners",
    "USC Trojans",
    "Massachusetts Minutemen",
    "UMass Minutemen",
}
FBS_TEAMS_FILE = DATA / "fbs_teams.json"

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


def get_json(url):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


# Team-list integrity limits (data/ncaaf/fbs_teams.json is checked before anything is written).
MIN_TEAMS = 120          # FBS has ~136; the 2026 list has 138
MAX_DROPPED_TEAMS = 2    # previously monitored teams (status.json) that may leave in one run


class TeamListError(RuntimeError):
    """fbs_teams.json failed validation; the run stops before any file is written."""


def saved_team_identities():
    """ESPN team id <-> code pairs recorded in the saved roster files (state/ and latest/).

    Returns (codes_by_id, ids_by_code). Files without a teamId are ignored."""
    codes_by_id, ids_by_code = {}, {}
    for folder in (STATE, LATEST):
        if not folder.exists():
            continue
        for path in folder.glob("*.json"):
            try:
                data = load_json(path) or {}
            except (OSError, ValueError) as error:
                raise TeamListError(f"cannot read {path.relative_to(ROOT)} to check team identities: {error}; nothing was written")
            team_id = str(data.get("teamId") or "").strip() if isinstance(data, dict) else ""
            if team_id:
                codes_by_id.setdefault(team_id, set()).add(path.stem)
                ids_by_code.setdefault(path.stem, set()).add(team_id)
    return codes_by_id, ids_by_code


def previous_team_codes():
    """Team codes of the last completed run (data/ncaaf/status.json), or None if there is none."""
    path = DATA / "status.json"
    if not path.exists():
        return None
    try:
        rows = load_json(path)
    except (OSError, ValueError) as error:
        raise TeamListError(f"cannot read data/ncaaf/status.json to compare team lists: {error}; nothing was written")
    if not isinstance(rows, list):
        raise TeamListError("data/ncaaf/status.json is not a list; cannot compare team lists; nothing was written")
    return {safe_code(row.get("teamCode")) for row in rows if isinstance(row, dict) and row.get("teamCode")}


def validate_team_list(teams_data, previous_codes, codes_by_id, ids_by_code, min_teams=None, max_dropped=None):
    """Check fbs_teams.json content; returns the teams to process or raises TeamListError listing
    every problem found. Never writes anything."""
    min_teams = MIN_TEAMS if min_teams is None else min_teams
    max_dropped = MAX_DROPPED_TEAMS if max_dropped is None else max_dropped
    if not isinstance(teams_data, list):
        raise TeamListError("data/ncaaf/fbs_teams.json must be a JSON list of teams; nothing was written")

    problems, teams = [], []
    for index, entry in enumerate(teams_data):
        if not isinstance(entry, dict):
            problems.append(f"entry #{index} is not an object: {entry!r}")
            continue
        team_id = str(entry.get("id") if entry.get("id") is not None else "").strip()
        raw_code = str(entry.get("code") if entry.get("code") is not None else "").strip()
        code = safe_code(raw_code)
        name = str(entry.get("name") if entry.get("name") is not None else "").strip()
        missing = [field for field, value in (("id", team_id), ("code", code), ("name", name)) if not value]
        if missing:
            problems.append(f"entry #{index} {entry!r} has no {'/'.join(missing)}")
            continue
        teams.append({
            "index": index, "raw_code": raw_code, "id": team_id, "code": code, "name": name,
            "logo": str(entry.get("logo") or "").strip() or f"https://a.espncdn.com/i/teamlogos/ncaa/500/{team_id}.png",
        })

    def describe(team):
        return f"#{team['index']} {team['raw_code']}/{team['id']} '{team['name']}'"

    for key, label in (("id", "ESPN id"), ("code", "file code (after filename normalization)")):
        groups = {}
        for team in teams:
            groups.setdefault(team[key], []).append(team)
        for value, group in groups.items():
            if len(group) > 1:
                problems.append(f"duplicate {label} {value}: " + ", ".join(describe(t) for t in group))

    if len(teams) < min_teams:
        problems.append(f"only {len(teams)} valid teams; at least {min_teams} are required")

    if previous_codes:
        dropped = sorted(previous_codes - {team["code"] for team in teams})
        if len(dropped) > max_dropped:
            problems.append(f"{len(dropped)} teams monitored in the last run are missing (more than {max_dropped} "
                            f"allowed per run): {', '.join(dropped)}")

    for team in teams:
        saved_codes = codes_by_id.get(team["id"], set())
        if saved_codes and team["code"] not in saved_codes:
            problems.append(f"{describe(team)}: the saved roster for ESPN id {team['id']} uses code "
                            f"{', '.join(sorted(saved_codes))}; a different code would split its history")
        saved_ids = ids_by_code.get(team["code"], set())
        if saved_ids and team["id"] not in saved_ids:
            problems.append(f"{describe(team)}: the saved roster under code {team['code']} belongs to ESPN id "
                            f"{', '.join(sorted(saved_ids))}; this entry would overwrite another team's roster")

    if problems:
        raise TeamListError("data/ncaaf/fbs_teams.json failed validation; nothing was written.\n  - "
                            + "\n  - ".join(problems))

    return sorted(({key: team[key] for key in ("id", "code", "name", "logo")} for team in teams),
                  key=lambda t: t["name"])


def fetch_teams():
    """The teams to process this run, from data/ncaaf/fbs_teams.json after validation."""
    try:
        teams_data = load_json(FBS_TEAMS_FILE)
    except (OSError, ValueError) as error:
        raise TeamListError(f"cannot read data/ncaaf/fbs_teams.json: {error}; nothing was written")
    if teams_data is None:
        raise TeamListError("data/ncaaf/fbs_teams.json is missing; nothing was written")
    codes_by_id, ids_by_code = saved_team_identities()
    return validate_team_list(teams_data, previous_team_codes(), codes_by_id, ids_by_code)


def roster_url(team_id, page=1):
    return (
        "https://site.api.espn.com/apis/site/v2/sports/"
        f"football/college-football/teams/{team_id}/roster?limit=100&page={page}"
    )


def core_team_athletes_url(team_id, page=1):
    return (
        "https://sports.core.api.espn.com/v2/sports/football/leagues/"
        f"college-football/seasons/2026/teams/{team_id}/athletes"
        f"?limit=300&page={page}"
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
        seen[player["name"].lower()] = player

    return sorted(seen.values(), key=lambda p: p["name"])


def player_from_core_item(item):
    if not isinstance(item, dict):
        return None

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
        url = core_team_athletes_url(team["id"], page)
        data = get_json(url)

        items = data.get("items", [])

        if not items:
            break

        for item in items:
            player = player_from_core_item(item)
            if player:
                all_players.append(player)

        if len(items) < 300:
            break

    by_name = {}
    for player in all_players:
        by_name[player["name"].lower()] = player

    return sorted(by_name.values(), key=lambda p: p["name"])


def fetch_site_roster(team):
    all_players = []
    last_data = None

    for page in range(1, 6):
        url = roster_url(team["id"], page)
        data = get_json(url)

        last_data = data
        page_players = extract_players(data)

        if not page_players:
            break

        all_players.extend(page_players)

        if len(page_players) < 100:
            break

    by_name = {}
    for player in all_players:
        by_name[player["name"].lower()] = player

    return sorted(by_name.values(), key=lambda p: p["name"]), last_data or {}


def fetch_roster(team):
    site_players = []
    site_data = {}
    site_error = None

    try:
        site_players, site_data = fetch_site_roster(team)
    except Exception as error:
        site_error = error
        print(f"{team['code']}: site roster failed: {error}")

    core_players = []
    core_error = None

    try:
        core_players = fetch_core_roster(team)
    except Exception as error:
        core_error = error
        print(f"{team['code']}: core roster failed: {error}")

    # Both ESPN APIs failing is a fetch failure, not an empty roster.
    if site_error is not None and core_error is not None:
        raise RuntimeError(f"Both ESPN roster APIs failed. Site API: {site_error} | Core API: {core_error}")

    if len(core_players) > len(site_players):
        players = core_players
        source = "ESPN College Football Core API"
        url = core_team_athletes_url(team["id"], 1)
    else:
        players = site_players
        source = "ESPN College Football API"
        url = roster_url(team["id"], 1)

    team_name = site_data.get("team", {}).get("displayName") or team["name"]

    return {
        "code": team["code"],
        "team": team_name,
        "teamId": team["id"],
        "logo": team.get("logo", ""),
        "source": source,
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
        "source": roster.get("source", "ESPN College Football API"),
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
    # Validate the team list before anything is written (raises TeamListError: no file touched).
    teams = fetch_teams()
    print(f"Team list valid: {len(teams)} teams, unique ESPN ids and file codes")

    ensure_dirs()
    # fbs_teams.json only decides which teams are processed in this run. Files of teams that are
    # not in it (roster, state, team log, all_changes.json entries) are left untouched: a team that
    # returns later is compared against its preserved state, and history is never deleted.

    statuses = []
    all_changes = []

    print(f"Discovered {len(teams)} FBS NCAAF teams from ESPN")

    for team in teams:
        code = team["code"]

        outcome = roster_safety.guarded_update(
            league="ncaaf",
            code=code,
            fetch=lambda: fetch_roster(team),
            diff=diff_rosters,
            state_dir=STATE,
            latest_dir=LATEST,
            team_log=LOGS / f"{code}.json",
            log_changes=lambda changes: append_team_log(code, changes),
        )
        roster = outcome["roster"]
        changes = outcome["changes"]

        if changes:
            all_changes.extend(changes)

        team_name = (roster or {}).get("team") or team["name"]
        source = (roster or {}).get("source") or "ESPN College Football API"
        statuses.append({
            "teamCode": code,
            "teamName": team_name,
            "teamId": team["id"],
            "logo": team.get("logo", ""),
            **roster_safety.status_fields(outcome),
            "source": source,
        })
        roster_safety.record_issue(DATA, code=code, team_name=team_name, outcome=outcome, source=source)

        if outcome["accepted"]:
            print(f"{code}: {len(roster['players'])} players, {len(changes)} changes, {source}")
        else:
            print(f"{code}: {outcome['kind'].upper()} {outcome['message']}")

        time.sleep(0.25)

    append_all_log(all_changes)

    summary = {
        "lastRunFinished": now_iso(),
        "teamsChecked": len(teams),
        **roster_safety.summary_counts(statuses),
        "totalChanges": len(all_changes),
        "source": "ESPN College Football API / Core API",
    }

    save_json(DATA / "status.json", statuses)
    save_json(DATA / "summary.json", summary)

    print("Done:", summary)


if __name__ == "__main__":
    try:
        run()
    except TeamListError as error:
        print(f"NCAAF RUN ABORTED: {error}")
        raise SystemExit(1)
