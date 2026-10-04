"""Delivered report-contract evidence survives replay, migrates from schema 1, and gates warm follow-ups."""

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foreman import composition, engagement, recovery, report_delivery, supervision
from foreman.assign import freeze_paths
from foreman.errors import UsageError
from foreman.state import STATE_SCHEMA_VERSION, add_assignment, empty_state, load_state_checked, save_state
from tests import test_report_delivery as delivery_fixture

AT = "2026-02-03T10:00:00+00:00"
LATER = "2026-02-03T11:00:00+00:00"
REQUIREMENT = {"specialty": "ux", "required_capabilities": ["interaction-design"],
               "independent": False, "engagement": "onboarding-design"}
#: A consultation report meeting a two-criterion brief.
CONSULT_REPORT = ("Inspected onboarding. Recommend grouping account fields; implementation remains open.\n"
                  "ACCEPTANCE 1/2: met — grouping proposal in section 2\n"
                  "ACCEPTANCE 2/2: met — flow verified against the shipped screens\n")


def frozen_brief(root, criteria=2, name="consult-brief.md"):
    """A consultation brief carrying `criteria` CRITERION lines, frozen the way apply freezes it."""
    source = Path(root) / name
    lines = ["# Brief", "", "## Acceptance Criteria", ""]
    lines += ["CRITERION {}: criterion number {}".format(k, k) for k in range(1, criteria + 1)]
    source.write_text("\n".join(lines + ["", "## Report", "", "Write the report."]) + "\n")
    return freeze_paths({"brief": str(source)})["brief"]


def legacy_record(record):
    """The schema-1 shape of a report-sourced record, as a pre-#625 foreman wrote it."""
    keep = {key: record[key] for key in ("id", "dispatch", "report", "delivery", "at", "assignment_index", "task",
                                         "role", "agent", "report_evidence", "delivery_evidence")}
    return {**keep, "schema_version": 1, "outcome": "legacy outcome", "summary": "legacy summary",
            "contribution": "none"}


class EngagementTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "state.json"
        environment = patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root / "xdg")})
        environment.start()
        self.addCleanup(environment.stop)
        who = supervision.identity("lead", str(self.root), "fixture", pane_id="lead-pane")
        supervision.bind(self.path, who, AT)
        self.report = self.root / "report.md"
        self.report.write_text(CONSULT_REPORT)
        self.delivery = self.root / "delivery.json"
        self.delivery.write_text(json.dumps({"found": True, "agent": "worker", "report_path": str(self.report)}))
        self.state = empty_state()
        self.dispatch = {"id": "consult-1", "fingerprint": "a" * 64, "task": "task-1", "role": "advisor",
                         "agent": "worker", "fix_round": None, "plan": None, "work": None,
                         "requirements": copy.deepcopy(REQUIREMENT), "brief": frozen_brief(self.root)}
        self.seed(self.dispatch)
        self.data = {"id": "assessment-1", "dispatch": "consult-1", "report": str(self.report),
                     "delivery": str(self.delivery)}

    def seed(self, dispatch):
        recovery.reserve(self.state["recovery"], dispatch, AT)
        add_assignment(self.state, AT, dispatch["role"], dispatch["agent"], task=dispatch["task"],
                       requirements=dispatch.get("requirements"), reviewer_scope=dispatch.get("reviewer_scope"))
        result = {key: copy.deepcopy(dispatch[key]) for key in ("task", "role", "agent", "fix_round", "requirements", "reviewer_scope") if key in dispatch}
        recovery.finish_dispatch(self.state["recovery"], dispatch["id"], {**result, "status": "applied"}, len(self.state["assignments"]) - 1, AT)
        supervision.enroll(self.path, {"id": dispatch["id"], "agent": dispatch["agent"], "task": dispatch["task"],
                                      "report": dispatch.get("report", str(self.report)),
                                      "pane_id": dispatch["agent"] + "-pane", "native_session": None}, AT)

    def assess(self, data=None, at=LATER):
        return engagement.record_assessment(self.state, self.path, self.data if data is None else data, at)

    def retire(self):
        return supervision.resolve(self.path, {"id": self.dispatch["id"], "outcome": "Report assessed; consultation ended",
                                               "evidence": [str(self.report)]}, LATER)

    def test_assessment_records_report_lines_bound_to_delivery_without_accepting_task(self):
        before = copy.deepcopy(self.state["assignments"])
        result = self.assess()
        self.assertEqual(result["assignment_index"], 0)
        self.assertEqual(result["task"], "task-1")
        self.assertEqual(result["source"], "report")
        self.assertEqual(result["criteria"], 2)
        self.assertEqual([row["state"] for row in result["acceptance"]], ["met", "met"])
        self.assertIsNone(result["verdict"])
        self.assertIsNone(result["contribution"])
        self.assertIsNone(result["legacy"])
        self.assertEqual(result["brief_evidence"]["path"], self.dispatch["brief"])
        self.assertEqual(result["report_evidence"], recovery.receipt(str(self.report))[0])
        self.assertEqual(result["delivery_evidence"], recovery.receipt(str(self.delivery))[0])
        self.assertEqual(self.state["assignments"], before)
        self.assertTrue(supervision.load(self.path)["members"][0]["active"])
        save_state(self.path, self.state)
        loaded, usable = load_state_checked(self.path)
        self.assertTrue(usable)
        self.assertEqual(loaded, self.state)

    def test_retired_foreman_inputs_are_refused_by_name(self):
        for extra in ({"outcome": "done"}, {"summary": "read it"}, {"contribution": "none"}):
            with self.subTest(extra=extra), self.assertRaises(UsageError) as caught:
                self.assess({**self.data, **extra})
            self.assertIn(next(iter(extra)), caught.exception.message)
        self.assertEqual(self.state["specialist_assessments"], [])

    def test_criteria_count_comes_from_the_dispatched_brief(self):
        # The report says N=3; the frozen brief says 2. The brief decides.
        self.report.write_text("ACCEPTANCE 1/3: met — a\nACCEPTANCE 2/3: met — b\nACCEPTANCE 3/3: met — c\n")
        with self.assertRaises(UsageError) as caught:
            self.assess()
        self.assertIn("recorded 2", caught.exception.message)
        self.assertEqual(self.state["specialist_assessments"], [])

    def test_consultation_without_a_frozen_brief_is_refused(self):
        self.state["recovery"]["dispatches"][0].pop("brief")
        with self.assertRaises(UsageError) as caught:
            self.assess()
        self.assertIn("frozen brief", caught.exception.message)

    def test_verdict_rule_follows_the_dispatch_specialty(self):
        verdict = CONSULT_REPORT + "VERDICT: blocking\n"
        for specialty, text, ok in (("security", verdict, True), ("ux-product", verdict, True),
                                    ("documentation", verdict, True), ("security", CONSULT_REPORT, False),
                                    ("ux", verdict, False), ("performance-reliability", verdict, False),
                                    ("accessibility", CONSULT_REPORT, True)):
            with self.subTest(specialty=specialty, verdict="VERDICT" in text):
                self.state["recovery"]["dispatches"][0]["requirements"]["specialty"] = specialty
                self.state["assignments"][0]["requirements"]["specialty"] = specialty
                self.state["specialist_assessments"] = []
                self.report.write_text(text)
                if ok:
                    result = self.assess()
                    self.assertEqual(result["verdict"], "blocking" if "VERDICT" in text else None)
                    engagement.validate_assessments(self.state)
                else:
                    with self.assertRaises(UsageError):
                        self.assess()

    def test_a_review_seats_delivered_report_is_assessable(self):
        # The seat stays on the dispatch, so a slice's verdict reaches its
        # assessment through the responsibility it fills (#434).
        report = self.root / "slice-report.md"
        report.write_text("Reviewed the api slice at the pushed tip; no blocking findings.\nVERDICT: approved\n")
        delivery = self.root / "slice-delivery.json"
        delivery.write_text(json.dumps({"found": True, "agent": "slicer", "report_path": str(report)}))
        seat = {**self.dispatch, "id": "slice-1", "role": "reviewer#api", "agent": "slicer",
                "reviewer_scope": "verification"}
        seat.pop("requirements")
        seat.pop("brief")
        self.seed({**seat, "report": str(report)})
        result = self.assess({"id": "assessment-slice", "dispatch": "slice-1",
                              "report": str(report), "delivery": str(delivery)})
        # The assessment record is independently versioned, so its `role` keeps
        # holding the RESPONSIBILITY. The seat stays on the dispatch the record
        # cites (#434).
        self.assertEqual(result["role"], "reviewer")
        self.assertEqual(result["verdict"], "approved")
        self.assertIsNone(result["criteria"])
        self.assertEqual(result["dispatch"], "slice-1")
        self.assertEqual(self.state["recovery"]["dispatches"][-1]["role"], "reviewer#api")
        self.assertEqual(self.state["assignments"][-1]["role"], "reviewer")
        engagement.validate_assessments(self.state)

    def test_a_contract_gap_records_nothing(self):
        for text in ("No lines at all.\n", CONSULT_REPORT + "ACCEPTANCE 2/2: met — again\n",
                     CONSULT_REPORT + "VERDICT: approved\n", CONSULT_REPORT.replace("ACCEPTANCE 2/2: met — flow verified against the shipped screens\n", "")):
            with self.subTest(text=text):
                self.report.write_text(text)
                with self.assertRaises(UsageError):
                    self.assess()
                self.assertEqual(self.state["specialist_assessments"], [])

    def test_exact_assessment_retry_preserves_original_time_and_unavailable_receipts(self):
        original = copy.deepcopy(self.assess())
        self.report.unlink()
        self.delivery.unlink()
        self.assertEqual(self.assess(at="2026-02-03T12:00:00Z"), original)
        self.assertEqual(self.state["specialist_assessments"], [original])
        engagement.validate_assessments(self.state)

    def test_changed_retry_refuses_without_rewriting_original_assessment(self):
        self.assess()
        before = copy.deepcopy(self.state)
        for change in ({"report": str(self.root / "other.md")}, {"delivery": str(self.root / "other.json")},
                       {"dispatch": "other-dispatch"}):
            with self.subTest(change=change), self.assertRaises(UsageError):
                self.assess({**self.data, **change})
            self.assertEqual(self.state, before)

    def test_pending_wrong_worker_or_wrong_report_delivery_is_not_assessable(self):
        for proof in ({"found": False, "agent": "worker", "report_path": str(self.report)},
                      {"found": True, "agent": "other", "report_path": str(self.report)},
                      {"found": True, "agent": "worker", "report_path": str(self.root / "other.md")},
                      {"found": 1, "agent": "worker", "report_path": str(self.report)}, [], "done"):
            with self.subTest(proof=proof):
                self.delivery.write_text(json.dumps(proof))
                with self.assertRaises(UsageError):
                    self.assess()
                self.assertEqual(self.state["specialist_assessments"], [])
        self.delivery.write_text("not JSON")
        with self.assertRaises(UsageError):
            self.assess()

    def test_missing_or_unreadable_evidence_does_not_append_assessment(self):
        for source in (self.report, self.delivery):
            before = source.read_bytes()
            source.unlink()
            with self.assertRaises(UsageError):
                self.assess()
            self.assertEqual(self.state["specialist_assessments"], [])
            source.write_bytes(before)

    def test_unenrolled_report_and_unconfirmed_dispatch_are_refused(self):
        other = self.root / "unenrolled.md"
        other.write_text("Different report")
        with self.assertRaises(UsageError):
            self.assess({**self.data, "report": str(other)})
        for status in ("reserved", "sending", "sent_but_not_started", "not_sent"):
            self.state["recovery"]["dispatches"][0]["status"] = status
            with self.subTest(status=status), self.assertRaises(UsageError):
                self.assess()
        self.assertEqual(self.state["specialist_assessments"], [])

    def test_assessment_time_is_explicit(self):
        for at in ("2026-02-03T09:00:00Z", "2026-02-03T11:00:00"):
            with self.subTest(at=at), self.assertRaises(UsageError):
                self.assess(at=at)
        self.assertEqual(self.state["specialist_assessments"], [])

    def test_warm_followup_requires_assessment_and_retired_enrollment(self):
        with self.assertRaises(UsageError):
            engagement.require_followup(self.state, self.path, {"advisor": "worker"})
        self.assess()
        with self.assertRaises(UsageError):
            engagement.require_followup(self.state, self.path, {"advisor": "worker"})
        self.retire()
        engagement.require_followup(self.state, self.path, {"advisor": "worker"})

    def test_changed_sources_refuse_followup_while_saved_assessment_remains_readable(self):
        self.assess()
        self.retire()
        for path in (self.report, self.delivery):
            before = path.read_bytes()
            path.write_text("Changed evidence")
            with self.assertRaises(UsageError):
                engagement.require_followup(self.state, self.path, {"advisor": "worker"})
            engagement.validate_assessments(self.state)
            path.write_bytes(before)

    def test_intervening_assignment_cannot_reuse_previous_consultation_assessment(self):
        self.assess()
        self.retire()
        add_assignment(self.state, LATER, "advisor", "worker", task="other-task", requirements=REQUIREMENT)
        with self.assertRaises(UsageError):
            engagement.require_followup(self.state, self.path, {"advisor": "worker"})

    def test_accepted_needs_every_criterion_met_and_current_bytes(self):
        with self.assertRaises(UsageError):
            engagement.require_accepted(self.state, "consult-1", str(self.report))
        self.assess()
        record = engagement.require_accepted(self.state, "consult-1", str(self.report))
        self.assertEqual((record or {}).get("id"), "assessment-1")
        self.report.write_text(CONSULT_REPORT + "late edit\n")
        with self.assertRaises(UsageError):
            engagement.require_accepted(self.state, "consult-1", str(self.report))
        self.report.write_text(CONSULT_REPORT.replace("2/2: met", "2/2: unmet"))
        self.assess({**self.data, "id": "assessment-2"})
        with self.assertRaises(UsageError) as caught:
            engagement.require_accepted(self.state, "consult-1", str(self.report))
        self.assertEqual(dict(caught.exception.details or {}).get("unmet"), [2])

    def test_legacy_record_migrates_idempotently_and_satisfies_nothing(self):
        self.assess()
        legacy = legacy_record(self.state["specialist_assessments"][0])
        legacy["contribution"] = "design"
        self.state["specialist_assessments"] = [legacy]
        self.path.write_text(json.dumps(self.state))
        loaded, usable = load_state_checked(self.path)
        self.assertTrue(usable)
        record = loaded["specialist_assessments"][0]
        self.assertEqual(record["schema_version"], engagement.ASSESSMENT_SCHEMA_VERSION)
        self.assertEqual(record["source"], "foreman_assessment")
        self.assertEqual(record["legacy"], {"outcome": "legacy outcome", "summary": "legacy summary"})
        self.assertEqual(record["contribution"], "design")
        rewritten = self.path.read_bytes()
        again, usable = load_state_checked(self.path)
        self.assertTrue(usable)
        self.assertEqual(self.path.read_bytes(), rewritten)
        self.assertFalse(engagement.migrate_assessments(again))
        self.assertEqual(again, loaded)
        # A legacy record never accepts, never supports a warm follow-up, and
        # never counts toward the diagnose or judge-investigation gate.
        with self.assertRaises(UsageError):
            engagement.require_accepted(again, "consult-1", str(self.report))
        self.retire()
        with self.assertRaises(UsageError):
            engagement.require_followup(again, self.path, {"advisor": "worker"})
        self.assertEqual(recovery.accepted(again["specialist_assessments"]), [])
        with self.assertRaises(UsageError) as caught:
            engagement.record_assessment(again, self.path, self.data, LATER)
        self.assertIn("migrated foreman assessment", caught.exception.message)

    def test_schema_one_record_with_schema_two_fields_is_corrupt(self):
        self.assess()
        legacy = legacy_record(self.state["specialist_assessments"][0])
        for extra in ({"source": "report"}, {"verdict": None}, {"legacy": None}):
            with self.subTest(extra=extra):
                self.state["specialist_assessments"] = [{**legacy, **extra}]
                self.path.write_text(json.dumps(self.state))
                before = self.path.read_bytes()
                _loaded, usable = load_state_checked(self.path, warn=lambda _: None)
                self.assertFalse(usable)
                self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_assessment_preserves_owner_file_and_refuses_read(self):
        self.assess()
        variants = ({"schema_version": 3}, {"assignment_index": 9}, {"assignment_index": False}, {"agent": "other-worker"},
                    {"task": "another-task"}, {"at": "2026-02-03T09:00:00Z"}, {"source": "operator"},
                    {"verdict": "approved"}, {"criteria": 3}, {"legacy": {"outcome": "x", "summary": "y"}},
                    {"gap": {"message": "x", "gaps": []}},
                    {"contribution": "maybe"},
                    {"report_evidence": {**self.state["specialist_assessments"][0]["report_evidence"], "path": "/other.md"}})
        for change in variants:
            with self.subTest(change=change):
                corrupted = copy.deepcopy(self.state)
                corrupted["specialist_assessments"][0].update(change)
                self.path.write_text(json.dumps(corrupted))
                before = self.path.read_bytes()
                warnings = []
                _loaded, usable = load_state_checked(self.path, warn=warnings.append)
                self.assertFalse(usable)
                self.assertTrue(warnings)
                self.assertEqual(self.path.read_bytes(), before)

    def test_a_brief_receipt_other_than_the_dispatchs_frozen_brief_is_corrupt(self):
        # #625 review: a report-sourced consultation record binds the frozen
        # brief its own dispatch sent, never an unrelated receipt.
        record = self.assess()
        other = frozen_brief(self.root, criteria=3, name="unrelated-brief.md")
        other_sha = recovery.receipt(other)[0]["sha256"]
        for evidence in ({"path": other, "sha256": other_sha},
                         {"path": record["brief_evidence"]["path"], "sha256": other_sha},
                         {"path": str(self.root / "brief.md"), "sha256": record["brief_evidence"]["sha256"]}):
            with self.subTest(evidence=evidence):
                corrupted = copy.deepcopy(self.state)
                corrupted["specialist_assessments"][0]["brief_evidence"] = evidence
                with self.assertRaises(UsageError):
                    engagement.validate_assessments(corrupted)
        engagement.validate_assessments(self.state)

    def test_boolean_or_unhashable_line_values_are_corrupt(self):
        self.assess()
        rows = self.state["specialist_assessments"][0]["acceptance"]
        variants = ({"acceptance": [{**rows[0], "k": True}, rows[1]]}, {"criteria": True},
                    {"acceptance": [{**rows[0], "state": ["met"]}, rows[1]]}, {"contribution": ["design"]},
                    {"source": ["report"]})
        for change in variants:
            with self.subTest(change=change):
                corrupted = copy.deepcopy(self.state)
                corrupted["specialist_assessments"][0].update(change)
                with self.assertRaises(UsageError):
                    engagement.validate_assessments(corrupted)

    def seed_reviewer(self, text):
        report = self.root / "review.md"
        report.write_text(text)
        delivery = self.root / "review-delivery.json"
        delivery.write_text(json.dumps({"found": True, "agent": "verifier", "report_path": str(report)}))
        seat = {**self.dispatch, "id": "review-1", "role": "reviewer", "agent": "verifier",
                "reviewer_scope": "verification", "report": str(report)}
        seat.pop("requirements")
        seat.pop("brief")
        self.seed(seat)
        return {"id": "review-assessment", "dispatch": "review-1", "report": str(report), "delivery": str(delivery)}

    def test_quoted_implementation_examples_add_no_exclusion_while_genuine_survives_gap(self):
        quoted = ("`CONTRIBUTION: implementation`\n"
                  "> CONTRIBUTION: implementation\n"
                  "```\nCONTRIBUTION: implementation\n```\n"
                  "Reviewed the tip; no verdict here.\n")
        data = self.seed_reviewer(quoted)
        with self.assertRaises(UsageError) as caught:
            self.assess(data)
        self.assertNotIsInstance(caught.exception, engagement.ContractGap)
        self.assertEqual(self.state["specialist_assessments"], [])
        Path(data["report"]).write_text(quoted + "CONTRIBUTION: design\n")
        with self.assertRaises(engagement.ContractGap) as gapped:
            self.assess(data)
        record = self.state["specialist_assessments"][-1]
        self.assertIs(gapped.exception.record, record)
        self.assertEqual((record["source"], record["contribution"], record["verdict"]),
                         ("contribution_only", "design", None))
        constraints = composition.selection_constraints(
            ["reviewer"], [], {}, self.state["assignments"], "task-1",
            assessments=self.state["specialist_assessments"], candidate_names=["verifier"])
        self.assertEqual(constraints["exclude"]["reviewer"], ["verifier"])

    def test_a_gapped_report_still_records_its_declared_contribution(self):
        # #625 review: add-only survives a refusal. A reviewer report missing
        # its VERDICT but declaring implementation keeps the worker excluded.
        data = self.seed_reviewer("Reviewed the tip; I rewrote the parser myself.\nCONTRIBUTION: implementation\n")
        with self.assertRaises(engagement.ContractGap) as caught:
            self.assess(data)
        self.assertIn("missing VERDICT line", caught.exception.details["gaps"])
        record = self.state["specialist_assessments"][-1]
        self.assertIs(caught.exception.record, record)
        self.assertEqual((record["source"], record["contribution"], record["verdict"], record["acceptance"]),
                         ("contribution_only", "implementation", None, None))
        engagement.validate_assessments(self.state)
        constraints = composition.selection_constraints(
            ["reviewer"], [], {}, self.state["assignments"], "task-1",
            assessments=self.state["specialist_assessments"], candidate_names=["verifier"])
        self.assertEqual(constraints["exclude"]["reviewer"], ["verifier"])
        # It never satisfies acceptance.
        with self.assertRaises(UsageError):
            engagement.require_accepted(self.state, "review-1", data["report"])
        # An identical retry replays the same refusal and records nothing new.
        before = copy.deepcopy(self.state["specialist_assessments"])
        with self.assertRaises(engagement.ContractGap) as replay:
            self.assess(data, at="2026-02-03T12:00:00+00:00")
        self.assertEqual(replay.exception.message, caught.exception.message)
        self.assertEqual(replay.exception.details["gaps"], caught.exception.details["gaps"])
        self.assertEqual(self.state["specialist_assessments"], before)
        # After source cleanup the retry replays without reading the report.
        original = Path(data["report"]).read_bytes()
        Path(data["report"]).unlink()
        with self.assertRaises(engagement.ContractGap) as cleaned:
            self.assess(data, at="2026-02-03T12:30:00+00:00")
        self.assertEqual(cleaned.exception.message, caught.exception.message)
        self.assertEqual(self.state["specialist_assessments"], before)
        Path(data["report"]).write_bytes(original)
        # Different report bytes under that id are id reuse.
        Path(data["report"]).write_text("Reviewed again.\nVERDICT: approved\n")
        with self.assertRaisesRegex(UsageError, "new id") as reused:
            self.assess(data)
        self.assertNotIsInstance(reused.exception, engagement.ContractGap)
        self.assertEqual(self.state["specialist_assessments"], before)
        self.assertEqual(recovery.accepted(self.state["specialist_assessments"]), [])
        save_state(self.path, self.state)
        self.assertTrue(load_state_checked(self.path)[1])

    def test_a_blocking_verdict_is_an_accepted_assignment_that_still_gates_the_round(self):
        # #625 note section 3: acceptance is contract completeness; the
        # blocking verdict gates the round, never the assignment.
        data = self.seed_reviewer("Reviewed the tip; B1 is blocking.\nVERDICT: blocking\n")
        record = self.assess(data)
        accepted = engagement.require_accepted(self.state, "review-1", data["report"])
        self.assertEqual((accepted or {}).get("id"), "review-assessment")
        self.assertEqual(record["verdict"], "blocking")
        # A verdict-bearing investigator's blocking outcome is no ground for a diagnosis.
        self.assertTrue(engagement.all_met(record))
        self.assertFalse(engagement.investigated(record))

    def test_a_gapped_report_without_an_excluding_contribution_records_nothing(self):
        data = self.seed_reviewer("Reviewed.\n")
        for text in ("Reviewed.\n", "Reviewed.\nCONTRIBUTION: none\n", "Reviewed.\nCONTRIBUTION: some\n"):
            with self.subTest(text=text):
                Path(data["report"]).write_text(text)
                with self.assertRaises(UsageError) as caught:
                    self.assess(data)
                self.assertNotIsInstance(caught.exception, engagement.ContractGap)
                self.assertEqual(self.state["specialist_assessments"], [])

    def test_unhashable_or_forged_contribution_only_fields_are_corrupt(self):
        data = self.seed_reviewer("Reviewed.\nCONTRIBUTION: design\n")
        with self.assertRaises(engagement.ContractGap):
            self.assess(data)
        for change in ({"contribution": "none"}, {"contribution": ["design"]}, {"verdict": "approved"},
                       {"legacy": {"outcome": "x", "summary": "y"}}, {"gap": None}, {"gap": {"message": "x"}},
                       {"gap": {"message": "x", "gaps": [1]}}):
            with self.subTest(change=change):
                corrupted = copy.deepcopy(self.state)
                corrupted["specialist_assessments"][-1].update(change)
                with self.assertRaises(UsageError):
                    engagement.validate_assessments(corrupted)

    def test_schema_one_record_with_an_unhashable_contribution_is_corrupt(self):
        self.assess()
        legacy = legacy_record(self.state["specialist_assessments"][0])
        for value in ([], {}, ["design"]):
            with self.subTest(value=value):
                self.state["specialist_assessments"] = [{**legacy, "contribution": value}]
                self.path.write_text(json.dumps(self.state))
                before = self.path.read_bytes()
                warnings = []
                _loaded, usable = load_state_checked(self.path, warn=warnings.append)
                self.assertFalse(usable)
                self.assertTrue(any("corrupt specialist assessment" in item for item in warnings))
                self.assertEqual(self.path.read_bytes(), before)

    def test_duplicate_assessment_ids_are_invalid(self):
        self.assess()
        self.state["specialist_assessments"].append(copy.deepcopy(self.state["specialist_assessments"][0]))
        with self.assertRaises(UsageError):
            engagement.validate_assessments(self.state)

    def test_missing_or_invalid_reviewer_scope_preserves_history_without_crashing(self):
        for role in ("reviewer", "advisor", "developer"):
            for missing, value in ((True, None), (False, []), (False, {}), (False, False), (False, "invalid")):
                state = empty_state()
                add_assignment(state, AT, role, "worker", task="task-1")
                if missing:
                    state["assignments"][0].pop("reviewer_scope")
                else:
                    state["assignments"][0]["reviewer_scope"] = value
                self.path.write_text(json.dumps(state))
                before = self.path.read_bytes()
                warnings = []
                with self.subTest(role=role, missing=missing, value=value):
                    _loaded, usable = load_state_checked(self.path, warn=warnings.append)
                    self.assertFalse(usable)
                    self.assertTrue(any("reviewer" in item for item in warnings))
                    self.assertEqual(self.path.read_bytes(), before)

    def test_schema_five_migration_preserves_unknown_specialty_and_reviewer_provenance(self):
        old = empty_state()
        add_assignment(old, AT, "reviewer", "worker", task="old-task")
        old.pop("specialist_assessments")
        old["schema_version"] = 5
        old["recovery"]["schema_version"] = 4
        del old["recovery"]["refusal_authorizations"]
        del old["recovery"]["diagnoses"]
        del old["recovery"]["legacy_ruling_recoveries"]
        del old["recovery"]["approaches"]
        original = old["assignments"][0]
        original["schema_version"] = 5
        original.pop("requirements")
        original.pop("reviewer_scope")
        original.pop("judge_mode")
        self.path.write_text(json.dumps(old))
        migrated, usable = load_state_checked(self.path)
        self.assertTrue(usable)
        self.assertEqual(migrated["specialist_assessments"], [])
        self.assertEqual(migrated["assignments"][0], {**original, "schema_version": STATE_SCHEMA_VERSION,
                                                    "requirements": None, "reviewer_scope": "unknown",
                                                    "judge_mode": None})
        self.assertEqual(migrated["recovery"]["schema_version"], recovery.RECOVERY_STORE_VERSION)

    def test_older_schema_cannot_bless_future_composition_fields(self):
        for target in ("document", "assignment"):
            old = empty_state()
            add_assignment(old, AT, "reviewer", "worker", task="old-task")
            old["schema_version"] = 5
            if target == "assignment":
                old.pop("specialist_assessments")
                old["assignments"][0]["schema_version"] = 5
            self.path.write_text(json.dumps(old))
            before = self.path.read_bytes()
            _loaded, usable = load_state_checked(self.path, warn=lambda _: None)
            self.assertFalse(usable)
            self.assertEqual(self.path.read_bytes(), before)

    def recovered_delivery_fixture(self):
        case = delivery_fixture.NativeDeliveryTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.report.write_text("Current report bytes.\nVERDICT: approved\n")
        state, request = case.recovery_fixture()
        state["assignments"][0].update(role="reviewer", reviewer_scope="unknown", judge_mode=None)
        dispatch = state["recovery"]["dispatches"][0]
        dispatch["role"] = "reviewer"
        dispatch["result"]["role"] = "reviewer"
        source = Path(request["source"])
        source.write_text(source.read_text().replace("Your role for this task is JUDGE", "Your role for this task is REVIEWER"))
        recovered = report_delivery.recover(state["recovery"], state["assignments"], request, delivery_fixture.AT)
        path = case.tmp / "state.json"
        who = supervision.identity("delivery-lead", str(case.tmp), "fixture", pane_id="delivery-lead-pane")
        supervision.bind(path, who, delivery_fixture.AT)
        supervision.enroll(path, {"id": dispatch["id"], "agent": dispatch["agent"], "task": dispatch["task"],
            "report": str(case.report), "pane_id": delivery_fixture.PANE, "native_session": None}, delivery_fixture.AT)
        delivery = case.tmp / "recovered-delivery.json"
        delivery.write_text(json.dumps(recovered))
        data = {"id": "recovered-assessment", "dispatch": dispatch["id"], "report": str(case.report), "delivery": str(delivery)}
        return state, path, data, recovered

    def test_exact_owner_recovered_delivery_can_be_assessed(self):
        state, path, data, recovered = self.recovered_delivery_fixture()
        self.assertTrue(recovered["found"])
        result = engagement.record_assessment(state, path, data, delivery_fixture.AT)
        self.assertEqual(result["dispatch"], recovered["dispatch"])
        self.assertEqual(result["delivery_evidence"], recovery.receipt(data["delivery"])[0])
        save_state(path, state)
        _loaded, usable = load_state_checked(path)
        self.assertTrue(usable)

    def test_recovered_delivery_cannot_assess_changed_report_bytes(self):
        state, path, data, _recovered = self.recovered_delivery_fixture()
        Path(data["report"]).write_text("A later report never covered by that delivery")
        with self.assertRaises(UsageError):
            engagement.record_assessment(state, path, data, delivery_fixture.AT)
        self.assertEqual(state["specialist_assessments"], [])

    def test_altered_or_unowned_recovered_delivery_cannot_be_assessed(self):
        state, path, data, recovered = self.recovered_delivery_fixture()
        for change in ({"id": "forged"}, {"dispatch": "different-dispatch"}, {"found": False},
                       {"input": {**recovered["input"], "report": "/other-report.md"}}):
            with self.subTest(change=change):
                Path(data["delivery"]).write_text(json.dumps({**recovered, **change}))
                with self.assertRaises(UsageError):
                    engagement.record_assessment(state, path, data, delivery_fixture.AT)
                self.assertEqual(state["specialist_assessments"], [])
        Path(data["delivery"]).write_text(json.dumps(recovered))
        state["recovery"]["delivery_recoveries"] = []
        with self.assertRaises(UsageError):
            engagement.record_assessment(state, path, data, delivery_fixture.AT)


if __name__ == "__main__":
    unittest.main()
