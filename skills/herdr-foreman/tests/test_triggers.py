"""Diff-time Team Composition trigger detection and its unaddressed-trigger gate."""

import io
import hashlib
import json
import subprocess
import os
import shlex
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foreman import cli, triggers
from tests import test_specialist_cli as consultation_fixture
from foreman.state import save_state
from foreman.errors import UsageError


DECLARATION = {
    "schema_version": 1,
    "package_roots": ["src/*", "tools"],
    "package_change_lines": 50,
    "trust_boundary_paths": ["src/auth/*", "verify-*.sh"],
    "cli_spec_paths": ["src/cli/*.py"],
    "cli_surface_markers": ["add_parser(", "add_argument("],
    "user_doc_paths": ["docs/*", "README.md"],
}


def declaration(**overrides):
    return {**DECLARATION, **overrides, "path": ".herdr/triggers.json",
            "authority": {"kind": "repository", "revision": "fixture"}}


class TempCase(unittest.TestCase):
    def temp_dir(self):
        """A directory removed on teardown (rules/testing-standards.md)."""
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        return Path(holder.name)


def namespace(**overrides):
    fields = {"repo": ".", "base": "BASE", "head": None, "roles": None,
              "requirements": None, "decisions": None, "bootstrap_declaration": None}
    return SimpleNamespace(**{**fields, **overrides})


class DeclarationTest(TempCase):
    def setUp(self):
        self.tmp = self.temp_dir()
        (self.tmp / ".herdr").mkdir()
        self.path = self.tmp / triggers.DECLARATION_FILE

    def write(self, payload):
        self.path.write_text(json.dumps(payload))

    def test_reads_a_complete_declaration(self):
        self.write(DECLARATION)
        result = triggers.load_declaration(self.tmp)
        self.assertEqual(result["package_change_lines"], 50)
        self.assertEqual(result["package_roots"], ["src/*", "tools"])
        self.assertEqual(result["path"], str(self.path))

    def test_missing_declaration_names_what_to_state(self):
        with self.assertRaises(UsageError) as caught:
            triggers.load_declaration(self.tmp)
        self.assertIn("No trigger declaration at", caught.exception.message)
        self.assertIn("package roots", caught.exception.message)

    def test_only_a_missing_optional_declaration_is_tolerated(self):
        result = triggers.load_declaration(self.tmp, required=False)
        self.assertEqual(result, {"path": str(self.path),
                                  "authority": {"kind": "repository", "revision": "worktree"}})
        self.path.write_text("{not json")
        with self.assertRaises(UsageError) as caught:
            triggers.load_declaration(self.tmp, required=False)
        self.assertIn("invalid JSON", caught.exception.message)

    def test_a_dangling_optional_declaration_is_refused(self):
        self.path.symlink_to(self.tmp / "missing-declaration.json")
        with self.assertRaises(UsageError) as caught:
            triggers.load_declaration(self.tmp, required=False)
        self.assertIn("No trigger declaration at", caught.exception.message)

    def test_invalid_json_is_refused(self):
        self.path.write_text("{not json")
        with self.assertRaises(UsageError) as caught:
            triggers.load_declaration(self.tmp)
        self.assertIn("invalid JSON", caught.exception.message)

    def test_every_surface_is_required(self):
        for field in triggers.DECLARATION_FIELDS - {"schema_version"}:
            payload = {key: value for key, value in DECLARATION.items() if key != field}
            self.write(payload)
            with self.assertRaises(UsageError) as caught:
                triggers.load_declaration(self.tmp)
            self.assertIn("requires exactly", caught.exception.message)

    def test_unknown_field_is_refused(self):
        self.write({**DECLARATION, "extra": []})
        with self.assertRaises(UsageError):
            triggers.load_declaration(self.tmp)

    def test_unsupported_schema_version_is_refused(self):
        self.write({**DECLARATION, "schema_version": 2})
        with self.assertRaises(UsageError) as caught:
            triggers.load_declaration(self.tmp)
        self.assertIn("schema_version 2", caught.exception.message)

    def test_unstated_package_size_is_refused(self):
        for value in (0, -1, None, "400", True):
            self.write({**DECLARATION, "package_change_lines": value})
            with self.assertRaises(UsageError) as caught:
                triggers.load_declaration(self.tmp)
            self.assertIn("package_change_lines", caught.exception.message)

    def test_empty_surface_list_states_the_absence(self):
        self.write({**DECLARATION, "trust_boundary_paths": []})
        self.assertEqual(triggers.load_declaration(self.tmp)["trust_boundary_paths"], [])

    def test_malformed_globs_are_refused(self):
        for value in ("src/*", [""], ["a\nb"], [3]):
            self.write({**DECLARATION, "user_doc_paths": value})
            with self.assertRaises(UsageError):
                triggers.load_declaration(self.tmp)

    def test_malformed_markers_are_refused(self):
        for value in ("add_parser(", [""], ["a\tb".replace("\t", "\x01")], [3]):
            self.write({**DECLARATION, "cli_surface_markers": value})
            with self.assertRaises(UsageError):
                triggers.load_declaration(self.tmp)


class DecisionsTest(TempCase):
    def setUp(self):
        self.tmp = self.temp_dir()
        self.path = self.tmp / "decisions.json"

    def test_absent_file_is_no_decision(self):
        self.assertEqual(triggers.load_decisions(None), {})

    def test_reads_a_recorded_reason(self):
        self.path.write_text(json.dumps({"schema_version": 1, "decisions": {"security": "  no foreign input reaches it  "}}))
        self.assertEqual(triggers.load_decisions(self.path), {"security": "no foreign input reaches it"})

    def test_unknown_trigger_is_refused(self):
        self.path.write_text(json.dumps({"schema_version": 1, "decisions": {"perf": "later"}}))
        with self.assertRaises(UsageError) as caught:
            triggers.load_decisions(self.path)
        self.assertIn("unknown trigger", caught.exception.message)

    def test_empty_reason_is_refused(self):
        self.path.write_text(json.dumps({"schema_version": 1, "decisions": {"security": "   "}}))
        with self.assertRaises(UsageError) as caught:
            triggers.load_decisions(self.path)
        self.assertIn("silence is never that decision", caught.exception.message)

    def test_unreadable_file_is_refused(self):
        with self.assertRaises(UsageError) as caught:
            triggers.load_decisions(self.tmp / "absent.json")
        self.assertIn("Cannot read staffing decisions", caught.exception.message)

    def test_wrong_shape_is_refused(self):
        self.path.write_text(json.dumps({"schema_version": 2, "decisions": {}}))
        with self.assertRaises(UsageError):
            triggers.load_decisions(self.path)


class RequirementsTest(TempCase):
    def setUp(self):
        self.tmp = self.temp_dir()
        self.path = self.tmp / "requirements.json"

    def test_collects_planned_specialties(self):
        self.path.write_text(json.dumps({"schema_version": 1, "assignments": {
            "advisor": {"specialty": "security", "required_capabilities": ["threat"],
                        "independent": False, "engagement": "boundary-review"}}}))
        self.assertEqual(triggers.load_requirements(self.path), {"security"})

    def test_absent_path_staffs_nothing(self):
        self.assertEqual(triggers.load_requirements(None), set())

    def test_wrong_shape_is_refused(self):
        self.path.write_text(json.dumps({"assignments": {}}))
        with self.assertRaises(UsageError):
            triggers.load_requirements(self.path)


