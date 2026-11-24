"""Synthetic tests for the roster safety guard (scripts/roster_safety.py) and its use in all six
league checkers. No network: every checker's fetch is replaced, and each test runs the checker's
real run() inside a temporary folder.

Run from the repository root:  python -m unittest discover -s tests -v
"""
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

# The checkers import these at module level; the tests never use them (fetches are replaced),
# so stand-ins are used only when the real packages are not installed.
for name in ("requests", "bs4"):
    try:
        __import__(name)
    except ImportError:
        stub = types.ModuleType(name)
        if name == "bs4":
            stub.BeautifulSoup = object
        sys.modules[name] = stub

import roster_safety  # noqa: E402

CHECKERS = {
    "nfl": "check_rosters.py",
    "nba": "check_nba_rosters.py",
    "wnba": "check_wnba_rosters.py",
    "mlb": "check_mlb_rosters.py",
    "nhl": "check_nhl_rosters.py",
    "ncaaf": "check_ncaaf_rosters.py",
    "ncaab": "check_ncaab_rosters.py",
}
CODE = "TST"
BASE = [f"Player {i:02d}" for i in range(60)]
BIG_DROP = BASE[:24]  # 60 -> 24: -60% and -36 players, above every league's limits


def jersey(name):
    """Stable synthetic jersey number per player, so only the intended changes appear."""
    return str(sum(map(ord, name)) % 100)


def removal(name, when):
    return {"timestamp": when, "teamCode": CODE, "teamName": "Test Team", "type": "removed",
            "playerName": name, "position": "QB", "oldValue": "ACTIVE", "newValue": "",
            "source": "synthetic", "severity": "medium"}


DECOY = {"timestamp": "2026-11-16T00:00:00+00:00", "teamCode": CODE, "teamName": "Test Team", "type": "added",
         "playerName": "Decoy Only In All Changes", "position": "QB", "oldValue": "", "newValue": "ACTIVE",
         "source": "synthetic", "severity": "low"}


def load_checker(league):
    spec = importlib.util.spec_from_file_location(f"checker_{league}", SCRIPTS / CHECKERS[league])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Harness:
    """Runs one league checker for a single synthetic team inside a temp directory."""

    def __init__(self, league, tmp):
        self.league = league
        self.module = load_checker(league)
        self.response = None
        root = Path(tmp)
        (root / "data").mkdir()
        (root / "logs").mkdir()
        m = self.module
        m.ROOT = root
        m.DATA = root / "data" / league
        m.LATEST = m.DATA / "latest"
        m.STATE = m.DATA / "state"
        m.LOGS = root / "logs" / league
        if league == "nfl":
            m.TEAMS = [(CODE, "Test Team", "test-team")]
            m.fetch_html = self._fake_fetch_html
            m.parse_roster = self._fake_parse_roster
        elif league == "ncaaf":
            self.ncaaf_team = {"id": "999", "code": CODE, "name": "Test University", "logo": ""}
            m.fetch_teams = lambda: [dict(self.ncaaf_team)]
            m.fetch_roster = self._fake_fetch_roster
        elif league == "ncaab":
            m.TEAMS_FILE, m.BUNDLE_FILE = m.DATA / "d1_teams.json", m.DATA / "rosters.json"
            m.TEAM_PAUSE = 0
            m.fetch_teams = lambda: [{"id": "999", "code": CODE, "name": "Test University", "conference": "Test Conference",
                                      "conferenceId": "1", "logo": ""}]
            m.fetch_roster = self._fake_fetch_roster
        else:
            m.TEAMS = [{"code": CODE, "name": "Test Team", "id": 1}]
            m.fetch_roster = self._fake_fetch_roster

    # --- fakes -------------------------------------------------------------------------------
    def _roster(self, names):
        return {
            "code": CODE,
            "team": "Test Team",
            "source": "synthetic",
            "url": "about:blank",
            "checkedAt": roster_safety.now_iso(),
            "players": [{"name": n, "position": "QB", "number": jersey(n), "status": "ACTIVE", "source": "synthetic"}
                        for n in sorted(names)],
        }

    def _next(self):
        if isinstance(self.response, Exception):
            raise self.response
        return self._roster(self.response)

    def _fake_fetch_html(self, url):
        if isinstance(self.response, Exception):
            raise self.response
        return "<html></html>"

    def _fake_parse_roster(self, html, code, team_name, slug):
        return self._roster(self.response)

    def _fake_fetch_roster(self, team):
        return self._next()

    # --- running and reading results -----------------------------------------------------
    def run(self, response):
        self.response = response
        with mock.patch("time.sleep"), mock.patch("builtins.print"):
            self.module.run()

    def read(self, path):
        path = Path(path)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    @property
    def status(self):
        return self.read(self.module.DATA / "status.json")[0]

    @property
    def summary(self):
        return self.read(self.module.DATA / "summary.json")

    @property
    def latest(self):
        return self.read(self.module.LATEST / f"{CODE}.json")

    @property
    def state(self):
        return self.read(self.module.STATE / f"{CODE}.json")

    @property
    def pending(self):
        return self.read(self.module.STATE / "pending" / f"{CODE}.json")

    @property
    def all_changes(self):
        return self.read(self.module.LOGS / "all_changes.json") or []

    @property
    def team_log(self):
        return self.read(self.module.LOGS / f"{CODE}.json") or []

    @property
    def issues(self):
        return self.read(self.module.DATA / "source_issues.json") or []

    def names(self, roster):
        return sorted(p["name"] for p in roster["players"])

    # --- legacy bad state (written by the checkers before the safety guard) --------------
    EMPTY_SINCE = "2026-11-15T16:26:47.484766+00:00"
    WIPE_AT = "2026-11-15T16:26:47.490000+00:00"

    def seed_legacy_empty(self, wiped=None, history=None):
        """Save an empty roster the way the old checkers did. wiped: names the wipe run logged as
        removed (None = no log at all). history: older log entries kept below the wipe run."""
        m = self.module
        empty = {"code": CODE, "team": "Test Team", "source": "synthetic", "url": "about:blank",
                 "checkedAt": self.EMPTY_SINCE, "players": []}
        roster_safety.save_json(m.STATE / f"{CODE}.json", empty)
        roster_safety.save_json(m.LATEST / f"{CODE}.json", empty)
        if wiped is None and history is None:
            roster_safety.save_json(m.LOGS / "all_changes.json", [DECOY])
            return
        wipe = [removal(n, self.WIPE_AT) for n in (wiped or [])]
        log = wipe + (history or [])
        roster_safety.save_json(m.LOGS / f"{CODE}.json", log)
        # all_changes.json gets a newer, different entry for this team: if the guard read the
        # league-wide log instead of the team log, the newest run would not be a wipe.
        roster_safety.save_json(m.LOGS / "all_changes.json", [DECOY] + log)


