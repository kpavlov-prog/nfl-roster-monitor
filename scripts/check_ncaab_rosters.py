"""NCAA Division I men's basketball (NCAAB) roster checker. Source: ESPN only.

Teams: data/ncaab/d1_teams.json (built by build_ncaab_d1_master.py, validated here before anything
is written). Teams that leave the list keep all their files; nothing is deleted.

Rosters, per team:
1. ESPN site roster API (normal path):
     site.api.espn.com/.../mens-college-basketball/teams/{id}/roster
2. Only when that request FAILS (HTTP error, network error, unreadable response): ESPN core API for
   the same season, one request for the athlete list plus one per athlete:
     sports.core.api.espn.com/.../mens-college-basketball/seasons/{SEASON}/teams/{id}/athletes
   The roster records rosterSource "core-fallback" and the reason. Last season's roster is never used.
3. Both failing is a fetch error: the known-good roster is kept and a source issue is recorded.
Whatever the source, the roster passes the shared safety guard (empty / suspicious drop / hold +
second-run confirmation) in roster_safety.py.

Players keep the shared name-based identity (diffs compare lower-cased names). Each player also
stores providerPlayerId (ESPN athlete id) and class; class changes are not logged (every player
would otherwise "change" at the yearly rollover).

After the run, data/ncaab/rosters.json bundles the known-good latest/ rosters of the monitored
teams for the frontend (one request instead of ~365). latest/ stays the source of truth: the bundle
is written last, atomically, and the frontend uses it only when it matches status.json/summary.json.
"""
import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

import roster_safety


ROOT = Path(__file__).resolve().parents[1]

DATA = ROOT / "data" / "ncaab"
LATEST = DATA / "latest"
STATE = DATA / "state"
LOGS = ROOT / "logs" / "ncaab"
TEAMS_FILE = DATA / "d1_teams.json"
BUNDLE_FILE = DATA / "rosters.json"

SEASON = 2027  # ESPN season year of the 2026-27 season
SITE_SOURCE = "ESPN Men's College Basketball API"
CORE_SOURCE = "ESPN Men's College Basketball Core API"

# Team-list integrity limits (d1_teams.json is checked before anything is written).
MIN_TEAMS = 350          # Division I has 365 active teams in 2026-27
MAX_DROPPED_TEAMS = 3    # previously monitored teams (status.json) that may leave in one run
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}

TEAM_PAUSE = 0.3         # seconds between teams (about 2.5 requests/second overall)
ATHLETE_PAUSE = 0.15     # seconds between core-API athlete requests (fallback only)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs():
    for folder in (LATEST, STATE, LOGS):
        folder.mkdir(parents=True, exist_ok=True)


def safe_code(value):
    return re.sub(r"[^A-Z0-9]+", "_", str(value or "").upper()).strip("_")


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


# --- team list ------------------------------------------------------------------------------------

class TeamListError(RuntimeError):
    """d1_teams.json failed validation; the run stops before any file is written."""


def saved_team_identities():
    """ESPN team id <-> code pairs recorded in the saved roster files (state/ and latest/)."""
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
    """Team codes of the last completed run (data/ncaab/status.json), or None if there is none."""
    path = DATA / "status.json"
    if not path.exists():
        return None
    try:
        rows = load_json(path)
    except (OSError, ValueError) as error:
        raise TeamListError(f"cannot read data/ncaab/status.json to compare team lists: {error}; nothing was written")
    if not isinstance(rows, list):
        raise TeamListError("data/ncaab/status.json is not a list; cannot compare team lists; nothing was written")
    return {safe_code(row.get("teamCode")) for row in rows if isinstance(row, dict) and row.get("teamCode")}