class MatchTest(unittest.TestCase):
    def test_path_globs_span_path_separators(self):
        self.assertTrue(triggers.matches("docs/guide/install.md", ["docs/*"]))
        self.assertFalse(triggers.matches("README.md", ["docs/*"]))

    def test_package_globs_match_one_segment(self):
        self.assertTrue(triggers.package_matches("src/auth", ["src/*"]))
        self.assertFalse(triggers.package_matches("src/auth/tokens", ["src/*"]))
        self.assertEqual(triggers.package_of("src/auth/tokens/rs256.py", ["src/*"]), "src/auth")

    def test_nearest_declared_ancestor_owns_the_file(self):
        self.assertEqual(triggers.package_of("src/auth/token.py", ["src/*"]), "src/auth")
        self.assertEqual(triggers.package_of("tools/build.sh", ["src/*", "tools"]), "tools")
        self.assertIsNone(triggers.package_of("README.md", ["src/*"]))


class DetectTest(unittest.TestCase):
    def test_a_package_absent_from_the_base_fires_the_architect(self):
        fired = triggers.detect(declaration(), {"src/new/mod.py": "A"}, {"src/new/mod.py": 3},
                                {"src/new": False})
        self.assertEqual([signal["signal"] for signal in fired["architect"]], ["new_package"])
        self.assertEqual(fired["architect"][0]["evidence"], "src/new")

    def test_a_package_above_the_stated_size_fires_the_architect(self):
        fired = triggers.detect(declaration(), {"src/old/a.py": "M", "src/old/b.py": "M"},
                                {"src/old/a.py": 30, "src/old/b.py": 21}, {"src/old": True})
        self.assertEqual(fired["architect"][0]["signal"], "package_change_lines")
        self.assertIn("51 lines > 50", fired["architect"][0]["evidence"])

    def test_a_package_at_the_stated_size_stays_quiet(self):
        fired = triggers.detect(declaration(), {"src/old/a.py": "M"}, {"src/old/a.py": 50},
                                {"src/old": True})
        self.assertNotIn("architect", fired)

    def test_a_changed_trust_boundary_path_fires_security(self):
        fired = triggers.detect(declaration(), {"src/auth/token.py": "M"}, {"src/auth/token.py": 2},
                                {"src/auth": True})
        self.assertEqual(fired["security"], [{"signal": "trust_boundary_path", "evidence": "src/auth/token.py"}])

    def test_an_added_user_document_fires_documentation(self):
        fired = triggers.detect(declaration(), {"docs/install.md": "A"}, {"docs/install.md": 9}, {})
        self.assertEqual(fired["documentation"][0]["evidence"], "docs/install.md")

    def test_an_edited_user_document_does_not_fire_documentation(self):
        fired = triggers.detect(declaration(), {"docs/install.md": "M"}, {"docs/install.md": 9}, {})
        self.assertNotIn("documentation", fired)

    def test_an_undeclared_path_fires_nothing(self):
        self.assertEqual(triggers.detect(declaration(), {"CHANGELOG.md": "M"}, {"CHANGELOG.md": 4}, {}), {})

    def test_binary_churn_counts_no_lines(self):
        fired = triggers.detect(declaration(), {"src/old/logo.png": "M"}, {"src/old/logo.png": 0},
                                {"src/old": True})
        self.assertNotIn("architect", fired)


class CliSurfaceTest(unittest.TestCase):
    def test_a_declared_marker_on_an_added_line_fires(self):
        found = triggers.cli_surface(declaration(), {"src/cli/main.py": "M"},
                                     {"src/cli/main.py": ['    sub.add_parser("ship")']})
        self.assertEqual(found[0]["signal"], "added_cli_surface")
        self.assertIn('add_parser("ship")', found[0]["evidence"])

    def test_an_added_line_without_a_marker_stays_quiet(self):
        self.assertEqual(triggers.cli_surface(declaration(), {"src/cli/main.py": "M"},
                                              {"src/cli/main.py": ["    return payload"]}), [])

    def test_a_declared_refusal_marker_fires(self):
        # rules/agent-team-operation.md: a new user-facing refusal path is a
        # UX and product trigger, not only a new command or flag.
        found = triggers.cli_surface(declaration(cli_surface_markers=["raise UsageError("]),
                                     {"src/cli/main.py": "M"},
                                     {"src/cli/main.py": ['        raise UsageError("state it", {})']})
        self.assertEqual(found[0]["signal"], "added_cli_surface")
        self.assertIn("raise UsageError(", found[0]["evidence"])

    def test_a_marker_outside_the_spec_surface_stays_quiet(self):
        self.assertEqual(triggers.cli_surface(declaration(), {"src/old/a.py": "M"},
                                              {"src/old/a.py": ["add_argument("]}), [])


class ParseTest(unittest.TestCase):
    def test_name_status_pairs_records(self):
        self.assertEqual(triggers.parse_name_status("M\0a.py\0A\0b.py\0"), {"a.py": "M", "b.py": "A"})

    def test_odd_name_status_output_is_refused(self):
        with self.assertRaises(UsageError):
            triggers.parse_name_status("M\0a.py\0A\0")

    def test_numstat_sums_added_and_deleted(self):
        self.assertEqual(triggers.parse_numstat("3\t4\ta.py\0-\t-\tlogo.png\0"), {"a.py": 7, "logo.png": 0})

    def test_added_lines_come_from_the_hunks_alone(self):
        patch = "\n".join([
            "diff --git a/x.py b/x.py",
            "--- a/x.py",
            "+++ b/x.py",
            "@@ -1 +1,2 @@",
            "+added one",
            "+++ content that looks like a file header",
        ])
        self.assertEqual(triggers.parse_added_lines(patch),
                         ["added one", "++ content that looks like a file header"])

    def test_a_quoted_path_header_is_never_read(self):
        # git quotes a path carrying a quote, a backslash or a non-ASCII byte.
        patch = "\n".join([
            'diff --git "a/src/cli/a\\"b.py" "b/src/cli/a\\"b.py"',
            '--- "a/src/cli/a\\"b.py"',
            '+++ "b/src/cli/a\\"b.py"',
            "@@ -1,0 +2 @@",
            '+    sub.add_parser("ship")',
        ])
        self.assertEqual(triggers.parse_added_lines(patch), ['    sub.add_parser("ship")'])


class ReportTest(unittest.TestCase):
    def fire(self, **kwargs):
        fired = {"security": [{"signal": "trust_boundary_path", "evidence": "src/auth/token.py"}]}
        return triggers.report(declaration(), "BASE", "HEAD", fired, **{
            "roles": [], "specialties": set(), "decisions": {}, **kwargs})

    def test_an_unstaffed_fired_trigger_fails_the_round(self):
        payload, failure = self.fire()
        self.assertEqual(payload["unaddressed"], ["security"])
        assert failure is not None
        self.assertEqual(failure["error"], "unaddressed_trigger")
        self.assertIn("security", failure["message"])

    def test_a_planned_specialty_addresses_the_trigger(self):
        payload, failure = self.fire(specialties={"security"})
        self.assertIsNone(failure)
        row = next(row for row in payload["triggers"] if row["trigger"] == "security")
        self.assertEqual(row["addressed"], "specialty:security")

    def test_a_planned_role_addresses_the_architect(self):
        payload, failure = triggers.report(
            declaration(), "BASE", "HEAD", {"architect": [{"signal": "new_package", "evidence": "src/new"}]},
            roles=["developer", "architect"], specialties=set(), decisions={})
        self.assertIsNone(failure)
        row = next(row for row in payload["triggers"] if row["trigger"] == "architect")
        self.assertEqual(row["addressed"], "role:architect")

    def test_a_recorded_decision_addresses_the_trigger(self):
        payload, failure = self.fire(decisions={"security": "the changed line is a comment"})
        self.assertIsNone(failure)
        row = next(row for row in payload["triggers"] if row["trigger"] == "security")
        self.assertEqual(row["addressed"], "decision")
        self.assertEqual(row["decision"], "the changed line is a comment")

    def test_a_decision_for_a_quiet_trigger_is_reported_unused(self):
        payload, failure = self.fire(specialties={"security"}, decisions={"documentation": "no docs here"})
        self.assertIsNone(failure)
        self.assertEqual(payload["unused_decisions"], ["documentation"])

    def test_every_trigger_is_reported(self):
        payload, _failure = self.fire(specialties={"security"})
        self.assertEqual([row["trigger"] for row in payload["triggers"]], list(triggers.TRIGGERS))
        self.assertEqual(payload["fired"], ["security"])