def for_each_league(scenario):
    """Run a test scenario once per league checker, each in its own temporary folder."""
    def test(self):
        for league in CHECKERS:
            with self.subTest(league=league), tempfile.TemporaryDirectory() as tmp:
                scenario(self, Harness(league, tmp))
    test.__name__ = scenario.__name__
    test.__doc__ = scenario.__doc__
    return test


class GuardBehaviourInEveryChecker(unittest.TestCase):
    """Every scenario runs against all six league checkers."""

    def assert_healthy(self, h, count):
        s = h.status
        self.assertEqual(s["lastError"], "")
        self.assertEqual(s["sourceIssue"], "")
        self.assertEqual(s["playerCount"], count)
        self.assertEqual(s["checkedAt"], h.latest["checkedAt"])
        self.assertEqual(s["lastSuccessfulCheckedAt"], s["checkedAt"])
        self.assertTrue(s["lastAttemptedAt"])

    def assert_kept_previous(self, h, good, kind):
        """Previous roster preserved, no changes logged, successful timestamp unchanged."""
        s = h.status
        self.assertEqual(h.latest, good)
        self.assertEqual(h.state, good)
        self.assertEqual(s["sourceIssue"], kind)
        self.assertTrue(s["lastError"])
        self.assertEqual(s["playerCount"], len(good["players"]))
        self.assertEqual(s["checkedAt"], good["checkedAt"])
        self.assertEqual(s["lastSuccessfulCheckedAt"], good["checkedAt"])
        self.assertGreaterEqual(s["lastAttemptedAt"], good["checkedAt"])
        self.assertEqual(s["lastChangeCount"], 0)
        self.assertEqual(h.summary["teamsErrored"], 1)
        self.assertEqual(h.summary["teamsOk"], 0)

    @for_each_league
    def test_first_run_creates_baseline_without_changes(self, h):
        h.run(BASE)
        self.assert_healthy(h, 60)
        self.assertEqual(h.all_changes, [])
        self.assertEqual(h.issues, [])

    @for_each_league
    def test_normal_update_two_swapped_players(self, h):
        h.run(BASE)
        h.run(BASE[2:] + ["New A", "New B"])
        self.assert_healthy(h, 60)
        types_ = sorted(c["type"] for c in h.all_changes)
        self.assertEqual(types_, ["added", "added", "removed", "removed"])
        self.assertEqual(len(h.team_log), 4)

    @for_each_league
    def test_one_player_added(self, h):
        h.run(BASE)
        h.run(BASE + ["Rookie"])
        self.assert_healthy(h, 61)
        self.assertEqual([(c["type"], c["playerName"]) for c in h.all_changes], [("added", "Rookie")])

    @for_each_league
    def test_one_player_removed(self, h):
        h.run(BASE)
        h.run(BASE[1:])
        self.assert_healthy(h, 59)
        self.assertEqual([(c["type"], c["playerName"]) for c in h.all_changes], [("removed", BASE[0])])
        self.assertNotIn("confirmedAfterHold", h.all_changes[0])

    @for_each_league
    def test_fetch_exception_keeps_previous_roster(self, h):
        h.run(BASE)
        good = h.latest
        h.run(RuntimeError("503 Service Unavailable"))
        self.assert_kept_previous(h, good, "fetch_error")
        self.assertIn("503", h.status["lastError"])
        self.assertEqual(h.all_changes, [])
        self.assertEqual([i["kind"] for i in h.issues], ["fetch_error"])
        self.assertEqual(h.issues[0]["previousCount"], 60)
        self.assertIsNone(h.issues[0]["fetchedCount"])

    @for_each_league
    def test_empty_response_keeps_previous_roster(self, h):
        h.run(BASE)
        good = h.latest
        h.run([])
        self.assert_kept_previous(h, good, "empty_response")
        self.assertEqual(h.all_changes, [], "no fake removals")
        self.assertEqual(h.issues[0]["kind"], "empty_response")
        self.assertEqual(h.issues[0]["fetchedCount"], 0)

    @for_each_league
    def test_first_run_empty_saves_nothing(self, h):
        h.run([])
        self.assertIsNone(h.latest)
        self.assertIsNone(h.state)
        s = h.status
        self.assertEqual((s["sourceIssue"], s["playerCount"], s["checkedAt"]), ("empty_response", 0, ""))
        self.assertEqual(h.all_changes, [])
        self.assertEqual(len(h.issues), 1)

    @for_each_league
    def test_suspicious_drop_is_held(self, h):
        h.run(BASE)
        good = h.latest
        h.run(BIG_DROP)
        self.assert_kept_previous(h, good, "suspicious_drop")
        self.assertIn("60 to 24", h.status["lastError"])
        self.assertEqual(h.all_changes, [], "no removals from a held run")
        self.assertEqual(h.summary["teamsNeedingVerification"], 1)
        self.assertEqual(sorted(h.pending["missing"]), sorted(n.lower() for n in BASE[24:]))
        self.assertEqual(h.names(h.pending["candidate"]), sorted(BIG_DROP))
        self.assertEqual(h.issues[0]["kind"], "suspicious_drop")

    @for_each_league
    def test_recovery_after_hold_logs_nothing(self, h):
        h.run(BASE)
        h.run(BIG_DROP)
        h.run(BASE)
        self.assert_healthy(h, 60)
        self.assertEqual(h.all_changes, [])
        self.assertIsNone(h.pending, "hold cleared")

    @for_each_league
    def test_recovery_after_fetch_error_and_empty(self, h):
        h.run(BASE)
        h.run(RuntimeError("timeout"))
        h.run([])
        h.run(BASE + ["Late Signing"])
        self.assert_healthy(h, 61)
        self.assertEqual([c["type"] for c in h.all_changes], ["added"])
        self.assertEqual([i["kind"] for i in h.issues], ["empty_response", "fetch_error"])

    @for_each_league
    def test_second_matching_drop_is_accepted_with_confirmed_flag(self, h):
        h.run(BASE)
        h.run(BIG_DROP)
        h.run(BIG_DROP)
        self.assert_healthy(h, 24)
        removed = [c for c in h.all_changes if c["type"] == "removed"]
        self.assertEqual(len(removed), 36)
        self.assertTrue(all(c["confirmedAfterHold"] is True for c in h.all_changes))
        self.assertTrue(all(c["severity"] == "high" for c in removed))
        self.assertEqual(len(h.team_log), 36)
        self.assertIsNone(h.pending)

    @for_each_league
    def test_confirmation_allows_small_differences(self, h):
        h.run(BASE)
        h.run(BIG_DROP)
        # 2nd run: one more player missing -> overlap 36/37 = 97% >= 90%
        h.run(BIG_DROP[1:])
        self.assert_healthy(h, 23)
        self.assertEqual(sum(1 for c in h.all_changes if c["type"] == "removed"), 37)

    @for_each_league
    def test_different_second_drop_is_held_again(self, h):
        h.run(BASE)
        good = h.latest
        h.run(BIG_DROP)
        h.run(BASE[36:])  # a different set of 36 missing players
        self.assert_kept_previous(h, good, "suspicious_drop")
        self.assertEqual(h.all_changes, [])
        self.assertEqual(h.names(h.pending["candidate"]), sorted(BASE[36:]))
        self.assertEqual(len(h.issues), 2)

    @for_each_league
    def test_fetch_error_between_hold_and_confirmation_keeps_hold(self, h):
        h.run(BASE)
        h.run(BIG_DROP)
        h.run(RuntimeError("timeout"))
        self.assertIsNotNone(h.pending)
        h.run(BIG_DROP)
        self.assert_healthy(h, 24)
        self.assertEqual(sum(1 for c in h.all_changes if c["type"] == "removed"), 36)

    @for_each_league
    def test_previous_roster_with_history_never_rewritten_on_failure(self, h):
        h.run(BASE)
        h.run(BASE[1:])  # one real removal logged
        log_before = h.all_changes
        good = h.latest
        for bad in (RuntimeError("x"), [], BIG_DROP):
            h.run(bad)
            self.assertEqual(h.latest, good)
            self.assertEqual(h.all_changes, log_before)