def validate_team_list(teams_data, previous_codes, codes_by_id, ids_by_code, min_teams=None, max_dropped=None):
    """Check d1_teams.json content; returns the teams to process or raises TeamListError listing every
    problem found. Never writes anything."""
    min_teams = MIN_TEAMS if min_teams is None else min_teams
    max_dropped = MAX_DROPPED_TEAMS if max_dropped is None else max_dropped
    if not isinstance(teams_data, list):
        raise TeamListError("data/ncaab/d1_teams.json must be a JSON list of teams; nothing was written")

    problems, teams = [], []
    for index, entry in enumerate(teams_data):
        if not isinstance(entry, dict):
            problems.append(f"entry #{index} is not an object: {entry!r}")
            continue
        field = lambda key: str(entry.get(key) if entry.get(key) is not None else "").strip()
        team_id, raw_code, name, conference = field("id"), field("code"), field("name"), field("conference")
        code = safe_code(raw_code)
        missing = [k for k, v in (("id", team_id), ("code", code), ("name", name), ("conference", conference)) if not v]
        if missing:
            problems.append(f"entry #{index} {entry!r} has no {'/'.join(missing)}")
            continue
        if code in WINDOWS_RESERVED:
            problems.append(f"entry #{index} code {code} is a reserved Windows file name; choose another code")
            continue
        teams.append({"index": index, "raw_code": raw_code, "id": team_id, "code": code, "name": name,
                      "conference": conference, "conferenceId": field("conferenceId"),
                      "logo": field("logo") or f"https://a.espncdn.com/i/teamlogos/ncaa/500/{team_id}.png"})

    def describe(team):
        return f"#{team['index']} {team['raw_code']}/{team['id']} '{team['name']}'"

    for key, label in (("id", "ESPN id"), ("code", "file code (after filename normalization)")):
        groups = {}
        for team in teams:
            groups.setdefault(team[key].upper(), []).append(team)
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
        raise TeamListError("data/ncaab/d1_teams.json failed validation; nothing was written.\n  - " + "\n  - ".join(problems))

    keys = ("id", "code", "name", "conference", "conferenceId", "logo")
    return sorted(({k: team[k] for k in keys} for team in teams), key=lambda t: t["name"])


def fetch_teams():
    """The teams to process this run, from data/ncaab/d1_teams.json after validation."""
    try:
        teams_data = load_json(TEAMS_FILE)
    except (OSError, ValueError) as error:
        raise TeamListError(f"cannot read data/ncaab/d1_teams.json: {error}; nothing was written")
    if teams_data is None:
        raise TeamListError("data/ncaab/d1_teams.json is missing; nothing was written")
    codes_by_id, ids_by_code = saved_team_identities()
    return validate_team_list(teams_data, previous_team_codes(), codes_by_id, ids_by_code)


# --- rosters --------------------------------------------------------------------------------------

def site_roster_url(team_id):
    return f"https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams/{team_id}/roster"


def core_athletes_url(team_id):
    return (f"https://sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball/"
            f"seasons/{SEASON}/teams/{team_id}/athletes?limit=200")


def player_record(athlete, source):
    """One player in the shared roster format, plus providerPlayerId and class."""
    if not isinstance(athlete, dict):
        return None
    name = str(athlete.get("displayName") or athlete.get("fullName") or "").strip()
    if not name:
        return None
    position = athlete.get("position") or {}
    status = athlete.get("status") or {}
    status_type = str(status.get("type") or status.get("name") or "active").strip() if isinstance(status, dict) else str(status)
    return {
        "name": name,
        "position": (position.get("abbreviation") or position.get("displayName") or "") if isinstance(position, dict) else str(position),
        "number": str(athlete.get("jersey") or ""),
        "status": status_type.upper() or "ACTIVE",
        "class": str((athlete.get("experience") or {}).get("displayValue") or ""),
        "providerPlayerId": str(athlete.get("id") or ""),
        "source": source,
    }


def by_name(players):
    """One entry per (lower-cased) name - the shared identity - sorted by name."""
    seen = {}
    for player in players:
        if player:
            seen[player["name"].lower()] = player
    return sorted(seen.values(), key=lambda p: p["name"])


def fetch_site_players(team):
    data = get_json(site_roster_url(team["id"]))
    athletes = data.get("athletes", [])
    if athletes and isinstance(athletes[0], dict) and "items" in athletes[0]:  # grouped layout, if ESPN ever uses it
        athletes = [a for group in athletes for a in group.get("items", [])]
    return by_name(player_record(a, SITE_SOURCE) for a in athletes), (data.get("team") or {}).get("displayName")


