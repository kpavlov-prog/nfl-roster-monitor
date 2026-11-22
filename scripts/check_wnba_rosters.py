import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

import roster_safety


ROOT = Path(__file__).resolve().parents[1]

DATA = ROOT / "data" / "wnba"
LATEST = DATA / "latest"
STATE = DATA / "state"
LOGS = ROOT / "logs" / "wnba"

# Teams monitored now. Connecticut Sun (CON) is not here any more: the franchise moved to Houston
# for 2027 (see FRANCHISE_TRANSITIONS). Its files - data/wnba/latest|state/CON.json and
# logs/wnba/CON.json - are kept unchanged as history and are no longer written.
TEAMS = [
    {"code": "ATL", "name": "Atlanta Dream"},
    {"code": "CHI", "name": "Chicago Sky"},
    {"code": "DAL", "name": "Dallas Wings"},
    {"code": "GS", "name": "Golden State Valkyries"},
    {"code": "HOU", "name": "Houston Comets"},
    {"code": "IND", "name": "Indiana Fever"},
    {"code": "LV", "name": "Las Vegas Aces"},
    {"code": "LA", "name": "Los Angeles Sparks"},
    {"code": "MIN", "name": "Minnesota Lynx"},
    {"code": "NY", "name": "New York Liberty"},
    {"code": "PHX", "name": "Phoenix Mercury"},
    {"code": "POR", "name": "Portland Fire"},
    {"code": "SEA", "name": "Seattle Storm"},
    {"code": "TOR", "name": "Toronto Tempo"},
    {"code": "WSH", "name": "Washington Mystics"},
]

# Relocated franchises: same franchise, new team code. ESPN gives the new team a different id
# (verified 2026-11-21: Connecticut Sun = id 18 / CON, isActive false for 2027 and its roster
# endpoint returns no athletes; Houston Comets = id 4 / HOU, active, 14 athletes - the same 14
# players, positions and numbers as the last good CON roster), so the lineage is recorded here.
# On the first run with the successor active and no saved successor roster yet, the predecessor's
# last good roster becomes the successor's baseline: the inherited players are not logged as
# "added", the predecessor's own roster is not logged as "removed", and the usual safety guard
# (empty / partial responses) protects the successor from its very first fetch.
FRANCHISE_TRANSITIONS = [
    {
        "from": {"code": "CON", "name": "Connecticut Sun", "espnId": "18"},
        "to": {"code": "HOU", "name": "Houston Comets", "espnId": "4"},
        "effectiveSeason": 2027,
    },
]

FRANCHISE_TRANSITION = "franchise_transition"


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


def roster_url(team_code):
    return f"https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/teams/{team_code}/roster"


