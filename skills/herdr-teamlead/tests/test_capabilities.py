"""The model-capability table: its cadence, its refresh, and what it refuses."""

import json
from datetime import datetime, timedelta, timezone
import os as _os
import sys as _sys
import tempfile
import unittest
from pathlib import Path

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from teamlead import capabilities
from teamlead.errors import UsageError

#: A fixed past reference; every other instant and date is derived from it
#: (rules/testing-standards.md Determinism).
REFERENCE = datetime(2026, 1, 5, tzinfo=timezone.utc)


def instant(days=0, seconds=0):
    return (REFERENCE + timedelta(days=days, seconds=seconds)).isoformat()


def dated(days):
    return (REFERENCE + timedelta(days=days)).date().isoformat()


AT = instant()
LATER = instant(days=8)


def found(document, model, effort, capability):
    """The entry, or a failure naming the row the table does not hold."""
    row = capabilities.lookup(document, model, effort, capability)
    assert row is not None, "no entry for {} / {} / {}".format(model, effort, capability)
    return row


def entry(**overrides):
    row = {"model": "opus-5", "effort": "high", "capability": "independent-defect-detection",
           "verdict": "adequate",
           "source": {"kind": "benchmark", "ref": "SWE-bench Verified", "dated": dated(-20)}}
    row.update(overrides)
    return row


class CapabilityTableTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state.json"

    # -- reading -----------------------------------------------------------

    def test_an_absent_table_reads_empty_and_creates_no_file(self):
        self.assertEqual(capabilities.load(self.state), capabilities.empty())
        self.assertFalse(capabilities.storage_path(self.state).exists())

    def test_a_malformed_table_names_its_repair(self):
        capabilities.storage_path(self.state).write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(UsageError, "not valid JSON"):
            capabilities.load(self.state)

    def test_an_unsupported_schema_refuses_rather_than_guessing(self):
        capabilities.storage_path(self.state).write_text(
            json.dumps({"schema_version": 99, "refreshed_at": None, "entries": []}), encoding="utf-8")
        with self.assertRaisesRegex(UsageError, "Unsupported capability-table schema"):
            capabilities.load(self.state)

    # -- cadence -----------------------------------------------------------

    def test_a_fleet_that_dispatched_nothing_is_never_due(self):
        # The table is read at dispatch time, so a week with no rounds is a week
        # where a stale table is never consulted (#481).
        result = capabilities.cadence(capabilities.empty(), AT)
        self.assertEqual((result["due"], result["reason"]), (False, "no_recorded_work"))

    def test_recorded_work_with_no_table_is_due_immediately(self):
        result = capabilities.cadence(capabilities.empty(), AT, existing_work=True)
        self.assertEqual((result["due"], result["reason"]), (True, "never_refreshed"))

    def test_the_interval_is_a_week(self):
        capabilities.record(self.state, {"entries": [entry()]}, AT)
        document = capabilities.load(self.state)
        for when, due in ((instant(days=1), False),
                          (instant(days=7, seconds=-1), False),
                          (instant(days=7), True),
                          (LATER, True)):
            with self.subTest(when=when):
                self.assertEqual(capabilities.cadence(document, when)["due"], due)

    def test_a_checkpoint_before_the_saved_refresh_is_refused(self):
        capabilities.record(self.state, {"entries": [entry()]}, LATER)
        with self.assertRaisesRegex(UsageError, "precedes the saved refresh"):
            capabilities.cadence(capabilities.load(self.state), AT)

    # -- recording ---------------------------------------------------------

    def test_a_refresh_stamps_and_sorts_what_it_records(self):
        capabilities.record(self.state, {"entries": [
            entry(model="sonnet-5"), entry(model="haiku-4.5", verdict="unknown")]}, AT)
        document = capabilities.load(self.state)
        self.assertEqual([row["model"] for row in document["entries"]], ["haiku-4.5", "sonnet-5"])
        self.assertEqual(document["refreshed_at"], AT)
        self.assertTrue(all(row["recorded_at"] == AT for row in document["entries"]))

    def test_a_partial_refresh_leaves_every_other_row_untouched(self):
        # One report about two models must not retire the rest of the table.
        capabilities.record(self.state, {"entries": [
            entry(), entry(model="haiku-4.5", effort="low", verdict="inadequate",
                           source={"kind": "project", "ref": "coding-policy#324", "dated": dated(-69)})]}, AT)
        capabilities.record(self.state, {"entries": [entry(
            source={"kind": "evaluation", "ref": "third-party eval", "dated": dated(-1)})]}, LATER)
        document = capabilities.load(self.state)
        self.assertEqual(len(document["entries"]), 2)
        kept = found(document, "haiku-4.5", "low", "independent-defect-detection")
        replaced = found(document, "opus-5", "high", "independent-defect-detection")
        self.assertEqual(kept["source"]["ref"], "coding-policy#324")
        self.assertEqual(replaced["source"]["ref"], "third-party eval")

    def test_an_empty_report_refreshes_nothing(self):
        with self.assertRaisesRegex(UsageError, "at least one entry"):
            capabilities.record(self.state, {"entries": []}, AT)

    def test_one_refresh_cannot_record_a_key_twice(self):
        with self.assertRaisesRegex(UsageError, "twice"):
            capabilities.record(self.state, {"entries": [entry(), entry(verdict="unknown")]}, AT)

    # -- the source hierarchy ----------------------------------------------

    def test_a_vendor_claim_cannot_support_an_adequate_verdict(self):
        # A vendor's claim about its own model would route real work on
        # marketing; the hierarchy exists to keep that out (#480).
        with self.assertRaisesRegex(UsageError, "routes real work on marketing"):
            capabilities.record(self.state, {"entries": [entry(
                source={"kind": "vendor", "ref": "launch blog", "dated": dated(-1)})]}, AT)

    def test_a_vendor_claim_still_records_what_it_can_support(self):
        for verdict in ("inadequate", "unknown"):
            with self.subTest(verdict=verdict):
                capabilities.record(self.state, {"entries": [entry(
                    verdict=verdict,
                    source={"kind": "vendor", "ref": "deprecation notice", "dated": dated(-1)})]}, AT)

    def test_this_projects_own_result_supports_a_verdict(self):
        # A model that failed here is evidence, cited like any other source.
        capabilities.record(self.state, {"entries": [entry(
            model="weak-1", verdict="inadequate",
            source={"kind": "project", "ref": "coding-policy#324", "dated": dated(-69)})]}, AT)
        row = found(capabilities.load(self.state), "weak-1", "high", "independent-defect-detection")
        self.assertEqual(row["verdict"], "inadequate")

    # -- entry shape -------------------------------------------------------

    def test_an_entry_carries_exactly_its_fields(self):
        for broken in ({"model": "m"}, {**entry(), "extra": 1}):
            with self.subTest(broken=broken):
                with self.assertRaisesRegex(UsageError, "carries exactly"):
                    capabilities.record(self.state, {"entries": [broken]}, AT)

    def test_a_verdict_comes_from_the_fixed_set(self):
        with self.assertRaisesRegex(UsageError, "one of"):
            capabilities.record(self.state, {"entries": [entry(verdict="probably fine")]}, AT)

    def test_a_source_is_dated_so_a_stale_reading_is_visible(self):
        for dated in ("2026-9-1", "September 2026", "", "2026-09-01T00:00:00Z"):
            with self.subTest(dated=dated):
                with self.assertRaisesRegex(UsageError, "dated YYYY-MM-DD"):
                    capabilities.record(self.state, {"entries": [entry(
                        source={"kind": "benchmark", "ref": "x", "dated": dated})]}, AT)

    def test_a_source_names_where_it_was_read(self):
        with self.assertRaisesRegex(UsageError, "names where it was read"):
            capabilities.record(self.state, {"entries": [entry(
                source={"kind": "benchmark", "ref": "  ", "dated": dated(-20)})]}, AT)

    def test_a_record_carries_entries_alone(self):
        with self.assertRaisesRegex(UsageError, "carries `entries` alone"):
            capabilities.record(self.state, {"entries": [entry()], "refreshed_at": AT}, AT)

    def test_lookup_returns_none_for_a_row_the_table_does_not_hold(self):
        capabilities.record(self.state, {"entries": [entry()]}, AT)
        self.assertIsNone(capabilities.lookup(capabilities.load(self.state), "opus-5", "low", "rebase"))


if __name__ == "__main__":
    unittest.main()