class ThisRepoDeclarationTest(unittest.TestCase):
    """This repo's own declaration, dogfooded as a consuming repo."""

    def setUp(self):
        self.repo = Path(__file__).resolve().parents[3]
        self.declaration = triggers.load_declaration(self.repo)

    def test_the_modules_that_gate_generated_evidence_are_trust_boundaries(self):
        # rules/agent-team-operation.md: anything deciding whether generated
        # content or a proposed change is safe triggers security.
        for path in ("skills/herdr-foreman/foreman/recovery.py",
                     "skills/herdr-foreman/foreman/engagement.py",
                     "skills/herdr-foreman/foreman/report_delivery.py",
                     "skills/herdr-foreman/foreman/supervision.py",
                     "skills/herdr-foreman/foreman/assign.py",
                     "skills/herdr-foreman/foreman/composition.py",
                     "skills/herdr-foreman/foreman/cli.py",
                     "skills/herdr-foreman/foreman/tiers.py",
                     "skills/herdr-foreman/foreman/launch.py",
                     "skills/herdr-foreman/foreman/triggers.py",
                     "skills/herdr-foreman/foreman.sh",
                     ".herdr/triggers.json",
                     "rules/agent-team-operation.md",
                     "rules/review-severity.md",
                     "skills/release/watch-pr-reviews.sh",
                     "skills/release/poll-pr-reviews.sh",
                     "skills/release/smart-publish.sh",
                     ".github/workflows/tests.yml"):
            with self.subTest(path=path):
                self.assertTrue((self.repo / path).exists(), path)
                fired = triggers.detect(self.declaration, {path: "M"}, {path: 1}, {})
                self.assertEqual([signal["evidence"] for signal in fired["security"]], [path])

    def test_each_skill_is_one_package_root(self):
        self.assertEqual(triggers.package_of("skills/herdr-foreman/foreman/cli.py",
                                             self.declaration["package_roots"]), "skills/herdr-foreman")

    def test_a_new_refusal_path_fires_ux_product(self):
        found = triggers.cli_surface(self.declaration, {"skills/herdr-foreman/foreman/recovery.py": "M"},
                                     {"skills/herdr-foreman/foreman/recovery.py":
                                      ['        raise UsageError("state the surface", {})']})
        self.assertEqual(len(found), 1)

    def test_a_shipped_shell_command_is_a_cli_surface(self):
        # A shell command carries its own flags and refusals, and a new one
        # must fire UX and product the way a new Python flag does.
        for path, line in (("skills/herdr-foreman/prune-worktrees.sh",
                            '      --dry-run) warn "usage: prune-worktrees.sh <shared-checkout>"; return 1 ;;'),
                           ("skills/release/watch-pr-reviews.sh", 'echo "usage: watch-pr-reviews.sh" >&2'),
                           ("scripts/run-tests.sh", 'echo "Usage: run-tests.sh [base-dir]" >&2')):
            with self.subTest(path=path):
                self.assertTrue((self.repo / path).exists(), path)
                self.assertTrue(triggers.matches(path, self.declaration["cli_spec_paths"]), path)
                found = triggers.cli_surface(self.declaration, {path: "M"}, {path: [line]})
                self.assertEqual(len(found), 1, path)


