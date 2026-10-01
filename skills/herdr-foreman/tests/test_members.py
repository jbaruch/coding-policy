"""close-member and check-member compose the per-report chains in one owner call (#508)."""

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import errno
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from foreman import engagement, members, recovery
from foreman import supervision as store
from foreman.errors import StateError, UsageError
from foreman.state import add_assignment, empty_state, save_state

AT = "2026-09-01T12:00:00+00:00"
LATER = "2026-09-01T12:05:00+00:00"


def ledger_text(task, events, state: "str | Path" = "/state.json", version="1", base="a" * 40):
    lines = ["---", "schema_version: " + version, "task: " + task, "base_revision: " + base,
             "dispatch_state: " + str(state), "---", "", "# Task Ledger", ""]
    for event in events:
        lines.append("## " + event.pop("_heading", event["id"]))
        lines.append("")
        for key, value in event.items():
            lines.append("- {}: {}".format(key, value))
        lines.append("")
    return "\n".join(lines)


class MembersCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="foreman-members-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "state.json"
        who = store.identity("lead-session", str(self.root), "fixture", pane_id="lead-pane")
        store.bind(self.path, who, AT, root=self.root / "bindings")
        self.report = str(self.root / "reviewer.md")
        store.enroll(self.path, {"id": "dispatch-a", "agent": "codex-a", "task": "task-a", "report": self.report,
                                 "pane_id": "pane-a", "native_session": None}, AT)
        self.ledger = self.root / "TASK-LEDGER.md"
        # The ledger binds the utility dispatch state; it exists wherever a
        # dispatch was recorded, and close-member refuses a missing one.
        self.path.write_text(json.dumps(empty_state()))

    def write_ledger(self, *decisions, task="task-a", report=None, dispatch="dispatch-a", state=None, version="1",
                     drop=None, base="a" * 40, fields=None):
        events = [{"schema_version": 1, "id": "event-{}".format(index), "at": AT, "subject": "assignment",
                   "dispatch_id": dispatch, "worker": "codex-a", "role": "reviewer", "report": report or self.report,
                   "observed": "report delivered", "decision": decision, "head_revision": "unknown",
                   "evidence": self.report, "assessment": "read in full"}
                  for index, decision in enumerate(decisions, 1)]
        if drop is not None:
            for event in events:
                event.pop(drop, None)
        for event in events:
            event.update(fields or {})
        self.ledger.write_text(ledger_text(task, events, state or self.path, version, base))

    def emit(self):
        store.transaction(self.path, lambda data: store.append_event(data, AT, "dispatch-a", "report", {"observation_only": True}))


def seed_contract(case):
    """Record the owner records an `accepted` closure reads (#625), over the report's current bytes.

    The reviewer dispatch the enrollment is, and the contract lines of its report.
    """
    delivery = case.root / "delivery.json"
    delivery.write_text(json.dumps({"found": True, "agent": "codex-a", "report_path": case.report}))
    state = empty_state()
    dispatch = {"id": "dispatch-a", "fingerprint": "a" * 64, "task": "task-a", "role": "reviewer", "agent": "codex-a",
                "fix_round": None, "plan": None, "work": None, "reviewer_scope": "verification"}
    recovery.reserve(state["recovery"], dispatch, AT)
    add_assignment(state, AT, "reviewer", "codex-a", task="task-a", reviewer_scope="verification")
    recovery.finish_dispatch(state["recovery"], "dispatch-a", {"task": "task-a", "role": "reviewer", "agent": "codex-a",
                                                              "fix_round": None, "reviewer_scope": "verification",
                                                              "status": "applied"}, 0, AT)
    engagement.record_assessment(state, case.path, {"id": "assess-a", "dispatch": "dispatch-a",
                                                    "report": case.report, "delivery": str(delivery)}, AT)
    save_state(case.path, state)
    return state, delivery


