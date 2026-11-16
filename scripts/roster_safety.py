"""Roster safety guard shared by all league checkers.

A fetched roster only replaces the last known-good roster when it is believable.

- Fetch failure (exception): keep the previous roster, record a source issue, log no changes.
- Empty roster: treated as a source failure, whether or not a previous roster exists.
- Suspiciously large drop (both limits below reached): hold it. The previous roster stays,
  no removals are logged, and the candidate is saved to data/<league>/state/pending/<TEAM>.json.
  If a later run shows the same drop again (missing-player sets overlap by at least
  CONFIRM_OVERLAP), the new roster is accepted and its changes are tagged confirmedAfterHold;
  removals from that run are marked high severity.
- Any other error while processing a team (unreadable file, unexpected data) only affects that
  team: previous roster kept, recorded as "processing_error"; the league's run continues.
- Every failed or held run is appended to data/<league>/source_issues.json (newest first).
- Recovery from a known-invalid EMPTY saved roster (left by checkers that ran before this guard;
  the guard itself never saves an empty roster): when the saved roster exists but has 0 players,
  a valid non-empty fetch is accepted as a recovered baseline - without logging the whole roster
  as "added" - only if the team's own change log proves the empty state was invalid:
    * logged_wipe: the newest run(s) in the team log contain only "removed" entries, i.e. the
      run(s) that emptied the roster. Their players form the wiped roster:
        - the fetch is compared with it using the league's drop limits; a partial return is held
          as suspicious_drop (roster stays empty) until a second matching fetch confirms it;
        - returning players are not logged again (their removal was false); players NOT on the
          wiped roster are real additions and are logged ("added", afterBaselineRecovery: true);
          status/position differences of returning players are counted in the recovery record;
        - wiped players that did not return keep the removal the wipe already logged.
    * never_populated: the team log exists as a concept but has no entries (file absent or empty),
      i.e. no change was ever recorded for this team, so this is its first real baseline (same as a
      first-ever run: nothing logged).
  Anything else (no saved roster = first-ever run; a non-empty saved roster; an empty roster whose
  log does not fit either pattern or cannot be read; no team log path given) goes through the
  normal comparison. Each recovery is recorded in source_issues.json as kind "baseline_recovered".

Timestamps in status rows: checkedAt / lastSuccessfulCheckedAt = last accepted roster check;
lastAttemptedAt = every attempt. Failed or held runs never advance checkedAt.

Thresholds come from each league's roster-size history (Jun-Nov 2026 runs): every limit sits
above the largest genuine one-run drop seen, e.g. NFL cut-down day 105 -> 73 (30%) or NHL camp
cuts 47 -> 27 (43%).
"""
import json
from datetime import datetime, timezone
from pathlib import Path

# A drop is suspicious only when BOTH limits are reached: the share of the previous roster
# that disappeared, and the number of players that disappeared.
DROP_THRESHOLDS = {
    "nfl":   {"fraction": 0.35, "min_players": 15},
    "nba":   {"fraction": 0.40, "min_players": 6},
    "wnba":  {"fraction": 0.40, "min_players": 5},
    "mlb":   {"fraction": 0.40, "min_players": 8},
    "nhl":   {"fraction": 0.50, "min_players": 10},
    "ncaaf": {"fraction": 0.40, "min_players": 20},
}

# Share of the held missing-player set that must be missing again to accept a held drop
# (Jaccard overlap: |held & now| / |held | now|).
CONFIRM_OVERLAP = 0.90

FETCH_ERROR = "fetch_error"
EMPTY_RESPONSE = "empty_response"
SUSPICIOUS_DROP = "suspicious_drop"
PROCESSING_ERROR = "processing_error"
BASELINE_RECOVERED = "baseline_recovered"

LOGGED_WIPE = "logged_wipe"
NEVER_POPULATED = "never_populated"

# Change entries a checker writes for one team in one run share (almost) the same timestamp;
# separate runs are hours apart (smallest gap in the 2026 logs: ~17.5 h). Entries within this
# many seconds of a run's newest entry belong to that run.
RUN_WINDOW_SECONDS = 300

# Only real additions are logged when a roster returns after a logged wipe. Status and position
# differences of returning players are counted in the recovery record instead of logged: the wipe
# entries may come from a different source format (NCAAF alternates between two ESPN APIs), and
# logging them could recreate the change storm this recovery exists to prevent.
RECOVERY_CHANGE_TYPES = {"added"}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_json(path):
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def name_key(name):
    """Same player identity the checkers use today: lower-cased name."""
    return str(name or "").lower()


def player_keys(roster):
    return {name_key(player.get("name")) for player in (roster or {}).get("players", [])}


