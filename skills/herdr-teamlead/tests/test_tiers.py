"""Policy boundaries for tier choice and launch proof."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead.errors import ConfigError, HerdrError, UsageError
from teamlead.tiers import MissingTierError, SEATABLE_ROLES, launch_flags, mechanical_allowed, parse_tiers, require_seatable, select_tier as _select_tier, verify_argv, verify_worker_permissions, worker_launch_args


def select_tier(*args, **kwargs):
    result = _select_tier(*args, **kwargs)
    assert result is not None, "the test configured a tier table"
    return result


def agent(kind="claude"):
    model = {"claude": "opus-5", "codex": "gpt-5.6-sol", "grok": "grok-4.6"}[kind]
    return SimpleNamespace(name=kind, kind=kind, tiers=parse_tiers({
        "review": {"model": model, "effort": "high"},
        "hostile_verify": {"model": model, "effort": "high"},
        "build": {"model": "sonnet-5" if kind == "claude" else model, "effort": "high"},
        "fix": {"model": "sonnet-5" if kind == "claude" else model, "effort": "high"},
        "mechanical": ({"model": "claude-haiku-4-5"} if kind == "claude" else {"model": model, "effort": "low"}),
    }, kind))


def mechanical_context():
    """A round licensed cheap by a whole-result oracle, not by assertions."""
    return {"oracle": {"kind": "digest", "value": "a" * 64}}


#: Synthetic session identifiers for resumed-process proof (#382); no real session.
SESSION = "00000000-0000-0000-0000-000000000000"
OTHER_SESSION = "11111111-1111-4111-8111-111111111111"
YOLO = {"claude": "--dangerously-skip-permissions", "codex": "--dangerously-bypass-approvals-and-sandbox",
        "grok": "--always-approve"}


class TierConfigTest(unittest.TestCase):
    def test_claude_all_five_efforts_and_haiku_omission(self):
        for effort in ("low", "medium", "high", "xhigh", "max"):
            self.assertEqual(parse_tiers({"build": {"model": "opus-5", "effort": effort}}, "claude")["build"]["effort"], effort)
        tier = parse_tiers({"mechanical": {"model": "claude-haiku-4-5"}}, "claude")["mechanical"]
        self.assertIsNone(tier["effort"])

    def test_effort_missing_invalid_or_impossible_is_refused(self):
        for entry in ({"model": "opus-5"}, {"model": "opus-5", "effort": "ultra"},
                      {"model": "claude-haiku-4-5", "effort": "low"}):
            with self.subTest(entry=entry), self.assertRaises(ConfigError):
                parse_tiers({"build": entry}, "claude")

    def test_judgment_cannot_be_lowered_by_config(self):
        for round_type in ("review", "critic", "recheck", "test_plan", "release_adjudication"):
            for entry in ({"model": "sonnet-5", "effort": "high"}, {"model": "opus-5", "effort": "medium"}):
                with self.subTest(round_type=round_type, entry=entry), self.assertRaises(ConfigError):
                    parse_tiers({round_type: entry}, "claude")

    def test_dead_rounds_kinds_and_bad_costs_are_refused(self):
        with self.assertRaises(ConfigError):
            parse_tiers({"review": {"model": "gemini-3.1-pro", "effort": "high"}}, "agy")
        with self.assertRaises(ConfigError):
            parse_tiers({"typo": {"model": "opus-5", "effort": "high"}}, "claude")
        for multiplier in (0, -1, True, "0.5", float("nan"), float("inf")):
            with self.subTest(multiplier=multiplier), self.assertRaises(ConfigError):
                parse_tiers({"build": {"model": "opus-5", "effort": "high", "multiplier": multiplier}}, "claude")


class SelectionTest(unittest.TestCase):
    def test_only_missing_candidate_tiers_have_a_skippable_error(self):
        worker = agent()
        with self.assertRaisesRegex(MissingTierError, "architect"):
            select_tier(worker, "advisor")
        worker.tiers.pop("review")
        with self.assertRaisesRegex(MissingTierError, "review tier"):
            select_tier(worker, "developer", context={"prior_high_miss": True})
        for role, requested, context in (("advisor", "build", {}), ("developer", "build", {"unknown": True})):
            with self.subTest(role=role), self.assertRaises(UsageError) as caught:
                select_tier(worker, role, requested, context)
            self.assertNotIsInstance(caught.exception, MissingTierError)

    def test_consultations_use_fixed_judgment_rounds(self):
        worker = agent()
        worker.tiers.update(parse_tiers({
            "architect": {"model": "opus-5", "effort": "high"},
            "reconciliation": {"model": "opus-5", "effort": "high"},
        }, worker.kind))
        for role, expected in (("advisor", "architect"), ("investigator", "reconciliation")):
            with self.subTest(role=role):
                self.assertEqual(select_tier(worker, role)["round"], expected)
                self.assertEqual(select_tier(worker, role)["model"], "opus-5")
                for forbidden in ("build", "fix", "mechanical", "release_mechanics", "review"):
                    with self.assertRaisesRegex(UsageError, "cannot perform role"):
                        select_tier(worker, role, forbidden, mechanical_context())

    def test_initial_build_and_late_fix_have_different_models(self):
        worker = agent()
        self.assertEqual(select_tier(worker, "developer")["model"], "sonnet-5")
        self.assertEqual(select_tier(worker, "developer", fix_round=4)["model"], "opus-5")
        for number in (6, 7):
            self.assertEqual(select_tier(worker, "developer", fix_round=number)["model"], "opus-5")
        self.assertEqual(select_tier(worker, "developer", context={"failed_gates": 2})["model"], "opus-5")
        for context, fix_round, requested in (({}, None, "build"), ({"failed_gates": 2}, None, "build"),
                                             ({}, 4, "fix"), ({"prior_high_miss": True}, None, "build")):
            tier = select_tier(worker, "developer", context=context, fix_round=fix_round)
            self.assertEqual(tier["round"], requested)
            self.assertEqual(tier["tier_row"], "review" if context or fix_round else "build")

    def test_recorded_risk_evidence_selects_top_xhigh(self):
        for context in ({"risk_flags": ["network", "persistence"]}, {"input_bytes": 250001}, {"prior_high_miss": True}):
            with self.subTest(context=context):
                tier = select_tier(agent(), "developer", context=context)
                self.assertEqual((tier["model"], tier["effort"]), ("opus-5", "xhigh"))

    def test_a_tester_round_escalates_on_evidence_not_on_its_name(self):
        # coding-policy#477: `hostile_verify` is the tester's DEFAULT round, so
        # escalating on the round name pinned every tester round in the fleet to
        # the top model at xhigh whatever the surface. The round's name is not
        # evidence; its floor is the operator's configured row.
        for kind in ("claude", "codex", "grok"):
            with self.subTest(kind=kind):
                tier = select_tier(agent(kind), "tester")
                self.assertEqual(tier["round"], "hostile_verify")
                self.assertEqual(tier["effort"], "high")

    def test_a_tester_round_still_escalates_when_the_evidence_says_so(self):
        for context in ({"risk_flags": ["network", "persistence"]}, {"input_bytes": 250001},
                        {"prior_high_miss": True}):
            with self.subTest(context=context):
                self.assertEqual(select_tier(agent("codex"), "tester", context=context)["effort"], "xhigh")

    def test_a_recorded_whole_result_oracle_licenses_the_cheap_round(self):
        # coding-policy#480: the retired predicate wanted a task named in a
        # closed list of eight, ten hand-typed booleans and two size caps. It
        # fired zero times in 702 assignments and could not fire.
        context = mechanical_context()
        self.assertTrue(mechanical_allowed(context))
        self.assertEqual(select_tier(agent(), "developer", "mechanical", context)["model"], "claude-haiku-4-5")

    def test_a_declared_oracle_is_checked_rather_than_taken(self):
        with tempfile.TemporaryDirectory() as root:
            written = Path(root) / "exact.patch"
            written.write_text("--- a\n+++ b\n", encoding="utf-8")
            for label, oracle in (
                ("a written patch", {"kind": "patch", "path": str(written)}),
                ("a written fixture", {"kind": "fixture", "path": str(written)}),
            ):
                with self.subTest(label=label):
                    self.assertTrue(mechanical_allowed({"oracle": oracle}))
            for label, oracle in (
                ("a patch nobody wrote", {"kind": "patch", "path": str(Path(root) / "absent.patch")}),
                ("a directory", {"kind": "fixture", "path": root}),
                # A plan replays at apply, possibly from another directory.
                ("a relative path", {"kind": "patch", "path": "exact.patch"}),
            ):
                with self.subTest(label=label):
                    self.assertFalse(mechanical_allowed({"oracle": oracle}))

    def test_a_malformed_oracle_licenses_nothing(self):
        for label, context in (
            ("no oracle", {}),
            ("not an object", {"oracle": True}),
            ("unknown kind", {"oracle": {"kind": "vibes", "value": "a" * 64}}),
            ("unhashable kind", {"oracle": {"kind": ["digest"], "value": "a" * 64}}),
            ("short digest", {"oracle": {"kind": "digest", "value": "a" * 63}}),
            ("uppercase digest", {"oracle": {"kind": "digest", "value": "A" * 64}}),
            ("digest carrying a path", {"oracle": {"kind": "digest", "value": "a" * 64, "path": "/x"}}),
            ("patch carrying a digest", {"oracle": {"kind": "patch", "path": "/x", "value": "a" * 64}}),
            ("stray key", {"oracle": {"kind": "digest", "value": "a" * 64, "extra": 1}}),
        ):
            with self.subTest(label=label):
                self.assertFalse(mechanical_allowed(context))
                with self.assertRaises(UsageError):
                    select_tier(agent(), "developer", "mechanical", context)

    def test_a_context_written_for_the_retired_predicate_names_its_replacement(self):
        for field, value in (("task_kind", "rebase"), ("spec_complete", True), ("files", 2),
                             ("whole_result_oracle", True), ("tool_retries", 0),
                             ("gate_red_after_repair", False)):
            with self.subTest(field=field):
                with self.assertRaisesRegex(UsageError, "Declare an `oracle` instead"):
                    select_tier(agent(), "developer", "mechanical", {field: value})

    def test_reviewer_cannot_claim_mechanical_role(self):
        with self.assertRaises(UsageError):
            select_tier(agent(), "reviewer", "mechanical", mechanical_context())

    def test_missing_tier_never_falls_back_to_untiered_dispatch(self):
        worker = agent()
        del worker.tiers["review"]
        with self.assertRaises(UsageError):
            select_tier(worker, "reviewer")


class ArgvTest(unittest.TestCase):
    def test_invalid_live_options_are_distinct_from_restrictive_modes(self):
        for argv in (["grok", "--always-approve", "--always-approve"],
                     ["grok", "--permission-mode", "unknown"]):
            with self.subTest(argv=argv), self.assertRaisesRegex(HerdrError, "invalid permission/UI") as caught:
                verify_worker_permissions("grok", argv)
            self.assertNotIn("restrictive permission", str(caught.exception))
        with self.assertRaisesRegex(HerdrError, "restrictive permission"):
            verify_worker_permissions("grok", ["grok", "--permission-mode", "plan"])

    def test_legacy_live_permission_proof_accepts_canonical_and_equivalent_modes(self):
        for kind, argv in (
            ("claude", ["claude", "--dangerously-skip-permissions", "--model", "opus-5"]),
            ("claude", ["claude", "--permission-mode", "bypassPermissions"]),
            ("codex", ["codex", "--dangerously-bypass-approvals-and-sandbox", "-c", "model_reasoning_effort=high"]),
            ("codex", ["codex", "-a", "never", "-s", "danger-full-access", "--no-alt-screen"]),
            ("grok", ["grok", "--always-approve", "--no-subagents", "--reasoning-effort", "high"]),
        ):
            with self.subTest(kind=kind, argv=argv):
                self.assertIsNone(verify_worker_permissions(kind, argv))

    def test_legacy_live_permission_proof_rejects_missing_overrides_and_lookalikes(self):
        for kind, argv in (
            ("claude", ["claude", "--model", "opus-5"]),
            ("claude", ["claude", "--model", "--dangerously-skip-permissions"]),
            ("claude", ["claude", "--dangerously-skip-permissions", "--permission-mode", "plan"]),
            ("claude", ["claude", "--permission-mode"]),
            ("codex", ["codex", "-a", "never"]),
            ("codex", ["codex", "-s", "danger-full-access"]),
            ("codex", ["codex", "--dangerously-bypass-approvals-and-sandbox", "-c", 'approval_policy="on-request"']),
            ("grok", ["grok", "--always-approve", "--resume", "session"]),
            ("grok", ["grok", "--always-approve", "--permission-mode", "default"]),
            ("grok", ["wrapper", "grok", "--always-approve"]),
            ("grok", "grok --always-approve"), ("grok", ["grok", 1]),
            ("unknown", []),
        ):
            with self.subTest(kind=kind, argv=argv), self.assertRaisesRegex(HerdrError, "before dispatch"):
                verify_worker_permissions(kind, argv)

    def test_resumed_live_permission_proof_accepts_one_explicit_session_with_yolo(self):
        for kind, argv in (
            # The issue's own reproduction: a resumed Codex worker with the explicit YOLO flag.
            ("codex", ["codex", "resume", SESSION, "--dangerously-bypass-approvals-and-sandbox"]),
            ("codex", ["/opt/homebrew/bin/codex", "resume", "--dangerously-bypass-approvals-and-sandbox", SESSION, "--no-alt-screen"]),
            ("codex", ["codex", "resume", SESSION, "-a", "never", "-s", "danger-full-access", "-c", "model_reasoning_effort=high"]),
            ("claude", ["claude", "--resume", SESSION, "--dangerously-skip-permissions"]),
            ("claude", ["claude", "--dangerously-skip-permissions", "-r", SESSION.upper(), "--model", "opus-5", "--effort", "high"]),
            ("claude", ["claude", "--permission-mode", "bypassPermissions", "--resume", SESSION]),
            ("grok", ["grok", "-r", SESSION, "--always-approve", "--no-subagents"]),
            ("grok", ["grok", "--resume", SESSION, "--permission-mode", "bypassPermissions", "--reasoning-effort", "high"]),
        ):
            with self.subTest(kind=kind, argv=argv):
                self.assertIsNone(verify_worker_permissions(kind, argv))

    def test_resumed_proof_still_requires_yolo_and_refuses_restrictive_modes(self):
        for kind, argv, pattern in (
            ("codex", ["codex", "resume", SESSION], "do not prove YOLO"),
            ("codex", ["codex", "resume", SESSION, "-a", "never"], "do not prove YOLO"),
            ("codex", ["codex", "resume", SESSION, "--full-auto"], "restrictive permission"),
            ("codex", ["codex", "resume", SESSION, "--dangerously-bypass-approvals-and-sandbox", "-s", "read-only"], "restrictive permission"),
            ("codex", ["codex", "resume", SESSION, "--dangerously-bypass-approvals-and-sandbox", "-c", 'approval_policy="on-request"'], "config override"),
            ("claude", ["claude", "--resume", SESSION, "--permission-mode", "plan"], "restrictive permission"),
            ("claude", ["claude", "--resume", SESSION, "--dangerously-skip-permissions", "--permission-mode", "acceptEdits"], "restrictive permission"),
            ("claude", ["claude", "--resume", SESSION, "--model", "opus-5"], "do not prove YOLO"),
            ("grok", ["grok", "--resume", SESSION], "do not prove YOLO"),
            ("grok", ["grok", "--resume", SESSION, "--always-approve", "--always-approve"], "invalid permission/UI"),
        ):
            with self.subTest(kind=kind, argv=argv), self.assertRaisesRegex(HerdrError, pattern) as caught:
                verify_worker_permissions(kind, argv)
            # The diagnostic names both recoveries: a fresh worker, or the same-session restoration.
            self.assertIn("dispatch-recovery.md", str(caught.exception))
            self.assertIn("before dispatch", str(caught.exception))

    def test_ambiguous_or_identity_changing_resume_forms_are_refused(self):
        for kind, extra in (
            ("claude", ["--resume"]), ("claude", ["--resume", "--model", "opus-5"]),
            ("claude", ["--continue"]), ("claude", ["-c"]),
            ("claude", ["--resume", SESSION, "--fork-session"]), ("claude", ["--session-id", SESSION]),
            ("claude", ["--resume", "search term"]), ("claude", ["--resume", SESSION.replace("-", "")]),
            ("claude", ["--resume", SESSION, "--resume", SESSION]), ("claude", ["-r", SESSION, "--resume", OTHER_SESSION]),
            ("claude", ["--resume=" + SESSION]), ("claude", ["--from-pr", "12"]), ("claude", ["--teleport", SESSION]),
            ("codex", ["resume"]), ("codex", ["resume", "--last"]), ("codex", ["resume", "--all", SESSION]),
            ("codex", ["resume", "--include-non-interactive", SESSION]), ("codex", ["resume", "my-session-name"]),
            ("codex", ["resume", SESSION, "fix the bug"]), ("codex", ["resume", SESSION, OTHER_SESSION]),
            ("codex", [SESSION]),
            ("grok", ["--resume"]), ("grok", ["--continue"]), ("grok", ["-c"]),
            ("grok", ["--resume", "Session Title"]), ("grok", ["--resume", SESSION, "--fork-session"]),
            ("grok", ["-s", SESSION]), ("grok", ["--session-id", SESSION]),
            ("grok", ["--resume", SESSION, "--restore-code"]), ("grok", ["--resume", SESSION, "--worktree", "feat"]),
        ):
            argv = [kind] + extra + [YOLO[kind]]
            with self.subTest(kind=kind, argv=argv), self.assertRaisesRegex(HerdrError, "before dispatch"):
                verify_worker_permissions(kind, argv)
        for kind, argv in (
            ("codex", ["codex", "--dangerously-bypass-approvals-and-sandbox", "resume", SESSION]),
            ("codex", ["sh", "-c", "codex resume " + SESSION + " --dangerously-bypass-approvals-and-sandbox"]),
            ("codex", ["wrapper", "codex", "resume", SESSION, "--dangerously-bypass-approvals-and-sandbox"]),
        ):
            with self.subTest(kind=kind, argv=argv), self.assertRaisesRegex(HerdrError, "before dispatch"):
                verify_worker_permissions(kind, argv)

    def test_ambiguous_resume_diagnostics_name_the_explicit_session_requirement(self):
        for kind, argv in (
            ("codex", ["codex", "resume", "--last", "--dangerously-bypass-approvals-and-sandbox"]),
            ("codex", ["codex", "resume", "--dangerously-bypass-approvals-and-sandbox"]),
            ("claude", ["claude", "--continue", "--dangerously-skip-permissions"]),
            ("claude", ["claude", "--resume", "--dangerously-skip-permissions"]),
            ("grok", ["grok", "--resume", "Session Title", "--always-approve"]),
        ):
            with self.subTest(kind=kind, argv=argv), self.assertRaisesRegex(HerdrError, "explicit session UUID"):
                verify_worker_permissions(kind, argv)

    def test_launch_args_and_tier_proof_keep_refusing_resume_forms(self):
        for kind, options in (("claude", ["--resume", SESSION]), ("codex", ["resume", SESSION]), ("grok", ["-r", SESSION])):
            with self.subTest(kind=kind), self.assertRaisesRegex(ConfigError, "resume"):
                worker_launch_args(kind, options)
        tier = select_tier(agent(), "reviewer")
        with self.assertRaises(HerdrError):
            verify_argv("claude", tier, ["claude", "--dangerously-skip-permissions", "--resume", SESSION, "--model", "opus-5", "--effort", "high"],
                        ["--dangerously-skip-permissions"])

    def test_worker_defaults_and_ui_options_use_yolo_for_each_cli(self):
        for kind, options, expected in (
            ("claude", [], ["--dangerously-skip-permissions"]),
            ("codex", ["--no-alt-screen"], ["--dangerously-bypass-approvals-and-sandbox", "--no-alt-screen"]),
            ("grok", ["--no-subagents", "--no-alt-screen"], ["--always-approve", "--no-subagents", "--no-alt-screen"]),
        ):
            with self.subTest(kind=kind):
                self.assertEqual(worker_launch_args(kind, options), expected)
                self.assertEqual(worker_launch_args(kind, expected), expected)

    def test_permission_aliases_normalize_without_duplicate_flags(self):
        cases = (("claude", ["--permission-mode", "bypassPermissions"], "--dangerously-skip-permissions"),
                 ("codex", ["-a", "never", "--sandbox", "danger-full-access"], "--dangerously-bypass-approvals-and-sandbox"),
                 ("grok", ["--permission-mode", "bypassPermissions", "--always-approve"], "--always-approve"))
        for kind, options, expected in cases:
            with self.subTest(kind=kind):
                self.assertEqual(worker_launch_args(kind, options), [expected])

    def test_restrictive_options_cannot_override_yolo(self):
        cases = (("claude", ["--permission-mode", "default"]),
                 ("claude", ["--dangerously-skip-permissions", "--permission-mode", "plan"]),
                 ("codex", ["--full-auto"]), ("codex", ["-a", "on-request"]),
                 ("codex", ["--sandbox", "workspace-write"]),
                 ("codex", ["--dangerously-bypass-approvals-and-sandbox", "-s", "read-only"]),
                 ("grok", ["--permission-mode", "acceptEdits"]))
        for kind, options in cases:
            with self.subTest(kind=kind, options=options), self.assertRaisesRegex(ConfigError, "required YOLO mode"):
                worker_launch_args(kind, options)

    def test_unknown_kind_and_unsupported_arguments_fail(self):
        for kind, options in (("unsupported", []), ("codex", ["--model", "other"]),
                              ("claude", ["--permission-mode"]), ("grok", ["--always-approve", "--always-approve"])):
            with self.subTest(kind=kind), self.assertRaises(ConfigError):
                worker_launch_args(kind, options)

    def test_each_installed_cli_has_exact_flags(self):
        for kind in ("claude", "codex", "grok"):
            tier = select_tier(agent(kind), "reviewer")
            argv = ["/usr/local/bin/" + kind] + launch_flags(kind, tier)
            self.assertEqual(verify_argv(kind, tier, argv)["effort"], "high")

    def test_no_effort_flag_for_haiku(self):
        tier = select_tier(agent(), "developer", "mechanical", mechanical_context())
        self.assertEqual(launch_flags("claude", tier), ["--model", "claude-haiku-4-5"])

    def test_wrong_missing_duplicate_or_transcript_values_do_not_verify(self):
        tier = select_tier(agent(), "reviewer")
        wanted = ["claude", "--model", "opus-5", "--effort", "high"]
        for argv in (" ".join(wanted), [], wanted[:-2], wanted[:-1] + ["xhigh"],
                     wanted + ["--effort", "low"], wanted + ["--resume"],
                     ["echo"] + wanted, ["claude", "--model", "opus-5-extra", "--effort", "high"]):
            with self.subTest(argv=argv), self.assertRaises(HerdrError):
                verify_argv("claude", tier, argv)


class SeatableRoleTest(unittest.TestCase):
    """Only a responsibility whose verification a slice terminates is seated (#434)."""

    def test_a_seat_of_a_seatable_role_passes_through(self):
        for name in ("reviewer#api", "tester#core", "reviewer", "developer", "release"):
            with self.subTest(name=name):
                self.assertEqual(require_seatable(name), name)

    def test_a_seat_whose_slice_cannot_address_it_is_refused(self):
        # A seat is a CLI key. `--brief SEAT=PATH` splits at the first `=`, and
        # `compose-briefs.sh` reads a line-oriented `.roles` key, so a slice
        # carrying a separator, whitespace or nothing at all plans a seat the
        # round cannot address (#434).
        # `=`, `,` and control characters are refused as unaddressable before
        # the slice grammar is reached; both refusals name the same defect.
        for name in ("reviewer#", "reviewer#a b", "reviewer#-lead"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(UsageError, "cannot address it"):
                    require_seatable(name)
        for name in ("reviewer#a=b", "reviewer#a,b", "tester#a\nb"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(UsageError, "cannot be addressed"):
                    require_seatable(name)

    def test_a_role_name_the_cli_keys_cannot_carry_is_refused(self):
        # `plan --roles dev/eloper` emitted an assignment `compose-briefs.sh`
        # then refused, so the same set is refused where the name is read.
        for name in ("dev/eloper", "dev=eloper", "dev,eloper", "dev\neloper"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(UsageError, "cannot be addressed"):
                    require_seatable(name)
        for name in ("developer", "role_v2", "reviewer.v2", "foo..bar", "two words"):
            with self.subTest(name=name):
                self.assertEqual(require_seatable(name), name)

    def test_a_seat_of_a_per_task_counter_role_is_refused(self):
        for name in ("developer#api", "release#core", "judge#api", "lead#x"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(UsageError, "names a seat of"):
                    require_seatable(name)

    def test_the_refusal_names_the_seatable_roles(self):
        with self.assertRaises(UsageError) as caught:
            require_seatable("developer#api")
        for role in SEATABLE_ROLES:
            self.assertIn(role, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