class CloseMemberTest(MembersCase):
    def setUp(self):
        super().setUp()
        Path(self.report).write_text("Reviewed the tip.\nVERDICT: approved\n")
        self.state, self.delivery = seed_contract(self)

    def assess(self):
        engagement.record_assessment(self.state, self.path, {"id": "assess-b", "dispatch": "dispatch-a",
                                                             "report": self.report, "delivery": str(self.delivery)}, AT)
        save_state(self.path, self.state)

    def test_accepted_needs_the_reports_recorded_contract_lines(self):
        # #625: a reviewer's `accepted` rests on its recorded VERDICT line at
        # the current bytes; `needs_work` is never refused on that ground.
        self.emit()
        self.write_ledger("accepted")
        self.state["specialist_assessments"] = []
        save_state(self.path, self.state)
        with self.assertRaisesRegex(UsageError, "no report-contract assessment"):
            members.close(self.path, "dispatch-a", self.ledger, LATER)
        self.assertTrue(store.pending(store.load(self.path)))
        self.assess()
        Path(self.report).write_text("Reviewed the tip again.\nVERDICT: approved\n")
        with self.assertRaisesRegex(UsageError, "changed since its assessment"):
            members.close(self.path, "dispatch-a", self.ledger, LATER)
        self.write_ledger("needs_work")
        self.assertEqual(members.close(self.path, "dispatch-a", self.ledger, LATER)["decision"], "needs_work")

    def test_accepted_without_an_owner_dispatch_is_refused(self):
        save_state(self.path, empty_state())
        self.write_ledger("accepted")
        with self.assertRaisesRegex(UsageError, "no owner dispatch"):
            members.close(self.path, "dispatch-a", self.ledger, LATER)

    def test_accepted_on_an_unusable_state_is_refused(self):
        self.write_ledger("accepted")
        with patch("foreman.members.load_state_checked", return_value=(empty_state(), False)), \
             self.assertRaisesRegex(StateError, "restore it"):
            members.close(self.path, "dispatch-a", self.ledger, LATER)

    def test_refuses_before_the_ledger_records_an_assessed_outcome(self):
        self.emit()
        for decisions in ((), ("reported",), ("accepted", "reported")):
            with self.subTest(decisions=decisions):
                self.write_ledger(*decisions)
                with self.assertRaisesRegex(UsageError, "no assessed outcome"):
                    members.close(self.path, "dispatch-a", self.ledger, LATER)
                data = store.load(self.path)
                self.assertTrue(store.pending(data))
                self.assertTrue(next(row for row in data["members"] if row["id"] == "dispatch-a")["active"])

    def test_acknowledges_this_members_events_and_resolves_it(self):
        self.emit()
        store.enroll(self.path, {"id": "dispatch-b", "agent": "codex-b", "task": "task-a", "report": str(self.root / "b.md"),
                                 "pane_id": "pane-b", "native_session": None}, AT)
        store.transaction(self.path, lambda data: store.append_event(data, AT, "dispatch-b", "report", {"observation_only": True}))
        self.write_ledger("reported", "accepted")
        result = members.close(self.path, "dispatch-a", self.ledger, LATER)
        self.assertEqual((result["decision"], result["ledger_event"]), ("accepted", "event-2"))
        self.assertEqual(len(result["acknowledged"]), 1)
        data = store.load(self.path)
        self.assertEqual([row["member"] for row in store.pending(data)], ["dispatch-b"])
        self.assertFalse(next(row for row in data["members"] if row["id"] == "dispatch-a")["active"])

    def test_a_repeated_close_replays(self):
        self.emit()
        self.write_ledger("needs_work")
        first = members.close(self.path, "dispatch-a", self.ledger, LATER)
        again = members.close(self.path, "dispatch-a", self.ledger, LATER)
        self.assertEqual(again["resolved"], first["resolved"])
        self.assertEqual(again["acknowledged"], [])

    def test_assignment_scoped_close_removes_the_pane_before_resolution(self):
        dispatch = self.state["recovery"]["dispatches"][0]
        dispatch.update(schema_version=recovery.ASSIGNMENT_DISPATCH_VERSION, worker_kind="codex")
        dispatch["result"].update(schema_version=recovery.ASSIGNMENT_DISPATCH_VERSION,
                                  worker_kind="codex", assignment_scoped=True, pane_id="pane-a")
        save_state(self.path, self.state)
        self.emit()
        self.write_ledger("needs_work")
        client = Mock()
        closure = {"pane_id": "pane-a", "agent": "codex-a", "closed": True, "replayed": False}
        with patch("foreman.members.lifecycle.close", return_value=closure) as close_pane:
            result = members.close(self.path, "dispatch-a", self.ledger, LATER, client=client)
        close_pane.assert_called_once_with(client, "codex-a", "pane-a")
        self.assertEqual(result["pane_closure"], closure)
        self.assertFalse(next(row for row in store.load(self.path)["members"]
                              if row["id"] == "dispatch-a")["active"])

    def test_reconciled_scoped_close_uses_the_dispatch_worker_kind_marker(self):
        dispatch = self.state["recovery"]["dispatches"][0]
        dispatch["schema_version"] = recovery.ASSIGNMENT_DISPATCH_VERSION
        dispatch["worker_kind"] = "codex"
        dispatch["result"]["schema_version"] = recovery.ASSIGNMENT_DISPATCH_VERSION
        dispatch["result"]["worker_kind"] = "codex"
        dispatch["result"]["pane_id"] = "pane-a"
        dispatch["result"].pop("assignment_scoped", None)
        save_state(self.path, self.state)
        self.emit()
        self.write_ledger("needs_work")
        client = Mock()
        with patch("foreman.members.lifecycle.close", return_value={"closed": True}) as close_pane:
            members.close(self.path, "dispatch-a", self.ledger, LATER, client=client)
        close_pane.assert_called_once_with(client, "codex-a", "pane-a")

    def test_a_ledger_for_another_task_is_refused(self):
        self.write_ledger("accepted", task="task-z")
        with self.assertRaisesRegex(UsageError, "records task 'task-z'"):
            members.close(self.path, "dispatch-a", self.ledger, LATER)

    def test_an_event_for_another_dispatch_does_not_count(self):
        self.write_ledger("accepted", dispatch="dispatch-earlier")
        with self.assertRaisesRegex(UsageError, "missing"):
            members.close(self.path, "dispatch-a", self.ledger, LATER)

    def test_an_unusable_ledger_closes_nothing(self):
        self.emit()
        other = self.root / "other-state.json"
        other.write_text("{}")
        for kwargs, why in (({"state": str(other)}, "bound to dispatch state"),
                            ({"state": "/elsewhere/state.json"}, "does not resolve"),
                            ({"version": "2"}, "is schema 2"), ({"drop": "evidence"}, "lacks evidence")):
            with self.subTest(why=why):
                self.write_ledger("accepted", **kwargs)
                with self.assertRaisesRegex(UsageError, why):
                    members.close(self.path, "dispatch-a", self.ledger, LATER)
                self.assertTrue(store.pending(store.load(self.path)))

    def test_a_malformed_field_format_closes_nothing(self):
        self.emit()
        sha = "a" * 40
        cases = (({"at": "not-a-timestamp"}, sha, None, "has at 'not-a-timestamp'"),
                 ({"at": "2026-09-01T12:00:00"}, sha, None, "not a timezone-qualified"),
                 ({"head_revision": "not-a-sha"}, sha, None, "has head_revision 'not-a-sha'"),
                 ({"head_revision": "abc1234"}, sha, None, "has head_revision 'abc1234'"),
                 ({"head_revision": "g" * 40}, sha, None, "not a full hexadecimal commit SHA"),
                 ({}, "g" * 40, None, "base_revision '{}' is not a full hexadecimal".format("g" * 40)),
                 ({"_heading": "renamed-section"}, sha, None, "carries id 'event-1', not its section heading"),
                 ({}, "abc1234", None, "base_revision 'abc1234'"),
                 ({}, sha, "relative/state.json", "dispatch_state 'relative/state.json' is not an absolute"),
                 ({}, sha, "~/state.json", "dispatch_state '~/state.json' is not an absolute"),
                 ({}, sha, "/nul\0state.json", "dispatch_state '/nul\\x00state.json' is not an absolute"),
                 ({"subject": "round"}, sha, None, "has subject 'round', not task or assignment"),
                 ({"decision": "approved"}, sha, None, "has decision 'approved', not one of the assignment"),
                 ({"decision": "completed"}, sha, None, "has decision 'completed', not one of the assignment"),
                 ({"worker": "not_applicable"}, sha, None, "has worker 'not_applicable'"),
                 ({"report": "reviewer.md"}, sha, None, "has report 'reviewer.md', not an absolute path or unknown"),
                 ({"report": "not_applicable"}, sha, None, "has report 'not_applicable', not an absolute path"),
                 ({"subject": "task", "decision": "in_progress", "dispatch_id": "not_applicable",
                   "worker": "not_applicable", "role": "not_applicable", "report": "not_applicable"}, sha, None,
                  "has report 'not_applicable', not an absolute path or unknown"))
        for fields, base, state, why in cases:
            with self.subTest(why=why):
                self.write_ledger("accepted", fields=fields, base=base, state=state)
                with self.assertRaisesRegex(UsageError, re.escape(why)):
                    members.close(self.path, "dispatch-a", self.ledger, LATER)
                data = store.load(self.path)
                self.assertTrue(store.pending(data))
                self.assertTrue(next(row for row in data["members"] if row["id"] == "dispatch-a")["active"])

    def test_a_dispatch_state_in_a_symlink_loop_closes_nothing(self):
        self.emit()
        (self.root / "loop-a").symlink_to(self.root / "loop-b")
        (self.root / "loop-b").symlink_to(self.root / "loop-a")
        self.write_ledger("accepted", state=str(self.root / "loop-a" / "state.json"))
        with self.assertRaisesRegex(UsageError, "does not resolve"):
            members.close(self.path, "dispatch-a", self.ledger, LATER)
        self.assertTrue(store.pending(store.load(self.path)))

    def test_a_symlink_loop_is_refused_under_the_python_3_13_resolve_contract(self):
        # From 3.13, non-strict resolve returns a looping path without raising
        # and only strict resolve reports ELOOP; model that on any interpreter.
        self.emit()
        looping = str(self.root / "loop" / "state.json")
        self.write_ledger("accepted", state=looping)
        real = Path.resolve

        def resolve(path, strict=False):
            if str(path) == looping:
                if strict:
                    raise OSError(errno.ELOOP, "Too many levels of symbolic links", looping)
                return path
            return real(path, strict=strict)

        with patch.object(Path, "resolve", resolve), self.assertRaisesRegex(UsageError, "does not resolve"):
            members.close(self.path, "dispatch-a", self.ledger, LATER)
        self.assertTrue(store.pending(store.load(self.path)))

    def test_a_missing_dispatch_state_refuses_without_a_second_resolve(self):
        # On 3.11/3.12 a missing component followed by `..` and a loop makes a
        # strict resolve raise FileNotFoundError and a non-strict one raise
        # RuntimeError; either way the refusal must stay the usable-ledger one.
        self.emit()
        missing = str(self.root / "gone" / ".." / "loop" / "state.json")
        self.write_ledger("accepted", state=missing)
        real = Path.resolve

        def resolve(path, strict=False):
            if str(path) == missing:
                if strict:
                    raise FileNotFoundError(errno.ENOENT, "No such file or directory", missing)
                raise RuntimeError("Symlink loop from {!r}".format(missing))
            return real(path, strict=strict)

        with patch.object(Path, "resolve", resolve), self.assertRaisesRegex(UsageError, "does not resolve"):
            members.close(self.path, "dispatch-a", self.ledger, LATER)
        self.assertTrue(store.pending(store.load(self.path)))

    def test_a_missing_dispatch_state_never_matches_by_string(self):
        # An already-normalized missing path equals the state path as a
        # string; the ledger must still bind a state that exists.
        self.emit()
        self.write_ledger("accepted", state=str(self.path))
        real = Path.resolve
        bound = str(self.path)

        def resolve(path, strict=False):
            if strict and str(path) == bound:
                raise FileNotFoundError(errno.ENOENT, "No such file or directory", bound)
            return real(path, strict=strict)

        with patch.object(Path, "resolve", resolve), self.assertRaisesRegex(UsageError, "does not resolve"):
            members.close(self.path, "dispatch-a", self.ledger, LATER)
        self.assertTrue(store.pending(store.load(self.path)))

    def test_a_ledger_line_cannot_forge_the_section_heading(self):
        self.emit()
        self.write_ledger("accepted", fields={"_heading": "renamed-section", "_section": "event-1"})
        with self.assertRaisesRegex(UsageError, "event renamed-section carries id 'event-1'"):
            members.close(self.path, "dispatch-a", self.ledger, LATER)
        self.assertTrue(store.pending(store.load(self.path)))

    def test_an_unrelated_malformed_event_still_closes_nothing(self):
        self.emit()
        self.write_ledger("accepted")
        task_event = ("## task-1\n\n- schema_version: 1\n- id: task-1\n- at: {}\n- subject: task\n"
                      "- dispatch_id: dispatch-a\n- worker: not_applicable\n- role: not_applicable\n"
                      "- report: unknown\n- observed: round started\n- decision: in_progress\n"
                      "- head_revision: not_applicable\n- evidence: unknown\n- assessment: open\n").format(AT)
        self.ledger.write_text(self.ledger.read_text() + "\n" + task_event)
        with self.assertRaisesRegex(UsageError, "event task-1 has dispatch_id 'dispatch-a'"):
            members.close(self.path, "dispatch-a", self.ledger, LATER)
        self.assertTrue(store.pending(store.load(self.path)))

    def test_a_repeated_field_cannot_replace_a_malformed_value(self):
        self.emit()
        self.write_ledger("accepted", fields={"at": "not-a-timestamp"})
        text = self.ledger.read_text().replace("- at: not-a-timestamp\n", "- at: not-a-timestamp\n- at: {}\n".format(AT))
        self.ledger.write_text(text)
        with self.assertRaisesRegex(UsageError, "event event-1 repeats at"):
            members.close(self.path, "dispatch-a", self.ledger, LATER)
        self.write_ledger("accepted")
        text = self.ledger.read_text().replace("schema_version: 1\ntask:", "schema_version: 1\nbase_revision: bad\ntask:", 1)
        self.ledger.write_text(text)
        with self.assertRaisesRegex(UsageError, "frontmatter repeats base_revision"):
            members.close(self.path, "dispatch-a", self.ledger, LATER)
        self.assertTrue(store.pending(store.load(self.path)))

    def test_a_task_event_and_prose_bullets_are_accepted(self):
        self.emit()
        self.write_ledger("accepted")
        task_event = ("## task-1\n\n- schema_version: 1\n- id: task-1\n- at: {}\n- subject: task\n"
                      "- dispatch_id: not_applicable\n- worker: not_applicable\n- role: not_applicable\n"
                      "- report: unknown\n- observed: round started\n- decision: in_progress\n"
                      "- head_revision: not_applicable\n- evidence: unknown\n- assessment: open\n"
                      "- note: a prose bullet\n- note: another prose bullet\n").format(AT)
        self.ledger.write_text(self.ledger.read_text() + "\n" + task_event)
        self.assertEqual(members.close(self.path, "dispatch-a", self.ledger, LATER)["decision"], "accepted")

    def test_a_repeated_event_id_closes_nothing(self):
        self.emit()
        self.write_ledger("reported", "accepted", fields={"id": "event-1", "_heading": "event-1"})
        with self.assertRaisesRegex(UsageError, "event id event-1 names more than one event"):
            members.close(self.path, "dispatch-a", self.ledger, LATER)
        self.assertTrue(store.pending(store.load(self.path)))

    def test_well_formed_field_formats_are_accepted(self):
        self.emit()
        for head in ("b" * 40, "c" * 64, "A" * 40, "unknown", "not_applicable"):
            with self.subTest(head=head):
                self.write_ledger("accepted", fields={"head_revision": head, "at": "2026-09-01T12:00:00Z"},
                                  base="D" * 40)
                self.assertEqual(members.ledger_events(self.ledger)[1][0]["head_revision"], head)
        result = members.close(self.path, "dispatch-a", self.ledger, LATER)
        self.assertEqual(result["decision"], "accepted")

    def test_an_event_for_another_report_does_not_count(self):
        self.write_ledger("accepted", report=str(self.root / "other.md"))
        with self.assertRaisesRegex(UsageError, "missing"):
            members.close(self.path, "dispatch-a", self.ledger, LATER)