def fetch_roster(team):
    url = roster_url(team["code"])

    response = requests.get(url, timeout=25)
    response.raise_for_status()

    data = response.json()

    athletes = data.get("athletes", [])
    players = []

    for athlete in athletes:
        position = athlete.get("position") or {}

        players.append({
            "name": athlete.get("displayName") or athlete.get("fullName") or "",
            "position": position.get("abbreviation", ""),
            "number": str(athlete.get("jersey") or ""),
            "status": "ACTIVE",
            "source": "ESPN WNBA API",
        })

    players = sorted(players, key=lambda p: p["name"])

    team_name = (
        data.get("team", {}).get("displayName")
        or team["name"]
    )

    return {
        "code": team["code"],
        "team": team_name,
        "source": "ESPN WNBA API",
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
        "source": "ESPN WNBA API",
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


def validate_teams(teams, transitions):
    """Team codes must be unique, and a relocated franchise must never be monitored under both its
    old and its new code (that would show the same franchise twice)."""
    codes = [team["code"] for team in teams]
    duplicates = sorted({code for code in codes if codes.count(code) > 1})
    if duplicates:
        raise ValueError(f"duplicate WNBA team codes: {', '.join(duplicates)}")
    for transition in transitions:
        old, new = transition["from"]["code"], transition["to"]["code"]
        if old in codes and new in codes:
            raise ValueError(f"{old} and {new} are the same franchise ({transition['from']['name']} -> "
                             f"{transition['to']['name']}, {transition['effectiveSeason']}); monitor only {new}")


def migrate_franchise_transitions(teams, transitions):
    """Seed each active successor's baseline from its predecessor's last good roster, once.

    Writes only the successor's state/latest files and an audit entry in source_issues.json; the
    predecessor's files are only read. Returns {successor code: problem message} for successors
    whose migration could not be done safely - those are not fetched this run, because a first
    fetch without a baseline would be accepted even if it were partial."""
    active = {team["code"] for team in teams}
    blocked = {}
    for transition in transitions:
        old, new = transition["from"], transition["to"]
        if new["code"] not in active:
            continue
        successor_state = STATE / f"{new['code']}.json"
        try:
            if successor_state.exists():
                continue  # already migrated (or the successor has its own saved roster)
            predecessor = load_json(STATE / f"{old['code']}.json")
        except Exception as error:
            blocked[new["code"]] = f"Processing error: franchise transition from {old['code']} not done: {error}"
            continue
        players = (predecessor or {}).get("players") or []
        if not players:
            # No usable predecessor roster: nothing to inherit. The successor starts like any new
            # team; the guard still rejects an empty response.
            continue
        try:
            baseline = {
                **predecessor,
                "code": new["code"],
                "team": new["name"],
                "url": roster_url(new["code"]),
                "players": players,
                "inheritedFrom": {
                    "code": old["code"],
                    "team": old["name"],
                    "checkedAt": predecessor.get("checkedAt", ""),
                    "effectiveSeason": transition["effectiveSeason"],
                },
            }
            latest = LATEST / f"{new['code']}.json"
            if not latest.exists():
                save_json(latest, baseline)
            save_json(successor_state, baseline)
        except Exception as error:
            blocked[new["code"]] = f"Processing error: franchise transition from {old['code']} not done: {error}"
            continue
        entry = {
            "timestamp": now_iso(),
            "teamCode": new["code"],
            "teamName": new["name"],
            "kind": FRANCHISE_TRANSITION,
            "message": (f"Franchise transition: {old['name']} ({old['code']}) became {new['name']} "
                        f"({new['code']}) for the {transition['effectiveSeason']} season. The last good "
                        f"{old['code']} roster ({len(players)} players, checked "
                        f"{predecessor.get('checkedAt', '')[:10]}) is the {new['code']} baseline: nothing "
                        f"logged as added or removed; {old['code']} history left unchanged."),
            "previousCount": len(players),
            "fetchedCount": None,
            "lastSuccessfulCheckedAt": predecessor.get("checkedAt", ""),
            "source": "ESPN WNBA API",
            "fromCode": old["code"],
            "fromTeam": old["name"],
            "toCode": new["code"],
            "effectiveSeason": transition["effectiveSeason"],
        }
        try:
            save_json(DATA / "source_issues.json", [entry] + (load_json(DATA / "source_issues.json") or []))
        except Exception as error:  # the audit trail must never stop the run
            print(f"{new['code']}: WARNING could not record franchise transition ({error})")
        print(f"{new['code']}: franchise transition from {old['code']}, inherited {len(players)} players")
    return blocked


def blocked_outcome(message):
    """Outcome for a team that is not fetched this run (same shape as roster_safety.guarded_update)."""
    return {"accepted": False, "kind": roster_safety.PROCESSING_ERROR, "message": message, "roster": None,
            "previous": None, "changes": [], "attemptedAt": now_iso(), "fetchedCount": None,
            "confirmed": False, "recovered": "", "recovery": None}


def run():
    ensure_dirs()
    validate_teams(TEAMS, FRANCHISE_TRANSITIONS)
    blocked = migrate_franchise_transitions(TEAMS, FRANCHISE_TRANSITIONS)

    statuses = []
    all_changes = []

    for team in TEAMS:
        code = team["code"]

        if code in blocked:
            outcome = blocked_outcome(blocked[code])
        else:
            outcome = roster_safety.guarded_update(
                league="wnba",
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
        source = "ESPN WNBA API"
        statuses.append({
            "teamCode": code,
            "teamName": team_name,
            **roster_safety.status_fields(outcome),
            "source": source,
        })
        roster_safety.record_issue(DATA, code=code, team_name=team_name, outcome=outcome, source=source)

        if outcome["accepted"]:
            print(f"{code}: {len(roster['players'])} players, {len(changes)} changes")
        else:
            print(f"{code}: {outcome['kind'].upper()} {outcome['message']}")

        time.sleep(0.5)

    append_all_log(all_changes)

    summary = {
        "lastRunFinished": now_iso(),
        "teamsChecked": len(TEAMS),
        **roster_safety.summary_counts(statuses),
        "totalChanges": len(all_changes),
        "source": "ESPN WNBA API",
    }

    save_json(DATA / "status.json", statuses)
    save_json(DATA / "summary.json", summary)

    print("Done:", summary)


if __name__ == "__main__":
    run()