def fetch_core_players(team):
    listing = get_json(core_athletes_url(team["id"]))
    refs = [item["$ref"] for item in listing.get("items", []) if isinstance(item, dict) and item.get("$ref")]
    if int(listing.get("pageCount") or 1) > 1:
        raise RuntimeError(f"core athlete list has {listing.get('pageCount')} pages; only one is supported")
    if not refs:
        return []
    players, unreadable = [], []
    for ref in refs:
        try:
            player = player_record(get_json(ref), CORE_SOURCE)
        except Exception as error:
            player = None
            unreadable.append(f"{ref.split('/athletes/')[-1].split('?')[0]} ({error.__class__.__name__}: {str(error)[:40]})")
        if player:
            players.append(player)
        elif not unreadable or not unreadable[-1].startswith(ref.split('/athletes/')[-1].split('?')[0]):
            unreadable.append(ref.split('/athletes/')[-1].split('?')[0] + " (no name)")
        time.sleep(ATHLETE_PAUSE)
    if unreadable:
        # A roster with athletes ESPN cannot return is incomplete: never accepted as current.
        raise RuntimeError(f"core athlete list names {len(refs)} athletes but {len(unreadable)} could not be read "
                           f"(athlete ids: {', '.join(unreadable)}); incomplete roster not used")
    return by_name(players)


def fetch_roster(team):
    """The current roster: site API, or - only when that request fails - the core API (same season)."""
    roster = {"code": team["code"], "team": team["name"], "teamId": team["id"], "conference": team["conference"],
              "logo": team.get("logo", ""), "checkedAt": "", "players": []}
    try:
        players, site_name = fetch_site_players(team)
        roster.update(team=site_name or team["name"], source=SITE_SOURCE, rosterSource="site", url=site_roster_url(team["id"]))
    except Exception as site_error:
        reason = f"site roster failed: {site_error}"
        print(f"{team['code']}: {reason}; trying ESPN core API fallback")
        try:
            players = fetch_core_players(team)
        except Exception as core_error:
            raise RuntimeError(f"Both ESPN roster APIs failed. Site API: {site_error} | Core API: {core_error}")
        roster.update(source=CORE_SOURCE, rosterSource="core-fallback", fallbackReason=reason, url=core_athletes_url(team["id"]))
        print(f"{team['code']}: core API fallback returned {len(players)} players")
    roster["checkedAt"] = now_iso()
    roster["players"] = players
    return roster


# --- changes --------------------------------------------------------------------------------------

def player_map(roster):
    return {player["name"].lower(): player for player in roster.get("players", [])}


def make_change(roster, change_type, player, old_value="", new_value=""):
    return {
        "timestamp": now_iso(),
        "teamCode": roster["code"],
        "teamName": roster["team"],
        "type": change_type,
        "playerName": player.get("name", ""),
        "position": player.get("position", ""),
        "oldValue": old_value or "",
        "newValue": new_value or "",
        "source": roster.get("source", SITE_SOURCE),
        "severity": "medium" if change_type in ("removed", "status changed") else "low",
    }


def diff_rosters(old_roster, new_roster):
    """Added / removed / status / position / number changes. Class is stored but not compared."""
    if old_roster is None:
        return []
    old_players, new_players = player_map(old_roster), player_map(new_roster)
    changes = []
    for key, new_player in new_players.items():
        old_player = old_players.get(key)
        if old_player is None:
            changes.append(make_change(new_roster, "added", new_player, "", new_player.get("status", "")))
            continue
        for field, label in (("status", "status changed"), ("position", "position changed"), ("number", "number changed")):
            if old_player.get(field, "") != new_player.get(field, ""):
                changes.append(make_change(new_roster, label, new_player, old_player.get(field, ""), new_player.get(field, "")))
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


def history_metadata():
    """Which change logs exist on disk, so the dashboard requests only logs that exist.

    Read from the log files themselves (cumulative; never deleted), not from this run's counts:
    a team that changed on an earlier run keeps its entry on later quiet runs."""
    teams = sorted(p.stem for p in LOGS.glob("*.json") if p.name != "all_changes.json") if LOGS.exists() else []
    return {"historyTeams": teams, "hasLeagueLog": (LOGS / "all_changes.json").exists()}


# --- bundle ---------------------------------------------------------------------------------------