class CheckMemberTest(MembersCase):
    def test_passes_base_send_time_and_worktree_from_the_records(self):
        state = empty_state()
        state["recovery"]["tasks"]["task-a"] = {"task": "task-a", "base_revision": "b" * 40}
        state["recovery"]["dispatches"] += [
            {"id": "dispatch-earlier", "agent": "codex-a", "task": "task-a", "report": self.report, "status": "applied",
             "result": {"at": AT}},
            {"id": "dispatch-a", "agent": "codex-a", "task": "task-a", "report": self.report, "status": "applied",
             "result": {"at": LATER}}]
        calls = []

        def run(argv, **_kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 1, stdout='{"found": false}', stderr="pending")

        with patch("foreman.members.load_state_checked", return_value=(state, True)):
            payload, code = members.check(self.path, "dispatch-a", "/work/tree", run=run)
        argv = calls[0]
        self.assertEqual(argv[2:], ["--once", "--worktree", "/work/tree", "--base", "b" * 40, "--since", LATER,
                                    "codex-a", self.report])
        self.assertTrue(argv[1].endswith("wait-report.sh"))
        self.assertEqual((code, payload["exit"], payload["wait"]), (1, 1, '{"found": false}'))

    def test_a_dispatch_without_a_send_time_is_refused_before_waiting(self):
        with patch("foreman.members.load_state_checked", return_value=(empty_state(), True)), \
             self.assertRaisesRegex(UsageError, "no applied send time"):
            members.check(self.path, "dispatch-a", run=subprocess.run)

    def test_an_unknown_enrollment_is_refused(self):
        with self.assertRaisesRegex(UsageError, "Unknown enrollment"):
            members.check(self.path, "dispatch-z", run=subprocess.run)

    def test_an_unusable_state_is_refused_with_its_repair(self):
        from foreman.errors import StateError
        with patch("foreman.members.load_state_checked", return_value=(empty_state(), False)), \
             self.assertRaisesRegex(StateError, "restore it"):
            members.check(self.path, "dispatch-a", run=subprocess.run)


