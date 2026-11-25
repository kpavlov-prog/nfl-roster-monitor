"""Tests for scripts/check_upcoming_events.py (Games page schedule data). No network: a fake ESPN."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 25)


def load_checker():
    spec = importlib.util.spec_from_file_location("check_upcoming_events_under_test", ROOT / "scripts" / "check_upcoming_events.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def team(tid, abbr, name):
    return {"id": str(tid), "abbreviation": abbr, "displayName": name, "shortDisplayName": name.split()[-1], "logo": f"https://a.espncdn.com/{abbr}.png"}


def event(eid, when, home, away, state="pre", status_name=None, home_score="0", away_score="0", period=0, clock="0:00",
          neutral=False, detail=None, away_first=False):
    names = {"pre": "STATUS_SCHEDULED", "in": "STATUS_IN_PROGRESS", "post": "STATUS_FINAL"}
    comps = [{"homeAway": "home", "team": home, "score": home_score}, {"homeAway": "away", "team": away, "score": away_score}]
    if away_first:
        comps.reverse()
    return {"id": str(eid), "date": when, "name": f"{away['displayName']} at {home['displayName']}",
            "status": {"period": period, "displayClock": clock,
                       "type": {"name": status_name or names[state], "state": state, "completed": state == "post",
                                "shortDetail": detail or {"pre": "11/25 - 7:00 PM EST", "in": f"Q{period} {clock}", "post": "Final"}[state]}},
            "season": {"year": 2027, "type": 2}, "week": {"number": 12},
            "competitions": [{"competitors": comps, "neutralSite": neutral, "venue": {"fullName": "Arena"}}]}


DUKE, KU, UK = team(150, "DUKE", "Duke Blue Devils"), team(2305, "KU", "Kansas Jayhawks"), team(96, "UK", "Kentucky Wildcats")
NOND1, NOND1B = team(99001, "DII", "Division Two College"), team(99002, "DII2", "Another Small College")
BAMA, FCS1, FCS2 = team(333, "ALA", "Alabama Crimson Tide"), team(2534, "SAM", "Sam Houston FCS"), team(2535, "FCS", "Other FCS")
BOS, NYK = team(2, "BOS", "Boston Celtics"), team(18, "NY", "New York Knicks")
LAK, NJD = team(8, "LA", "Los Angeles Kings"), team(11, "NJ", "New Jersey Devils")
WSH, DAL = team(28, "WSH", "Washington Commanders"), team(6, "DAL", "Dallas Cowboys")
TBD = {"id": "-1", "abbreviation": "TBD", "displayName": "TBD"}


class FakeESPN:
    """Scoreboard answers keyed by (league path, ESPN date). Unknown -> empty slate."""
    def __init__(self):
        self.boards, self.fail, self.calls = {}, set(), []

    def set(self, league, ds, events):
        self.boards[(league, ds)] = events

    def get(self, url):
        self.calls.append(url)
        path = url.split("/sports/")[1].split("/scoreboard")[0]
        ds = url.split("dates=")[1][:8]
        if (path, ds) in self.fail:
            raise RuntimeError("HTTP 503")
        value = self.boards.get((path, ds), [])
        if value == "malformed":
            return {"no": "events"}
        return {"events": json.loads(json.dumps(value))}


P = {"nfl": "football/nfl", "ncaaf": "football/college-football", "nba": "basketball/nba", "wnba": "basketball/wnba",
     "ncaab": "basketball/mens-college-basketball", "mlb": "baseball/mlb", "nhl": "hockey/nhl"}


class Site:
    def __init__(self, tmp):
        self.m = m = load_checker()
        root = Path(tmp)
        m.DATA, m.OUT = root / "data", root / "data" / "events"
        m.REQUEST_PAUSE = 0
        self.espn = FakeESPN()
        m.get_json = self.espn.get
        teams = {"nfl": [("teamCode", c) for c in ("WAS", "DAL")], "nba": [("teamCode", c) for c in ("BOS", "NY")],
                 "wnba": [("teamCode", "LV")], "mlb": [("teamCode", "NYY")], "nhl": [("teamCode", c) for c in ("LAK", "NJD")],
                 "ncaaf": [("teamId", "333")], "ncaab": [("teamId", i) for i in ("150", "2305", "96")]}
        for lg, rows in teams.items():
            (m.DATA / lg).mkdir(parents=True, exist_ok=True)
            (m.DATA / lg / "status.json").write_text(json.dumps([{k: v} for k, v in rows]), encoding="utf-8")

    def run(self, today=TODAY):
        with mock.patch("builtins.print"):
            return self.m.run(today)

    def read(self, name):
        p = self.m.OUT / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def events(self, league=None):
        evs = (self.read("upcoming.json") or {}).get("events", [])
        return [e for e in evs if league is None or e["leagueKey"] == league]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.s = Site(self.tmp.name)


class Schedule(Base):
    def test_all_seven_leagues_normalised_into_one_schema(self):
        e = self.s.espn
        e.set(P["nfl"], "20261126", [event(1, "2026-11-26T21:30Z", DAL, WSH)])
        e.set(P["ncaaf"], "20261128", [event(2, "2026-11-28T17:00Z", BAMA, FCS1)])
        e.set(P["nba"], "20261125", [event(3, "2026-11-26T00:30Z", NYK, BOS)])
        e.set(P["wnba"], "20261125", [])
        e.set(P["ncaab"], "20261125", [event(4, "2026-11-25T23:00Z", DUKE, KU)])
        e.set(P["mlb"], "20261125", [])
        e.set(P["nhl"], "20261125", [event(5, "2026-11-26T00:00Z", NJD, LAK)])
        self.s.run()
        u = self.s.read("upcoming.json")
        self.assertEqual(sorted(e["leagueKey"] for e in u["events"]), ["nba", "ncaab", "ncaaf", "nfl", "nhl"])
        self.assertEqual(u["window"], {"fromUTC": "2026-11-24T00:00:00Z", "toUTC": "2026-12-03T05:00:00Z"})
        for name in ("nfl", "ncaaf", "nba", "wnba", "ncaab", "mlb", "nhl"):
            self.assertIsNotNone(self.s.read(f"{name}.json"), name)
        nba = self.s.events("nba")[0]
        self.assertEqual(set(nba), {"eventId", "league", "leagueKey", "sport", "startTimeUTC", "espnDate", "homeTeam", "awayTeam", "venue",
                                    "neutralSite", "status", "score", "gameState", "seasonType", "week", "source", "updatedAt"})
        self.assertEqual((nba["startTimeUTC"], nba["espnDate"], nba["league"], nba["sport"]), ("2026-11-26T00:30:00Z", "20261125", "NBA", "basketball"))
        self.assertEqual(nba["score"], {"home": None, "away": None})       # no score before the game
        self.assertEqual(len(self.s.espn.calls), 70)                      # 7 leagues x 10 ESPN dates, one request each

    def test_home_and_away_come_from_espn_flags_never_from_order(self):
        self.s.espn.set(P["nba"], "20261125", [event(3, "2026-11-26T00:30Z", NYK, BOS, state="post", home_score="101", away_score="99", away_first=True)])
        self.s.run()
        ev = self.s.events("nba")[0]
        self.assertEqual((ev["homeTeam"]["code"], ev["awayTeam"]["code"], ev["score"]), ("NY", "BOS", {"home": 101, "away": 99}))

    def test_event_without_one_home_and_one_away_is_skipped(self):
        bad = event(9, "2026-11-25T23:00Z", DUKE, KU); bad["competitions"][0]["competitors"][1]["homeAway"] = "home"
        self.s.espn.set(P["ncaab"], "20261125", [bad, event(10, "2026-11-25T23:30Z", UK, KU)])
        self.s.run()
        self.assertEqual([e["eventId"] for e in self.s.events("ncaab")], ["10"])
        self.assertEqual(self.s.read("ncaab.json")["dates"]["20261125"]["skipped"], 1)

    def test_neutral_site(self):
        self.s.espn.set(P["ncaab"], "20261126", [event(4, "2026-11-26T17:00Z", DUKE, UK, neutral=True)])
        self.s.run()
        self.assertTrue(self.s.events("ncaab")[0]["neutralSite"])

    def test_week_and_season_type(self):
        self.s.espn.set(P["nfl"], "20261126", [event(1, "2026-11-26T21:30Z", DAL, WSH)])
        self.s.run()
        ev = self.s.events("nfl")[0]
        self.assertEqual((ev["seasonType"], ev["week"]), (2, 12))


class Relevance(Base):
    def test_ncaab_d1_vs_d1_and_d1_vs_non_d1_included_non_d1_pair_excluded(self):
        self.s.espn.set(P["ncaab"], "20261125", [event(1, "2026-11-25T23:00Z", DUKE, KU), event(2, "2026-11-25T23:00Z", UK, NOND1),
                                                  event(3, "2026-11-25T23:00Z", NOND1, NOND1B)])
        self.s.run()
        evs = {e["eventId"]: e for e in self.s.events("ncaab")}
        self.assertEqual(sorted(evs), ["1", "2"])
        self.assertEqual((evs["2"]["homeTeam"]["monitored"], evs["2"]["awayTeam"]["monitored"]), (True, False))

    def test_ncaaf_fbs_vs_fcs_included_fcs_pair_excluded(self):
        self.s.espn.set(P["ncaaf"], "20261128", [event(1, "2026-11-28T17:00Z", BAMA, FCS1), event(2, "2026-11-28T17:00Z", FCS1, FCS2)])
        self.s.run()
        self.assertEqual([e["eventId"] for e in self.s.events("ncaaf")], ["1"])

    def test_pro_abbreviations_that_differ_from_our_codes_still_match(self):
        self.s.espn.set(P["nhl"], "20261125", [event(5, "2026-11-26T00:00Z", NJD, LAK)])
        self.s.espn.set(P["nfl"], "20261126", [event(1, "2026-11-26T21:30Z", DAL, WSH)])
        self.s.run()
        nhl, nfl = self.s.events("nhl")[0], self.s.events("nfl")[0]
        self.assertTrue(nhl["homeTeam"]["monitored"] and nhl["awayTeam"]["monitored"])
        self.assertEqual(nhl["homeTeam"]["code"], "NJ")                   # ESPN's code is kept as-is
        self.assertTrue(nfl["awayTeam"]["monitored"])

    def test_tbd_bracket_slots(self):
        self.s.espn.set(P["ncaab"], "20261127", [event(1, "2026-11-27T17:00Z", TBD, dict(TBD, id="-2")), event(2, "2026-11-27T19:00Z", DUKE, TBD)])
        self.s.run()
        evs = self.s.events("ncaab")
        self.assertEqual([e["eventId"] for e in evs], ["2"])               # TBD vs TBD: no monitored team yet
        self.assertEqual((evs[0]["awayTeam"]["name"], evs[0]["awayTeam"]["monitored"]), ("TBD", False))
        self.assertEqual(self.s.read("ncaab.json")["dates"]["20261127"]["skipped"], 0)   # placeholders are not "malformed"

    def test_large_ncaab_slate_is_kept_whole_and_requested_with_groups_50(self):
        slate = [event(1000 + i, "2026-11-25T23:00Z", DUKE if i % 3 == 0 else UK if i % 3 == 1 else KU, team(50000 + i, f"T{i}", f"Team {i}")) for i in range(450)]
        self.s.espn.set(P["ncaab"], "20261125", slate)
        self.s.run()
        self.assertEqual(len(self.s.events("ncaab")), 450)
        self.assertTrue(all("groups=50&limit=500" in u for u in self.s.espn.calls if "mens-college" in u))
        self.assertTrue(all("groups=80&limit=400" in u for u in self.s.espn.calls if "college-football" in u))


class Identity(Base):
    def test_same_event_on_two_espn_dates_appears_once(self):
        ev = event(7, "2026-11-26T02:00Z", DUKE, KU)
        self.s.espn.set(P["ncaab"], "20261125", [ev]); self.s.espn.set(P["ncaab"], "20261126", [ev])
        self.s.run()
        self.assertEqual([e["eventId"] for e in self.s.events("ncaab")], ["7"])

    def test_rescheduled_event_updates_the_same_id(self):
        self.s.espn.set(P["nba"], "20261127", [event(3, "2026-11-28T00:30Z", NYK, BOS)]); self.s.run()
        self.s.espn.set(P["nba"], "20261127", []); self.s.espn.set(P["nba"], "20261129", [event(3, "2026-11-30T00:30Z", NYK, BOS)]); self.s.run()
        evs = self.s.events("nba")
        self.assertEqual([(e["eventId"], e["startTimeUTC"]) for e in evs], [("3", "2026-11-30T00:30:00Z")])

    def test_moved_event_while_old_date_fails_keeps_only_the_fresh_copy(self):
        self.s.espn.set(P["nba"], "20261127", [event(3, "2026-11-28T00:30Z", NYK, BOS)]); self.s.run()
        self.s.espn.fail.add((P["nba"], "20261127")); self.s.espn.set(P["nba"], "20261129", [event(3, "2026-11-30T00:30Z", NYK, BOS)]); self.s.run()
        self.assertEqual([e["startTimeUTC"] for e in self.s.events("nba")], ["2026-11-30T00:30:00Z"])

    def test_postponed_and_cancelled_stay_visible_with_their_status(self):
        self.s.espn.set(P["nhl"], "20261126", [event(5, "2026-11-27T00:00Z", NJD, LAK, status_name="STATUS_POSTPONED", detail="Postponed"),
                                                event(6, "2026-11-27T00:30Z", LAK, NJD, status_name="STATUS_CANCELED", detail="Canceled")])
        self.s.run()
        st = {e["eventId"]: e["status"] for e in self.s.events("nhl")}
        self.assertEqual((st["5"]["postponed"], st["5"]["cancelled"], st["5"]["completed"]), (True, False, False))
        self.assertEqual((st["6"]["cancelled"], st["6"]["detail"]), (True, "Canceled"))

    def test_live_and_final_values(self):
        self.s.espn.set(P["nba"], "20261125", [event(3, "2026-11-26T00:30Z", NYK, BOS, state="in", home_score="78", away_score="74", period=3, clock="4:21"),
                                                event(4, "2026-11-25T22:00Z", BOS, NYK, state="post", home_score="x", away_score="99")])
        self.s.run()
        ev = {e["eventId"]: e for e in self.s.events("nba")}
        self.assertEqual((ev["3"]["score"], ev["3"]["gameState"]), ({"home": 78, "away": 74}, {"period": 3, "clock": "4:21", "display": "Q3 4:21"}))
        self.assertEqual(ev["4"]["score"], {"home": None, "away": 99})    # an unreadable score is shown as unknown, never invented
        self.assertIsNone(ev["4"]["gameState"]["clock"])


class Safety(Base):
    def seed(self):
        self.s.espn.set(P["nba"], "20261125", [event(3, "2026-11-26T00:30Z", NYK, BOS), event(31, "2026-11-26T01:00Z", BOS, NYK), event(32, "2026-11-26T02:00Z", NYK, BOS)])
        self.s.espn.set(P["nba"], "20261126", [event(4, "2026-11-27T00:30Z", BOS, NYK)])
        self.s.run()

    def test_failed_date_keeps_known_good_and_other_dates_update(self):
        self.seed()
        self.s.espn.fail.add((P["nba"], "20261125"))
        self.s.espn.set(P["nba"], "20261126", [event(4, "2026-11-27T00:30Z", BOS, NYK), event(5, "2026-11-27T01:00Z", NYK, BOS)])
        self.s.run()
        self.assertEqual(sorted(e["eventId"] for e in self.s.events("nba")), ["3", "31", "32", "4", "5"])
        d = self.s.read("nba.json")["dates"]
        self.assertEqual((d["20261125"]["status"], d["20261126"]["status"]), ("preserved", "ok"))
        self.assertIn("HTTP 503", d["20261125"]["error"])
        self.assertEqual(self.s.read("source_issues.json")[0]["kind"], "fetch_error")

    def test_malformed_response_keeps_known_good(self):
        self.seed()
        self.s.espn.set(P["nba"], "20261125", "malformed"); self.s.run()
        self.assertEqual(len(self.s.events("nba")), 4)
        self.assertEqual(self.s.read("nba.json")["dates"]["20261125"]["status"], "preserved")

    def test_every_event_malformed_counts_as_a_failed_date(self):
        self.seed()
        self.s.espn.set(P["nba"], "20261125", [{"id": "x"}, {"id": "y"}]); self.s.run()
        self.assertEqual(len(self.s.events("nba")), 4)

    def test_suspicious_empty_is_held_once_then_accepted(self):
        self.seed()
        self.s.espn.set(P["nba"], "20261125", []); self.s.run()
        self.assertEqual(len(self.s.events("nba")), 4)
        self.assertEqual(self.s.read("nba.json")["dates"]["20261125"]["status"], "held_empty")
        self.s.run()                                                    # confirmed empty on the next run
        self.assertEqual([e["eventId"] for e in self.s.events("nba")], ["4"])
        self.assertEqual(self.s.read("status.json")["pendingEmpty"], {})

    def test_legitimate_empty_schedule_is_fine(self):
        self.s.run()
        self.assertEqual(self.s.read("upcoming.json")["eventCount"], 0)
        self.assertEqual(self.s.read("source_issues.json"), [])
        self.assertTrue(all(i["status"] == "ok" for i in self.s.read("mlb.json")["dates"].values()))

    def test_missing_team_list_keeps_known_good_and_reports(self):
        self.seed()
        (self.s.m.DATA / "nba" / "status.json").unlink(); self.s.run()
        self.assertEqual(len(self.s.events("nba")), 4)
        self.assertEqual({i["kind"] for i in self.s.read("source_issues.json")}, {"no_team_list"})

    def test_rolling_window_expires_old_events(self):
        self.s.espn.set(P["nba"], "20261123", [event(1, "2026-11-24T00:30Z", NYK, BOS)])
        self.s.espn.set(P["nba"], "20261125", [event(2, "2026-11-26T00:30Z", NYK, BOS)])
        self.s.run()
        self.assertEqual(sorted(e["eventId"] for e in self.s.events("nba")), ["1", "2"])
        self.s.run(date(2026, 11, 26))                                    # D-1 is now 25/11: the 24/11 game leaves
        self.assertEqual([e["eventId"] for e in self.s.events("nba")], ["2"])

    def test_window_edges(self):
        self.s.espn.set(P["nba"], "20261123", [event(1, "2026-11-23T23:59Z", NYK, BOS), event(2, "2026-11-24T00:00Z", NYK, BOS)])
        self.s.espn.set(P["nba"], "20261202", [event(3, "2026-12-03T04:59Z", NYK, BOS), event(4, "2026-12-03T05:00Z", NYK, BOS)])
        self.s.run()
        self.assertEqual(sorted(e["eventId"] for e in self.s.events("nba")), ["2", "3"])


class Writing(Base):
    def test_output_is_deterministic_and_unchanged_runs_write_nothing(self):
        self.s.espn.set(P["ncaab"], "20261125", [event(4, "2026-11-25T23:00Z", DUKE, KU), event(5, "2026-11-25T23:00Z", UK, KU)])
        self.assertTrue(self.s.run()["changed"])
        before = {p.name: p.read_bytes() for p in self.s.m.OUT.iterdir()}
        self.assertFalse(self.s.run()["changed"])
        self.assertEqual({p.name: p.read_bytes() for p in self.s.m.OUT.iterdir()}, before)

    def test_unchanged_event_keeps_its_updated_time(self):
        self.s.espn.set(P["ncaab"], "20261125", [event(4, "2026-11-25T23:00Z", DUKE, KU)]); self.s.run()
        first = self.s.events("ncaab")[0]["updatedAt"]
        self.s.espn.set(P["ncaab"], "20261126", [event(5, "2026-11-26T23:00Z", UK, KU)])
        with mock.patch.object(self.s.m, "now_iso", return_value="2026-11-25T20:00:00Z"):
            self.s.run()
        evs = {e["eventId"]: e for e in self.s.events("ncaab")}
        self.assertEqual((evs["4"]["updatedAt"], evs["5"]["updatedAt"]), (first, "2026-11-25T20:00:00Z"))

    def test_recurring_issue_does_not_rewrite_files(self):
        self.s.espn.fail.add((P["nba"], "20261125")); self.s.run()
        before = {p.name: p.read_bytes() for p in self.s.m.OUT.iterdir()}
        self.assertFalse(self.s.run()["changed"])
        self.assertEqual({p.name: p.read_bytes() for p in self.s.m.OUT.iterdir()}, before)

    def test_a_failed_write_leaves_the_old_file_and_no_temp_file(self):
        self.s.espn.set(P["nba"], "20261125", [event(3, "2026-11-26T00:30Z", NYK, BOS)]); self.s.run()
        old = (self.s.m.OUT / "nba.json").read_bytes()
        self.s.espn.set(P["nba"], "20261126", [event(4, "2026-11-27T00:30Z", BOS, NYK)])
        with mock.patch.object(self.s.m.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.s.run()
        self.assertEqual((self.s.m.OUT / "nba.json").read_bytes(), old)
        self.assertEqual([p.name for p in self.s.m.OUT.iterdir() if p.name.endswith(".tmp")], [])

    def test_untrusted_strings_are_bounded_plain_text(self):
        evil = team(150, "DUKE", "Duke <img src=x onerror=alert(1)>\u0000\u001b" + "x" * 500)
        evil["logo"] = "javascript:alert(1)"
        self.s.espn.set(P["ncaab"], "20261125", [event(4, "2026-11-25T23:00Z", evil, KU)]); self.s.run()
        home = self.s.events("ncaab")[0]["homeTeam"]
        self.assertLessEqual(len(home["name"]), 80)
        self.assertNotIn("\u0000", home["name"])
        self.assertEqual(home["logo"], "")                                # only https logos are kept


if __name__ == "__main__":
    unittest.main()
