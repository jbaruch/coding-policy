"""The model catalog: store, discovery, refusal, and selection gate (T1-T15)."""

import copy
import io
import json
import os as _os
import sys as _sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from foreman import capabilities, catalog
from foreman.cli import main
from foreman.errors import UsageError
from foreman.tiers import NO_EFFORT_MODELS, TOP_MODELS, parse_tiers, verify_argv
from tests.test_cli import CliCase
from tests.test_capability_routing import CONFIG as _UNUSED_CONFIG  # noqa: F401

REFERENCE = datetime(2026, 1, 5, tzinfo=timezone.utc)
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "catalog"
SEED = Path(_ROOT) / "foreman" / "catalog_sources" / "seed-judgment-family.json"


def instant(days=0, seconds=0):
    return (REFERENCE + timedelta(days=days, seconds=seconds)).isoformat()


AT = instant()
LATER = instant(days=8)

GROK_CACHE = FIXTURES / "grok-models-cache.json"
CODEX_CACHE = FIXTURES / "codex-models-cache.json"
CLAUDE_CACHE = FIXTURES / "claude-model-catalog.json"
REFUSAL_TEXT = (FIXTURES / "opus-5-access-refusal.txt").read_text(encoding="utf-8")


def caches(grok=GROK_CACHE, codex=CODEX_CACHE, claude=CLAUDE_CACHE):
    return {"grok": grok, "codex": codex, "claude": claude}


class CatalogStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state.json"

    def test_an_absent_catalog_reads_empty_and_creates_no_file(self):
        self.assertEqual(catalog.load(self.state), catalog.empty())
        self.assertFalse(catalog.storage_path(self.state).exists())

    def test_a_malformed_catalog_names_its_repair(self):
        catalog.storage_path(self.state).write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(UsageError, "not valid JSON"):
            catalog.load(self.state)

    def test_a_newer_catalog_reads_as_no_prior_state_and_is_never_overwritten(self):
        target = catalog.storage_path(self.state)
        original = json.dumps({
            "schema_version": 99, "refreshed_at": None, "scopes": [], "entries": [],
            "observations": [], "future": 1,
        })
        target.write_text(original, encoding="utf-8")
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(catalog.load(self.state), catalog.empty())
        self.assertIn("newer than this build", err.getvalue())
        with self.assertRaisesRegex(UsageError, "Update the coding-policy plugin before recording"):
            catalog.record(self.state, json.loads(SEED.read_text(encoding="utf-8")), AT)
        self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_a_dangling_catalog_link_is_refused_never_read_as_missing(self):
        target = catalog.storage_path(self.state)
        missing = Path(self.tmp.name) / "moved" / "catalog.json"
        target.symlink_to(missing)
        with self.assertRaisesRegex(UsageError, "is a symlink"):
            catalog.load(self.state)
        with self.assertRaisesRegex(UsageError, "is a symlink"):
            catalog.discover(self.state, AT, caches=caches())
        self.assertTrue(target.is_symlink())
        self.assertEqual(_os.readlink(target), str(missing))
        self.assertFalse(missing.exists())

    def test_a_live_catalog_link_is_refused_and_its_target_left_as_found(self):
        real = Path(self.tmp.name) / "elsewhere.json"
        catalog.discover(real, AT, caches=caches())
        original = catalog.storage_path(real).read_bytes()
        target = catalog.storage_path(self.state)
        target.symlink_to(catalog.storage_path(real))
        with self.assertRaisesRegex(UsageError, "is a symlink"):
            catalog.load(self.state)
        self.assertTrue(target.is_symlink())
        self.assertEqual(catalog.storage_path(real).read_bytes(), original)

    def test_a_link_swapped_in_after_the_probe_is_never_read_through(self):
        real = Path(self.tmp.name) / "elsewhere.json"
        catalog.discover(real, AT, caches=caches())
        target = catalog.storage_path(self.state)
        target.symlink_to(catalog.storage_path(real))
        genuine = _os.lstat

        def probe_misses_the_swap(candidate, *args, **kwargs):
            if str(candidate) == str(target):
                raise FileNotFoundError(candidate)
            return genuine(candidate, *args, **kwargs)

        with mock.patch.object(catalog.os, "lstat", probe_misses_the_swap):
            with self.assertRaisesRegex(UsageError, "is a symlink"):
                catalog.load(self.state)
        self.assertTrue(target.is_symlink())

    def test_an_older_or_malformed_envelope_refuses_rather_than_guessing(self):
        target = catalog.storage_path(self.state)
        for label, document, message in (
            ("older version", {"schema_version": 0, "refreshed_at": None, "scopes": [],
                               "entries": [], "observations": []}, "Unsupported catalog schema"),
            ("no refreshed_at", {"schema_version": 1, "scopes": [], "entries": [],
                                 "observations": []}, "carries exactly"),
            ("stray key", {"schema_version": 1, "refreshed_at": None, "scopes": [],
                           "entries": [], "observations": [], "x": 1}, "carries exactly"),
        ):
            with self.subTest(label=label):
                target.write_text(json.dumps(document), encoding="utf-8")
                with self.assertRaisesRegex(UsageError, message):
                    catalog.load(self.state)

    def test_an_empty_catalog_is_due_immediately(self):
        result = catalog.cadence(catalog.empty(), AT)
        self.assertEqual((result["due"], result["reason"]), (True, "never_refreshed"))

    def test_the_interval_is_a_week(self):
        catalog.discover(self.state, AT, caches=caches())
        document = catalog.load(self.state)
        for when, due in ((instant(days=1), False),
                          (instant(days=7, seconds=-1), False),
                          (instant(days=7), True),
                          (LATER, True)):
            with self.subTest(when=when):
                self.assertEqual(catalog.cadence(document, when)["due"], due)

    def test_t9_the_owner_has_no_config_writer(self):
        config = Path(self.tmp.name) / "config.json"
        config.write_text("{}", encoding="utf-8")
        original = config.read_bytes()
        catalog.discover(self.state, AT, caches=caches())
        catalog.record(self.state, json.loads(SEED.read_text(encoding="utf-8")), AT)
        self.assertEqual(config.read_bytes(), original)
        with self.assertRaisesRegex(UsageError, "does not rewrite"):
            catalog.config_candidate_path(config, AT)
        self.assertEqual(config.read_bytes(), original)


class CatalogDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state.json"

    def test_t3_a_grok_only_discover_leaves_claude_incomplete_and_due(self):
        catalog.discover(self.state, AT, caches={"grok": GROK_CACHE, "codex": None, "claude": None})
        document = catalog.load(self.state)
        grok = next(scope for scope in document["scopes"] if scope["adapter"] == "grok")
        claude = next(scope for scope in document["scopes"] if scope["adapter"] == "claude")
        self.assertTrue(grok["complete"])
        self.assertFalse(claude["complete"])
        result = catalog.cadence(document, instant(days=1))
        self.assertTrue(result["due"])
        self.assertEqual(result["reason"], "incomplete_scope")
        self.assertIn("claude", result["incomplete"])

    def test_t4_a_missing_cache_invents_no_entries_and_creates_no_file_on_read(self):
        missing = Path(self.tmp.name) / "absent.json"
        self.assertEqual(catalog.load(self.state), catalog.empty())
        self.assertFalse(catalog.storage_path(self.state).exists())
        catalog.discover(self.state, AT, caches={"grok": missing, "codex": missing, "claude": missing})
        document = catalog.load(self.state)
        self.assertEqual(document["entries"], [])
        self.assertTrue(all(not scope["complete"] for scope in document["scopes"]))

    def test_t1_fixture_lists_grok_49_without_a_python_allowlist_edit(self):
        catalog.discover(self.state, AT, caches=caches())
        document = catalog.load(self.state)
        fingerprint = catalog.pair_fingerprint(document, "grok")
        row = catalog.lookup_model(document, "grok", "grok-4.9", fingerprint)
        assert row is not None
        self.assertEqual(row["catalog_presence"], "listed")
        self.assertEqual(row["availability"], "unknown")
        self.assertFalse(row["judgment_family"])
        self.assertNotIn("grok-4.9", TOP_MODELS["grok"])
        parsed = parse_tiers({"build": {"model": "grok-4.9", "effort": "high"}}, "grok")
        self.assertEqual(parsed["build"]["model"], "grok-4.9")

    def test_t2_listed_judgment_needs_a_supporting_family_record(self):
        catalog.discover(self.state, AT, caches=caches())
        document = catalog.load(self.state)
        fingerprint = catalog.pair_fingerprint(document, "grok")
        parse_tiers({"review": {"model": "grok-4.9", "effort": "high"}}, "grok")
        with self.assertRaises(catalog.CatalogRefusal) as caught:
            catalog.assess_pair(document, "grok", "grok-4.9", "high", fingerprint, judgment=True)
        self.assertEqual(caught.exception.details["reason"], "not_judgment_family")
        vendor = {"entries": [{"adapter": "grok", "model": "grok-4.9", "judgment_family": True,
                               "source": {"kind": "vendor", "ref": "https://docs.x.ai/developers/models",
                                          "dated": "2026-10-03"}}]}
        with self.assertRaisesRegex(UsageError, "Vendor copy"):
            catalog.record(self.state, vendor, AT)
        catalog.record(self.state, {"entries": [{"adapter": "grok", "model": "grok-4.9",
                                                 "judgment_family": True,
                                                 "source": {"kind": "project",
                                                            "ref": "evaluation fixture",
                                                            "dated": "2026-10-03"}}]}, AT)
        document = catalog.load(self.state)
        self.assertEqual(catalog.assess_pair(document, "grok", "grok-4.9", "high", fingerprint,
                                            judgment=True), "ok")

    def test_t13_grok_xhigh_from_the_fixture_parses(self):
        catalog.discover(self.state, AT, caches=caches())
        document = catalog.load(self.state)
        fingerprint = catalog.pair_fingerprint(document, "grok")
        row = catalog.lookup_model(document, "grok", "grok-4.6", fingerprint)
        assert row is not None
        self.assertIn("xhigh", row["efforts"])
        self.assertEqual(parse_tiers({"build": {"model": "grok-4.6", "effort": "xhigh"}}, "grok",
                                     catalog=document)["build"]["effort"], "xhigh")
        self.assertEqual(parse_tiers({"build": {"model": "grok-4.6", "effort": "high"}}, "grok")["build"]["effort"],
                         "high")

    def test_t12_a_new_no_effort_id_parses_from_a_complete_claude_catalog(self):
        catalog.discover(self.state, AT, caches=caches())
        document = catalog.load(self.state)
        self.assertNotIn("claude-haiku-4-9", NO_EFFORT_MODELS)
        parsed = parse_tiers({"mechanical": {"model": "claude-haiku-4-9"}}, "claude", catalog=document)
        self.assertIsNone(parsed["mechanical"]["effort"])
        self.assertIsNone(parse_tiers({"mechanical": {"model": "claude-haiku-4-5"}}, "claude")["mechanical"]["effort"])

    def test_t14_spark_is_deprecated_from_docs_with_no_replacement_slug(self):
        catalog.discover(self.state, AT, caches=caches())
        document = catalog.load(self.state)
        fingerprint = catalog.pair_fingerprint(document, "codex")
        self.assertIsNone(catalog.lookup_model(document, "codex", "gpt-5.3-codex-spark", fingerprint))
        retired = catalog.lookup_deprecated(document, "codex", "gpt-5.3-codex-spark")
        assert retired is not None
        self.assertEqual(retired["catalog_presence"], "deprecated")
        with self.assertRaises(catalog.CatalogRefusal) as caught:
            catalog.assess_pair(document, "codex", "gpt-5.3-codex-spark", "high", fingerprint)
        self.assertEqual(caught.exception.details["reason"], "deprecated")
        self.assertNotIn("gpt-5.6-sol", caught.exception.message)
        models = {row["model"] for row in document["entries"] if row["adapter"] == "codex"}
        self.assertNotIn("gpt-5.3-codex-spark-replacement", models)

    def test_discover_does_not_follow_a_claude_catalog_symlink(self):
        folder = Path(self.tmp.name) / "model-catalog"
        folder.mkdir()
        real = folder / "real.json"
        real.write_text(CLAUDE_CACHE.read_text(encoding="utf-8"), encoding="utf-8")
        link = folder / "link.json"
        link.symlink_to(real)
        catalog.discover(self.state, AT, caches={"grok": None, "codex": None, "claude": folder})
        document = catalog.load(self.state)
        claude = next(scope for scope in document["scopes"] if scope["adapter"] == "claude")
        self.assertTrue(claude["complete"])
        self.assertIn("real.json", claude["source"]["ref"])


class CatalogAccessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state.json"

    def test_t5_an_observation_under_fingerprint_a_is_invisible_under_b(self):
        catalog.discover(self.state, AT, caches=caches())
        catalog.record_access(self.state, {
            "adapter": "claude", "model": "opus-5", "effort": "high",
            "fingerprint": "aaaa", "class": "request_refused", "text": REFUSAL_TEXT,
        }, AT)
        document = catalog.load(self.state)
        with self.assertRaises(catalog.CatalogRefusal):
            catalog.assess_pair(document, "claude", "opus-5", "high", "aaaa")
        self.assertEqual(catalog.assess_pair(document, "claude", "opus-5", "high", "bbbb"), "ok")

    def test_t7_quota_and_transport_append_history_and_leave_availability_unknown(self):
        catalog.discover(self.state, AT, caches=caches())
        fingerprint = catalog.pair_fingerprint(catalog.load(self.state), "grok")
        catalog.record_access(self.state, {
            "adapter": "grok", "model": "grok-4.6", "effort": "high",
            "fingerprint": fingerprint, "class": "quota", "text": "quota exceeded",
        }, AT)
        catalog.record_access(self.state, {
            "adapter": "grok", "model": "grok-4.6", "effort": "high",
            "fingerprint": fingerprint, "class": "transport", "text": "connection refused",
        }, AT)
        document = catalog.load(self.state)
        classes = [row["availability_class"] for row in document["observations"]]
        self.assertEqual(classes, ["quota", "transport"])
        self.assertEqual(catalog.assess_pair(document, "grok", "grok-4.6", "high", fingerprint), "ok")
        pair = catalog.lookup(document, "grok", "grok-4.6", "high", fingerprint)
        self.assertTrue(pair is None or pair["availability"] == "unknown")

    def test_match_output_uses_the_placement_tester_signature(self):
        matched = catalog.match_output(REFUSAL_TEXT, adapter="claude")
        assert matched is not None
        self.assertEqual(matched["class"], "request_refused")
        self.assertEqual(matched["model"], "opus-5")

    def test_t11_empty_catalog_leaves_opus5_seed_keeps_it_refusal_bars_it(self):
        document = catalog.empty()
        self.assertEqual(catalog.assess_pair(document, "claude", "opus-5", "high", "unknown",
                                            judgment=True), "ok")
        catalog.record(self.state, json.loads(SEED.read_text(encoding="utf-8")), AT)
        document = catalog.load(self.state)
        fingerprint = catalog.pair_fingerprint(document, "claude")
        self.assertEqual(catalog.assess_pair(document, "claude", "opus-5", "high", fingerprint,
                                            judgment=True), "ok")
        catalog.record_access(self.state, {
            "adapter": "claude", "model": "opus-5", "effort": "high",
            "fingerprint": fingerprint, "class": "request_refused", "text": REFUSAL_TEXT,
        }, AT)
        document = catalog.load(self.state)
        with self.assertRaises(catalog.CatalogRefusal) as caught:
            catalog.assess_pair(document, "claude", "opus-5", "high", fingerprint, judgment=True)
        self.assertEqual(caught.exception.details["reason"], "unavailable")
        self.assertNotIn("claude-opus-5-5", caught.exception.message)
        self.assertNotIn("opusplan", caught.exception.message)

    def test_t10_a_pinned_judge_skips_family_and_has_no_fallback(self):
        catalog.discover(self.state, AT, caches=caches())
        document = catalog.load(self.state)
        fingerprint = catalog.pair_fingerprint(document, "codex")
        self.assertEqual(catalog.assess_pair(
            document, "codex", "gpt-6-astra", "high", fingerprint, judgment=True, judge=True), "ok")
        catalog.record_access(self.state, {
            "adapter": "codex", "model": "gpt-6-astra", "effort": "high",
            "fingerprint": fingerprint, "class": "request_refused",
            "text": "There's an issue with the selected model (gpt-6-astra). It may not exist or you may not have access",
        }, AT)
        document = catalog.load(self.state)
        with self.assertRaises(catalog.CatalogRefusal) as caught:
            catalog.assess_pair(document, "codex", "gpt-6-astra", "high", fingerprint,
                                judgment=True, judge=True)
        self.assertEqual(caught.exception.details["reason"], "unavailable")
        self.assertNotIn("gpt-5.6-sol", caught.exception.message)
        self.assertNotIn("medium", caught.exception.message)