class PlannedSurfacesTest(TempCase):
    """A pre-implementation round has no diff; it declares its surfaces."""

    def setUp(self):
        self.tmp = self.temp_dir()
        (self.tmp / ".herdr").mkdir()
        (self.tmp / triggers.DECLARATION_FILE).write_text(json.dumps(DECLARATION))
        self.plan = self.tmp / "planned.json"
        self.calls = []

    def runner(self, responses=None):
        responses = responses or {}
        def run(arguments):
            self.calls.append(arguments)
            if arguments[0] == "show" and arguments[1].endswith(":" + triggers.DECLARATION_FILE):
                return (self.tmp / triggers.DECLARATION_FILE).read_text()
            if arguments[0] == "ls-tree" and arguments[-1] == triggers.DECLARATION_FILE:
                return triggers.DECLARATION_FILE + "\n"
            for key, value in responses.items():
                if key in arguments:
                    return value
            return ""
        return run

    def write(self, **overrides):
        payload = {"schema_version": 1, "added": [], "changed": [], "package_lines": {}, "cli_surface": []}
        payload.update(overrides)
        self.plan.write_text(json.dumps(payload))
        return str(self.plan)

    def test_an_empty_round_without_a_plan_is_refused(self):
        with self.assertRaises(UsageError) as caught:
            triggers.run_command(namespace(repo=self.tmp), runner=self.runner())
        self.assertIn("--planned", caught.exception.message)

    def test_an_empty_plan_classifies_no_more_than_an_absent_one(self):
        # A well-formed but empty plan would otherwise report success with no
        # triggers, which is the silence the detector exists to end.
        with self.assertRaises(UsageError) as caught:
            triggers.run_command(namespace(repo=self.tmp, planned=self.write()), runner=self.runner())
        self.assertIn("classifies nothing", caught.exception.message)

    def test_a_round_that_writes_nothing_classifies_and_fires_nothing(self):
        # coding-policy#471: an investigation touches no repository surface,
        # so it has nothing to declare and Step 5 must not refuse it.
        payload, failure = triggers.run_command(
            namespace(repo=self.tmp, roles="investigator",
                      planned=self.write(writes_repository=False)),
            runner=self.runner())
        self.assertIsNone(failure)
        self.assertEqual(payload["fired"], [])
        self.assertEqual(payload["unaddressed"], [])
        self.assertTrue(all(row["fired"] is False for row in payload["triggers"]))

    def test_a_round_that_writes_nothing_needs_no_declaration(self):
        (self.tmp / triggers.DECLARATION_FILE).unlink()
        payload, failure = triggers.run_command(
            namespace(repo=self.tmp, roles="investigator",
                      planned=self.write(writes_repository=False)),
            runner=self.runner())
        self.assertIsNone(failure)
        self.assertEqual(payload["declaration"], str(self.tmp / triggers.DECLARATION_FILE))
        self.assertEqual(payload["fired"], [])

    def test_a_round_that_writes_nothing_never_lists_untracked_files(self):
        # coding-policy#499: untracked files are no surface of a round that
        # writes nothing, so they are never enumerated or read.
        triggers.run_command(
            namespace(repo=self.tmp, roles="investigator",
                      planned=self.write(writes_repository=False)),
            runner=self.runner())
        self.assertFalse(any("--others" in call for call in self.calls))
        # The tracked-diff check still runs.
        self.assertTrue(any("--name-status" in call for call in self.calls))

    def test_every_read_only_responsibility_may_declare_it(self):
        for role in sorted(triggers.READ_ONLY_ROLES):
            with self.subTest(role=role):
                _payload, failure = triggers.run_command(
                    namespace(repo=self.tmp, roles=role,
                              planned=self.write(writes_repository=False)),
                    runner=self.runner())
                self.assertIsNone(failure)

    def test_a_writing_responsibility_cannot_declare_it(self):
        for roles in ("developer", "release", "investigator,developer"):
            with self.subTest(roles=roles):
                with self.assertRaises(UsageError) as caught:
                    triggers.run_command(
                        namespace(repo=self.tmp, roles=roles,
                                  planned=self.write(writes_repository=False)),
                        runner=self.runner())
                self.assertIn("write no repository content", caught.exception.message)

    def test_the_read_only_claim_needs_the_roles_that_make_it_checkable(self):
        with self.assertRaises(UsageError) as caught:
            triggers.run_command(
                namespace(repo=self.tmp, planned=self.write(writes_repository=False)),
                runner=self.runner())
        self.assertIn("--roles", caught.exception.message)

    def test_a_tracked_diff_contradicts_the_no_write_claim(self):
        with self.assertRaises(UsageError) as caught:
            triggers.run_command(
                namespace(repo=self.tmp, roles="investigator", head="HEAD",
                          planned=self.write(writes_repository=False)),
                runner=self.runner({"--name-status": "M\0src/old/a.py\0"}))
        self.assertIn("tracked diff is not empty", caught.exception.message)

    def test_a_no_write_plan_naming_a_surface_is_refused(self):
        for overrides in ({"added": ["src/new/mod.py"]}, {"changed": ["src/old/a.py"]},
                          {"cli_surface": ["src/cli/main.py"]}, {"package_lines": {"src/old": 5}}):
            with self.subTest(**overrides):
                with self.assertRaises(UsageError) as caught:
                    triggers.run_command(
                        namespace(repo=self.tmp, roles="investigator",
                                  planned=self.write(writes_repository=False, **overrides)),
                        runner=self.runner())
                self.assertIn("names no surface", caught.exception.message)

    def test_writes_repository_must_be_a_boolean(self):
        with self.assertRaises(UsageError) as caught:
            triggers.run_command(
                namespace(repo=self.tmp, roles="investigator",
                          planned=self.write(writes_repository="false")),
                runner=self.runner())
        self.assertIn("JSON boolean", caught.exception.message)

    def test_an_omitted_writes_repository_still_classifies_nothing(self):
        # Omission keeps every plan written before the field meaning what it
        # meant: only an explicit false opens the no-surface path.
        with self.assertRaises(UsageError) as caught:
            triggers.run_command(namespace(repo=self.tmp, roles="investigator", planned=self.write()),
                                 runner=self.runner())
        self.assertIn("classifies nothing", caught.exception.message)

    def test_a_plan_declaring_only_a_package_size_classifies(self):
        payload, _failure = triggers.run_command(
            namespace(repo=self.tmp, planned=self.write(package_lines={"src/old": 51})),
            runner=self.runner({"ls-tree": "src/old/a.py\n"}))
        self.assertEqual(payload["fired"], ["architect"])

    def test_a_plan_declaring_only_a_cli_surface_classifies(self):
        payload, _failure = triggers.run_command(
            namespace(repo=self.tmp, planned=self.write(cli_surface=["src/cli/main.py"])),
            runner=self.runner({"ls-tree": "src/cli/main.py\n"}))
        self.assertEqual(payload["fired"], ["ux-product"])

    def test_a_planned_new_package_fires_the_architect(self):
        payload, failure = triggers.run_command(
            namespace(repo=self.tmp, planned=self.write(added=["src/new/mod.py"])), runner=self.runner())
        self.assertEqual(payload["fired"], ["architect"])
        assert failure is not None

    def test_a_planned_package_above_the_stated_size_fires_the_architect(self):
        payload, _failure = triggers.run_command(
            namespace(repo=self.tmp, planned=self.write(changed=["src/old/a.py"], package_lines={"src/old": 51})),
            runner=self.runner({"ls-tree": "src/old/a.py\n"}))
        self.assertEqual(payload["fired"], ["architect"])

    def test_a_planned_package_at_the_stated_size_stays_quiet(self):
        payload, failure = triggers.run_command(
            namespace(repo=self.tmp, planned=self.write(changed=["src/old/a.py"], package_lines={"src/old": 50})),
            runner=self.runner({"ls-tree": "src/old/a.py\n"}))
        self.assertEqual(payload["fired"], [])
        self.assertIsNone(failure)

    def test_a_planned_trust_boundary_fires_security(self):
        payload, _failure = triggers.run_command(
            namespace(repo=self.tmp, planned=self.write(changed=["src/auth/token.py"])),
            runner=self.runner({"ls-tree": "src/auth/token.py\n"}))
        self.assertEqual(payload["fired"], ["security"])

    def test_a_planned_document_fires_documentation(self):
        payload, _failure = triggers.run_command(
            namespace(repo=self.tmp, planned=self.write(added=["docs/guide.md"])), runner=self.runner())
        self.assertEqual(payload["fired"], ["documentation"])

    def test_a_planned_cli_surface_fires_ux_product(self):
        payload, _failure = triggers.run_command(
            namespace(repo=self.tmp, planned=self.write(cli_surface=["src/cli/main.py"])),
            runner=self.runner({"ls-tree": "src/cli/main.py\n"}))
        self.assertIn("ux-product", payload["fired"])
        row = next(row for row in payload["triggers"] if row["trigger"] == "ux-product")
        self.assertEqual(row["signals"][0]["signal"], "planned_cli_surface")

    def test_a_package_declared_only_by_its_size_is_checked_against_the_base(self):
        # Its only mention is the size, so nothing puts it in `changes`; a
        # missing candidate would read as a package the base already held.
        payload, failure = triggers.run_command(
            namespace(repo=self.tmp, planned=self.write(package_lines={"src/new": 1})),
            runner=self.runner())
        self.assertEqual(payload["fired"], ["architect"])
        self.assertEqual(payload["triggers"][0]["signals"][0]["signal"], "new_package")
        assert failure is not None
        self.assertIn(["ls-tree", "--name-only", "BASE", "--", "src/new/"], self.calls)

    def test_planned_package_lines_name_a_declared_package_root(self):
        with self.assertRaises(UsageError) as caught:
            triggers.run_command(namespace(repo=self.tmp, planned=self.write(package_lines={"src/new/deep": 1})),
                                 runner=self.runner())
        self.assertIn("declared package roots", caught.exception.message)

    def test_a_planned_cli_surface_is_classified_against_every_surface(self):
        # A spec path that is also a trust boundary fires security too;
        # answering UX and product alone must not let the round pass.
        declared = {**DECLARATION, "trust_boundary_paths": ["src/cli/*"]}
        (self.tmp / triggers.DECLARATION_FILE).write_text(json.dumps(declared))
        payload, failure = triggers.run_command(
            namespace(repo=self.tmp, planned=self.write(cli_surface=["src/cli/main.py"]),
                      requirements=None, roles=None),
            runner=self.runner({"ls-tree": "src/cli/main.py\n"}))
        self.assertEqual(payload["fired"], ["security", "ux-product"])
        assert failure is not None
        self.assertEqual(failure["details"]["unaddressed"], ["security", "ux-product"])

    def test_a_planned_cli_surface_outside_the_spec_paths_is_refused(self):
        with self.assertRaises(UsageError) as caught:
            triggers.run_command(namespace(repo=self.tmp, planned=self.write(cli_surface=["src/old/a.py"])),
                                 runner=self.runner())
        self.assertIn("declared CLI spec paths", caught.exception.message)

    def test_a_staffed_plan_passes(self):
        payload, failure = triggers.run_command(
            namespace(repo=self.tmp, planned=self.write(added=["src/new/mod.py"]), roles="developer,architect"),
            runner=self.runner())
        self.assertIsNone(failure)
        self.assertEqual(payload["unaddressed"], [])

    def test_a_malformed_plan_is_refused(self):
        for payload in ({"schema_version": 2, "added": [], "changed": [], "package_lines": {}, "cli_surface": []},
                        {"schema_version": 1, "added": [], "changed": [], "package_lines": []},
                        {"schema_version": 1, "added": [], "changed": [], "package_lines": {"src/old": -1}, "cli_surface": []}):
            self.plan.write_text(json.dumps(payload))
            with self.assertRaises(UsageError):
                triggers.load_plan(self.plan)

    def test_an_unreadable_plan_is_refused(self):
        with self.assertRaises(UsageError) as caught:
            triggers.load_plan(self.tmp / "absent.json")
        self.assertIn("Cannot read planned surfaces", caught.exception.message)


