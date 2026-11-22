"""Tests for the WNBA franchise transition Connecticut Sun (CON) -> Houston Comets (HOU), 2027.

The real WNBA checker runs with its real TEAMS and FRANCHISE_TRANSITIONS inside a temporary folder;
only the network fetch is replaced. On Windows the folder is addressed with an extended-length path
(\\\\?\\...) so files named CON.json can exist there, as they do in the repository on GitHub's runners.

Run from the repository root:  python -m unittest discover -s tests -v
"""
import importlib.util
import json
import os
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
    import requests  # noqa: F401  (the checker imports it; the tests never use it)
except ImportError:
    sys.modules["requests"] = types.ModuleType("requests")

import roster_safety  # noqa: E402

# The last good Connecticut roster (14 players) as the checker saved it on 2026-11-09.
CON_CHECKED = "2026-11-09T19:35:03.536610+00:00"
CON_PLAYERS = [(f"Sun Player {i:02d}", "GF"[i % 2], str(i + 1)) for i in range(14)]
CON_LOG = [  # Connecticut's own change history (kept unchanged)
    {"timestamp": f"2026-08-{day:02d}T10:42:08.904902+00:00", "teamCode": "CON", "teamName": "Connecticut Sun",
     "type": "added", "playerName": f"Sun Player {day:02d}", "position": "G", "oldValue": "",
     "newValue": "ACTIVE", "source": "ESPN WNBA API", "severity": "low"}
    for day in (20, 15, 10, 5, 3, 1)
]
OLD_ISSUE = {"timestamp": "2026-11-20T17:37:27.560330+00:00", "teamCode": "CON", "teamName": "Connecticut Sun",
             "kind": "fetch_error", "message": "400 Client Error: Bad Request", "previousCount": 14,
             "fetchedCount": None, "lastSuccessfulCheckedAt": CON_CHECKED, "source": "ESPN WNBA API"}


