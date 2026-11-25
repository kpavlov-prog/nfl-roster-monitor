"""
Upcoming / recent events for the Games page (separate from every roster checker).

Source: ESPN Site API scoreboards, one request per league per ESPN date (US Eastern calendar date,
which is what ESPN's `dates=` parameter means). Date ranges are not used: `dates=A-B` returns HTTP 400
and `startDate/endDate` silently returns a partial slate.

Window (UTC, computed at run time, D = today in UTC):
    from D-1 00:00 UTC  (start of yesterday in GMT; also covers yesterday in fixed EST)
    to   D+8 05:00 UTC  (end of D+7 in fixed EST, which is later than the end of D+7 in GMT)
To cover that window the checker reads ESPN dates D-2 .. D+7 (10 requests per league).

Relevant events: at least one monitored team takes part.
    NCAAF / NCAAB: monitored ESPN team ids from data/<league>/status.json (the roster monitors' lists)
    NFL / NBA / WNBA / MLB / NHL: monitored team codes from data/<league>/status.json, matched to ESPN
    abbreviations (a few ESPN abbreviations differ; see ALIASES).

Source safety (same philosophy as the roster monitors):
    - a date that fails (HTTP error, bad JSON, no "events" list) keeps its known-good events
    - a date that suddenly comes back empty while it previously had events (today or later) is held
      once; the empty answer is accepted only if the next run confirms it
    - one malformed event is skipped and counted, never fatal
    - events leave only by moving out of the window; postponed / cancelled events stay with their status
    - files are written atomically and only when the content meaningfully changed

Outputs (data/events/): <league>.json, upcoming.json (all leagues, what the page loads), status.json,
source_issues.json.
"""

import json
import os
import re
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "events"
SITE = "https://site.api.espn.com/apis/site/v2/sports"
SOURCE = "ESPN"
REQUEST_PAUSE = 0.5
MAX_ISSUES = 300
SUSPICIOUS_EMPTY_MIN = 3        # a date that had at least this many events and now returns none is held once