class RunCommandTest(TempCase):
    def setUp(self):
        self.tmp = self.temp_dir()
        (self.tmp / ".herdr").mkdir()
        (self.tmp / triggers.DECLARATION_FILE).write_text(json.dumps(DECLARATION))
        self.calls = []

    def runner(self, responses):
        def run(arguments):
            self.calls.append(arguments)
            if arguments[0] == "show" and arguments[1].endswith(":" + triggers.DECLARATION_FILE):
                return (self.tmp / triggers.DECLARATION_FILE).read_text()
            if arguments[0] == "ls-tree" and arguments[-1] == triggers.DECLARATION_FILE:
                return triggers.DECLARATION_FILE + "\n"
            for key, value in responses.items():
                if key in arguments:
                    return value
            return ""
        return run

    def test_collects_the_diff_facts_and_reports(self):
        responses = {"--name-status": "A\0src/new/mod.py\0", "--numstat": "9\t0\tsrc/new/mod.py\0"}
        payload, failure = triggers.run_command(namespace(repo=self.tmp), runner=self.runner(responses))
        self.assertEqual(payload["fired"], ["architect"])
        assert failure is not None
        self.assertEqual(failure["details"]["unaddressed"], ["architect"])
        self.assertIn(["ls-tree", "--name-only", "BASE", "--", "src/new/"], self.calls)

    def test_a_head_compares_from_the_merge_base(self):
        responses = {"merge-base": "MERGEBASE\n", "--name-status": "A\0src/new/mod.py\0",
                     "--numstat": "9\t0\tsrc/new/mod.py\0"}
        triggers.run_command(namespace(repo=self.tmp, head="HEAD"), runner=self.runner(responses))
        self.assertIn(["merge-base", "BASE", "HEAD"], self.calls)
        self.assertTrue(any("BASE...HEAD" in call for call in self.calls))
        # "Absent from the base" is read at the merge base, not at BASE.
        self.assertIn(["ls-tree", "--name-only", "MERGEBASE", "--", "src/new/"], self.calls)

    def test_no_head_reads_the_working_tree(self):
        responses = {"--name-status": "M\0README.md\0", "--numstat": "1\t1\tREADME.md\0"}
        payload, _failure = triggers.run_command(namespace(repo=self.tmp), runner=self.runner(responses))
        self.assertIn("BASE", self.calls[0])
        self.assertEqual(payload["head"], "worktree")

    def test_the_cli_surface_patch_is_only_read_for_spec_paths(self):
        responses = {"--name-status": "M\0README.md\0", "--numstat": "1\t1\tREADME.md\0"}
        triggers.run_command(namespace(repo=self.tmp), runner=self.runner(responses))
        self.assertFalse(any("--unified=0" in call for call in self.calls))

    def test_an_added_parser_fires_ux_product(self):
        responses = {"--name-status": "M\0src/cli/main.py\0", "--numstat": "2\t0\tsrc/cli/main.py\0",
                     "ls-tree": "src/cli/main.py\n",
                     "--unified=0": ("diff --git a/src/cli/main.py b/src/cli/main.py\n"
                                     "--- a/src/cli/main.py\n+++ b/src/cli/main.py\n"
                                     "@@ -1,0 +2 @@\n+    sub.add_parser(\"ship\")\n")}
        payload, failure = triggers.run_command(namespace(repo=self.tmp), runner=self.runner(responses))
        self.assertEqual(payload["fired"], ["ux-product"])
        assert failure is not None

    def test_a_refusal_only_change_fires_ux_product(self):
        (self.tmp / triggers.DECLARATION_FILE).write_text(json.dumps(
            {**DECLARATION, "cli_surface_markers": ["raise UsageError("]}))
        responses = {"--name-status": "M\0src/cli/main.py\0", "--numstat": "1\t0\tsrc/cli/main.py\0",
                     "ls-tree": "src/cli/main.py\n",
                     "--unified=0": ("diff --git a/src/cli/main.py b/src/cli/main.py\n"
                                     "--- a/src/cli/main.py\n+++ b/src/cli/main.py\n"
                                     "@@ -9,0 +10 @@\n+    raise UsageError(\"state the surface\", {})\n")}
        payload, failure = triggers.run_command(namespace(repo=self.tmp), runner=self.runner(responses))
        self.assertEqual(payload["fired"], ["ux-product"])
        assert failure is not None

    def test_untracked_files_are_only_collected_without_a_head(self):
        responses = {"--name-status": "M\0README.md\0", "--numstat": "1\t1\tREADME.md\0"}
        triggers.run_command(namespace(repo=self.tmp, head="HEAD"), runner=self.runner(responses))
        self.assertFalse(any("--others" in call for call in self.calls))
        self.calls.clear()
        triggers.run_command(namespace(repo=self.tmp), runner=self.runner(responses))
        self.assertTrue(any("--others" in call for call in self.calls))

    def test_roles_are_read_from_the_comma_list(self):
        responses = {"--name-status": "A\0src/new/mod.py\0", "--numstat": "9\t0\tsrc/new/mod.py\0"}
        payload, failure = triggers.run_command(namespace(repo=self.tmp, roles="developer,architect"),
                                                runner=self.runner(responses))
        self.assertIsNone(failure)
        self.assertEqual(payload["unaddressed"], [])