def overlap(a, b):
    a, b = set(a), set(b)
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def is_suspicious_drop(league, previous_count, new_count):
    limits = DROP_THRESHOLDS[league]
    dropped = previous_count - new_count
    if previous_count <= 0 or dropped <= 0:
        return False
    return dropped >= limits["min_players"] and dropped / previous_count >= limits["fraction"]


def _parse_time(value):
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def group_runs(team_log):
    """Team-log entries grouped into runs, newest run first. None if any timestamp is unreadable."""
    timed = []
    for entry in team_log:
        when = _parse_time(entry.get("timestamp"))
        if when is None:
            return None
        timed.append((when, entry))
    timed.sort(key=lambda item: item[0], reverse=True)
    runs = []
    for when, entry in timed:
        if runs and (runs[-1]["newest"] - when).total_seconds() <= RUN_WINDOW_SECONDS:
            runs[-1]["entries"].append(entry)
        else:
            runs.append({"newest": when, "entries": [entry]})
    return [run["entries"] for run in runs]


def empty_baseline_evidence(team_log):
    """Why a saved EMPTY roster is known to be invalid, judged from the team's own change log.

    team_log: list of change entries ([] = no change ever recorded), or None = unknown/unreadable.
    Returns (evidence, wipe_entries): evidence is LOGGED_WIPE, NEVER_POPULATED or None (no proof);
    wipe_entries are the "removed" entries of the consecutive newest runs that consist only of
    removals - the run(s) that emptied the roster (LOGGED_WIPE only)."""
    if team_log is None or not isinstance(team_log, list):
        return None, []
    entries = [entry for entry in team_log if isinstance(entry, dict)]
    if len(entries) != len(team_log):
        return None, []
    if not entries:
        return NEVER_POPULATED, []
    runs = group_runs(entries)
    if not runs:
        return None, []
    wipe = []
    for run in runs:
        if not all(entry.get("type") == "removed" for entry in run):
            break
        wipe.extend(run)
    if not wipe:
        return None, []
    return LOGGED_WIPE, wipe


def wiped_roster(wipe_entries, like):
    """The roster just before the logged wipe, rebuilt from its "removed" entries (newest wins)."""
    players = {}
    for entry in reversed(wipe_entries):  # oldest first, so newer entries overwrite older ones
        players[name_key(entry.get("playerName"))] = {
            "name": str(entry.get("playerName") or ""),
            "position": entry.get("position", ""),
            "number": "",
            "status": entry.get("oldValue", ""),
        }
    return {**like, "players": list(players.values())}


def evaluate(league, previous, candidate, pending, team_log=None):
    """Decide what to do with a fetched roster. Returns a dict:
    {"accept": bool, "kind": issue kind or "", "message": str, "confirmed": bool, "missing": [...],
     "recovered": "" or evidence, "wipe_entries": [...], "reference": "previous" | LOGGED_WIPE}"""
    new_players = (candidate or {}).get("players", [])
    previous_players = (previous or {}).get("players", [])
    previous_count, new_count = len(previous_players), len(new_players)
    missing = sorted(player_keys(previous) - player_keys(candidate))
    decision = {"accept": True, "kind": "", "confirmed": False, "missing": missing, "message": "",
                "recovered": "", "wipe_entries": [], "reference": "previous"}

    if new_count == 0:
        kept = f"previous roster kept ({previous_count} players)" if previous_count else "no roster saved"
        return {**decision, "accept": False, "kind": EMPTY_RESPONSE, "missing": [],
                "message": f"Source issue: empty roster returned; {kept}"}

    if previous is not None and previous_count == 0:
        evidence, wipe_entries = empty_baseline_evidence(team_log)
        if evidence is None:
            return decision  # empty saved roster without proof it was invalid: normal comparison
        recovery = {**decision, "recovered": evidence, "wipe_entries": wipe_entries}
        if evidence == NEVER_POPULATED:
            return recovery
        wiped = {name_key(entry.get("playerName")) for entry in wipe_entries}
        returned = wiped & player_keys(candidate)
        wipe_missing = sorted(wiped - returned)
        recovery.update(missing=wipe_missing, reference=LOGGED_WIPE)
        if not is_suspicious_drop(league, len(wiped), len(returned)):
            return recovery
        if pending and pending.get("reference") == LOGGED_WIPE \
                and overlap(pending.get("missing", []), wipe_missing) >= CONFIRM_OVERLAP:
            return {**recovery, "confirmed": True}
        pct = round(100 * (len(wiped) - len(returned)) / len(wiped))
        return {**recovery, "accept": False, "kind": SUSPICIOUS_DROP, "recovered": "",
                "message": (f"Needs verification: only {len(returned)} of the {len(wiped)} players "
                            f"removed by the logged wipe returned (-{pct}%); saved roster stays empty "
                            f"until a second matching fetch confirms it")}

    if previous_count and is_suspicious_drop(league, previous_count, new_count):
        if pending and pending.get("reference", "previous") == "previous" \
                and overlap(pending.get("missing", []), missing) >= CONFIRM_OVERLAP:
            return {**decision, "confirmed": True}
        pct = round(100 * (previous_count - new_count) / previous_count)
        return {**decision, "accept": False, "kind": SUSPICIOUS_DROP,
                "message": (f"Needs verification: roster dropped from {previous_count} to {new_count} "
                            f"players (-{pct}%); previous roster kept until the drop is confirmed")}

    return decision