OLD_ADD = {"timestamp": "2026-08-01T09:00:00+00:00", "teamCode": CODE, "teamName": "Test Team",
           "type": "added", "playerName": "Old Signing", "position": "QB", "oldValue": "",
           "newValue": "ACTIVE", "source": "synthetic", "severity": "low"}


class BaselineRecoveryFromLegacyEmptyRoster(unittest.TestCase):
    """An empty saved roster left by the pre-guard checkers, then a valid roster again."""

    def assert_recovered(self, h, count, evidence):
        s = h.status
        self.assertEqual((s["lastError"], s["sourceIssue"], s["playerCount"]), ("", "", count))
        self.assertEqual(s["checkedAt"], h.latest["checkedAt"])
        self.assertEqual(s["lastSuccessfulCheckedAt"], s["checkedAt"])
        self.assertGreater(s["checkedAt"], Harness.EMPTY_SINCE)
        self.assertEqual(h.names(h.latest), h.names(h.state))
        self.assertEqual(len(h.latest["players"]), count)
        self.assertEqual((h.summary["teamsErrored"], h.summary["teamsOk"]), (0, 1))
        self.assertIsNone(h.pending)
        record = h.issues[0]
        self.assertEqual((record["kind"], record["evidence"]), ("baseline_recovered", evidence))
        self.assertEqual((record["teamCode"], record["fetchedCount"], record["previousCount"]), (CODE, count, 0))
        self.assertTrue(record["teamName"] and record["source"])
        self.assertEqual(record["emptySince"], Harness.EMPTY_SINCE)
        self.assertEqual(record["lastSuccessfulCheckedAt"], h.latest["checkedAt"])
        self.assertEqual(s["lastChangeCount"], record["newPlayersLogged"])
        return record

    @for_each_league
    def test_logged_wipe_recovery_logs_no_mass_additions(self, h):
        h.seed_legacy_empty(wiped=BASE, history=[OLD_ADD])
        log_before, all_before = h.team_log, h.all_changes
        h.run(BASE)
        record = self.assert_recovered(h, 60, "logged_wipe")
        self.assertEqual(h.team_log, log_before, "history untouched, no additions")
        self.assertEqual(h.all_changes, all_before)
        self.assertEqual(h.status["lastChangeCount"], 0)
        self.assertEqual((record["wipedPlayers"], record["returnedPlayers"], record["newPlayersLogged"]), (60, 60, 0))
        self.assertEqual(record["wipeLoggedAt"], Harness.WIPE_AT)
        self.assertFalse(record["confirmedAfterHold"])
        self.assertIn("removed all 60 players", record["message"])

    @for_each_league
    def test_logged_wipe_recovery_still_logs_genuinely_new_players(self, h):
        h.seed_legacy_empty(wiped=BASE)
        log_before = h.team_log
        h.run(BASE[1:] + ["Fresh Recruit"])  # one wiped player never came back, one is new
        record = self.assert_recovered(h, 60, "logged_wipe")
        new = h.team_log[:len(h.team_log) - len(log_before)]
        self.assertEqual([(c["type"], c["playerName"]) for c in new], [("added", "Fresh Recruit")])
        self.assertTrue(new[0]["afterBaselineRecovery"])
        self.assertEqual(h.team_log[len(new):], log_before, "older history preserved as it was")
        self.assertEqual((record["returnedPlayers"], record["newPlayersLogged"]), (59, 1))

    @for_each_league
    def test_partial_fetch_after_logged_wipe_is_held_not_saved(self, h):
        h.seed_legacy_empty(wiped=BASE)
        all_before = h.all_changes
        h.run(BIG_DROP)  # only 24 of the 60 wiped players
        s = h.status
        self.assertEqual((s["sourceIssue"], s["playerCount"], s["checkedAt"]), ("suspicious_drop", 0, Harness.EMPTY_SINCE))
        self.assertIn("only 24 of the 60 players", s["lastError"])
        self.assertEqual(h.latest["players"], [], "partial roster not saved as known-good")
        self.assertEqual(h.state["players"], [])
        self.assertEqual(h.all_changes, all_before)
        self.assertEqual(h.pending["reference"], "logged_wipe")
        self.assertEqual(h.pending["previousCount"], 60)
        self.assertEqual([i["kind"] for i in h.issues], ["suspicious_drop"])
        self.assertEqual(h.summary["teamsNeedingVerification"], 1)
        h.run(BASE)  # the full roster comes back
        record = self.assert_recovered(h, 60, "logged_wipe")
        self.assertEqual(h.all_changes, all_before, "no added storm after the hold")
        self.assertEqual(record["newPlayersLogged"], 0)

    @for_each_league
    def test_partial_fetch_after_logged_wipe_confirmed_by_second_run(self, h):
        h.seed_legacy_empty(wiped=BASE)
        all_before = h.all_changes
        h.run(BIG_DROP)
        h.run(BIG_DROP)
        record = self.assert_recovered(h, 24, "logged_wipe")
        self.assertTrue(record["confirmedAfterHold"])
        self.assertEqual((record["returnedPlayers"], record["newPlayersLogged"]), (24, 0))
        self.assertEqual(h.all_changes, all_before, "the 36 missing players were already logged as removed")

    @for_each_league
    def test_wipe_spread_over_two_runs_counts_as_one_wipe(self, h):
        earlier = [removal(n, "2026-11-14T16:00:00+00:00") for n in BASE[:57]]
        h.seed_legacy_empty(wiped=BASE[57:], history=earlier + [OLD_ADD])
        all_before = h.all_changes
        h.run(BASE)
        record = self.assert_recovered(h, 60, "logged_wipe")
        self.assertEqual((record["wipedPlayers"], record["returnedPlayers"], record["newPlayersLogged"]), (60, 60, 0))
        self.assertEqual(h.all_changes, all_before)

    @for_each_league
    def test_never_populated_empty_roster_becomes_first_baseline(self, h):
        h.seed_legacy_empty()  # empty saved roster, no team log at all
        all_before = h.all_changes
        h.run(BASE)
        record = self.assert_recovered(h, 60, "never_populated")
        self.assertEqual(h.all_changes, all_before)
        self.assertEqual(h.team_log, [])
        self.assertEqual(record["newPlayersLogged"], 0)

    @for_each_league
    def test_first_ever_run_is_normal_baseline_without_recovery_record(self, h):
        h.run(BASE)  # no saved roster at all
        self.assertEqual(h.status["sourceIssue"], "")
        self.assertEqual(h.all_changes, [])
        self.assertEqual(h.issues, [], "a genuine first run is not a recovery")

    @for_each_league
    def test_small_non_empty_previous_roster_still_logs_additions(self, h):
        h.run(BASE[:2])
        h.run(BASE)
        self.assertEqual(sorted(c["playerName"] for c in h.all_changes), sorted(BASE[2:]))
        self.assertTrue(all(c["type"] == "added" and "afterBaselineRecovery" not in c for c in h.all_changes))
        self.assertEqual(h.issues, [])

    @for_each_league
    def test_empty_roster_without_proof_uses_normal_comparison(self, h):
        # Inconsistent history: the newest logged run is an addition, yet the saved roster is empty.
        h.seed_legacy_empty(history=[dict(OLD_ADD, timestamp="2026-11-15T16:00:00+00:00")])
        before = len(h.team_log)
        h.run(BASE)
        new = h.team_log[:len(h.team_log) - before]
        self.assertEqual(sorted(c["playerName"] for c in new if c["type"] == "added"), sorted(BASE))
        self.assertEqual(h.issues, [], "no recovery without evidence")

    @for_each_league
    def test_unreadable_team_log_is_a_processing_issue_not_a_crash(self, h):
        h.seed_legacy_empty(wiped=BASE)
        (h.module.LOGS / f"{CODE}.json").write_text("{ not json", encoding="utf-8")
        h.run(BASE)  # must not raise
        s = h.status
        self.assertEqual(s["sourceIssue"], "processing_error")
        self.assertEqual(h.latest["players"], [], "no roster saved when its changes could not be logged")
        self.assertEqual(h.issues[0]["kind"], "processing_error")

    @for_each_league
    def test_empty_fetch_while_legacy_empty_then_recovery(self, h):
        h.seed_legacy_empty(wiped=BASE)
        all_before = h.all_changes
        h.run([])
        self.assertEqual(h.status["sourceIssue"], "empty_response")
        self.assertEqual(h.latest["players"], [])
        h.run(BASE)
        self.assert_recovered(h, 60, "logged_wipe")
        self.assertEqual([i["kind"] for i in h.issues], ["baseline_recovered", "empty_response"])
        self.assertEqual(h.all_changes, all_before)

    @for_each_league
    def test_normal_operation_resumes_after_recovery(self, h):
        h.seed_legacy_empty(wiped=BASE)
        h.run(BASE)
        all_before = h.all_changes
        h.run(BASE[1:])
        new = h.all_changes[:len(h.all_changes) - len(all_before)]
        self.assertEqual([(c["type"], c["playerName"]) for c in new], [("removed", BASE[0])])
        self.assertNotIn("afterBaselineRecovery", new[0])
        self.assertEqual(h.all_changes[len(new):], all_before)
        self.assertEqual(len(h.issues), 1)