class DetectTriggersCommandTest(TempCase):
    """The packaged command against a real git repository."""

    def setUp(self):
        self.tmp = self.temp_dir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "tests@example.invalid")
        self.git("config", "user.name", "Tests")
        (self.tmp / ".herdr").mkdir()
        (self.tmp / triggers.DECLARATION_FILE).write_text(json.dumps(DECLARATION))
        (self.tmp / "README.md").write_text("start\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *arguments):
        completed = subprocess.run(["git", "-C", str(self.tmp), *arguments],
                                   capture_output=True, text=True, check=True)
        return completed.stdout

    def run_cli(self, *arguments):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["detect-triggers", "--repo", str(self.tmp), "--base", self.base, *arguments],
                        stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def test_a_quiet_diff_exits_zero(self):
        (self.tmp / "README.md").write_text("start\nmore\n")
        code, out, err = self.run_cli()
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["fired"], [])

    def test_a_new_package_fails_until_it_is_addressed(self):
        (self.tmp / "src" / "new").mkdir(parents=True)
        (self.tmp / "src" / "new" / "mod.py").write_text("value = 1\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "add package")
        code, out, err = self.run_cli("--head", "HEAD")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["unaddressed"], ["architect"])
        self.assertEqual(json.loads(err)["error"], "unaddressed_trigger")
        code, out, err = self.run_cli("--head", "HEAD", "--roles", "architect,developer")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["unaddressed"], [])

    def test_an_added_document_fires_documentation(self):
        (self.tmp / "docs").mkdir()
        (self.tmp / "docs" / "install.md").write_text("how to install\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "add doc")
        code, out, _err = self.run_cli("--head", "HEAD")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["fired"], ["documentation"])

    def test_an_untracked_package_fires_in_the_working_tree(self):
        # coding-policy#415: `git diff` reports tracked changes only, so a whole
        # new package would fire nothing while it sits untracked.
        (self.tmp / "src" / "new").mkdir(parents=True)
        (self.tmp / "src" / "new" / "mod.py").write_text("value = 1\n")
        code, out, _err = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["unaddressed"], ["architect"])

    def test_an_untracked_document_fires_documentation(self):
        (self.tmp / "docs").mkdir()
        (self.tmp / "docs" / "install.md").write_text("how to install\n")
        code, out, _err = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["fired"], ["documentation"])

    def test_untracked_scratch_fires_nothing_on_a_round_that_writes_nothing(self):
        # An investigation's foreman shares a checkout that may hold scratch. The
        # scratch is no surface of the round: it must not refuse the round, and
        # it must not fire a trigger either (#471 review).
        (self.tmp / "docs").mkdir()
        (self.tmp / "docs" / "notes.md").write_text("scratch the foreman wrote\n")
        planned = self.tmp.parent / (self.tmp.name + "-planned.json")
        planned.write_text(json.dumps({"schema_version": 1, "added": [], "changed": [],
                                       "package_lines": {}, "cli_surface": [],
                                       "writes_repository": False}))
        self.addCleanup(planned.unlink)
        code, out, err = self.run_cli("--roles", "investigator", "--planned", str(planned))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["fired"], [])

    def test_an_unreadable_untracked_file_does_not_refuse_a_round_that_writes_nothing(self):
        # coding-policy#499: a dangling symlink is listed as untracked and
        # cannot be read. It is unrelated workspace state, so an investigation
        # proceeds; a writing round still refuses it.
        (self.tmp / "scratch").symlink_to(self.tmp / "missing-target")
        planned = self.tmp.parent / (self.tmp.name + "-planned-499.json")
        planned.write_text(json.dumps({"schema_version": 1, "added": [], "changed": [],
                                       "package_lines": {}, "cli_surface": [],
                                       "writes_repository": False}))
        self.addCleanup(planned.unlink)
        code, out, err = self.run_cli("--roles", "investigator", "--planned", str(planned))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["fired"], [])
        code, _out, err = self.run_cli()
        self.assertNotEqual(code, 0)
        self.assertIn("Cannot read untracked file scratch", err)

    def test_a_tracked_diff_still_refuses_a_round_that_writes_nothing(self):
        (self.tmp / "README.md").write_text("start\nmore\n")
        planned = self.tmp.parent / (self.tmp.name + "-planned-tracked.json")
        planned.write_text(json.dumps({"schema_version": 1, "added": [], "changed": [],
                                       "package_lines": {}, "cli_surface": [],
                                       "writes_repository": False}))
        self.addCleanup(planned.unlink)
        code, _out, err = self.run_cli("--roles", "investigator", "--planned", str(planned))
        self.assertNotEqual(code, 0)
        self.assertIn("README.md", err)

    def test_an_untracked_spec_file_fires_ux_product(self):
        (self.tmp / "src" / "cli").mkdir(parents=True)
        (self.tmp / "src" / "cli" / "ship.py").write_text('sub.add_parser("ship")\n')
        code, out, _err = self.run_cli("--roles", "architect")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["unaddressed"], ["ux-product"])

    def test_an_untracked_binary_file_counts_no_lines(self):
        (self.tmp / "src" / "new").mkdir(parents=True)
        (self.tmp / "src" / "new" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
        code, out, _err = self.run_cli()
        # It is still an added file in a package absent from the base.
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["fired"], ["architect"])

    def test_a_pushed_head_ignores_the_working_tree(self):
        (self.tmp / "README.md").write_text("start\nmore\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "edit readme")
        # Untracked in the working tree, absent from the pushed commits.
        (self.tmp / "docs").mkdir()
        (self.tmp / "docs" / "install.md").write_text("how to install\n")
        code, out, err = self.run_cli("--head", "HEAD")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["fired"], [])

    def test_a_round_with_nothing_to_classify_is_refused(self):
        code, _out, err = self.run_cli("--head", "HEAD")
        self.assertEqual(code, 1)
        self.assertIn("--planned", json.loads(err)["message"])

    def test_a_quoted_filename_still_fires_ux_product(self):
        # git quotes a path carrying a quote or a non-ASCII byte in its patch
        # header; the added command must still be seen.
        spec = self.tmp / "src" / "cli"
        spec.mkdir(parents=True)
        awkward = spec / 'a"b\u00e9.py'
        awkward.write_text("# spec\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "add spec")
        base = self.git("rev-parse", "HEAD").strip()
        awkward.write_text('# spec\nsub.add_parser("ship")\n')
        self.git("add", "-A")
        self.git("commit", "-qm", "add command")
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["detect-triggers", "--repo", str(self.tmp), "--base", base, "--head", "HEAD"],
                        stdout=out, stderr=err)
        self.assertEqual(code, 1, err.getvalue())
        self.assertIn("ux-product", json.loads(out.getvalue())["fired"])

    def test_a_missing_declaration_refuses_the_round(self):
        (self.tmp / triggers.DECLARATION_FILE).unlink()
        self.git("add", "-A")
        self.git("commit", "-qm", "drop declaration")
        code, _out, err = self.run_cli("--head", "HEAD")
        self.assertEqual(code, 1)
        self.assertIn("No trigger declaration at", json.loads(err)["message"])

    def test_a_read_only_round_needs_no_declaration(self):
        (self.tmp / triggers.DECLARATION_FILE).unlink()
        self.git("add", "-A")
        self.git("commit", "-qm", "drop declaration")
        self.base = self.git("rev-parse", "HEAD").strip()
        planned = self.tmp.parent / (self.tmp.name + "-planned-read-only.json")
        planned.write_text(json.dumps({"schema_version": 1, "added": [], "changed": [],
                                       "package_lines": {}, "cli_surface": [],
                                       "writes_repository": False}))
        self.addCleanup(planned.unlink)
        code, out, err = self.run_cli("--roles", "investigator", "--planned", str(planned))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["fired"], [])

    def test_a_rename_reads_as_a_delete_and_an_add(self):
        (self.tmp / "docs").mkdir()
        (self.tmp / "docs" / "install.md").write_text("how to install\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "add doc")
        moved = self.base
        self.git("mv", "docs/install.md", "docs/setup.md")
        self.git("commit", "-qm", "rename doc")
        code, out, _err = self.run_cli("--head", "HEAD")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["triggers"][1]["trigger"], "documentation")
        self.assertEqual(self.base, moved)