class CheckMemberCliTest(MembersCase):
    def run_cli(self, code):
        import io
        import json as _json
        from foreman.cli import main
        payload = {"schema_version": 1, "enrollment": "dispatch-a", "inputs": {}, "exit": code, "wait": "",
                   "diagnostics": "wait-report: agent not found"}
        out, err = io.StringIO(), io.StringIO()
        with patch("foreman.members.check", return_value=(payload, code)):
            rc = main(["--state", str(self.path), "--config", str(self.root / "config.json"), "check-member",
                       "--enrollment", "dispatch-a"], stdout=out, stderr=err)
        return rc, _json.loads(out.getvalue()), err.getvalue()

    def test_a_verdict_exits_zero_with_it_in_the_payload(self):
        for code in (0, 1, 3, 4, 5):
            with self.subTest(code=code):
                rc, payload, _ = self.run_cli(code)
                self.assertEqual((rc, payload["exit"]), (0, code))

    def test_a_wait_that_did_not_run_fails_the_command(self):
        rc, payload, err = self.run_cli(2)
        self.assertEqual((rc, payload["exit"]), (1, 2))
        self.assertIn("wait_failed", err)
        self.assertIn("agent not found", err)


class LedgerDocsPointAtTheConstants(unittest.TestCase):
    """The ledger docs name members.py's format constants and restate none of them (#589)."""

    SKILL = Path(_ROOT)
    # All-caps prose words the docs use that are not constants.
    ACRONYMS = {"API", "BLOCKED", "JSON", "SHA", "VCS"}
    CONSTANT = re.compile(r"(?<![\w.-])([A-Z][A-Z_]{2,})(?![\w.-])")

    def docs(self):
        schema = (self.SKILL / "state-schema.md").read_text(encoding="utf-8")
        schema = schema[schema.index("## Task Ledger"):schema.index("\n## ", schema.index("## Task Ledger") + 1)]
        return {"state-schema.md": schema,
                "references/task-ledger.md": (self.SKILL / "references" / "task-ledger.md").read_text(encoding="utf-8")}

    def test_every_constant_the_docs_name_exists(self):
        for name, text in self.docs().items():
            cited = set(self.CONSTANT.findall(text)) - self.ACRONYMS
            with self.subTest(doc=name):
                self.assertTrue(cited)
                self.assertEqual(sorted(c for c in cited if not hasattr(members, c)), [])

    def test_the_template_carries_exactly_the_schema_fields(self):
        text = self.docs()["references/task-ledger.md"]
        template = text[text.index("```markdown"):]
        match = re.search(r"^---\n(.*?)\n---$", template, re.M | re.S)
        assert match is not None, "the blank template lost its frontmatter"
        front = match.group(1)
        self.assertEqual(tuple(line.split(":")[0] for line in front.splitlines()), members.FRONT_FIELDS)
        self.assertEqual(tuple(re.findall(r"^- ([a-z_]+): ", template, re.M)), members.EVENT_FIELDS)

    def test_the_schema_table_covers_exactly_the_event_fields(self):
        rows = re.findall(r"^\| (`[a-z_]+`(?:, `[a-z_]+`)*) \|", self.docs()["state-schema.md"], re.M)
        fields = [field for row in rows for field in re.findall(r"`([a-z_]+)`", row)]
        self.assertEqual(sorted(fields), sorted(members.EVENT_FIELDS))

    def test_no_doc_line_restates_a_vocabulary(self):
        literals = set().union(*members.DECISIONS.values())
        # Narrative may use one decision by name; a vocabulary table or list names several.
        for name, text in self.docs().items():
            with self.subTest(doc=name):
                self.assertLess(len({v for v in literals if "`{}`".format(v) in text}), 2)

    def test_the_constants_agree_with_each_other(self):
        self.assertLessEqual(members.ASSESSED, members.DECISIONS["assignment"])
        self.assertEqual(set(members.SUBJECTS), set(members.DECISIONS))
        self.assertLessEqual(set(members.FREE_TEXT_FIELDS) | set(members.ASSIGNMENT_IDENTITY),
                             set(members.EVENT_FIELDS))
        self.assertLessEqual(members.REPORT_PLACEHOLDERS | members.HEAD_PLACEHOLDERS,
                             {members.UNKNOWN, members.NOT_APPLICABLE})


if __name__ == "__main__":
    unittest.main()
