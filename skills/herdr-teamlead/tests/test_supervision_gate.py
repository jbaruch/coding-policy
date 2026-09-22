"""What the supervision gate suppresses, and everything it must not."""

import os as _os
import sys as _sys
import unittest

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from teamlead import supervision_gate as gate
from teamlead.errors import UsageError

AT = "2026-09-21T00:00:00+00:00"


def store(events, members=("m1",), acknowledgements=()):
    return {"schema_version": 1,
            "members": [{"id": name, "at": AT, "active": True, "observed": {},
                         "assignment": {}, "refinements": [], "resolution": None}
                        for name in members],
            "events": [{"schema_version": 1, "id": "e{}".format(i), "seq": i, "at": AT,
                        "member": member, "kind": kind, "data": data}
                       for i, (member, kind, data) in enumerate(events, start=1)],
            "acknowledgements": list(acknowledgements)}


def verdicts(result):
    return {row["event"]: (row["kind"], row["reason"]) for row in result["suppressed"]}


class DefaultWakeTest(unittest.TestCase):
    """The safety property: only named cases are suppressed."""

    def test_a_kind_this_module_has_never_seen_wakes_the_lead(self):
        # Recorded history contains none of the failure kinds. A gate that
        # enumerated what to WAKE on would be silent for exactly those.
        for kind in ("watcher_lost", "observation_error_observed", "report_error_observed",
                     "unavailable_observed", "report_unreadable_observed",
                     "some_kind_invented_next_month"):
            with self.subTest(kind=kind):
                result = gate.evaluate(store([("m1", kind, {})]))
                self.assertEqual(result["counts"]["suppressed"], 0)
                self.assertEqual(result["wake"][0]["reason"], "kind is not suppressible")

    def test_the_kinds_that_carry_the_work_always_wake(self):
        for kind, data in (("report_observed", {"present": True, "path": "/r.md"}),
                           ("lifecycle_observed", {"status": "idle"}),
                           ("identity_changed_observed", {"pane_id": "w1:p1"})):
            with self.subTest(kind=kind):
                self.assertEqual(gate.evaluate(store([("m1", kind, data)]))["counts"]["suppressed"], 0)

    def test_an_event_with_no_member_wakes(self):
        result = gate.evaluate(store([(None, "visible_observed", {"sha256": "a" * 64})]))
        self.assertEqual(result["wake"][0]["reason"], "event has no member to join against")


class ScreenHashTest(unittest.TestCase):
    """A sha256 of a pane is never the only signal."""

    def test_a_screen_hash_is_suppressed_whatever_the_worker_is_doing(self):
        for lifecycle in ({"status": "working"}, {"status": "idle"}, {"status": "blocked"}):
            with self.subTest(lifecycle=lifecycle):
                result = gate.evaluate(store([
                    ("m1", "lifecycle_observed", lifecycle),
                    ("m1", "visible_observed", {"sha256": "a" * 64}),
                ]))
                self.assertEqual([row["kind"] for row in result["suppressed"]], ["visible_observed"])

    def test_a_landed_report_still_wakes_on_its_own_event(self):
        # The report's event is what wakes the lead; the screen change after it
        # adds nothing, which is why suppressing the latter loses nothing.
        result = gate.evaluate(store([
            ("m1", "report_observed", {"present": True, "path": "/r.md"}),
            ("m1", "visible_observed", {"sha256": "b" * 64}),
        ]))
        self.assertEqual([row["kind"] for row in result["wake"]], ["report_observed"])
        self.assertEqual([row["kind"] for row in result["suppressed"]], ["visible_observed"])