class BootstrapDeclarationCommandTest(TempCase):
    """The first declaration is externally reviewed, exact, and one-time."""

    def setUp(self):
        self.tmp = self.temp_dir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "core.autocrlf", "false")
        self.git("config", "user.email", "tests@example.invalid")
        self.git("config", "user.name", "Tests")
        (self.tmp / "README.md").write_text("start\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.artifact = self.tmp.parent / (self.tmp.name + "-reviewed-triggers.json")
        self.artifact.write_text(json.dumps(DECLARATION), encoding="utf-8")
        self.addCleanup(lambda: self.artifact.exists() and self.artifact.unlink())
        self.plan = self.tmp.parent / (self.tmp.name + "-bootstrap-plan.json")
        self.addCleanup(lambda: self.plan.exists() and self.plan.unlink())
        self.owner = consultation_fixture.SpecialistCliTest("test_assess_command_retrieves_real_receipts_without_worker_calls")
        self.owner.setUp()
        self.addCleanup(self.owner.doCleanups)
        self.owner.seed_warm_consultation(assess=False, retire=False)
        self.assess_artifact()

    def assess_artifact(self, *, binding_changes=None, acceptance="met"):
        report = self.owner.tmp / "prior-report.md"
        binding = {"repo": str(self.tmp.resolve()), "base_revision": self.base,
                   "path": str(self.artifact.resolve()),
                   "sha256": hashlib.sha256(self.artifact.read_bytes()).hexdigest()}
        binding.update(binding_changes or {})
        report.write_text("ACCEPTANCE 1/1: " + acceptance + " — declaration review evidence\nTRIGGER_DECLARATION: " + json.dumps(binding) + "\n")
        record = self.owner.tmp / "assessment.json"
        state = self.owner.saved()
        record.write_text(json.dumps({"id": "declaration-" + str(len(state["specialist_assessments"])),
            "dispatch": "prior:advisor", "report": str(report),
            "delivery": str(self.owner.tmp / "prior-delivery.json")}))
        code, _, err = self.owner.invoke(["assess-specialist", "--record", str(record), "--now", "2026-01-08T12:00:00Z"], self.owner._client({}))
        self.assertEqual(code, 0, err)

    def git(self, *arguments):
        completed = subprocess.run(["git", "-C", str(self.tmp), *arguments],
                                   capture_output=True, text=True, check=True)
        return completed.stdout

    def write_plan(self, *, digest=None):
        digest = digest or hashlib.sha256(self.artifact.read_bytes()).hexdigest()
        self.plan.write_text(json.dumps({
            "schema_version": 1,
            "added": [triggers.DECLARATION_FILE],
            "changed": [],
            "package_lines": {},
            "cli_surface": [],
            "bootstrap_declaration_sha256": digest,
        }), encoding="utf-8")

    def run_cli(self, *arguments):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["detect-triggers", "--repo", str(self.tmp), "--base", self.base,
                         "--planned", str(self.plan), "--bootstrap-declaration", str(self.artifact),
                         "--state", str(self.owner.state), *arguments], stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def test_absent_base_accepts_the_byte_identical_reviewed_artifact(self):
        self.write_plan()
        code, out, err = self.run_cli()
        self.assertEqual(code, 0, err)
        authority = json.loads(out)["declaration_authority"]
        self.assertEqual(authority["kind"], "bootstrap")
        self.assertEqual(authority["sha256"], hashlib.sha256(self.artifact.read_bytes()).hexdigest())

    def test_altered_artifact_is_refused(self):
        self.write_plan()
        self.artifact.write_text(json.dumps({**DECLARATION, "package_change_lines": 51}), encoding="utf-8")
        code, out, err = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("plan binds", err)

    def test_stale_base_with_an_existing_declaration_is_refused(self):
        (self.tmp / ".herdr").mkdir()
        (self.tmp / triggers.DECLARATION_FILE).write_text(json.dumps(DECLARATION), encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "declare triggers")
        self.base = self.git("rev-parse", "HEAD").strip()
        (self.tmp / triggers.DECLARATION_FILE).unlink()
        self.write_plan()
        code, out, err = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("bootstrap is stale", err)

    def test_missing_artifact_is_refused(self):
        self.write_plan()
        self.artifact.unlink()
        code, out, err = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("Cannot read reviewed bootstrap declaration", err)

    def test_artifact_inside_the_target_repo_is_refused(self):
        inside = self.tmp / "reviewed-triggers.json"
        inside.write_bytes(self.artifact.read_bytes())
        self.write_plan(digest=hashlib.sha256(inside.read_bytes()).hexdigest())
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["detect-triggers", "--repo", str(self.tmp), "--base", self.base,
                         "--planned", str(self.plan), "--bootstrap-declaration", str(inside)],
                        stdout=out, stderr=err)
        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("outside the target repository", err.getvalue())

    def test_first_committed_declaration_must_match_the_reviewed_bytes(self):
        self.write_plan()
        (self.tmp / ".herdr").mkdir()
        altered = {**DECLARATION, "package_change_lines": 51}
        (self.tmp / triggers.DECLARATION_FILE).write_text(json.dumps(altered), encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "install altered declaration")
        code, out, err = self.run_cli("--head", "HEAD")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("byte-identical installation", err)

    def test_first_committed_declaration_accepts_the_reviewed_bytes(self):
        self.artifact.write_bytes(json.dumps(DECLARATION, indent=2).replace("\n", "\r\n").encode("utf-8"))
        self.assess_artifact()
        self.write_plan()
        (self.tmp / ".herdr").mkdir()
        (self.tmp / triggers.DECLARATION_FILE).write_bytes(self.artifact.read_bytes())
        self.git("add", "-A")
        self.git("commit", "-qm", "install reviewed declaration")
        code, out, err = self.run_cli("--head", "HEAD")
        self.assertEqual(code, 0, err)
        authority = json.loads(out)["declaration_authority"]
        self.assertEqual(authority["kind"], "repository")
        self.assertEqual(authority["bootstrap_sha256"], hashlib.sha256(self.artifact.read_bytes()).hexdigest())

    def test_existing_declaration_is_authoritative_without_bootstrap_artifact(self):
        (self.tmp / ".herdr").mkdir()
        (self.tmp / triggers.DECLARATION_FILE).write_text(json.dumps(DECLARATION), encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "existing authority")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.plan.write_text(json.dumps({
            "schema_version": 1, "added": [], "changed": ["README.md"],
            "package_lines": {}, "cli_surface": [],
        }), encoding="utf-8")
        self.artifact.unlink()
        stream, errors = io.StringIO(), io.StringIO()
        code = cli.main(["detect-triggers", "--repo", str(self.tmp), "--base", self.base,
                         "--planned", str(self.plan)], stdout=stream, stderr=errors)
        out, err = stream.getvalue(), errors.getvalue()
        self.assertEqual(code, 0, err)
        authority = json.loads(out)["declaration_authority"]
        self.assertEqual(authority, {"kind": "repository", "revision": "worktree"})

    def test_pushed_head_declaration_is_authoritative_over_the_checkout(self):
        (self.tmp / ".herdr").mkdir()
        branch_declaration = {**DECLARATION, "user_doc_paths": ["guides/*"]}
        (self.tmp / triggers.DECLARATION_FILE).write_text(json.dumps(branch_declaration), encoding="utf-8")
        (self.tmp / "guides").mkdir()
        (self.tmp / "guides" / "install.md").write_text("install\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "install declaration and guide")
        head = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", self.base)
        out_stream, err_stream = io.StringIO(), io.StringIO()
        code = cli.main(["detect-triggers", "--repo", str(self.tmp), "--base", self.base,
                         "--head", head], stdout=out_stream, stderr=err_stream)
        out, _err = out_stream.getvalue(), err_stream.getvalue()
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("requires --bootstrap-declaration", _err)

    def test_digest_alone_without_accepted_consultation_refuses(self):
        self.write_plan()
        state = self.owner.saved()
        state["specialist_assessments"] = []
        save_state(self.owner.state, state)
        code, out, err = self.run_cli()
        self.assertEqual((code, out), (1, ""))
        self.assertIn("no accepted consultation", err)

    def test_changed_assessed_report_refuses(self):
        self.write_plan()
        report = self.owner.tmp / "prior-report.md"
        report.write_text(report.read_text() + "altered after assessment\n")
        code, out, err = self.run_cli()
        self.assertEqual((code, out), (1, ""))
        self.assertIn("no accepted consultation", err)

    def test_first_worktree_declaration_cannot_omit_bootstrap_proof(self):
        self.write_plan()
        (self.tmp / ".herdr").mkdir()
        (self.tmp / triggers.DECLARATION_FILE).write_bytes(self.artifact.read_bytes())
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["detect-triggers", "--repo", str(self.tmp), "--base", self.base,
                         "--planned", str(self.plan)], stdout=out, stderr=err)
        self.assertEqual((code, out.getvalue()), (1, ""))
        self.assertIn("requires --bootstrap-declaration", err.getvalue())

    def test_first_consultation_classifies_without_declaration_or_bootstrap(self):
        self.plan.write_text(json.dumps({"schema_version": 1, "added": [], "changed": [],
            "package_lines": {}, "cli_surface": [], "writes_repository": False}))
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["detect-triggers", "--repo", str(self.tmp), "--base", self.base,
                         "--planned", str(self.plan), "--roles", "advisor"], stdout=out, stderr=err)
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(json.loads(out.getvalue())["fired"], [])

    def test_unmet_consultation_establishes_no_bootstrap_authority(self):
        self.write_plan()
        self.assess_artifact(acceptance="unmet")
        code, out, err = self.run_cli()
        self.assertEqual((code, out), (1, ""))
        self.assertIn("no accepted consultation", err)

    def test_accepted_artifact_for_a_different_repository_or_base_is_refused(self):
        self.write_plan()
        for changed in ({"repo": str(self.owner.tmp)}, {"base_revision": "a" * 40}):
            with self.subTest(changed=changed):
                self.assess_artifact(binding_changes=changed)
                code, out, err = self.run_cli()
                self.assertEqual((code, out), (1, ""))
                self.assertIn("no accepted consultation", err)