class CatalogSelectionTest(CliCase):
    def setUp(self):
        super().setUp()
        self.settings = copy.deepcopy(json.loads(self.config.read_text(encoding="utf-8")))
        self.settings["schema_version"] = 2
        self.settings["agents"] = [self.settings["agents"][0]]
        self.settings["agents"][0]["tiers"] = {
            "build": {"model": "opus-5", "effort": "high", "multiplier": 3.0},
            "review": {"model": "opus-5", "effort": "high", "multiplier": 3.0},
        }
        self.config.write_text(json.dumps(self.settings), encoding="utf-8")

    def plan(self, role="developer"):
        rc, output, error = self.run_cli(
            ["plan", *self.base(), "--roles", role, "--snapshot", str(self.snapshot), "--now", AT])
        document = json.loads(output) if output.strip() else None
        return rc, document, error

    def test_t6_an_access_refusal_makes_plan_and_apply_refuse_opus5(self):
        catalog.discover(self.state, AT, caches=caches())
        fingerprint = catalog.pair_fingerprint(catalog.load(self.state), "claude")
        catalog.record_access(self.state, {
            "adapter": "claude", "model": "opus-5", "effort": "high",
            "fingerprint": fingerprint, "class": "request_refused", "text": REFUSAL_TEXT,
        }, AT)
        rc, document, error = self.plan()
        self.assertEqual(rc, 1, error)
        self.assertIsNone(document)
        payload = json.loads(error)
        self.assertEqual(payload["error"], "catalog_refused")
        self.assertIn("opus-5", payload["message"])
        self.assertNotIn("claude-opus-5-5", payload["message"])
        self.assertNotIn('"opus"', payload["message"])

    def test_t1_a_configured_grok_49_build_row_plans_without_an_allowlist_edit(self):
        self.settings["agents"][0]["kind"] = "grok"
        self.settings["agents"][0]["name"] = "grok"
        self.settings["agents"][0]["tiers"] = {
            "build": {"model": "grok-4.9", "effort": "high", "multiplier": 1.0},
        }
        self.config.write_text(json.dumps(self.settings), encoding="utf-8")
        catalog.discover(self.state, AT, caches=caches())
        rc, document, error = self.plan("developer")
        self.assertEqual(rc, 0, error)
        assert document is not None
        self.assertEqual(document["tiers"]["developer"]["model"], "grok-4.9")

    def test_t8_verify_argv_still_requires_the_requested_pair(self):
        tier = {"model": "opus-5", "effort": "high"}
        argv = ["claude", "--dangerously-skip-permissions", "--model", "opus-5", "--effort", "high"]
        proof = verify_argv("claude", tier, argv, launch_args=["--dangerously-skip-permissions"])
        self.assertEqual(proof["model"], "opus-5")
        drifted = ["claude", "--dangerously-skip-permissions", "--model", "claude-opus-5-5", "--effort", "high"]
        with self.assertRaisesRegex(Exception, "did not match the requested model"):
            verify_argv("claude", tier, drifted, launch_args=["--dangerously-skip-permissions"])

    def test_t15_capability_table_does_not_stamp_catalog_freshness_or_route_on_judgment_tier(self):
        catalog.discover(self.state, AT, caches=caches())
        before = catalog.load(self.state)["refreshed_at"]
        capabilities.record(self.state, {"entries": [{
            "model": "opus-5", "effort": "high", "capability": "rotating-worker-judgment-tier",
            "verdict": "adequate",
            "source": {"kind": "project", "ref": "TOP_MODELS membership", "dated": "2026-01-01"},
        }]}, AT)
        self.assertEqual(catalog.load(self.state)["refreshed_at"], before)
        self.assertNotIn("rotating-worker-judgment-tier",
                         capabilities.required("reviewer", "review", {"review"}))
        self.assertEqual(capabilities.required("lead", "lead", {"lead"}), ())
        self.assertIn("rotating-worker-judgment-tier", capabilities.VOCABULARY)
        self.assertIn("rotating-worker-judgment-tier", capabilities.RECORDED_ONLY)


class CatalogCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state.json"

    def run_cmd(self, argv):
        out, err = io.StringIO(), io.StringIO()
        code = main(argv, stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def test_check_show_discover_and_record_round_trip(self):
        code, output, error = self.run_cmd(
            ["catalog-check", "--state", str(self.state), "--now", AT])
        self.assertEqual(code, 0, error)
        payload = json.loads(output)
        self.assertTrue(payload["due"])
        self.assertEqual(payload["reason"], "never_refreshed")
        code, output, error = self.run_cmd([
            "catalog-discover", "--state", str(self.state), "--now", AT,
            "--cache", "grok=" + str(GROK_CACHE),
            "--cache", "codex=" + str(CODEX_CACHE),
            "--cache", "claude=" + str(CLAUDE_CACHE),
        ])
        self.assertEqual(code, 0, error)
        code, output, error = self.run_cmd(["catalog-show", "--state", str(self.state)])
        self.assertEqual(code, 0, error)
        document = json.loads(output)
        self.assertGreater(len(document["entries"]), 0)
        seed = Path(self.tmp.name) / "seed.json"
        seed.write_text(SEED.read_text(encoding="utf-8"), encoding="utf-8")
        code, output, error = self.run_cmd(
            ["catalog-record", "--state", str(self.state), "--record", str(seed), "--now", AT])
        self.assertEqual(code, 0, error)
        refusal = Path(self.tmp.name) / "access.json"
        fingerprint = catalog.pair_fingerprint(catalog.load(self.state), "claude")
        refusal.write_text(json.dumps({
            "adapter": "claude", "model": "opus-5", "effort": "high",
            "fingerprint": fingerprint, "class": "request_refused", "text": REFUSAL_TEXT,
        }), encoding="utf-8")
        code, output, error = self.run_cmd(
            ["catalog-record-access", "--state", str(self.state), "--record", str(refusal), "--now", AT])
        self.assertEqual(code, 0, error)
        document = catalog.load(self.state)
        with self.assertRaises(catalog.CatalogRefusal):
            catalog.assess_pair(document, "claude", "opus-5", "high", fingerprint)


if __name__ == "__main__":
    unittest.main()