class DeferralTest(unittest.TestCase):
    """A recheck reads the same state, or it does not."""

    def test_an_unchanged_deferral_is_suppressed(self):
        result = gate.evaluate(store([
            ("m1", "lifecycle_observed", {"status": "working"}),
            ("m1", "recheck_due", {"event": "e1", "recheck_at": AT}),
        ]))
        self.assertIn("e2", verdicts(result))
        self.assertEqual(verdicts(result)["e2"][1], "observed state unchanged since the deferral")

    def test_a_deferral_whose_state_moved_wakes(self):
        result = gate.evaluate(store([
            ("m1", "lifecycle_observed", {"status": "working"}),
            ("m1", "report_observed", {"present": True, "path": "/r.md"}),
            ("m1", "recheck_due", {"event": "e1", "recheck_at": AT}),
        ]))
        self.assertEqual(result["counts"]["suppressed"], 0)
        self.assertEqual(result["wake"][-1]["reason"], "observed state changed since the deferral")

    def test_an_unchanging_worker_wakes_the_lead_rather_than_going_quiet_forever(self):
        # Indistinguishable from a stall, and a stall is the lead's to judge.
        events = [("m1", "lifecycle_observed", {"status": "working"})]
        events += [("m1", "recheck_due", {"event": "e1", "recheck_at": AT})
                   for _ in range(gate.MAX_QUIET_RECHECKS + 2)]
        result = gate.evaluate(store(events))
        self.assertEqual(result["counts"]["suppressed"], gate.MAX_QUIET_RECHECKS)
        self.assertIn("stall is the lead's to judge", result["wake"][-1]["reason"])

    def test_a_deferral_stays_woken_once_its_state_has_moved_past_the_baseline(self):
        events = [("m1", "lifecycle_observed", {"status": "working"}),
                  ("m1", "recheck_due", {"event": "e1", "recheck_at": AT}),
                  ("m1", "report_observed", {"present": True, "path": "/r.md"}),
                  ("m1", "recheck_due", {"event": "e1", "recheck_at": AT}),
                  ("m1", "recheck_due", {"event": "e1", "recheck_at": AT})]
        result = gate.evaluate(store(events))
        # e2 is quiet; e4 and e5 both wake, because e1's baseline never moves.
        # Herdr advances it by deferring the recheck itself, which carries a
        # newer event id and therefore a newer baseline.
        self.assertEqual([row["event"] for row in result["suppressed"]], ["e2"])
        self.assertEqual([row["event"] for row in result["wake"]], ["e1", "e3", "e4", "e5"])


class ReplayTest(unittest.TestCase):
    """A verdict reads the state as of its own event, never a later one."""

    def test_a_verdict_never_reads_the_change_it_is_judging(self):
        # The report lands in the same event being judged: it must still wake,
        # and the NEXT deferral is the one that sees the new state.
        result = gate.evaluate(store([
            ("m1", "lifecycle_observed", {"status": "working"}),
            ("m1", "recheck_due", {"event": "e1", "recheck_at": AT}),
            ("m1", "report_observed", {"present": True, "path": "/r.md"}),
            ("m1", "recheck_due", {"event": "e1", "recheck_at": AT}),
        ]))
        self.assertEqual([row["event"] for row in result["suppressed"]], ["e2"])

    def test_events_are_judged_in_sequence_order_not_file_order(self):
        data = store([("m1", "lifecycle_observed", {"status": "working"}),
                      ("m1", "recheck_due", {"event": "e1", "recheck_at": AT})])
        data["events"].reverse()
        result = gate.evaluate(data)
        self.assertEqual([row["event"] for row in result["suppressed"]], ["e2"])

    def test_members_are_judged_independently(self):
        result = gate.evaluate(store([
            ("m1", "lifecycle_observed", {"status": "working"}),
            ("m2", "recheck_due", {"event": "e1", "recheck_at": AT}),
        ], members=("m1", "m2")))
        # m2 never observed anything, so m1's sample must not answer for it.
        self.assertEqual(result["counts"]["suppressed"], 0)


class InputTest(unittest.TestCase):
    def test_a_store_without_events_is_refused(self):
        for broken in ({}, {"events": "none"}, []):
            with self.subTest(broken=broken):
                with self.assertRaisesRegex(UsageError, "events array"):
                    gate.evaluate(broken)

    def test_counts_add_up(self):
        result = gate.evaluate(store([("m1", "visible_observed", {"sha256": "a" * 64}),
                                      ("m1", "report_observed", {"present": True})]))
        counts = result["counts"]
        self.assertEqual(counts["wake"] + counts["suppressed"], counts["total"])
        self.assertEqual(counts["total"], 2)


if __name__ == "__main__":
    unittest.main()
