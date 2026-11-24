"""Tests for the NCAAB (NCAA Division I men's basketball) checker: team list, ESPN site/core sources,
the shared safety guard, diffs, provider ids / class, and the data/ncaab/rosters.json bundle.

The real checker runs in a temporary folder; ESPN is replaced by a fake that answers by URL.
Run from the repository root:  python -m unittest discover -s tests -v
"""
import copy
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
try:
    import requests  # noqa: F401
except ImportError:
    sys.modules["requests"] = types.ModuleType("requests")

import roster_safety  # noqa: E402

SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams/{}/roster"
CORE_LIST = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball/seasons/2027/teams/{}/athletes?limit=200"
ATHLETE = "http://sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball/seasons/2027/athletes/{}?lang=en&region=us"


def load_checker():
    spec = importlib.util.spec_from_file_location("checker_ncaab_test", SCRIPTS / "check_ncaab_rosters.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def team(tid, code, name, conference="Test Conference"):
    return {"id": str(tid), "code": code, "abbreviation": code, "name": name, "conference": conference,
            "conferenceId": "99", "logo": f"https://a.espncdn.com/i/teamlogos/ncaa/500/{tid}.png"}


def athlete(aid, name, pos="G", jersey="1", cls="Junior"):
    return {"id": str(aid), "displayName": name, "jersey": jersey, "position": {"abbreviation": pos},
            "experience": {"displayValue": cls}, "status": {"type": "active"}}


def squad(prefix, n, start=1, **kw):
    return [athlete(f"{prefix}{i}", f"{prefix} Player {i:02d}", jersey=str(i), **kw) for i in range(start, start + n)]


class HTTPError(Exception):
    pass


class FakeESPN:
    """URL -> JSON. Unknown URLs or explicit errors raise like requests' raise_for_status."""

    def __init__(self):
        self.routes, self.calls = {}, []

    def site(self, tid, athletes):
        self.routes[SITE.format(tid)] = {"team": {"displayName": None}, "athletes": athletes}

    def site_fails(self, tid):
        self.routes[SITE.format(tid)] = HTTPError(f"404 Client Error for url: {SITE.format(tid)}")

    def core(self, tid, athletes, unreadable=()):
        self.routes[CORE_LIST.format(tid)] = {"count": len(athletes), "pageCount": 1,
                                             "items": [{"$ref": ATHLETE.format(a["id"])} for a in athletes]}
        for a in athletes:
            self.routes[ATHLETE.format(a["id"])] = HTTPError("404 Client Error") if a["id"] in unreadable else a

    def core_fails(self, tid):
        self.routes[CORE_LIST.format(tid)] = HTTPError("503 Server Error")

    def get(self, url):
        self.calls.append(url)
        answer = self.routes.get(url, HTTPError(f"404 Client Error for url: {url}"))
        if isinstance(answer, Exception):
            raise answer
        return copy.deepcopy(answer)


class Site:
    def __init__(self, tmp, teams):
        self.m = m = load_checker()
        root = Path(tmp)
        m.ROOT = root
        m.DATA = root / "data" / "ncaab"
        m.LATEST, m.STATE, m.LOGS = m.DATA / "latest", m.DATA / "state", root / "logs" / "ncaab"
        m.TEAMS_FILE, m.BUNDLE_FILE = m.DATA / "d1_teams.json", m.DATA / "rosters.json"
        m.MIN_TEAMS = 1
        m.TEAM_PAUSE = m.ATHLETE_PAUSE = 0
        self.espn = FakeESPN()
        m.get_json = self.espn.get
        self.write_teams(teams)

    def write_teams(self, teams):
        roster_safety.save_json(self.m.TEAMS_FILE, teams)

    def run(self):
        with mock.patch("builtins.print"):
            self.m.run()

    def read(self, *parts):
        p = self.m.ROOT.joinpath(*parts)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def status(self, code):
        return next((r for r in self.read("data", "ncaab", "status.json") or [] if r["teamCode"] == code), None)

    def latest(self, code):
        return self.read("data", "ncaab", "latest", f"{code}.json")

    def log(self, code):
        return self.read("logs", "ncaab", f"{code}.json") or []


DUKE, UL, MER = team(150, "DUKE", "Duke Blue Devils", "Atlantic Coast Conference"), team(309, "UL", "Louisiana Ragin' Cajuns", "Sun Belt Conference"), team(2382, "MER", "Mercer Bears", "Southern Conference")


class SiteTest(unittest.TestCase):
    teams = [DUKE, UL, MER]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.s = Site(self._tmp.name, self.teams)

    def tearDown(self):
        self._tmp.cleanup()


# --- team list ------------------------------------------------------------------------------------

class TeamList(unittest.TestCase):
    def setUp(self):
        self.m = load_checker()

    def validate(self, teams, previous=None, codes_by_id=None, ids_by_code=None, **kw):
        return self.m.validate_team_list(teams, previous, codes_by_id or {}, ids_by_code or {}, **kw)

    def test_the_committed_division_i_list_is_valid(self):
        teams = json.loads((ROOT / "data" / "ncaab" / "d1_teams.json").read_text(encoding="utf-8"))
        result = self.validate(teams)
        self.assertEqual(len(result), 365)
        self.assertEqual(len({t["conference"] for t in result}), 32)
        self.assertNotIn("42", {t["id"] for t in result})            # Hartford (inactive) is not monitored
        self.assertTrue({"309", "2382"} <= {t["id"] for t in result})  # Louisiana and Mercer are

    def test_duplicate_espn_id_is_rejected(self):
        with self.assertRaisesRegex(self.m.TeamListError, "duplicate ESPN id 150"):
            self.validate([DUKE, dict(UL, id="150")], min_teams=1)

    def test_duplicate_code_is_rejected_including_after_normalisation(self):
        with self.assertRaisesRegex(self.m.TeamListError, "duplicate file code"):
            self.validate([dict(DUKE, code="TA&M"), dict(UL, code="TA-M")], min_teams=1)
        with self.assertRaisesRegex(self.m.TeamListError, "duplicate file code"):
            self.validate([dict(DUKE, code="duke"), dict(UL, code="DUKE")], min_teams=1)

    def test_missing_fields_and_reserved_names_are_rejected(self):
        with self.assertRaisesRegex(self.m.TeamListError, "has no conference"):
            self.validate([dict(DUKE, conference="")], min_teams=1)
        with self.assertRaisesRegex(self.m.TeamListError, "reserved Windows file name"):
            self.validate([dict(DUKE, code="CON")], min_teams=1)

    def test_huge_shrink_is_rejected(self):
        with self.assertRaisesRegex(self.m.TeamListError, "at least 350"):
            self.validate([DUKE, UL, MER])
        with self.assertRaisesRegex(self.m.TeamListError, "4 teams monitored in the last run are missing"):
            self.validate([DUKE], previous={"DUKE", "UL", "MER", "A", "B"}, min_teams=1)

    def test_identity_swap_is_rejected(self):
        with self.assertRaisesRegex(self.m.TeamListError, "a different code would split its history"):
            self.validate([DUKE], codes_by_id={"150": {"DUK"}}, min_teams=1)
        with self.assertRaisesRegex(self.m.TeamListError, "would overwrite another team's roster"):
            self.validate([DUKE], ids_by_code={"DUKE": {"999"}}, min_teams=1)

    def test_an_invalid_list_makes_zero_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Site(tmp, [DUKE, dict(UL, id="150")])
            with self.assertRaises(s.m.TeamListError), mock.patch("builtins.print"):
                s.m.run()
            self.assertEqual(s.espn.calls, [])
            written = sorted(str(p.relative_to(tmp)) for p in Path(tmp).rglob("*") if p.is_file())
            self.assertEqual(written, [str(Path("data/ncaab/d1_teams.json"))])


# --- sources --------------------------------------------------------------------------------------

class Sources(SiteTest):
    def test_site_roster_success_path(self):
        self.s.espn.site(150, squad("D", 15))
        self.s.espn.site_fails(309); self.s.espn.core(309, squad("L", 17))
        self.s.espn.site_fails(2382); self.s.espn.core_fails(2382)
        self.s.run()
        duke = self.s.latest("DUKE")
        self.assertEqual((duke["rosterSource"], len(duke["players"]), duke["source"]), ("site", 15, self.s.m.SITE_SOURCE))
        self.assertNotIn("fallbackReason", duke)
        self.assertFalse(any("/athletes" in c for c in self.s.espn.calls if "/150/" in c))   # core never asked for Duke

    def test_site_failure_then_core_fallback_success(self):
        self.s.espn.site(150, squad("D", 15)); self.s.espn.site(2382, squad("M", 15))
        self.s.espn.site_fails(309); self.s.espn.core(309, squad("L", 17))
        self.s.run()
        ul = self.s.latest("UL")
        self.assertEqual((ul["rosterSource"], len(ul["players"]), ul["source"]), ("core-fallback", 17, self.s.m.CORE_SOURCE))
        self.assertIn("site roster failed", ul["fallbackReason"])
        self.assertEqual(self.s.status("UL")["rosterSource"], "core-fallback")
        self.assertEqual(self.s.read("data", "ncaab", "summary.json")["coreFallbackTeams"], ["UL"])
        self.assertEqual(self.s.log("UL"), [])                                                 # first baseline: no history

    def test_site_and_core_both_fail_keeps_the_known_good_roster(self):
        self.s.espn.site(150, squad("D", 15)); self.s.espn.site(2382, squad("M", 15)); self.s.espn.site(309, squad("L", 16))
        self.s.run()
        self.s.espn.site_fails(309); self.s.espn.core_fails(309)
        self.s.run()
        ul = self.s.status("UL")
        self.assertEqual(ul["sourceIssue"], "fetch_error"); self.assertIn("Both ESPN roster APIs failed", ul["lastError"])
        self.assertEqual(len(self.s.latest("UL")["players"]), 16); self.assertEqual(ul["playerCount"], 16)
        self.assertEqual(self.s.log("UL"), [])

    def test_core_athletes_that_cannot_be_read_make_the_fallback_fail(self):
        # the real Louisiana/Mercer case: ESPN lists athletes whose records return 404
        self.s.espn.site(150, squad("D", 15)); self.s.espn.site(2382, squad("M", 15))
        self.s.espn.site_fails(309); self.s.espn.core(309, squad("L", 17), unreadable={"L5"})
        self.s.run()
        self.assertIsNone(self.s.latest("UL"))
        row = self.s.status("UL")
        self.assertEqual(row["sourceIssue"], "fetch_error")
        self.assertIn("17 athletes but 1 could not be read (athlete ids: L5", row["lastError"])

    def test_empty_core_result_is_rejected(self):
        self.s.espn.site(150, squad("D", 15)); self.s.espn.site(2382, squad("M", 15))
        self.s.espn.site_fails(309); self.s.espn.core(309, [])
        self.s.run()
        self.assertEqual(self.s.status("UL")["sourceIssue"], "empty_response")
        self.assertIsNone(self.s.latest("UL"))

    def test_partial_core_result_is_held_then_confirmed(self):
        self.s.espn.site(150, squad("D", 15)); self.s.espn.site(2382, squad("M", 15)); self.s.espn.site(309, squad("L", 15))
        self.s.run()
        self.s.espn.site_fails(309); self.s.espn.core(309, squad("L", 8))               # 15 -> 8: -47%, -7 players
        self.s.run()
        self.assertEqual(self.s.status("UL")["sourceIssue"], "suspicious_drop")
        self.assertEqual(len(self.s.latest("UL")["players"]), 15)
        self.assertIsNotNone(self.s.read("data", "ncaab", "state", "pending", "UL.json"))
        self.assertEqual(self.s.log("UL"), [])
        self.s.run()                                                                       # same drop again: confirmed
        self.assertEqual(len(self.s.latest("UL")["players"]), 8)
        self.assertIsNone(self.s.read("data", "ncaab", "state", "pending", "UL.json"))  # recovery clears the hold
        removed = [c for c in self.s.log("UL") if c["type"] == "removed"]
        self.assertEqual(len(removed), 7); self.assertTrue(all(c.get("confirmedAfterHold") and c["severity"] == "high" for c in removed))

    def test_fallback_with_the_same_players_creates_no_changes(self):
        players = squad("L", 16)
        self.s.espn.site(150, squad("D", 15)); self.s.espn.site(2382, squad("M", 15)); self.s.espn.site(309, players)
        self.s.run()
        self.s.espn.site_fails(309); self.s.espn.core(309, players)
        self.s.run()
        self.assertEqual(self.s.latest("UL")["rosterSource"], "core-fallback")
        self.assertEqual(self.s.log("UL"), []); self.assertEqual(self.s.status("UL")["lastChangeCount"], 0)

    def test_one_team_failing_does_not_stop_the_league(self):
        self.s.espn.site(150, squad("D", 15)); self.s.espn.site(2382, squad("M", 15))
        self.s.espn.routes[SITE.format(309)] = {"athletes": "not a list"}               # malformed answer
        self.s.run()
        self.assertEqual(len(self.s.read("data", "ncaab", "status.json")), 3)
        self.assertEqual((self.s.status("DUKE")["sourceIssue"], self.s.status("MER")["sourceIssue"]), ("", ""))
        self.assertNotEqual(self.s.status("UL")["sourceIssue"], "")


# --- diffs, ids and class -------------------------------------------------------------------------

class Changes(SiteTest):
    teams = [DUKE]

    def first(self, athletes):
        self.s.espn.site(150, athletes); self.s.run()

    def test_first_baseline_creates_no_history(self):
        self.first(squad("D", 15))
        self.assertEqual(self.s.log("DUKE"), []); self.assertIsNone(self.s.read("logs", "ncaab", "all_changes.json"))
        summary = self.s.read("data", "ncaab", "summary.json")
        self.assertEqual((summary["historyTeams"], summary["hasLeagueLog"]), ([], False))   # dashboard: nothing to request

    def test_history_metadata_follows_the_log_files_not_the_last_run(self):
        base = squad("D", 15); self.first(base)
        self.s.espn.site(150, base[1:]); self.s.run()                          # a real change: logs appear
        s1 = self.s.read("data", "ncaab", "summary.json")
        self.assertEqual((s1["historyTeams"], s1["hasLeagueLog"], s1["totalChanges"]), (["DUKE"], True, 1))
        self.s.run()                                                           # quiet run: history still known
        s2 = self.s.read("data", "ncaab", "summary.json")
        self.assertEqual((s2["historyTeams"], s2["hasLeagueLog"], s2["totalChanges"]), (["DUKE"], True, 0))
        self.assertEqual(self.s.status("DUKE")["lastChangeCount"], 0)
        self.assertEqual(len(self.s.log("DUKE")), 1)                           # the log itself is unchanged

    def test_provider_player_id_and_class_are_stored(self):
        self.first([athlete("5311504", "Jeremiah Bowman", pos="F", jersey="20", cls="Sophomore")])
        (p,) = self.s.latest("DUKE")["players"]
        self.assertEqual((p["providerPlayerId"], p["class"], p["position"], p["number"], p["status"]),
                         ("5311504", "Sophomore", "F", "20", "ACTIVE"))

    def test_added_removed_and_position_changes_are_logged(self):
        base = squad("D", 15); self.first(base)
        nxt = [a for a in base if a["id"] != "D3"] + [athlete("D99", "D Newcomer")]
        nxt = [dict(a, position={"abbreviation": "F"}) if a["id"] == "D7" else a for a in nxt]
        self.s.espn.site(150, nxt); self.s.run()
        self.assertEqual(sorted((c["type"], c["playerName"]) for c in self.s.log("DUKE")),
                         [("added", "D Newcomer"), ("position changed", "D Player 07"), ("removed", "D Player 03")])

    def test_no_change_logs_nothing(self):
        self.first(squad("D", 15)); self.s.run()
        self.assertEqual(self.s.log("DUKE"), []); self.assertEqual(self.s.status("DUKE")["lastChangeCount"], 0)

    def test_class_change_is_stored_but_not_logged(self):
        base = squad("D", 15, cls="Junior"); self.first(base)
        self.s.espn.site(150, [dict(a, experience={"displayValue": "Senior"}) for a in base]); self.s.run()
        self.assertEqual(self.s.log("DUKE"), [])
        self.assertTrue(all(p["class"] == "Senior" for p in self.s.latest("DUKE")["players"]))


# --- bundle ---------------------------------------------------------------------------------------

class Bundle(SiteTest):
    def populate(self):
        self.s.espn.site(150, squad("D", 15)); self.s.espn.site(2382, squad("M", 14))
        self.s.espn.site_fails(309); self.s.espn.core_fails(309)
        self.s.run()

    def test_bundle_mirrors_latest_and_status(self):
        self.populate()
        b = self.s.read("data", "ncaab", "rosters.json"); summary = self.s.read("data", "ncaab", "summary.json")
        self.assertEqual(b["generatedAt"], summary["lastRunFinished"])
        self.assertEqual([t["teamCode"] for t in b["teams"]], ["DUKE", "MER", "UL"])
        duke = next(t for t in b["teams"] if t["teamCode"] == "DUKE")
        self.assertEqual(duke["players"], self.s.latest("DUKE")["players"])
        self.assertEqual(duke["checkedAt"], self.s.status("DUKE")["checkedAt"])
        ul = next(t for t in b["teams"] if t["teamCode"] == "UL")
        self.assertIsNone(ul["players"]); self.assertEqual(ul["sourceIssue"], "fetch_error")   # never invented
        self.assertEqual((b["teamCount"], b["playerCount"]), (3, 29))

    def test_bundle_is_deterministic(self):
        self.populate()
        statuses = self.s.read("data", "ncaab", "status.json"); when = "2026-11-24T00:00:00+00:00"
        a = json.dumps(self.s.m.build_bundle(statuses, when)); b = json.dumps(self.s.m.build_bundle(list(reversed(statuses)), when))
        self.assertEqual(a, b)

    def test_a_failed_bundle_write_keeps_the_old_bundle_and_the_rosters(self):
        self.populate()
        before = (self.s.m.BUNDLE_FILE.read_bytes(), self.s.latest("DUKE"))
        with mock.patch.object(self.s.m.json, "dump", side_effect=OSError("disk full")):
            self.s.run()                                                                   # run still completes
        self.assertEqual(self.s.m.BUNDLE_FILE.read_bytes(), before[0])
        self.assertEqual(self.s.latest("DUKE")["players"], before[1]["players"])
        self.assertEqual(list(self.s.m.DATA.glob(".rosters-*.tmp")), [])                  # no temp file left behind
        self.assertNotEqual(self.s.read("data", "ncaab", "rosters.json")["generatedAt"],
                            self.s.read("data", "ncaab", "summary.json")["lastRunFinished"])  # stale: frontend falls back


class Thresholds(unittest.TestCase):
    def test_ncaab_drop_limits(self):
        drop = lambda prev, new: roster_safety.is_suspicious_drop("ncaab", prev, new)
        self.assertFalse(drop(15, 10))   # -5 players, 33%
        self.assertTrue(drop(15, 9))     # -6 players, 40%
        self.assertTrue(drop(12, 7))     # -5 players, 42%
        self.assertFalse(drop(25, 20))   # -5 players, 20%
        self.assertFalse(drop(10, 6))    # 40% but only -4 players


if __name__ == "__main__":
    unittest.main()