class WorktreeBaseCommandTest(TempCase):
    """Normal provision -> registered task -> packaged brief provenance."""

    def setUp(self):
        self.root = self.temp_dir()
        self.skill = Path(__file__).resolve().parents[1]
        self.origin = self.root / "origin.git"
        self.shared = self.root / "shared"
        self.worktree = self.root / "worktrees" / "developer"
        self.state = self.root / "owner-state.json"
        self.environment = {**os.environ, "WORKTREE_ROOT": str(self.root / "worktrees"),
            "GIT_AUTHOR_NAME": "Fixture", "GIT_COMMITTER_NAME": "Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
        self.git("init", "--bare", "-q", "-b", "main", str(self.origin))
        self.git("clone", "-q", str(self.origin), str(self.shared))
        (self.shared / "README.md").write_text("Base\n")
        self.git("-C", str(self.shared), "add", "-A")
        self.git("-C", str(self.shared), "commit", "-qm", "Base")
        self.git("-C", str(self.shared), "push", "-q", "origin", "main")
        self.base = self.git("-C", str(self.shared), "rev-parse", "HEAD").strip()
        self.policy = self.root / "policy.md"
        self.policy.write_text("Fixture policy and team contract\n")

    def git(self, *args):
        return subprocess.run(["git", *args], env=self.environment,
            capture_output=True, text=True, check=True).stdout

    def provision(self, *base, environment=None):
        return subprocess.run(["bash", str(self.skill / "provision-worktree.sh"), str(self.shared),
            "fix/fixture", str(self.worktree), *base], env=environment or self.environment,
            capture_output=True, text=True, check=False)

    def register(self, base=None, task="fixture-task"):
        record = self.root / "task.json"
        record.write_text(json.dumps({"task": task, "base_revision": base or self.base,
            "scope": "Fixture work", "allowed_paths": ["*"],
            "authorization": {"source": "fixture operator request", "quote": "Implement the fixture"}}))
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["task", "--state", str(self.state), "--record", str(record),
                         "--now", "2026-01-08T12:00:00Z"], stdout=out, stderr=err)
        self.assertEqual(code, 0, err.getvalue())

    def compose(self, *, task: str | None = "fixture-task", mutate=None):
        values = {"task": task, "state": str(self.state), "shared": {
            "SHARED_CHECKOUT": str(self.shared), "AUTHORITY_STATEMENT": "Owned fixture",
            "TASK_AUTHORIZATION": "Implement fixture", "AUTHORIZED_ACTIONS": "Implement fixture",
            "EXTERNAL_PERMISSION": "none", "POLICY_INDEX": str(self.policy), "RELEASE_SKILL": str(self.policy),
            "TEAM_OPERATION": str(self.policy), "GATES": "- Fixture gates", "ISSUE": "Fixture", "BRANCH": "fix/fixture"},
            "roles": {"developer": {"WORKTREE": str(self.worktree), "REPORTS_DIR": str(self.root),
                                     "REPORT": str(self.root / "developer-report.md")}}}
        if mutate:
            mutate(values)
        source = self.root / "values.json"
        source.write_text(json.dumps(values))
        return subprocess.run(["bash", str(self.skill / "compose-briefs.sh"), str(self.skill / "templates"),
                               str(source), str(self.root / "briefs")], env=self.environment,
                              capture_output=True, text=True, check=False)

    def test_failed_fetch_creates_no_directory_branch_or_worktree(self):
        shim = self.root / "bin"
        shim.mkdir()
        executable = shim / "git"
        real_git = shutil.which("git")
        assert real_git is not None
        executable.write_text("#!/bin/sh\nfor arg do\n  if [ \"$arg\" = fetch ]; then echo 'fixture fetch failure' >&2; exit 73; fi\ndone\nexec " + shlex.quote(real_git) + " \"$@\"\n")
        executable.chmod(0o755)
        result = self.provision(environment={**self.environment, "PATH": str(shim) + os.pathsep + self.environment["PATH"]})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertFalse(self.worktree.parent.exists())
        branches = self.git("-C", str(self.shared), "branch", "--list", "fix/fixture")
        self.assertEqual(branches, "")

    def test_normal_owner_records_exact_base_and_composes_it_automatically(self):
        result = self.provision()
        self.assertEqual(result.returncode, 0, result.stderr)
        proof = json.loads(result.stdout)
        self.assertEqual((proof["base_revision"], proof["fetched_default_revision"]), (self.base, self.base))
        self.register()
        before = self.state.read_bytes()
        result = self.compose()
        self.assertEqual(result.returncode, 0, result.stderr)
        common = (self.root / "briefs" / "COMMON.md").read_text()
        brief = (self.root / "briefs" / "brief-developer.md").read_text()
        self.assertIn(self.base, common)
        self.assertIn(self.base, brief)
        self.assertNotIn("{{", common + brief)
        self.assertEqual(self.state.read_bytes(), before)

    def test_pinned_task_base_survives_new_default_and_idempotent_provision(self):
        self.git("-C", str(self.shared), "commit", "--allow-empty", "-qm", "New default")
        new_default = self.git("-C", str(self.shared), "rev-parse", "HEAD").strip()
        self.git("-C", str(self.shared), "push", "-q", "origin", "main")
        result = self.provision(self.base)
        self.assertEqual(result.returncode, 0, result.stderr)
        proof = json.loads(result.stdout)
        self.assertEqual(proof["base_revision"], self.base)
        self.assertEqual(proof["fetched_default_revision"], new_default)
        again = self.provision()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(json.loads(again.stdout)["base_revision"], self.base)
        self.register()
        result = self.compose()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(new_default, (self.root / "briefs" / "brief-developer.md").read_text())
        self.assertEqual(json.loads(self.state.read_text())["recovery"]["tasks"]["fixture-task"]["base_revision"], self.base)
        self.assertEqual(self.git("-C", str(self.worktree), "rev-parse", "HEAD").strip(), self.base)

    def test_composition_refuses_missing_or_mismatched_task_without_outputs(self):
        self.assertEqual(self.provision().returncode, 0)
        self.register()
        for task in ("missing-task", None):
            with self.subTest(task=task):
                result = self.compose(task=task)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertFalse((self.root / "briefs").exists())
        self.git("-C", str(self.shared), "commit", "--allow-empty", "-qm", "Other base")
        other_base = self.git("-C", str(self.shared), "rev-parse", "HEAD").strip()
        self.register(base=other_base, task="other-task")
        result = self.compose(task="other-task")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "briefs").exists())

    def test_composition_refuses_caller_base_override_and_future_receipt(self):
        self.assertEqual(self.provision().returncode, 0)
        self.register()
        result = self.compose(mutate=lambda values: values["shared"].update(BASE_REVISION=self.base))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "briefs").exists())
        location = Path(self.git("-C", str(self.worktree), "rev-parse", "--path-format=absolute", "--git-path", "foreman-provision.json").strip())
        record = json.loads(location.read_text())
        record["schema_version"] = 2
        location.write_text(json.dumps(record))
        result = self.compose()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "briefs").exists())
        self.assertEqual(json.loads(location.read_text())["schema_version"], 2)


if __name__ == "__main__":
    unittest.main()