class RecoveryDetails(unittest.TestCase):
    def test_returning_players_status_and_position_differences_are_counted_not_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            diff = Harness("nba", tmp).module.diff_rosters
        wipe = [removal("Alpha", Harness.WIPE_AT), removal("Bravo", Harness.WIPE_AT)]
        candidate = {"code": CODE, "team": "Test Team", "players": [
            {"name": "Alpha", "position": "QB", "number": "1", "status": "ACTIVE"},
            {"name": "Bravo", "position": "WR", "number": "2", "status": "IR"},
            {"name": "Charlie", "position": "QB", "number": "3", "status": "ACTIVE"}]}
        changes, differences = roster_safety.recovery_changes(diff, candidate, wipe)
        self.assertEqual([(c["type"], c["playerName"]) for c in changes], [("added", "Charlie")])
        self.assertTrue(changes[0]["afterBaselineRecovery"])
        self.assertEqual(differences, {"statusDifferences": 1, "positionDifferences": 1})

    def test_no_team_log_path_means_no_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, latest = Path(tmp) / "state", Path(tmp) / "latest"
            roster_safety.save_json(state / f"{CODE}.json", {"code": CODE, "team": "T", "checkedAt": "x", "players": []})
            candidate = {"code": CODE, "team": "T", "checkedAt": roster_safety.now_iso(),
                         "players": [{"name": n} for n in BASE]}
            diff = lambda old, new: [{"type": "added", "playerName": p["name"]} for p in new["players"]]
            out = roster_safety.guarded_update(league="ncaaf", code=CODE, fetch=lambda: candidate, diff=diff,
                                               state_dir=state, latest_dir=latest)
        self.assertTrue(out["accepted"])
        self.assertEqual(out["recovered"], "")
        self.assertEqual(len(out["changes"]), 60, "without evidence the normal comparison runs")

    def test_unexpected_error_is_contained_and_keeps_previous_roster(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, latest = Path(tmp) / "state", Path(tmp) / "latest"
            good = {"code": CODE, "team": "T", "checkedAt": "2026-11-15T00:00:00+00:00", "players": [{"name": "A"}]}
            roster_safety.save_json(state / f"{CODE}.json", good)

            def broken_diff(old, new):
                raise KeyError("name")
            out = roster_safety.guarded_update(
                league="nba", code=CODE, diff=broken_diff, state_dir=state, latest_dir=latest,
                fetch=lambda: {"code": CODE, "team": "T", "checkedAt": "now", "players": [{"name": "A"}, {"name": "B"}]})
            self.assertEqual((out["accepted"], out["kind"]), (False, "processing_error"))
            self.assertEqual(roster_safety.load_json(state / f"{CODE}.json"), good)
            self.assertEqual(roster_safety.status_fields(out)["checkedAt"], good["checkedAt"])

    def test_failed_log_write_does_not_save_the_new_roster(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, latest = Path(tmp) / "state", Path(tmp) / "latest"
            good = {"code": CODE, "team": "T", "checkedAt": "2026-11-15T00:00:00+00:00", "players": [{"name": "A"}]}
            roster_safety.save_json(state / f"{CODE}.json", good)

            def failing_log(changes):
                raise OSError("disk full")
            out = roster_safety.guarded_update(
                league="nba", code=CODE, state_dir=state, latest_dir=latest, log_changes=failing_log,
                diff=lambda old, new: [{"type": "added", "playerName": "B"}],
                fetch=lambda: {"code": CODE, "team": "T", "checkedAt": "now", "players": [{"name": "A"}, {"name": "B"}]})
            self.assertEqual(out["kind"], "processing_error")
            self.assertEqual(roster_safety.load_json(state / f"{CODE}.json"), good, "change not lost: next run retries")


class EmptyBaselineEvidence(unittest.TestCase):
    def test_no_entries_means_never_populated_but_unknown_means_no_proof(self):
        self.assertEqual(roster_safety.empty_baseline_evidence([]), ("never_populated", []))
        self.assertEqual(roster_safety.empty_baseline_evidence(None), (None, []))
        self.assertEqual(roster_safety.empty_baseline_evidence({"not": "a list"}), (None, []))

    def test_newest_run_of_removals_is_a_logged_wipe(self):
        log = [removal("A", "2026-11-15T16:26:47.100+00:00"), removal("B", "2026-11-15T16:26:48.900+00:00"),
               dict(OLD_ADD, timestamp="2026-11-12T10:00:00+00:00"), removal("Z", "2026-11-10T10:00:00+00:00")]
        evidence, wipe = roster_safety.empty_baseline_evidence(log)
        self.assertEqual(evidence, "logged_wipe")
        self.assertEqual(sorted(e["playerName"] for e in wipe), ["A", "B"], "stops at the first run that is not a wipe")

    def test_run_window_separates_runs_an_hour_apart(self):
        # A removal run, and an addition one hour earlier: two separate runs, so still a wipe.
        log = [removal("A", "2026-11-15T16:00:00+00:00"), dict(OLD_ADD, timestamp="2026-11-15T15:00:00+00:00")]
        self.assertEqual(roster_safety.empty_baseline_evidence(log)[0], "logged_wipe")
        # Daily runs land at nearly the same clock time: one day and one minute apart is still two runs.
        log = [removal("A", "2026-11-15T16:00:00+00:00"), dict(OLD_ADD, timestamp="2026-11-14T15:59:00+00:00")]
        self.assertEqual(roster_safety.empty_baseline_evidence(log)[0], "logged_wipe")
        # Within the window they are one mixed run: no proof.
        log = [removal("A", "2026-11-15T16:00:00+00:00"), dict(OLD_ADD, timestamp="2026-11-15T15:56:00+00:00")]
        self.assertEqual(roster_safety.empty_baseline_evidence(log), (None, []))

    def test_consecutive_removal_only_runs_are_one_wipe(self):
        log = [removal("A", "2026-11-15T16:00:00+00:00"), removal("B", "2026-11-14T16:00:00+00:00"), dict(OLD_ADD)]
        evidence, wipe = roster_safety.empty_baseline_evidence(log)
        self.assertEqual((evidence, sorted(e["playerName"] for e in wipe)), ("logged_wipe", ["A", "B"]))

    def test_newest_run_with_any_other_change_is_not_proof(self):
        log = [removal("A", "2026-11-15T16:26:47+00:00"), dict(OLD_ADD, timestamp="2026-11-15T16:26:47+00:00")]
        self.assertEqual(roster_safety.empty_baseline_evidence(log), (None, []))

    def test_unreadable_entries_are_not_proof(self):
        self.assertEqual(roster_safety.empty_baseline_evidence([{"type": "removed", "timestamp": "n/a"}]), (None, []))
        self.assertEqual(roster_safety.empty_baseline_evidence(["text"]), (None, []))

    def test_order_and_timezone_format_do_not_matter(self):
        log = [dict(OLD_ADD), removal("A", "2026-11-15T16:26:47Z"), removal("B", "2026-11-15T16:26:47")]
        evidence, wipe = roster_safety.empty_baseline_evidence(log)
        self.assertEqual((evidence, len(wipe)), ("logged_wipe", 2))


class NcaafHistoryRetention(unittest.TestCase):
    """data/ncaaf/fbs_teams.json only selects the teams processed in a run; it never deletes state
    or history. Uses the checker's real fetch_teams() reading a temporary fbs_teams.json."""

    TEAMS = {code: {"id": str(900 + i), "code": code, "name": f"Team {code}", "logo": ""}
             for i, code in enumerate(["TST", "OTH", "AAA", "BBB", "CCC"])}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.h = Harness("ncaaf", self.tmp.name)
        m = self.h.module
        real = load_checker("ncaaf").fetch_teams
        m.fetch_teams = types.FunctionType(real.__code__, vars(m), "fetch_teams")  # reads FBS_TEAMS_FILE
        m.FBS_TEAMS_FILE = m.DATA / "fbs_teams.json"
        m.MIN_TEAMS = 1  # these tests use a handful of synthetic teams; the 120 minimum is tested separately
        self.rosters = {code: [f"{code} Player {i:02d}" for i in range(40)] for code in self.TEAMS}
        # Like the real fetch_roster: the saved roster records the team's ESPN id.
        m.fetch_roster = lambda team: self.h._roster(self.rosters[team["code"]]) | {
            "code": team["code"], "team": team["name"], "teamId": team["id"]}
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, codes):
        roster_safety.save_json(self.h.module.FBS_TEAMS_FILE, [self.TEAMS[c] for c in codes])
        self.h.run(None)

    def snapshot(self):
        """Every file under data/ncaaf and logs/ncaaf -> bytes."""
        return {str(p.relative_to(self.root)).replace("\\", "/"): p.read_bytes()
                for base in ("data/ncaaf", "logs/ncaaf") for p in (self.root / base).rglob("*") if p.is_file()}

    def read(self, rel):
        return json.loads((self.root / rel).read_text(encoding="utf-8"))

    def history_with_changes(self, codes):
        """Two runs so every team has a saved roster and a team log with real entries."""
        self.run_with(codes)
        for code in codes:
            self.rosters[code] = self.rosters[code][1:] + [f"{code} Signing"]
        self.run_with(codes)

    def test_removed_team_keeps_roster_state_team_log_and_all_changes_entries(self):
        self.history_with_changes(["TST", "OTH"])
        before = self.snapshot()
        oth_entries = [c for c in self.read("logs/ncaaf/all_changes.json") if c["teamCode"] == "OTH"]
        self.assertEqual(len(oth_entries), 2)
        self.run_with(["TST"])  # OTH disappears from the team list
        after = self.snapshot()
        for rel in ("data/ncaaf/latest/OTH.json", "data/ncaaf/state/OTH.json", "logs/ncaaf/OTH.json"):
            self.assertEqual(after.get(rel), before[rel], f"{rel} must be untouched")
        self.assertEqual([c for c in self.read("logs/ncaaf/all_changes.json") if c["teamCode"] == "OTH"], oth_entries)
        self.assertEqual([r["teamCode"] for r in self.read("data/ncaaf/status.json")], ["TST"], "OTH not processed")

    def test_current_teams_process_normally_while_another_team_is_absent(self):
        self.history_with_changes(["TST", "OTH"])
        self.rosters["TST"] = self.rosters["TST"] + ["TST Late Add"]
        self.run_with(["TST"])
        newest = self.read("logs/ncaaf/all_changes.json")[0]
        self.assertEqual((newest["teamCode"], newest["type"], newest["playerName"]), ("TST", "added", "TST Late Add"))
        self.assertEqual(self.read("data/ncaaf/status.json")[0]["sourceIssue"], "")

    def test_returning_team_is_compared_against_its_preserved_state(self):
        self.history_with_changes(["TST", "OTH"])
        self.run_with(["TST"])
        preserved = self.read("data/ncaaf/state/OTH.json")
        # While OTH was not monitored: one player left, one joined.
        self.rosters["OTH"] = self.rosters["OTH"][1:] + ["OTH Returner Pick-Up"]
        log_before = self.read("logs/ncaaf/OTH.json")
        self.run_with(["TST", "OTH"])
        log_after = self.read("logs/ncaaf/OTH.json")
        new = log_after[:len(log_after) - len(log_before)]
        self.assertEqual(sorted((c["type"], c["playerName"]) for c in new),
                         [("added", "OTH Returner Pick-Up"), ("removed", preserved["players"][0]["name"])])
        self.assertEqual(log_after[len(new):], log_before, "older history kept")
        oth = next(r for r in self.read("data/ncaaf/status.json") if r["teamCode"] == "OTH")
        self.assertEqual((oth["sourceIssue"], oth["playerCount"]), ("", 40))
        issues = self.read("data/ncaaf/source_issues.json") if (self.root / "data/ncaaf/source_issues.json").exists() else []
        self.assertEqual(issues, [], "a returning team is a normal comparison, not a recovery")

    def test_repeated_runs_with_a_shorter_list_are_idempotent(self):
        self.history_with_changes(["TST", "OTH", "AAA"])
        self.run_with(["TST"])
        first = self.snapshot()
        for _ in range(3):
            self.run_with(["TST"])
        again = self.snapshot()
        moving = {"data/ncaaf/status.json", "data/ncaaf/summary.json", "data/ncaaf/latest/TST.json", "data/ncaaf/state/TST.json"}
        self.assertEqual(set(again), set(first), "no file appears or disappears")
        for rel in set(first) - moving:
            self.assertEqual(again[rel], first[rel], f"{rel} changed on a repeated run")

    def test_drastically_shortened_team_list_cannot_delete_state_or_history(self):
        codes = list(self.TEAMS)
        self.history_with_changes(codes)
        before = self.snapshot()
        # 5 teams -> 1, like the 2-team fbs_teams.json of 2026-07-10: the run now stops before writing.
        roster_safety.save_json(self.h.module.FBS_TEAMS_FILE, [self.TEAMS["TST"]])
        with self.assertRaises(self.h.module.TeamListError) as raised:
            self.h.run(None)
        self.assertIn("4 teams monitored in the last run are missing", str(raised.exception))
        after = self.snapshot()
        self.assertEqual({k: v for k, v in after.items() if not k.endswith("fbs_teams.json")},
                         {k: v for k, v in before.items() if not k.endswith("fbs_teams.json")})

    def test_empty_team_list_aborts_before_touching_anything(self):
        self.history_with_changes(["TST", "OTH"])
        before = self.snapshot()
        roster_safety.save_json(self.h.module.FBS_TEAMS_FILE, [])
        with self.assertRaises(RuntimeError):
            self.h.run(None)
        after = self.snapshot()
        self.assertEqual({k: v for k, v in after.items() if not k.endswith("fbs_teams.json")},
                         {k: v for k, v in before.items() if not k.endswith("fbs_teams.json")})


def synthetic_teams(count, start=0):
    return [{"id": str(1000 + i), "code": f"T{i:03d}", "name": f"Team {i:03d}", "logo": ""}
            for i in range(start, start + count)]


class NcaafTeamListValidationRules(unittest.TestCase):
    """validate_team_list() on synthetic lists (no files involved)."""

    @classmethod
    def setUpClass(cls):
        cls.m = load_checker("ncaaf")

    def validate(self, teams, previous=None, codes_by_id=None, ids_by_code=None):
        return self.m.validate_team_list(teams, previous, codes_by_id or {}, ids_by_code or {})

    def assert_rejected(self, teams, expected, **kwargs):
        with self.assertRaises(self.m.TeamListError) as raised:
            self.validate(teams, **kwargs)
        self.assertIn(expected, str(raised.exception))
        self.assertIn("nothing was written", str(raised.exception))
        return str(raised.exception)

    def test_valid_list_passes(self):
        teams = self.validate(synthetic_teams(130))
        self.assertEqual(len(teams), 130)
        self.assertEqual(set(teams[0]), {"id", "code", "name", "logo"})

    def test_must_be_a_json_list(self):
        self.assert_rejected({"teams": synthetic_teams(130)}, "must be a JSON list")

    def test_duplicate_espn_id(self):
        teams = synthetic_teams(130) + [{"id": "1005", "code": "XTRA", "name": "Extra"}]
        self.assert_rejected(teams, "duplicate ESPN id 1005")

    def test_ids_compared_as_trimmed_text(self):
        teams = synthetic_teams(130) + [{"id": 1005, "code": "XTRA", "name": "Extra"}, {"id": " 1006 ", "code": "XTRB", "name": "B"}]
        message = self.assert_rejected(teams, "duplicate ESPN id 1005")
        self.assertIn("duplicate ESPN id 1006", message)

    def test_missing_id_code_or_name(self):
        for field in ("id", "code", "name"):
            with self.subTest(field=field):
                teams = synthetic_teams(130)
                teams[7] = dict(teams[7], **{field: "  "})
                self.assert_rejected(teams, f"has no {field}")
                teams[7] = {k: v for k, v in teams[7].items() if k != field}
                self.assert_rejected(teams, f"has no {field}")

    def test_code_that_normalizes_to_nothing_counts_as_missing(self):
        teams = synthetic_teams(130) + [{"id": "9", "code": "&-&", "name": "Symbols"}]
        self.assert_rejected(teams, "has no code")

    def test_filename_normalization_collisions(self):
        for first, second, normalized in [("TA&M", "TA M", "TA_M"), ("M-OH", "M_OH", "M_OH"), ("TST", "tst", "TST")]:
            with self.subTest(codes=(first, second)):
                teams = synthetic_teams(130) + [{"id": "1", "code": first, "name": "A"}, {"id": "2", "code": second, "name": "B"}]
                self.assert_rejected(teams, f"duplicate file code (after filename normalization) {normalized}")

    def test_fewer_than_120_teams(self):
        self.assert_rejected(synthetic_teams(119), "only 119 valid teams; at least 120 are required")
        self.assertEqual(len(self.validate(synthetic_teams(120))), 120)

    def test_dropping_more_than_two_previous_teams(self):
        previous = {t["code"] for t in synthetic_teams(130)}
        message = self.assert_rejected(synthetic_teams(127), "3 teams monitored in the last run are missing", previous=previous)
        self.assertIn("T127, T128, T129", message)

    def test_one_or_two_previous_teams_may_leave(self):
        previous = {t["code"] for t in synthetic_teams(130)}
        self.assertEqual(len(self.validate(synthetic_teams(129), previous=previous)), 129)
        self.assertEqual(len(self.validate(synthetic_teams(128), previous=previous)), 128)

    def test_new_teams_do_not_count_as_drops(self):
        previous = {t["code"] for t in synthetic_teams(130)}
        self.assertEqual(len(self.validate(synthetic_teams(140), previous=previous)), 140)

    def test_saved_id_under_another_code_is_rejected(self):
        teams = synthetic_teams(130)
        teams[3] = dict(teams[3], code="RENAMED")
        self.assert_rejected(teams, "uses code T003; a different code would split its history",
                             codes_by_id={"1003": {"T003"}}, ids_by_code={"T003": {"1003"}})

    def test_saved_code_under_another_id_is_rejected(self):
        teams = synthetic_teams(130)
        teams[3] = dict(teams[3], id="5555")
        self.assert_rejected(teams, "belongs to ESPN id 1003; this entry would overwrite another team's roster",
                             codes_by_id={"1003": {"T003"}}, ids_by_code={"T003": {"1003"}})

    def test_every_problem_is_listed_at_once(self):
        teams = synthetic_teams(118) + [{"id": "1000", "code": "DUPE", "name": "Dup"}, {"code": "NOID", "name": "x"}]
        message = self.assert_rejected(teams, "duplicate ESPN id 1000")
        self.assertIn("has no id", message)
        self.assertIn("at least 120 are required", message)


class NcaafRealTeamList(unittest.TestCase):
    """The repository's corrected fbs_teams.json against its own status.json and saved rosters (read-only)."""

    @classmethod
    def setUpClass(cls):
        cls.m = load_checker("ncaaf")  # paths point at the real repository; only read here
        cls.teams_data = json.loads((ROOT / "data/ncaaf/fbs_teams.json").read_text(encoding="utf-8"))
        cls.previous = cls.m.previous_team_codes()
        cls.codes_by_id, cls.ids_by_code = cls.m.saved_team_identities()

    def validate(self, teams):
        return self.m.validate_team_list(teams, self.previous, self.codes_by_id, self.ids_by_code)

    def test_corrected_138_team_list_passes(self):
        teams = self.validate(self.teams_data)
        self.assertEqual(len(teams), 138)
        self.assertEqual({t["code"] for t in teams}, self.previous, "same teams as the last run")

    def test_umass_keeps_its_canonical_code(self):
        umass = [t for t in self.validate(self.teams_data) if t["id"] == "113"]
        self.assertEqual([t["code"] for t in umass], ["UMASS"])
        self.assertEqual(self.codes_by_id.get("113"), {"UMASS"}, "saved roster files use UMASS")

    def test_mass_and_umass_together_are_rejected(self):
        bad = self.teams_data + [{"id": "113", "code": "MASS", "name": "Massachusetts Minutemen"}]
        with self.assertRaises(self.m.TeamListError) as raised:
            self.validate(bad)
        message = str(raised.exception)
        self.assertIn("duplicate ESPN id 113", message)
        self.assertIn("uses code UMASS; a different code would split its history", message)

    def test_mass_instead_of_umass_is_rejected(self):
        bad = [dict(t, code="MASS") if t["code"] == "UMASS" else t for t in self.teams_data]
        with self.assertRaises(self.m.TeamListError) as raised:
            self.validate(bad)
        self.assertIn("ESPN id 113 uses code UMASS", str(raised.exception))

    def test_sjsu_2459_next_to_niu_2459_is_rejected(self):
        bad = self.teams_data + [{"id": "2459", "code": "SJSU", "name": "San José State Spartans"}]
        with self.assertRaises(self.m.TeamListError) as raised:
            self.validate(bad)
        message = str(raised.exception)
        self.assertIn("duplicate ESPN id 2459", message)
        self.assertIn("duplicate file code (after filename normalization) SJSU", message)
        self.assertIn("belongs to ESPN id 23", message)

    def test_sjsu_2459_alone_is_still_rejected(self):
        # Like 2026-07-10/11: the wrong SJSU entry without NIU or SJSU/23 in the list.
        bad = [t for t in self.teams_data if t["code"] not in ("SJSU", "NIU")]
        bad.append({"id": "2459", "code": "SJSU", "name": "San José State Spartans"})
        with self.assertRaises(self.m.TeamListError) as raised:
            self.validate(bad)
        self.assertIn("would overwrite another team's roster", str(raised.exception))

    def test_sjsu_23_and_niu_2459_pair_passes(self):
        by_code = {t["code"]: t["id"] for t in self.validate(self.teams_data)}
        self.assertEqual((by_code["SJSU"], by_code["NIU"]), ("23", "2459"))


class NcaafInvalidTeamListWritesNothing(unittest.TestCase):
    """An invalid team list stops the real NCAAF run before any file is written."""

    TEAMS = NcaafHistoryRetention.TEAMS
    setUp = NcaafHistoryRetention.setUp
    tearDown = NcaafHistoryRetention.tearDown
    run_with = NcaafHistoryRetention.run_with
    snapshot = NcaafHistoryRetention.snapshot
    read = NcaafHistoryRetention.read
    history_with_changes = NcaafHistoryRetention.history_with_changes

    def assert_aborts_without_writes(self, teams_data, expected, after_history=None):
        self.history_with_changes(list(self.TEAMS))
        if after_history:
            after_history()
        roster_safety.save_json(self.h.module.DATA / "source_issues.json", [{"kind": "older issue"}])
        before = self.snapshot()
        roster_safety.save_json(self.h.module.FBS_TEAMS_FILE, teams_data)
        with self.assertRaises(self.h.module.TeamListError) as raised:
            self.h.run(None)
        self.assertIn(expected, str(raised.exception))
        after = self.snapshot()
        unchanged = lambda snap: {k: v for k, v in snap.items() if not k.endswith("fbs_teams.json")}
        self.assertEqual(unchanged(after), unchanged(before), "status, rosters, state, logs and issues byte-identical")
        for rel in ("data/ncaaf/status.json", "data/ncaaf/summary.json", "logs/ncaaf/all_changes.json",
                    "data/ncaaf/latest/TST.json", "data/ncaaf/state/TST.json", "logs/ncaaf/TST.json"):
            self.assertIn(rel, before)

    def all_teams(self):
        return list(self.TEAMS.values())

    def test_duplicate_espn_id_aborts(self):
        self.assert_aborts_without_writes(self.all_teams() + [dict(self.TEAMS["OTH"], code="NEW")], "duplicate ESPN id")

    def test_duplicate_file_code_aborts(self):
        self.assert_aborts_without_writes(self.all_teams() + [{"id": "4242", "code": "tst", "name": "Lower"}],
                                          "duplicate file code (after filename normalization) TST")

    def test_missing_fields_abort(self):
        for field in ("id", "code", "name"):
            with self.subTest(field=field):
                teams = self.all_teams()
                teams[1] = {k: v for k, v in teams[1].items() if k != field}
                self.assert_aborts_without_writes(teams, f"has no {field}")

    def test_too_few_teams_aborts(self):
        self.assert_aborts_without_writes(self.all_teams(), "only 5 valid teams; at least 6 are required",
                                          after_history=lambda: setattr(self.h.module, "MIN_TEAMS", 6))

    def test_dropping_three_previous_teams_aborts(self):
        self.assert_aborts_without_writes([self.TEAMS["TST"], self.TEAMS["OTH"]], "3 teams monitored in the last run are missing")

    def test_not_a_list_aborts(self):
        self.assert_aborts_without_writes({"teams": self.all_teams()}, "must be a JSON list")

    def test_identity_change_aborts(self):
        teams = [dict(t, code="OTHER") if t["code"] == "OTH" else t for t in self.all_teams()]
        self.assert_aborts_without_writes(teams, "uses code OTH; a different code would split its history")

    def test_valid_list_after_a_failed_run_continues_normally(self):
        self.history_with_changes(list(self.TEAMS))
        roster_safety.save_json(self.h.module.FBS_TEAMS_FILE, [self.TEAMS["TST"]])
        with self.assertRaises(self.h.module.TeamListError):
            self.h.run(None)
        self.rosters["TST"] = self.rosters["TST"] + ["TST Newcomer"]
        self.run_with(list(self.TEAMS))
        self.assertEqual(len(self.read("data/ncaaf/status.json")), 5)
        newest = self.read("logs/ncaaf/all_changes.json")[0]
        self.assertEqual((newest["teamCode"], newest["type"], newest["playerName"]), ("TST", "added", "TST Newcomer"))


class ThresholdBoundaries(unittest.TestCase):
    """The drop limits are inclusive: reaching both limits holds, staying under either accepts."""

    def roster(self, n):
        return {"players": [{"name": f"P{i}"} for i in range(n)]}

    def test_each_league_limits(self):
        for league, limits in roster_safety.DROP_THRESHOLDS.items():
            with self.subTest(league=league):
                big = 100
                keep_at_limit = big - max(limits["min_players"], int(round(big * limits["fraction"])))
                held = roster_safety.evaluate(league, self.roster(big), self.roster(keep_at_limit), None)
                self.assertFalse(held["accept"])
                self.assertEqual(held["kind"], "suspicious_drop")
                just_under = roster_safety.evaluate(league, self.roster(big), self.roster(keep_at_limit + 1), None)
                self.assertTrue(just_under["accept"])

    def test_small_rosters_need_the_minimum_player_count(self):
        # WNBA: 12 -> 7 is -42% but only 5 players: held (min 5). 12 -> 8 (-4, 33%) accepted.
        self.assertFalse(roster_safety.evaluate("wnba", self.roster(12), self.roster(7), None)["accept"])
        self.assertTrue(roster_safety.evaluate("wnba", self.roster(12), self.roster(8), None)["accept"])
        # NBA: 10 -> 5 is -50% but only 5 players (< 6): accepted.
        self.assertTrue(roster_safety.evaluate("nba", self.roster(10), self.roster(5), None)["accept"])

    def test_historical_real_drops_are_not_held(self):
        # Largest genuine one-run drops seen in Jun-Nov 2026 history.
        for league, prev, new in [("nfl", 105, 73), ("nba", 23, 16), ("mlb", 39, 26), ("nhl", 47, 27), ("ncaaf", 191, 131)]:
            with self.subTest(league=league):
                self.assertTrue(roster_safety.evaluate(league, self.roster(prev), self.roster(new), None)["accept"])

    def test_previously_empty_roster_accepts_recovery(self):
        decision = roster_safety.evaluate("ncaaf", {"players": []}, self.roster(116), None)
        self.assertTrue(decision["accept"])


class NcaafBothApisFailing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.h = Harness("ncaaf", self.tmp.name)
        self.real_fetch_roster = load_checker("ncaaf").fetch_roster.__code__

    def tearDown(self):
        self.tmp.cleanup()

    def use_real_fetch(self, site, core):
        """Restore the real fetch_roster but replace the two ESPN API calls."""
        m = self.h.module
        m.fetch_roster = types.FunctionType(self.real_fetch_roster, vars(m), "fetch_roster")
        m.fetch_site_roster = site
        m.fetch_core_roster = core

    def test_both_apis_failing_is_a_fetch_error(self):
        self.h.run(BASE)
        good = self.h.latest

        def boom(team):
            raise RuntimeError("HTTP 500")
        self.use_real_fetch(boom, boom)
        self.h.run(None)
        s = self.h.status
        self.assertEqual(s["sourceIssue"], "fetch_error")
        self.assertIn("Both ESPN roster APIs failed", s["lastError"])
        self.assertEqual(self.h.latest, good)
        self.assertEqual(self.h.all_changes, [])

    def test_one_api_failing_still_uses_the_other(self):
        self.h.run(BASE)
        players = [{"name": n, "position": "QB", "number": jersey(n), "status": "ACTIVE", "source": "x"}
                   for n in sorted(BASE + ["Walk-On"])]

        def boom(team):
            raise RuntimeError("HTTP 500")
        self.use_real_fetch(boom, lambda team: players)
        self.h.run(None)
        self.assertEqual(self.h.status["sourceIssue"], "")
        self.assertEqual(self.h.status["playerCount"], 61)
        self.assertEqual(self.h.status["source"], "ESPN College Football Core API")
        self.assertEqual([c["type"] for c in self.h.all_changes], ["added"])

    def test_one_api_failing_and_other_empty_is_held_as_empty(self):
        self.h.run(BASE)
        good = self.h.latest

        def boom(team):
            raise RuntimeError("HTTP 500")
        self.use_real_fetch(lambda team: ([], {}), boom)
        self.h.run(None)
        self.assertEqual(self.h.status["sourceIssue"], "empty_response")
        self.assertEqual(self.h.latest, good)
        self.assertEqual(self.h.all_changes, [])


if __name__ == "__main__":
    unittest.main()