def load_checker():
    spec = importlib.util.spec_from_file_location("checker_wnba_franchise", SCRIPTS / "check_wnba_rosters.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def device_safe(path):
    """Path that can hold reserved device names such as CON.json (Windows needs the \\\\?\\ prefix)."""
    path = Path(path).resolve()
    return Path("\\\\?\\" + str(path)) if os.name == "nt" else path


def roster(code, team, players):
    return {"code": code, "team": team, "source": "ESPN WNBA API", "url": f"about:{code}",
            "checkedAt": roster_safety.now_iso(),
            "players": [{"name": n, "position": p, "number": num, "status": "ACTIVE", "source": "ESPN WNBA API"}
                        for n, p, num in sorted(players)]}


class Site:
    """The WNBA checker pointed at a temporary repository copy."""

    def __init__(self, tmp):
        self.m = m = load_checker()
        root = device_safe(tmp)
        m.ROOT = root
        m.DATA = root / "data" / "wnba"
        m.LATEST = m.DATA / "latest"
        m.STATE = m.DATA / "state"
        m.LOGS = root / "logs" / "wnba"
        self.responses = {}      # code -> list of players, or an Exception
        self.fetched = []
        m.fetch_roster = self._fetch

    def _fetch(self, team):
        code = team["code"]
        self.fetched.append(code)
        answer = self.responses.get(code, [(f"{code} Player {i}", "G", str(i)) for i in range(12)])
        if isinstance(answer, Exception):
            raise answer
        return roster(code, team["name"], answer)

    def seed_connecticut(self, with_state=True):
        m = self.m
        con = {**roster("CON", "Connecticut Sun", CON_PLAYERS), "checkedAt": CON_CHECKED,
               "url": "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/teams/CON/roster"}
        if with_state:
            roster_safety.save_json(m.STATE / "CON.json", con)
            roster_safety.save_json(m.LATEST / "CON.json", con)
        roster_safety.save_json(m.LOGS / "CON.json", CON_LOG)
        roster_safety.save_json(m.LOGS / "all_changes.json", list(CON_LOG))
        roster_safety.save_json(m.DATA / "source_issues.json", [OLD_ISSUE])

    def run(self, **responses):
        self.responses.update(responses)
        self.fetched = []
        with mock.patch("time.sleep"), mock.patch("builtins.print"):
            self.m.run()

    def read(self, *parts):
        path = self.m.ROOT.joinpath(*parts)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def raw(self, *parts):
        path = self.m.ROOT.joinpath(*parts)
        return path.read_bytes() if path.exists() else None

    def status(self, code):
        return next((row for row in self.read("data", "wnba", "status.json") if row["teamCode"] == code), None)

    def transitions(self):
        return [i for i in self.read("data", "wnba", "source_issues.json") or [] if i["kind"] == "franchise_transition"]


class SiteTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.site = Site(self._tmp.name)

    def tearDown(self):
        # the extended-path files are removed with the folder (TemporaryDirectory handles CON.json via
        # the prefixed path below on Windows)
        import shutil
        shutil.rmtree(device_safe(self._tmp.name), ignore_errors=True)
        self._tmp.cleanup()


class TeamConfiguration(unittest.TestCase):
    def setUp(self):
        self.m = load_checker()

    def test_houston_replaces_connecticut_in_the_active_list(self):
        codes = [team["code"] for team in self.m.TEAMS]
        self.assertIn("HOU", codes)
        self.assertNotIn("CON", codes)
        self.assertEqual(len(codes), 15)
        self.assertEqual(len(set(codes)), 15)
        self.m.validate_teams(self.m.TEAMS, self.m.FRANCHISE_TRANSITIONS)

    def test_transition_is_the_verified_espn_lineage(self):
        (t,) = self.m.FRANCHISE_TRANSITIONS
        self.assertEqual((t["from"]["code"], t["from"]["espnId"], t["from"]["name"]), ("CON", "18", "Connecticut Sun"))
        self.assertEqual((t["to"]["code"], t["to"]["espnId"], t["to"]["name"]), ("HOU", "4", "Houston Comets"))
        self.assertEqual(t["effectiveSeason"], 2027)

    def test_both_versions_of_one_franchise_cannot_be_active(self):
        teams = self.m.TEAMS + [{"code": "CON", "name": "Connecticut Sun"}]
        with self.assertRaisesRegex(ValueError, "same franchise"):
            self.m.validate_teams(teams, self.m.FRANCHISE_TRANSITIONS)

    def test_duplicate_codes_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.m.validate_teams(self.m.TEAMS + [{"code": "HOU", "name": "Houston Comets"}], [])

    def test_a_duplicate_franchise_stops_the_run_before_anything_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Site(tmp)
            site.m.TEAMS = site.m.TEAMS + [{"code": "CON", "name": "Connecticut Sun"}]
            with self.assertRaises(ValueError), mock.patch("builtins.print"):
                site.m.run()
            self.assertEqual(site.fetched, [])
            self.assertIsNone(site.read("data", "wnba", "status.json"))


class Activation(SiteTest):
    def test_inherited_roster_is_not_logged_as_mass_changes(self):
        s = self.site
        s.seed_connecticut()
        s.run(HOU=CON_PLAYERS)
        self.assertIsNone(s.read("logs", "wnba", "HOU.json"))                  # nothing logged for HOU
        self.assertEqual(s.read("logs", "wnba", "all_changes.json"), CON_LOG)  # no new entries at all
        hou = s.status("HOU")
        self.assertEqual((hou["playerCount"], hou["lastChangeCount"], hou["lastError"], hou["sourceIssue"]),
                         (14, 0, "", ""))
        self.assertEqual(hou["teamName"], "Houston Comets")
        self.assertIsNone(s.status("CON"))                                     # no duplicate active team
        summary = s.read("data", "wnba", "summary.json")
        self.assertEqual((summary["teamsChecked"], summary["teamsOk"], summary["teamsErrored"]), (15, 15, 0))
        self.assertEqual(len(s.read("data", "wnba", "status.json")), 15)
        self.assertNotIn("CON", s.fetched)

    def test_connecticut_history_is_preserved_byte_for_byte(self):
        s = self.site
        s.seed_connecticut()
        before = {p: s.raw(*p) for p in (("data", "wnba", "state", "CON.json"), ("data", "wnba", "latest", "CON.json"),
                                          ("logs", "wnba", "CON.json"))}
        self.assertTrue(all(content for content in before.values()))  # the CON files really exist
        s.run(HOU=CON_PLAYERS)
        s.run(HOU=CON_PLAYERS[:-1] + [("Comets Rookie", "G", "99")])
        for path, content in before.items():
            self.assertEqual(s.raw(*path), content, path)
        changes = s.read("logs", "wnba", "all_changes.json")
        self.assertEqual([c for c in changes if c["teamCode"] == "CON"], CON_LOG)
        self.assertTrue(all(c["teamName"] == "Connecticut Sun" for c in changes if c["teamCode"] == "CON"))
        self.assertIn(OLD_ISSUE, s.read("data", "wnba", "source_issues.json"))

    def test_transition_is_recorded_once_as_an_audit_event(self):
        s = self.site
        s.seed_connecticut()
        s.run(HOU=CON_PLAYERS)
        s.run(HOU=CON_PLAYERS)
        (event,) = s.transitions()
        self.assertEqual((event["fromCode"], event["toCode"], event["effectiveSeason"], event["previousCount"]),
                         ("CON", "HOU", 2027, 14))
        self.assertEqual(event["lastSuccessfulCheckedAt"], CON_CHECKED)
        state = s.read("data", "wnba", "state", "HOU.json")
        self.assertEqual(state["code"], "HOU")

    def test_baseline_carries_its_lineage_until_the_first_houston_fetch(self):
        s = self.site
        s.seed_connecticut()
        s.run(HOU=RuntimeError("ESPN down"))
        state = s.read("data", "wnba", "state", "HOU.json")
        self.assertEqual(state["inheritedFrom"], {"code": "CON", "team": "Connecticut Sun",
                                                  "checkedAt": CON_CHECKED, "effectiveSeason": 2027})
        self.assertEqual(sorted(p["name"] for p in state["players"]), sorted(n for n, _, _ in CON_PLAYERS))
        self.assertEqual(s.read("data", "wnba", "latest", "HOU.json"), state)  # page shows the roster meanwhile
        hou = s.status("HOU")
        self.assertEqual((hou["sourceIssue"], hou["playerCount"], hou["checkedAt"]), ("fetch_error", 14, CON_CHECKED))


class SafetyGuardForHouston(SiteTest):
    def test_empty_houston_response_cannot_overwrite_the_inherited_roster(self):
        s = self.site
        s.seed_connecticut()
        s.run(HOU=[])
        self.assertEqual(len(s.read("data", "wnba", "state", "HOU.json")["players"]), 14)
        self.assertEqual(s.status("HOU")["sourceIssue"], "empty_response")
        self.assertIsNone(s.read("logs", "wnba", "HOU.json"))
        self.assertEqual(s.read("logs", "wnba", "all_changes.json"), CON_LOG)

    def test_partial_houston_response_is_held_by_the_guard(self):
        s = self.site
        s.seed_connecticut()
        s.run(HOU=CON_PLAYERS[:3])  # 14 -> 3
        self.assertEqual(len(s.read("data", "wnba", "state", "HOU.json")["players"]), 14)
        self.assertEqual(s.status("HOU")["sourceIssue"], "suspicious_drop")
        self.assertIsNotNone(s.read("data", "wnba", "state", "pending", "HOU.json"))
        self.assertIsNone(s.read("logs", "wnba", "HOU.json"))

    def test_without_a_predecessor_roster_nothing_is_inherited_and_empty_is_still_rejected(self):
        s = self.site
        s.seed_connecticut(with_state=False)
        s.run(HOU=[])
        self.assertEqual(s.transitions(), [])
        self.assertIsNone(s.read("data", "wnba", "state", "HOU.json"))
        self.assertEqual(s.status("HOU")["sourceIssue"], "empty_response")

    def test_houston_with_its_own_saved_roster_is_never_reseeded(self):
        s = self.site
        s.seed_connecticut()
        own = roster("HOU", "Houston Comets", [("Comets A", "G", "1"), ("Comets B", "F", "2")])
        roster_safety.save_json(s.m.STATE / "HOU.json", own)
        s.run(HOU=[("Comets A", "G", "1"), ("Comets B", "F", "2")])
        self.assertEqual(s.transitions(), [])
        self.assertEqual(sorted(p["name"] for p in s.read("data", "wnba", "state", "HOU.json")["players"]),
                         ["Comets A", "Comets B"])

    def test_a_failed_migration_blocks_only_houston_for_that_run(self):
        s = self.site
        s.seed_connecticut()
        real_save = s.m.save_json

        def failing_save(path, data):
            if Path(path).name == "HOU.json":
                raise OSError("disk full")
            return real_save(path, data)

        with mock.patch.object(s.m, "save_json", failing_save):
            s.run(HOU=CON_PLAYERS[:3])
        self.assertNotIn("HOU", s.fetched)          # a partial first fetch was never accepted
        self.assertIn("ATL", s.fetched)             # the rest of the league still ran
        hou = s.status("HOU")
        self.assertEqual(hou["sourceIssue"], "processing_error")
        self.assertIn("franchise transition", hou["lastError"])
        self.assertIsNone(s.read("data", "wnba", "state", "HOU.json"))


class LaterHoustonChanges(SiteTest):
    def test_genuine_houston_changes_are_logged_normally(self):
        s = self.site
        s.seed_connecticut()
        s.run(HOU=CON_PLAYERS)
        later = [p for p in CON_PLAYERS if p[0] != "Sun Player 05"] + [("Comets Rookie", "G", "99")]
        later = [(n, pos, "77") if n == "Sun Player 07" else (n, pos, num) for n, pos, num in later]
        s.run(HOU=later)
        log = s.read("logs", "wnba", "HOU.json")
        kinds = sorted((c["type"], c["playerName"]) for c in log)
        self.assertEqual(kinds, [("added", "Comets Rookie"), ("number changed", "Sun Player 07"),
                                 ("removed", "Sun Player 05")])
        self.assertTrue(all(c["teamCode"] == "HOU" and c["teamName"] == "Houston Comets" for c in log))
        all_changes = s.read("logs", "wnba", "all_changes.json")
        self.assertEqual(all_changes[:3], log)
        self.assertEqual(all_changes[3:], CON_LOG)
        self.assertEqual(s.read("logs", "wnba", "CON.json"), CON_LOG)
        self.assertEqual(s.status("HOU")["lastChangeCount"], 3)

    def test_changes_between_the_last_connecticut_check_and_the_first_houston_fetch_are_logged(self):
        s = self.site
        s.seed_connecticut()
        s.run(HOU=CON_PLAYERS + [("Offseason Signing", "F", "50")])
        (change,) = s.read("logs", "wnba", "HOU.json")
        self.assertEqual((change["type"], change["playerName"], change["teamCode"]), ("added", "Offseason Signing", "HOU"))


if __name__ == "__main__":
    unittest.main()