def recovery_changes(diff, candidate, wipe_entries):
    """Recovery from a logged wipe, compared with the checker's own diff so entries keep its format.

    Returns (changes, differences): changes = players NOT on the wiped roster, as "added" entries
    tagged afterBaselineRecovery (removals are not repeated - the wipe already logged them);
    differences = counts of status/position differences of returning players (not logged)."""
    compared = diff(wiped_roster(wipe_entries, candidate), candidate)
    changes = [change for change in compared if change.get("type") in RECOVERY_CHANGE_TYPES]
    for change in changes:
        change["afterBaselineRecovery"] = True
    differences = {
        "statusDifferences": sum(1 for change in compared if change.get("type") == "status changed"),
        "positionDifferences": sum(1 for change in compared if change.get("type") == "position changed"),
    }
    return changes, differences


def _read_team_log(team_log):
    """[] when the file does not exist (nothing ever logged), None when it cannot be read."""
    if team_log is None:
        return None
    try:
        data = load_json(team_log)
    except (OSError, ValueError):
        return None
    if data is None:
        return []
    return data if isinstance(data, list) else None


def guarded_update(*, league, code, fetch, diff, state_dir, latest_dir, team_log=None, log_changes=None):
    """Fetch one team's roster and apply it only if it passes the safety rules.

    team_log: path of the team's change log (logs/<league>/<TEAM>.json); read only, used as
    evidence when the saved roster is empty. Without it no empty-baseline recovery happens.
    log_changes: callable(changes) that appends this team's changes to its log; called before the
    new roster is saved, so a failed log write keeps the previous roster (no change is lost).

    Never raises: any error is returned as a fetch_error / processing_error outcome.
    Returns a dict with: accepted, kind, message, roster (accepted roster or None), previous,
    changes, attemptedAt, fetchedCount, confirmed, recovered ("" or the evidence), recovery."""
    state_dir, latest_dir = Path(state_dir), Path(latest_dir)
    attempted_at = now_iso()
    state_path = state_dir / f"{code}.json"
    pending_path = state_dir / "pending" / f"{code}.json"
    result = {"accepted": False, "kind": "", "message": "", "roster": None, "previous": None,
              "changes": [], "attemptedAt": attempted_at, "fetchedCount": None, "confirmed": False,
              "recovered": "", "recovery": None}

    try:
        previous = load_json(state_path)
        result["previous"] = previous
    except Exception as error:
        result.update(kind=PROCESSING_ERROR, message=f"Processing error: saved roster unreadable: {error}")
        return result

    try:
        candidate = fetch()
    except Exception as error:
        result.update(kind=FETCH_ERROR, message=str(error) or error.__class__.__name__)
        return result

    try:
        return _apply(result, league=league, code=code, candidate=candidate, previous=previous,
                      diff=diff, state_path=state_path, latest_path=latest_dir / f"{code}.json",
                      pending_path=pending_path, team_log=team_log, log_changes=log_changes)
    except Exception as error:
        result.update(accepted=False, kind=PROCESSING_ERROR, roster=None, changes=[], confirmed=False,
                      recovered="", recovery=None,
                      message=f"Processing error: {error.__class__.__name__}: {error}; previous roster kept")
        return result