LEAGUES = {
    "nfl":   {"label": "NFL",   "sport": "football",   "path": "football/nfl",                      "params": {}, "match": "code"},
    "ncaaf": {"label": "NCAAF", "sport": "football",   "path": "football/college-football",         "params": {"groups": "80", "limit": "400"}, "match": "id"},
    "nba":   {"label": "NBA",   "sport": "basketball", "path": "basketball/nba",                    "params": {}, "match": "code"},
    "wnba":  {"label": "WNBA",  "sport": "basketball", "path": "basketball/wnba",                   "params": {}, "match": "code"},
    "ncaab": {"label": "NCAAB", "sport": "basketball", "path": "basketball/mens-college-basketball", "params": {"groups": "50", "limit": "500"}, "match": "id"},
    "mlb":   {"label": "MLB",   "sport": "baseball",   "path": "baseball/mlb",                      "params": {}, "match": "code"},
    "nhl":   {"label": "NHL",   "sport": "hockey",     "path": "hockey/nhl",                        "params": {}, "match": "code"},
}
# ESPN abbreviation -> our roster team code, where they differ (checked against ESPN's team lists)
ALIASES = {
    "nfl": {"WSH": "WAS"},
    "nhl": {"LA": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL", "UTAH": "UTA"},
}

EVENT_ID = re.compile(r"^\d{1,15}$")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class SourceError(Exception):
    """The response for a league/date could not be used."""


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def write_json_atomic(path, data, compact=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            if compact:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            else:
                json.dump(data, f, indent=1, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def get_json(url):   # replaced in tests
    import requests
    response = requests.get(url, timeout=30, headers={"User-Agent": "roster-monitor-events/1.0", "Accept": "application/json"})
    response.raise_for_status()
    return response.json()


# --- window ---------------------------------------------------------------------------------------

def window(today):
    """(from, to) UTC datetimes and the ESPN dates to read, for UTC calendar day `today`."""
    start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc) - timedelta(days=1)
    end = datetime(today.year, today.month, today.day, tzinfo=timezone.utc) + timedelta(days=8, hours=5)
    espn_dates = [today + timedelta(days=d) for d in range(-2, 8)]
    return start, end, espn_dates


def iso_z(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        v = value.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z", v):      # ESPN often omits seconds
            v = v[:-1] + ":00Z"
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except ValueError:
        return None


# --- monitored teams ------------------------------------------------------------------------------

def monitored_teams(key):
    """Set of monitored ESPN team ids (match=id) or our team codes (match=code); None if unknown."""
    rows = load_json(DATA / key / "status.json")
    if not isinstance(rows, list) or not rows:
        return None
    field = "teamId" if LEAGUES[key]["match"] == "id" else "teamCode"
    values = {str(r.get(field)).strip() for r in rows if isinstance(r, dict) and r.get(field)}
    return values or None


def is_monitored(key, team, monitored):
    if team.get("placeholder"):
        return False
    if LEAGUES[key]["match"] == "id":
        return team["id"] in monitored
    code = ALIASES.get(key, {}).get(team["code"], team["code"])
    return code in monitored


# --- normalisation --------------------------------------------------------------------------------

def text(value, limit=120):
    """Untrusted ESPN string -> plain bounded text (rendered with textContent on the page)."""
    if value is None:
        return ""
    return CONTROL.sub(" ", str(value)).strip()[:limit]


def logo_url(value):
    v = text(value, 300)
    return v if v.startswith("https://") else ""


def score_value(raw, state):
    if state not in ("in", "post"):
        return None
    if isinstance(raw, dict):
        raw = raw.get("value", raw.get("displayValue"))
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return None
    return int(n) if n == int(n) and 0 <= n < 1000 else None


def status_of(event, comp):
    st = event.get("status") or comp.get("status") or {}
    typ = st.get("type") or {}
    name = text(typ.get("name"), 40).upper()
    state = typ.get("state") if typ.get("state") in ("pre", "in", "post") else None
    if state is None:
        raise ValueError("unknown status state")
    postponed = "POSTPONED" in name
    cancelled = "CANCEL" in name
    delayed = "DELAY" in name or "RAIN" in name
    return st, {
        "state": state,
        "name": name,
        "detail": text(typ.get("shortDetail") or typ.get("detail") or typ.get("description"), 60),
        "completed": bool(typ.get("completed")) and not (postponed or cancelled),
        "postponed": postponed,
        "cancelled": cancelled,
        "delayed": delayed,
    }


def is_placeholder(t):
    """ESPN bracket slot not yet decided ("TBD", negative team id)."""
    tid = str(t.get("id") or "")
    return tid.startswith("-") or text(t.get("displayName") or t.get("name")).upper() in ("TBD", "TBA")


def team_of(competitor):
    t = competitor.get("team") or {}
    if is_placeholder(t):
        return {"id": "", "code": "TBD", "name": "TBD", "shortName": "TBD", "logo": "", "placeholder": True}
    tid = text(t.get("id"), 12)
    if not tid.isdigit():
        raise ValueError("team without a numeric id")
    return {
        "id": tid,
        "code": text(t.get("abbreviation"), 10),
        "name": text(t.get("displayName") or t.get("name"), 80),
        "shortName": text(t.get("shortDisplayName") or t.get("name") or t.get("abbreviation"), 40),
        "logo": logo_url(t.get("logo")),
    }


def normalize(key, event, espn_date, monitored, checked_at):
    """One ESPN event -> our schema, or None when no monitored team plays. Raises ValueError if malformed."""
    eid = text(event.get("id"), 20)
    if not EVENT_ID.match(eid):
        raise ValueError("bad event id")
    start = parse_time(event.get("date"))
    if start is None:
        raise ValueError("bad start time")
    comps = event.get("competitions") or []
    if not comps or not isinstance(comps[0], dict):
        raise ValueError("no competition")
    comp = comps[0]
    competitors = comp.get("competitors") or []
    sides = {c.get("homeAway"): c for c in competitors if isinstance(c, dict)}
    if len(competitors) != 2 or set(sides) != {"home", "away"}:        # never guess home/away from order
        raise ValueError("competitors without one home and one away team")
    raw_status, status = status_of(event, comp)
    home, away = team_of(sides["home"]), team_of(sides["away"])
    home["monitored"], away["monitored"] = is_monitored(key, home, monitored), is_monitored(key, away, monitored)
    if not (home["monitored"] or away["monitored"]):
        return None
    period = raw_status.get("period")
    period = period if isinstance(period, int) and 0 <= period < 50 else None
    live = status["state"] == "in"
    season = event.get("season") or {}
    week = (event.get("week") or {}).get("number")
    venue = comp.get("venue") or {}
    return {
        "eventId": eid,
        "league": LEAGUES[key]["label"],
        "leagueKey": key,
        "sport": LEAGUES[key]["sport"],
        "startTimeUTC": iso_z(start),
        "espnDate": espn_date.strftime("%Y%m%d"),
        "homeTeam": home,
        "awayTeam": away,
        "venue": text(venue.get("fullName"), 100),
        "neutralSite": comp.get("neutralSite") is True,
        "status": status,
        "score": {"home": score_value(sides["home"].get("score"), status["state"]),
                  "away": score_value(sides["away"].get("score"), status["state"])},
        "gameState": {"period": period if status["state"] != "pre" else None,
                      "clock": text(raw_status.get("displayClock"), 10) if live else None,
                      "display": status["detail"] or None},
        "seasonType": season.get("type") if isinstance(season.get("type"), int) else None,
        "week": week if isinstance(week, int) else None,
        "source": SOURCE,
        "updatedAt": checked_at,
    }


def scoreboard_url(key, espn_date):
    params = dict(LEAGUES[key]["params"], dates=espn_date.strftime("%Y%m%d"))
    return f"{SITE}/{LEAGUES[key]['path']}/scoreboard?" + "&".join(f"{k}={v}" for k, v in params.items())


def fetch_date(key, espn_date, monitored, checked_at):
    """(events, skipped) for one league/date. Raises SourceError when the response is unusable."""
    try:
        data = get_json(scoreboard_url(key, espn_date))
    except Exception as exc:            # network, HTTP status, JSON decoding
        raise SourceError(f"{type(exc).__name__}: {str(exc)[:200]}") from None
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        raise SourceError("response has no events list")
    events, skipped = [], 0
    for raw in data["events"]:
        try:
            ev = normalize(key, raw, espn_date, monitored, checked_at) if isinstance(raw, dict) else None
            if not isinstance(raw, dict):
                skipped += 1
            elif ev:
                events.append(ev)
        except (ValueError, TypeError, KeyError, AttributeError):
            skipped += 1
    if data["events"] and skipped == len(data["events"]):
        raise SourceError(f"all {skipped} events were malformed")
    return events, skipped


# --- one league -----------------------------------------------------------------------------------

STABLE_KEYS = ("updatedAt",)


def same_event(a, b):
    return {k: v for k, v in a.items() if k not in STABLE_KEYS} == {k: v for k, v in b.items() if k not in STABLE_KEYS}


def run_league(key, today, previous, pending, checked_at, issues):
    """New league document. `previous` = last written document (or None); `pending` = held empty dates."""
    start, end, espn_dates = window(today)
    prev_events = (previous or {}).get("events") or []
    prev_dates = (previous or {}).get("dates") or {}
    by_date_prev = {}
    for ev in prev_events:
        by_date_prev.setdefault(ev.get("espnDate"), []).append(ev)
    monitored = monitored_teams(key)
    dates_out, fresh, kept, requests_made = {}, {}, {}, 0
    for d in espn_dates:
        ds = d.strftime("%Y%m%d")
        prev_info = prev_dates.get(ds) or {}
        prev_list = by_date_prev.get(ds, [])
        if monitored is None:
            kept[ds] = prev_list
            dates_out[ds] = dict(prev_info, status="preserved", checkedAt=checked_at, error="monitored team list unavailable")
            issues.append({"timestamp": checked_at, "league": key, "date": ds, "kind": "no_team_list", "message": "monitored team list unavailable; known-good events kept"})
            continue
        try:
            requests_made += 1
            events, skipped = fetch_date(key, d, monitored, checked_at)
            time.sleep(REQUEST_PAUSE)
        except SourceError as exc:
            time.sleep(REQUEST_PAUSE)
            kept[ds] = prev_list
            dates_out[ds] = {"status": "preserved", "checkedAt": checked_at, "lastSuccessAt": prev_info.get("lastSuccessAt"),
                             "events": len(prev_list), "error": str(exc)}
            issues.append({"timestamp": checked_at, "league": key, "date": ds, "kind": "fetch_error", "message": f"{exc}; {len(prev_list)} known-good events kept"})
            continue
        held_key = f"{key}|{ds}"
        if not events and len(prev_list) >= SUSPICIOUS_EMPTY_MIN and d >= today and held_key not in pending:
            pending[held_key] = checked_at          # hold once; a second empty answer is accepted
            kept[ds] = prev_list
            dates_out[ds] = {"status": "held_empty", "checkedAt": checked_at, "lastSuccessAt": prev_info.get("lastSuccessAt"),
                             "events": len(prev_list), "error": f"ESPN returned no events for a date that had {len(prev_list)}; held until confirmed"}
            issues.append({"timestamp": checked_at, "league": key, "date": ds, "kind": "suspicious_empty", "message": f"empty response; {len(prev_list)} known-good events kept until the next run confirms"})
            continue
        pending.pop(held_key, None)
        fresh[ds] = events
        dates_out[ds] = {"status": "ok", "checkedAt": checked_at, "lastSuccessAt": checked_at, "events": len(events), "skipped": skipped}
        if skipped:
            issues.append({"timestamp": checked_at, "league": key, "date": ds, "kind": "malformed_events", "message": f"{skipped} malformed events skipped"})

    # merge: fresh answers win over kept ones for the same event id (a rescheduled game appears once)
    merged = {}
    for ds in sorted(kept):
        for ev in kept[ds]:
            merged[ev["eventId"]] = ev
    for ds in sorted(fresh):
        for ev in fresh[ds]:
            merged[ev["eventId"]] = ev
    prev_by_id = {ev.get("eventId"): ev for ev in prev_events}
    out = []
    for ev in merged.values():
        t = parse_time(ev.get("startTimeUTC"))
        if t is None or not (start <= t < end):
            continue                                       # outside the rolling window: expires normally
        old = prev_by_id.get(ev["eventId"])
        if old and same_event(old, ev):
            ev = dict(ev, updatedAt=old.get("updatedAt", ev["updatedAt"]))   # unchanged event keeps its time
        out.append(ev)
    out.sort(key=lambda e: (e["startTimeUTC"], e["eventId"]))
    return {
        "league": LEAGUES[key]["label"], "leagueKey": key, "sport": LEAGUES[key]["sport"], "source": SOURCE,
        "window": {"fromUTC": iso_z(start), "toUTC": iso_z(end)},
        "dates": dates_out, "eventCount": len(out), "events": out,
    }, requests_made


def meaningful(doc):
    """Document without run timestamps, to decide whether anything changed."""
    d = json.loads(json.dumps(doc))
    d.pop("generatedAt", None)
    for info in (d.get("dates") or {}).values():
        for k in ("checkedAt", "lastSuccessAt"):
            info.pop(k, None)
    for ev in d.get("events") or []:
        ev.pop("updatedAt", None)
    return d


# --- run ------------------------------------------------------------------------------------------

def run(today=None):
    t0 = time.time()
    today = today or datetime.now(timezone.utc).date()
    checked_at = now_iso()
    status_prev = load_json(OUT / "status.json") or {}
    pending = dict(status_prev.get("pendingEmpty") or {})
    issues, docs, changed, requests_total = [], {}, False, 0
    for key in LEAGUES:
        previous = load_json(OUT / f"{key}.json")
        doc, n = run_league(key, today, previous, pending, checked_at, issues)
        requests_total += n
        if previous is None or meaningful(previous) != meaningful(doc):
            doc["generatedAt"] = checked_at
            changed = True
        else:
            doc["generatedAt"] = previous.get("generatedAt", checked_at)
            doc["dates"] = previous.get("dates", doc["dates"])          # keep the old file byte-identical
            doc["events"] = previous.get("events", doc["events"])
        docs[key] = doc
        print(f"{key}: {doc['eventCount']} events; dates " + " ".join(f"{d}:{i['status']}" for d, i in doc["dates"].items()))
    start, end, espn_dates = window(today)
    pending = {k: v for k, v in pending.items() if k.split("|")[1] >= espn_dates[0].strftime("%Y%m%d")}
    # a recurring issue is not news: only a new or resolved one counts as a change (no commit every run)
    open_issues = sorted({f"{i['league']}|{i['date']}|{i['kind']}" for i in issues})
    previous_open = status_prev.get("openIssues") or []
    if pending != (status_prev.get("pendingEmpty") or {}) or open_issues != previous_open:
        changed = True
    if not changed:
        print("No meaningful change: nothing written")
        return {"changed": False, "requests": requests_total, "issues": issues}

    for key, doc in docs.items():
        write_json_atomic(OUT / f"{key}.json", doc)
    all_events = sorted((ev for doc in docs.values() for ev in doc["events"]), key=lambda e: (e["startTimeUTC"], e["leagueKey"], e["eventId"]))
    write_json_atomic(OUT / "upcoming.json", {
        "generatedAt": checked_at, "source": SOURCE,
        "window": {"fromUTC": iso_z(start), "toUTC": iso_z(end)},
        "leagues": {k: {"label": d["league"], "sport": d["sport"], "eventCount": d["eventCount"], "generatedAt": d["generatedAt"],
                        "lastSuccessAt": max([i.get("lastSuccessAt") or "" for i in d["dates"].values()] or [""]) or None,
                        "issueDates": sorted(ds for ds, i in d["dates"].items() if i.get("status") != "ok")}
                    for k, d in docs.items()},
        "eventCount": len(all_events), "events": all_events,
    }, compact=True)
    new_issues = [i for i in issues if f"{i['league']}|{i['date']}|{i['kind']}" not in previous_open]
    old_issues = load_json(OUT / "source_issues.json") or []
    write_json_atomic(OUT / "source_issues.json", (new_issues + old_issues)[:MAX_ISSUES])
    write_json_atomic(OUT / "status.json", {
        "lastRunFinished": now_iso(), "runSeconds": round(time.time() - t0, 1), "requests": requests_total,
        "window": {"fromUTC": iso_z(start), "toUTC": iso_z(end), "espnDates": [d.strftime("%Y%m%d") for d in espn_dates]},
        "leagues": {k: {"eventCount": d["eventCount"], "dates": {ds: i["status"] for ds, i in d["dates"].items()}} for k, d in docs.items()},
        "issuesThisRun": len(issues), "openIssues": open_issues, "pendingEmpty": pending,
    })
    print(f"Wrote data/events ({len(all_events)} events, {requests_total} requests, {len(issues)} issues)")
    return {"changed": True, "requests": requests_total, "issues": issues}


if __name__ == "__main__":
    run()