def build_bundle(statuses, generated_at):
    """The known-good latest/ roster of every monitored team (status.json order is by name; the bundle
    is sorted by code), deterministic for the same inputs. Teams without a saved roster are listed
    with players: null, never invented."""
    teams = []
    for row in sorted(statuses, key=lambda r: r["teamCode"]):
        latest = load_json(LATEST / f"{row['teamCode']}.json")
        teams.append({
            "teamCode": row["teamCode"],
            "teamName": row["teamName"],
            "teamId": row.get("teamId", ""),
            "conference": row.get("conference", ""),
            "logo": row.get("logo", ""),
            "checkedAt": (latest or {}).get("checkedAt", ""),
            "source": (latest or {}).get("source", ""),
            "rosterSource": (latest or {}).get("rosterSource", ""),
            "sourceIssue": row.get("sourceIssue", ""),
            "players": (latest or {}).get("players") if latest else None,
        })
    return {"league": "ncaab", "generatedAt": generated_at, "teamCount": len(teams),
            "playerCount": sum(len(t["players"] or []) for t in teams), "teams": teams}


def write_bundle(bundle):
    """Atomic write: the old bundle stays intact unless the new one is complete."""
    BUNDLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(BUNDLE_FILE.parent), prefix=".rosters-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(bundle, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, BUNDLE_FILE)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# --- run ------------------------------------------------------------------------------------------

def run():
    # Validate the team list before anything is written (raises TeamListError: no file touched).
    teams = fetch_teams()
    print(f"Team list valid: {len(teams)} Division I teams, unique ESPN ids and file codes")
    ensure_dirs()
    # d1_teams.json only decides which teams are processed this run. Files of teams not in it are
    # left untouched: a returning team is compared with its preserved state; history is never deleted.

    statuses, all_changes, fallbacks = [], [], []
    for team in teams:
        code = team["code"]
        outcome = roster_safety.guarded_update(
            league="ncaab",
            code=code,
            fetch=lambda: fetch_roster(team),
            diff=diff_rosters,
            state_dir=STATE,
            latest_dir=LATEST,
            team_log=LOGS / f"{code}.json",
            log_changes=lambda changes: append_team_log(code, changes),
        )
        roster, changes = outcome["roster"], outcome["changes"]
        all_changes.extend(changes)
        kept = roster or outcome["previous"] or {}
        source = kept.get("source") or SITE_SOURCE
        statuses.append({
            "teamCode": code,
            "teamName": kept.get("team") or team["name"],
            "teamId": team["id"],
            "conference": team["conference"],
            "logo": team.get("logo", ""),
            **roster_safety.status_fields(outcome),
            "source": source,
            "rosterSource": kept.get("rosterSource", ""),
        })
        if roster and roster.get("rosterSource") == "core-fallback":
            fallbacks.append(code)
        roster_safety.record_issue(DATA, code=code, team_name=statuses[-1]["teamName"], outcome=outcome, source=source)
        if outcome["accepted"]:
            print(f"{code}: {len(roster['players'])} players, {len(changes)} changes, {roster.get('rosterSource')}")
        else:
            print(f"{code}: {outcome['kind'].upper()} {outcome['message']}")
        time.sleep(TEAM_PAUSE)

    append_json_log(LOGS / "all_changes.json", all_changes)
    finished = now_iso()
    summary = {
        "lastRunFinished": finished,
        "teamsChecked": len(teams),
        **roster_safety.summary_counts(statuses),
        "totalChanges": len(all_changes),
        "coreFallbackTeams": fallbacks,
        "source": f"{SITE_SOURCE} (core API fallback when the site roster fails)",
        **history_metadata(),
    }
    save_json(DATA / "status.json", statuses)
    save_json(DATA / "summary.json", summary)
    try:
        write_bundle(build_bundle(statuses, finished))
    except Exception as error:  # the frontend notices the stale bundle and uses the per-team files
        print(f"WARNING: could not write data/ncaab/rosters.json ({error}); per-team files are unaffected")
    print("Done:", summary)


if __name__ == "__main__":
    try:
        run()
    except TeamListError as error:
        print(f"NCAAB RUN ABORTED: {error}")
        raise SystemExit(1)