def _apply(result, *, league, code, candidate, previous, diff, state_path, latest_path, pending_path,
           team_log, log_changes):
    attempted_at = result["attemptedAt"]
    result["fetchedCount"] = len(candidate.get("players", []))
    pending = load_json(pending_path)
    history = None
    if previous is not None and not previous.get("players"):
        history = _read_team_log(team_log)  # only needed (and only read) for an empty saved roster
    decision = evaluate(league, previous, candidate, pending, team_log=history)

    if not decision["accept"]:
        result.update(kind=decision["kind"], message=decision["message"])
        if decision["kind"] == SUSPICIOUS_DROP:
            reference = decision["reference"]
            save_json(pending_path, {
                "heldAt": attempted_at,
                "teamCode": code,
                "reference": reference,
                "previousCount": (len({name_key(e.get("playerName")) for e in decision["wipe_entries"]})
                                  if reference == LOGGED_WIPE else len(previous.get("players", []))),
                "candidateCount": result["fetchedCount"],
                "missing": decision["missing"],
                "candidate": candidate,
            })
        return result

    recovered = decision["recovered"]
    differences = {"statusDifferences": 0, "positionDifferences": 0}
    if recovered == LOGGED_WIPE:
        changes, differences = recovery_changes(diff, candidate, decision["wipe_entries"])
    elif recovered == NEVER_POPULATED:
        changes = []
    else:
        changes = diff(previous, candidate)
    if decision["confirmed"]:
        for change in changes:
            change["confirmedAfterHold"] = True
            if change.get("type") == "removed":
                change["severity"] = "high"

    if changes and log_changes is not None:
        log_changes(changes)  # first: if this fails, the previous roster stays and nothing is lost
    save_json(state_path, candidate)
    save_json(latest_path, candidate)
    if pending_path.exists():
        pending_path.unlink()  # transient hold state only; the event itself stays in source_issues.json

    if recovered:
        wiped = {name_key(entry.get("playerName")) for entry in decision["wipe_entries"]}
        result["recovery"] = {
            "evidence": recovered,
            "emptySince": (previous or {}).get("checkedAt", ""),
            "wipeLoggedAt": max((str(entry.get("timestamp", "")) for entry in decision["wipe_entries"]), default=""),
            "wipedPlayers": len(wiped),
            "returnedPlayers": len(wiped & player_keys(candidate)),
            "newPlayersLogged": len(changes),
            **differences,
            "confirmedAfterHold": decision["confirmed"],
        }
    result.update(accepted=True, roster=candidate, changes=changes, confirmed=decision["confirmed"],
                  recovered=recovered)
    return result


def status_fields(outcome):
    """Status-row fields shared by every league (merged into each league's own row)."""
    previous = outcome["previous"] or {}
    if outcome["accepted"]:
        roster = outcome["roster"]
        checked_at = roster.get("checkedAt", "")
        player_count = len(roster.get("players", []))
    else:
        checked_at = previous.get("checkedAt", "")
        player_count = len(previous.get("players", []))
    return {
        "checkedAt": checked_at,
        "playerCount": player_count,
        "lastChangeCount": len(outcome["changes"]),
        "lastError": outcome["message"],
        "lastSuccessfulCheckedAt": checked_at,
        "lastAttemptedAt": outcome["attemptedAt"],
        "sourceIssue": outcome["kind"],
    }


def record_issue(data_dir, *, code, team_name, outcome, source):
    """Append a failed or held run, or a baseline recovery, to data/<league>/source_issues.json
    (newest first). Healthy runs are not recorded."""
    recovery = outcome.get("recovery")
    if outcome["accepted"] and not recovery:
        return
    previous = outcome["previous"] or {}
    entry = {
        "timestamp": outcome["attemptedAt"],
        "teamCode": code,
        "teamName": team_name,
        "kind": outcome["kind"],
        "message": outcome["message"],
        "previousCount": len(previous.get("players", [])),
        "fetchedCount": outcome["fetchedCount"],
        "lastSuccessfulCheckedAt": previous.get("checkedAt", ""),
        "source": source,
    }
    if recovery:
        if recovery["evidence"] == LOGGED_WIPE:
            confirmed = " (confirmed by a second matching fetch)" if recovery["confirmedAfterHold"] else ""
            why = (f"the team log shows the wipe on {recovery['wipeLoggedAt'][:10]} that removed all "
                   f"{recovery['wipedPlayers']} players; {recovery['returnedPlayers']} returned and were not "
                   f"logged again{confirmed}; logged {recovery['newPlayersLogged']} players not on that list "
                   f"as added; returning players differ in status for {recovery['statusDifferences']} and in "
                   f"position for {recovery['positionDifferences']} (counted here, not logged)")
        else:
            why = "the team log has no entries, so no valid roster was ever recorded; nothing logged"
        entry.update(
            kind=BASELINE_RECOVERED,
            message=(f"Recovered baseline: the saved roster was empty (left by the checker before the "
                     f"safety guard); accepted {outcome['fetchedCount']} players as the new baseline - {why}"),
            lastSuccessfulCheckedAt=outcome["roster"].get("checkedAt", ""),
            **recovery,
        )
    path = Path(data_dir) / "source_issues.json"
    try:
        existing = load_json(path) or []
        if not isinstance(existing, list):
            raise ValueError("source_issues.json is not a list")
        save_json(path, [entry] + existing)
    except Exception as error:  # the audit trail must never stop a league's run
        print(f"{code}: WARNING could not record source issue ({error}): {entry['kind']} {entry['message']}")


def summary_counts(statuses):
    return {
        "teamsOk": sum(1 for row in statuses if not row.get("lastError")),
        "teamsErrored": sum(1 for row in statuses if row.get("lastError")),
        "teamsNeedingVerification": sum(1 for row in statuses if row.get("sourceIssue") == SUSPICIOUS_DROP),
    }
