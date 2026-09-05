"""Policy boundaries for tier choice and launch proof."""

import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead.errors import ConfigError, HerdrError, UsageError
from teamlead.tiers import launch_flags, mechanical_allowed, parse_tiers, select_tier as _select_tier, verify_argv


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
    return {"task_kind": "rebase", "spec_complete": True, "no_semantic_decisions": True,
            "whole_result_oracle": True, "exact_plan": True, "risk_flags": [], "files": 2,
            "input_bytes": 64000}


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
    def test_initial_build_and_late_fix_have_different_models(self):
        worker = agent()
        self.assertEqual(select_tier(worker, "developer")["model"], "sonnet-5")
        self.assertEqual(select_tier(worker, "developer", fix_round=4)["model"], "opus-5")
        self.assertEqual(select_tier(worker, "developer", context={"failed_gates": 2})["model"], "opus-5")

    def test_risk_and_hostile_verification_select_top_xhigh(self):
        for context in ({"risk_flags": ["network", "persistence"]}, {"input_bytes": 250001}, {"prior_high_miss": True}):
            with self.subTest(context=context):
                tier = select_tier(agent(), "developer", context=context)
                self.assertEqual((tier["model"], tier["effort"]), ("opus-5", "xhigh"))
        self.assertEqual(select_tier(agent("codex"), "tester")["effort"], "xhigh")
        self.assertEqual(select_tier(agent("grok"), "tester")["effort"], "high")

    def test_mechanical_requires_whole_predicate(self):
        context = mechanical_context()
        self.assertTrue(mechanical_allowed(context))
        self.assertEqual(select_tier(agent(), "developer", "mechanical", context)["model"], "claude-haiku-4-5")
        for key, value in (("spec_complete", False), ("files", 3), ("input_bytes", 64001),
                           ("risk_flags", ["network"]), ("unplanned_file", True),
                           ("tool_retries", 3), ("gate_red_after_repair", True)):
            changed = copy.deepcopy(context)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(UsageError):
                select_tier(agent(), "developer", "mechanical", changed)

    def test_reviewer_cannot_claim_mechanical_role(self):
        with self.assertRaises(UsageError):
            select_tier(agent(), "reviewer", "mechanical", mechanical_context())

    def test_missing_tier_never_falls_back_to_untiered_dispatch(self):
        worker = agent()
        del worker.tiers["review"]
        with self.assertRaises(UsageError):
            select_tier(worker, "reviewer")


class ArgvTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
