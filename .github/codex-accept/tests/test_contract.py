#!/usr/bin/env python3
"""Deterministic central parser/scanner controls, not ACR runtime acceptance."""
from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock
import zipfile

HELPER = Path(__file__).resolve().parents[1] / "contract.py"
SPEC = importlib.util.spec_from_file_location("accept_contract", HELPER)
assert SPEC and SPEC.loader
c = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(c)
SEED = "synthetic-seed-" + "not-a-credential" * 2
SUITE = "synthetic-suite-" + "not-a-credential" * 2
CONTEXT = {"acr_sha": "a" * 40, "central_sha": "c" * 40, "run_id": "123", "run_attempt": "2", "host": "linux-amd64"}
ENV = {"ACR_ACCEPT_" + key.upper(): value for key, value in CONTEXT.items()}


def proof(context=None):
    return {"schema_version": 1, "contract": c.CONTRACT, "result": "passed", **(context or CONTEXT),
            "command": c.PROOF_COMMAND, "exit_code": 0, "events_sha256": "e" * 64,
            "tests": c.leaves(), "coverage": c.COVERAGE}


def events():
    rows = []
    for package, names in c.proof_packages().items():
        rows.append({"Action": "start", "Package": package})
        nodes = set(names)
        for name in names:
            parts = name.split("/")
            nodes.update("/".join(parts[:i]) for i in range(1, len(parts)))
        for name in sorted(nodes):
            rows.append({"Action": "run", "Package": package, "Test": name})
        for name in sorted(nodes, key=lambda n: (-n.count("/"), n)):
            rows.append({"Action": "pass", "Package": package, "Test": name})
        rows.append({"Action": "pass", "Package": package})
    return rows


def event_bytes(rows):
    return b"".join(json.dumps(row).encode() + b"\n" for row in rows)


def live_boundary():
    return {"contract": c.CONTRACT, "authInspected": True, "proposalChecked": True,
            "reportSanitized": True, "isolatedHomeRemoved": True, "refreshObserved": False}


def cli_report(phase):
    return {"ok": True, "result": {"wrote": phase == "apply",
            "credentialBoundary": {"contract": c.CONTRACT, "planChecked": True,
                                   "applicationChecked": phase == "apply", "reportSanitized": True},
            "agentRuns": [{"provider": "codex", "runtimeVersion": "codex-cli 0.154.0",
                           "isolation": "synthetic-native-boundary", "arguments": ["synthetic-codex", "exec"],
                           "requestDigest": "sha256:" + "b" * 64, "stdout": '{"type":"turn.started"}\n{"type":"turn.completed"}\n',
                           "credentialBoundary": live_boundary()}]}}


def git(repo, *args):
    return c.git(repo, *args).decode().strip()


class ProofTests(unittest.TestCase):
    def test_full_exact_inventory_passes(self):
        c.parse_events(event_bytes(events()), c.proof_packages(), 0)
        c.validate_proof(proof(), CONTEXT)

    def test_mutated_event_inventory_refuses(self):
        original = events()
        leaf = next(i for i, row in enumerate(original) if row.get("Test", "").endswith("/clean_rotation") and row["Action"] == "pass")
        mutations = []
        for action in ("skip", "fail", "output"):
            rows = copy.deepcopy(original); rows[leaf]["Action"] = action; mutations.append(rows)
        mutations += [original[:leaf] + original[leaf + 1:], original[:leaf] + [original[leaf]] + original[leaf:], original[:-1], []]
        rows = copy.deepcopy(original); rows[leaf]["Test"] += "/unexpected"; mutations.append(rows)
        rows = copy.deepcopy(original); rows[leaf]["Package"] = "foreign"; mutations.append(rows)
        for rows in mutations:
            with self.subTest(rows=len(rows)), self.assertRaises(c.Refusal):
                c.parse_events(event_bytes(rows), c.proof_packages(), 0)
        with self.assertRaises(c.Refusal):
            c.parse_events(event_bytes(original), c.proof_packages(), 1)
        for data in (b"not-json\n", b'{"Action":"start","Action":"pass"}\n', b"{}\n", b"\n"):
            with self.assertRaises(c.Refusal):
                c.parse_events(data, c.proof_packages(), 0)

    def test_empty_top_level_success_is_not_proof(self):
        rows = [row for row in events() if "/" not in row.get("Test", "")]
        with self.assertRaises(c.Refusal):
            c.parse_events(event_bytes(rows), c.proof_packages(), 0)

    def test_proof_requires_exact_current_bindings_and_types(self):
        for field in ("acr_sha", "central_sha", "run_id", "run_attempt", "host", "contract", "events_sha256"):
            changed = proof(); changed[field] = "wrong"
            with self.subTest(field=field), self.assertRaises(c.Refusal):
                c.validate_proof(changed, CONTEXT)
        for field in ("exit_code", "schema_version"):
            changed = proof(); changed[field] = True
            with self.assertRaises(c.Refusal):
                c.validate_proof(changed, CONTEXT)
        changed = proof(); changed["tests"] = changed["tests"][:-1] + [changed["tests"][0]]
        with self.assertRaises(c.Refusal):
            c.validate_proof(changed, CONTEXT)
        changed = proof(); changed["coverage"] = {**c.COVERAGE, "central_scanner": ["observed_refreshed"]}
        with self.assertRaises(c.Refusal):
            c.validate_proof(changed, CONTEXT)

    def test_cli_exact_clean_checkout_and_private_projection(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); repo = root / "candidate"; repo.mkdir()
            git(repo, "init", "-q")
            (repo / "source").write_text("candidate\n")
            commit(repo, "candidate")
            context = {**CONTEXT, "acr_sha": git(repo, "rev-parse", "HEAD")}
            rows = events()
            rows.insert(1, {"Action": "output", "Package": c.RUNTIME_PACKAGE, "Output": "arbitrary private diagnostics"})
            transcript = root / "events.jsonl"; transcript.write_bytes(event_bytes(rows))
            output = root / "proof.json"
            env = {**os.environ, **{"ACR_ACCEPT_" + k.upper(): v for k, v in context.items()}}
            argv = [sys.executable, str(HELPER), "prove-runtime", "--acr-root", str(repo), "--events", str(transcript), "--exit-code", "0", "--output", str(output)]
            result = subprocess.run(argv, env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(b"arbitrary private diagnostics", output.read_bytes())
            self.assertEqual(json.loads(output.read_bytes())["events_sha256"], c.sha(transcript.read_bytes()))
            self.assertNotEqual(subprocess.run(argv, env=env, capture_output=True).returncode, 0)
            output.unlink(); (repo / "source").write_text("dirty\n")
            self.assertNotEqual(subprocess.run(argv, env=env, capture_output=True).returncode, 0)
            self.assertFalse(output.exists())

    def test_failed_proof_prevents_seed_even_with_present_secret(self):
        with tempfile.TemporaryDirectory() as name, mock.patch.dict(os.environ, {**ENV, "CODEX_AUTH_JSON": json.dumps({"tokens": {"access_token": SEED}})}):
            root = Path(name)
            c.write_new(root / "evidence/credential-boundary.json", c.encoded({**proof(), "run_attempt": "1"}))
            with self.assertRaises(c.Refusal):
                c.seed(root)
            self.assertFalse((root / "seed/auth.json").exists())


class InputTests(unittest.TestCase):
    def values(self, phase="convert"):
        values = {"INPUT_PHASE": phase, "INPUT_ACR_SHA": CONTEXT["acr_sha"], "INPUT_CODEX_VERSION": "0.154.0",
                  "INPUT_PRODUCER_RUN_ID": "", "INPUT_PRODUCER_RUN_ATTEMPT": "", "INPUT_GOC_SOURCE": "", "INPUT_FFA_SOURCE": ""}
        if phase == "consume":
            values.update(INPUT_PRODUCER_RUN_ID="123", INPUT_PRODUCER_RUN_ATTEMPT="2")
            for key, spec in c.FIXTURES.items():
                values["INPUT_" + key.upper() + "_SOURCE"] = f"github:{spec['repository']}@v{spec['version']}"
        return values

    def test_positive_dispatches(self):
        for phase in ("convert", "consume"):
            with mock.patch.dict(os.environ, self.values(phase)):
                self.assertEqual(c.inputs()["phase"], phase)

    def test_candidate_installer_table_is_required(self):
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            git(repo, "init", "-q")
            installer = repo / ".github/scripts/install-codex.sh"
            installer.parent.mkdir(parents=True)
            installer.write_text("  0.154.0/codex-x86_64-unknown-linux-musl) sha256=" + "e" * 64 + " ;;\n")
            commit(repo, "pinned installer")
            env = {**self.values(), "INPUT_ACR_SHA": git(repo, "rev-parse", "HEAD")}
            with mock.patch.dict(os.environ, env):
                self.assertEqual(c.inputs(repo)["archive_sha256"], "e" * 64)
            with mock.patch.dict(os.environ, {**env, "INPUT_CODEX_VERSION": "0.999.0"}), self.assertRaises(c.Refusal):
                c.inputs(repo)

    def test_malformed_dispatch_cli_refuses_without_echo_or_execution(self):
        cases = [("INPUT_PHASE", ""), ("INPUT_PHASE", "conversion"), ("INPUT_ACR_SHA", "A" * 40),
                 ("INPUT_ACR_SHA", "../candidate"), ("INPUT_CODEX_VERSION", "0.154.0; echo injected"),
                 ("INPUT_PRODUCER_RUN_ID", "123")]
        for field, value in cases:
            result = subprocess.run([sys.executable, str(HELPER), "inputs"], capture_output=True,
                                    env={**os.environ, **self.values(), field: value})
            self.assertEqual(result.returncode, 1)
            self.assertNotIn(b"injected", result.stdout + result.stderr)
        for field, value in (("INPUT_PRODUCER_RUN_ATTEMPT", "0"), ("INPUT_PRODUCER_RUN_ID", "01"),
                             ("INPUT_GOC_SOURCE", "github:foreign/repo@latest"), ("INPUT_FFA_SOURCE", "/tmp/local")):
            with mock.patch.dict(os.environ, {**self.values("consume"), field: value}), self.assertRaises(c.Refusal):
                c.inputs()


def commit(repo, message):
    git(repo, "add", "-A")
    with mock.patch.dict(os.environ, {"GIT_AUTHOR_DATE": "2001-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2001-01-01T00:00:00Z",
                                     "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                                     "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}):
        git(repo, "commit", "-qm", message)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "acr-accept"; self.root.mkdir()
        self.evidence = self.root / "evidence"; self.evidence.mkdir()
        self.output = self.base / "export"
        self.specs = copy.deepcopy(c.FIXTURES)
        self.patch = mock.patch.dict(c.FIXTURES, self.specs)
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.env = mock.patch.dict(os.environ, {**ENV, "GH_TOKEN": SUITE})
        self.env.start(); self.addCleanup(self.env.stop)
        c.write_new(self.root / "seed/auth.json", c.encoded({"tokens": {"access_token": SEED}}))
        c.write_new(self.root / "codex.json", c.encoded({"version": "0.154.0", "archive_sha256": "d" * 64, "binary_sha256": "e" * 64}))
        c.write_new(self.evidence / "credential-boundary.json", c.encoded(proof()))
        for key, spec in self.specs.items():
            repo = self.root / "fixtures" / key; repo.mkdir(parents=True)
            git(repo, "init", "-q")
            for argv in spec["commands"]:
                path = repo / argv[1]; path.parent.mkdir(parents=True, exist_ok=True); path.write_text("fixed original test\n")
            if key == "ffa":
                (repo / "pyrightconfig.json").write_bytes(c.encoded({
                    "include": [".github/scripts", "skills/frequent-flyer-advocate/scripts", "skills/frequent-flyer-advocate/tests"],
                    "pythonVersion": "3.12", "typeCheckingMode": "standard", "reportMissingImports": "error"}))
            spec["upstream_sha"] = ""  # Replaced by the real synthetic ancestor, never a production bypass.
            commit(repo, "untouched upstream")
            spec["upstream_sha"] = git(repo, "rev-parse", "HEAD")
            baseline = c.inventory(repo, "HEAD")
            (repo / "agent-plugin.yaml").write_text("synthetic generated plugin\n")
            commit(repo, "generated producer")
            receipt = {"schema_version": 1, "key": spec["key"], "result": "passed", "acr_sha": CONTEXT["acr_sha"],
                       "upstream_sha": spec["upstream_sha"], "producer_sha": git(repo, "rev-parse", "HEAD"),
                       "tree_sha": git(repo, "rev-parse", "HEAD^{tree}"), "repository": spec["repository"], "version": spec["version"],
                       "source_root": str(repo), "operations": [], "commands": [], "inventories": {}, "checks": {k: True for k in ("deterministic_refusal", "source_preserved", "delta_validated", "validate", "rerun", "clean")},
                       "credential_boundary": {"contract": c.CONTRACT, "plan_checked": True, "runs": []}}
            for phase in ("dry-run", "apply"):
                self.put(key + "/" + phase + ".json", cli_report(phase))
                receipt["credential_boundary"]["runs"].append({"phase": phase, "index": 0, "boundary": live_boundary()})
            for phase, inv in (("baseline", baseline), ("converted", c.inventory(repo, "HEAD"))):
                name = key + "/" + phase + "-inventory.json"
                self.put(name, inv)
                receipt["inventories"][phase] = {"path": "evidence/" + name, "sha256": c.sha((self.evidence / name).read_bytes())}
                for index, argv in enumerate(spec["commands"]):
                    if key == "goc":
                        logs = [b"PASS all 15 classification cases + CLI/input/error checks\n",
                                b"All 16 installer-script tests passed\n",
                                b"All 23 commands + 1 negative path emitted valid envelopes against tesslio/good-oss-citizen\n"]
                        log = logs[index]
                    else:
                        log = b"pyright 1.1.411\n0 errors, 0 warnings, 0 informations\n19/19 passed\n114/114 passed\n51/51 passed\nAll gates passed.\n"
                    name = f"{key}/{phase}-test-{index + 1}.log"
                    (self.evidence / name).write_bytes(log)
                    receipt["commands"].append({"phase": phase, "argv": argv, "exit_code": 0, "output": "evidence/" + name,
                                                "sha256": c.sha(log), "counts": [(15, 16, 23)[index]] if key == "goc" else [19, 114, 51]})
            self.put(key + "/deterministic-dry-run.json", {"ok": False, "error": {"code": "unsupported_semantic_conversion"}})
            self.put(key + "/validate.json", {"ok": True, "result": {"valid": True}})
            self.put(key + "/rerun.json", {"ok": True, "result": {"current": True, "agentRuns": []}})
            package = str(repo / spec["package"])
            migration = ["migrate", "tessl-plugin", package, "--acr-only", "--repository", "https://github.com/" + spec["repository"], "--json"]
            operations = [("deterministic-dry-run", migration + ["--dry-run"], 1),
                          ("dry-run", migration + ["--agent", "codex", "--dry-run"], 0),
                          ("apply", migration + ["--agent", "codex"], 0),
                          ("validate", ["validate", str(repo), "--json"], 0),
                          ("rerun", migration + ["--dry-run"], 0)]
            for name, argv, exit_code in operations:
                path = f"{key}/{name}.json"
                receipt["operations"].append({"name": name, "argv": [str(self.root / "tmp/acr"), *argv], "exit_code": exit_code,
                                              "output": "evidence/" + path, "sha256": c.sha((self.evidence / path).read_bytes())})
            self.put(key + "/fixture-result.json", receipt)

    def put(self, name, value):
        path = self.evidence / name; path.parent.mkdir(exist_ok=True, parents=True); path.write_bytes(c.encoded(value))
        receipt_path = path.parent / "fixture-result.json"
        if path.name in ("deterministic-dry-run.json", "dry-run.json", "apply.json", "validate.json", "rerun.json") and receipt_path.exists():
            receipt = json.loads(receipt_path.read_bytes())
            for operation in receipt["operations"]:
                if operation["output"] == "evidence/" + name:
                    operation["sha256"] = c.sha(path.read_bytes())
            receipt_path.write_bytes(c.encoded(receipt))

    def get(self, name):
        return json.loads((self.evidence / name).read_bytes())

    def seal(self):
        return c.seal(self.root, self.evidence, self.output)

    def assert_refused(self):
        with self.assertRaises((c.Refusal, c.ToolFailure, OSError)):
            self.seal()
        self.assertFalse(self.output.exists())

    def test_seal_cleanup_then_import_preserves_exact_objects(self):
        manifest = self.seal()
        identities = [(item["producer_sha"], item["tree_sha"]) for item in manifest["fixtures"]]
        c.clean(self.root)
        self.assertFalse(self.root.exists())
        verified = c.verify_artifact(self.output, CONTEXT)
        self.assertEqual([(x["producer_sha"], x["tree_sha"]) for x in verified["fixtures"]], identities)
        self.assertEqual(set(c.members(self.output)), c.evidence_names() | {"manifest.json", "goc.bundle", "ffa.bundle"})

    def test_missing_and_partial_receipts_refuse(self):
        (self.evidence / "ffa/fixture-result.json").unlink(); self.assert_refused()

    def test_failed_post_commit_assertion_refuses(self):
        item = self.get("goc/fixture-result.json"); item["checks"]["validate"] = False
        self.put("goc/fixture-result.json", item); self.assert_refused()

    def test_omitted_original_suite_refuses(self):
        item = self.get("goc/fixture-result.json"); item["commands"].pop()
        self.put("goc/fixture-result.json", item); self.assert_refused()

    def test_diminished_original_suite_refuses_even_when_receipt_matches(self):
        log = b"All 22 commands + 1 negative path emitted valid envelopes against tesslio/good-oss-citizen\n"
        (self.evidence / "goc/converted-test-3.log").write_bytes(log)
        item = self.get("goc/fixture-result.json"); item["commands"][-1].update(sha256=c.sha(log), counts=[22])
        self.put("goc/fixture-result.json", item); self.assert_refused()

    def test_summary_not_pass_line_count(self):
        with self.assertRaises(c.Refusal):
            c.suite_counts("goc", 0, b"PASS\n" * 15)
        with self.assertRaises(c.Refusal):
            c.suite_counts("ffa", 0, b"PASS\n" * 184)

    def test_changed_original_gate_refuses(self):
        repo = self.root / "fixtures/ffa"
        (repo / ".github/scripts/pre-publish-gate.sh").write_text("exit 0\n")
        commit(repo, "weaken gate")
        item = self.get("ffa/fixture-result.json")
        item.update(producer_sha=git(repo, "rev-parse", "HEAD"), tree_sha=git(repo, "rev-parse", "HEAD^{tree}"))
        self.put("ffa/converted-inventory.json", c.inventory(repo, "HEAD"))
        item["inventories"]["converted"]["sha256"] = c.sha((self.evidence / "ffa/converted-inventory.json").read_bytes())
        self.put("ffa/fixture-result.json", item); self.assert_refused()

    def update_producer(self, key):
        repo = self.root / "fixtures" / key
        item = self.get(key + "/fixture-result.json")
        item.update(producer_sha=git(repo, "rev-parse", "HEAD"), tree_sha=git(repo, "rev-parse", "HEAD^{tree}"))
        name = key + "/converted-inventory.json"
        self.put(name, c.inventory(repo, "HEAD"))
        item["inventories"]["converted"]["sha256"] = c.sha((self.evidence / name).read_bytes())
        self.put(key + "/fixture-result.json", item)

    def test_original_ffa_diagnostics_bytes_and_mode_are_gate_inputs(self):
        repo = self.root / "fixtures/ffa"
        config = repo / "pyrightconfig.json"
        original = config.read_bytes()
        self.seal()  # Untouched configuration has a real retained Git inventory.
        self.output = self.base / "weakened-export"
        changed = json.loads(original)
        changed.update(typeCheckingMode="off", reportMissingImports="none")
        config.write_bytes(c.encoded(changed))
        commit(repo, "disable original diagnostic checks")
        self.update_producer("ffa")
        self.assert_refused()
        config.write_bytes(original)
        config.chmod(0o755)
        commit(repo, "change diagnostic configuration mode")
        self.update_producer("ffa")
        self.assert_refused()
        config.chmod(0o644)
        commit(repo, "restore original diagnostic input")
        self.update_producer("ffa")
        self.seal()

    def repair_runs(self, feedback=True):
        for phase in ("dry-run", "apply"):
            report = self.get("goc/" + phase + ".json")
            first = report["result"]["agentRuns"][0]
            first["scope"] = "runtime"
            second = copy.deepcopy(first)
            second["requestDigest"] = "sha256:" + "c" * 64
            if feedback:
                first.update(failure="ACR combined validation attempt 1: invalid generated reference",
                             failureKind="semantic_validation")
            report["result"]["agentRuns"] = [first, second]
            self.put("goc/" + phase + ".json", report)
        item = self.get("goc/fixture-result.json")
        item["credential_boundary"]["runs"] = [
            {"phase": phase, "index": index, "boundary": live_boundary()}
            for phase in ("dry-run", "apply") for index in range(2)]
        self.put("goc/fixture-result.json", item)

    def test_completed_multi_run_and_semantic_repair_success(self):
        self.repair_runs(feedback=False)
        self.seal()
        self.output = self.base / "repaired-export"
        self.repair_runs()
        manifest = self.seal()
        c.verify_artifact(self.output, CONTEXT)
        self.assertEqual(manifest["fixtures"][0]["producer_sha"], self.get("goc/fixture-result.json")["producer_sha"])
        report = json.loads((self.output / "evidence/goc/apply.json").read_bytes())
        self.assertEqual(len(report["result"]["agentRuns"]), 2)
        self.assertEqual(report["result"]["agentRuns"][0]["failureKind"], "semantic_validation")

    def test_repairs_do_not_hide_runtime_protocol_or_credential_failures(self):
        self.repair_runs()
        original = self.get("goc/apply.json")
        for kind in (None, "process", "protocol", "credential", "unknown"):
            with self.subTest(kind=kind):
                report = copy.deepcopy(original)
                first = report["result"]["agentRuns"][0]
                if kind is None:
                    del first["failureKind"]
                else:
                    first["failureKind"] = kind
                self.put("goc/apply.json", report)
                self.assert_refused()
        for mutation in ("failed-turn", "missing-completion", "credential", "last-run", "other-scope"):
            with self.subTest(mutation=mutation):
                report = copy.deepcopy(original)
                first, last = report["result"]["agentRuns"]
                if mutation == "failed-turn":
                    first["stdout"] += '{"type":"turn.failed"}\n'
                elif mutation == "missing-completion":
                    first["stdout"] = '{"type":"turn.started"}\n'
                elif mutation == "credential":
                    first["credentialBoundary"]["proposalChecked"] = False
                elif mutation == "last-run":
                    last.update(failure=first["failure"], failureKind=first["failureKind"])
                else:
                    last["scope"] = "delivery"
                self.put("goc/apply.json", report)
                self.assert_refused()

    def test_repair_mapping_must_retain_every_run_in_each_phase(self):
        self.repair_runs()
        original = self.get("goc/fixture-result.json")
        for index in range(4):
            item = copy.deepcopy(original)
            del item["credential_boundary"]["runs"][index]
            self.put("goc/fixture-result.json", item)
            self.assert_refused()
        self.put("goc/fixture-result.json", original)
        self.seal()

    def archive_bytes(self, root):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in sorted(c.members(root).items()):
                archive.writestr(zipfile.ZipInfo(name, date_time=(2001, 1, 1, 0, 0, 0)), data)
        return buffer.getvalue()

    @contextmanager
    def remote_archive(self, data, expected_digest=None):
        run, jobs, artifacts, workflow = ProvenanceTests().record()
        artifacts[0].update(digest=expected_digest or "sha256:" + c.sha(data), size_in_bytes=len(data))
        responses = {
            "/actions/runs/123/attempts/2": run,
            "/actions/runs/123/attempts/2/jobs?per_page=100&page=1": {"jobs": jobs, "total_count": 1},
            "/actions/runs/123/artifacts?per_page=100&page=1": {"artifacts": artifacts, "total_count": 1},
            "/actions/workflows/acr-codex-accept.yml": workflow,
        }
        def api_download(request, timeout):
            self.assertEqual(request.full_url, "https://api.github.com/repos/" + c.CENTRAL + "/actions/artifacts/77/zip")
            self.assertEqual(request.get_header("Authorization"), "Bearer " + SUITE)
            raise urllib.error.HTTPError(request.full_url, 302, "redirect", {"Location": "https://storage.example.invalid/artifact"}, None)
        def storage_download(location, timeout):
            # A plain URL, with no request headers, cannot forward the API token.
            self.assertEqual(location, "https://storage.example.invalid/artifact")
            return io.BytesIO(data)
        opener = mock.Mock()
        opener.open.side_effect = api_download
        with mock.patch.object(c, "api", side_effect=lambda path: copy.deepcopy(responses[path])), \
                mock.patch.object(c.urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(c.urllib.request, "urlopen", side_effect=storage_download) as storage:
            yield storage

    def verify_cli(self, root):
        argv = [str(HELPER), "verify", "--artifact", str(root), "--acr-sha", CONTEXT["acr_sha"],
                "--run-id", "123", "--run-attempt", "2"]
        with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return c.main()

    def consume_local(self, artifact, root, mutate_caller=False):
        original_run = c.subprocess.run
        called = []
        def child(argv, **kwargs):
            if argv[0] != "go":
                return original_run(argv, **kwargs)
            called.append(argv)
            env = kwargs["env"]
            for name in ("GH_TOKEN", "GITHUB_TOKEN", "CODEX_AUTH_JSON", "OPENAI_API_KEY"):
                self.assertNotIn(name, env)
            caller_manifest = artifact / "manifest.json"
            original = caller_manifest.read_bytes()
            if mutate_caller:
                caller_manifest.write_bytes(b"changed after authentication")
            try:
                manifest = json.loads(Path(env["ACR_CODEX_CONSUME_MANIFEST"]).read_bytes())
            finally:
                caller_manifest.write_bytes(original)
            c.write_new(Path(env["ACR_CODEX_CONSUME_EVIDENCE"]) / "consumer-result.json", c.encoded(consumer(manifest)))
            top = "TestCodexLivePublishedConsumption"
            rows = [{"Action": "start", "Package": c.CLI_PACKAGE}]
            for name in (top, top + "/GOC", top + "/FFA"):
                rows.append({"Action": "run", "Package": c.CLI_PACKAGE, "Test": name})
            for name in (top + "/GOC", top + "/FFA", top):
                rows.append({"Action": "pass", "Package": c.CLI_PACKAGE, "Test": name})
            rows.append({"Action": "pass", "Package": c.CLI_PACKAGE})
            kwargs["stdout"].write(event_bytes(rows))
            return subprocess.CompletedProcess(argv, 0)
        with mock.patch.dict(os.environ, {**InputTests().values("consume"), "ACR_ACCEPT_RUN_ID": "999"}), \
                mock.patch.object(c, "checkout"), mock.patch.object(c.subprocess, "run", side_effect=child):
            c.consume(self.base, artifact, root)
        self.assertEqual(len(called), 1)

    def test_authenticated_download_verify_and_standalone_consume(self):
        self.seal()
        data = self.archive_bytes(self.output)
        with self.remote_archive(data):
            destination = self.base / "download"
            c.download(CONTEXT["acr_sha"], "123", "2", destination)
            self.assertEqual(self.verify_cli(destination), 0)
            self.consume_local(destination, self.base / "acr-consume", mutate_caller=True)
        with self.remote_archive(data + b"changed", expected_digest="sha256:" + c.sha(data)), self.assertRaises(c.Refusal):
            c.download(CONTEXT["acr_sha"], "123", "2", self.base / "bad-download")
        self.assertFalse((self.base / "bad-download").exists())

    def test_authenticated_identity_refuses_self_consistent_substitutes(self):
        original_manifest = self.seal()
        remote = self.archive_bytes(self.output)
        repo = self.root / "fixtures/goc"
        # Amend only commit metadata: exactly the same tree, distinct commit.
        with mock.patch.dict(os.environ, {"GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
                                         "GIT_COMMITTER_DATE": "2001-01-01T00:00:00Z"}):
            git(repo, "commit", "--amend", "-qm", "substitute producer identity")
        for changed_tree in (False, True):
            with self.subTest(changed_tree=changed_tree):
                if changed_tree:
                    (repo / "agent-plugin.yaml").write_text("different generated plugin\n")
                    commit(repo, "substitute generated content")
                self.update_producer("goc")
                self.output = self.base / ("different-tree" if changed_tree else "same-tree")
                replacement = self.seal()
                self.assertNotEqual(replacement["fixtures"][0]["producer_sha"], original_manifest["fixtures"][0]["producer_sha"])
                self.assertEqual(replacement["fixtures"][0]["tree_sha"] == original_manifest["fixtures"][0]["tree_sha"], not changed_tree)
                # Internal consistency is deliberately insufficient as provenance.
                c.verify_artifact(self.output, CONTEXT)
                with self.subTest(interface="verify"), self.remote_archive(remote):
                    self.assertEqual(self.verify_cli(self.output), 1)
                with self.subTest(interface="consume"), self.remote_archive(remote), self.assertRaises(c.Refusal):
                    self.consume_local(self.output, self.base / ("consume-changed" if changed_tree else "consume-same"))

    def test_live_boundary_missing_false_or_non_boolean_refuses(self):
        for field in ("authInspected", "proposalChecked", "reportSanitized", "isolatedHomeRemoved"):
            for value in (False, 1, None):
                record = live_boundary(); record[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(c.Refusal):
                    c.boundary(record)
        record = live_boundary(); record["refreshObserved"] = True
        c.boundary(record)

    def test_live_run_index_mapping_refuses_omission(self):
        item = self.get("goc/fixture-result.json"); item["credential_boundary"]["runs"].pop()
        self.put("goc/fixture-result.json", item); self.assert_refused()

    def test_application_boundary_false_on_apply_refuses(self):
        report = self.get("goc/apply.json"); report["result"]["credentialBoundary"]["applicationChecked"] = False
        self.put("goc/apply.json", report); self.assert_refused()

    def test_missing_genuine_agent_run_refuses(self):
        report = self.get("goc/apply.json"); report["result"]["agentRuns"] = []
        self.put("goc/apply.json", report); self.assert_refused()

    def test_conversion_token_scope_and_required_environment(self):
        seen = {}
        def child(argv, **kwargs):
            seen.update(kwargs["env"])
            package = c.CLI_PACKAGE
            top = "TestCodexLiveUpstreamConversion"
            rows = [{"Package": package, "Action": "start"}, {"Package": package, "Action": "run", "Test": top}]
            for key in ("GOC", "FFA"):
                rows.extend([{"Package": package, "Action": "run", "Test": top + "/" + key},
                             {"Package": package, "Action": "pass", "Test": top + "/" + key}])
            rows.extend([{"Package": package, "Action": "pass", "Test": top}, {"Package": package, "Action": "pass"}])
            kwargs["stdout"].write(event_bytes(rows))
            return subprocess.CompletedProcess(argv, 0)
        with mock.patch.object(c, "checkout"), mock.patch.object(c.subprocess, "run", side_effect=child), mock.patch.dict(os.environ, {
                "CODEX_AUTH_JSON": "synthetic-unused", "GITHUB_TOKEN": "synthetic-unused", "CODEX_API_KEY": "synthetic-unused", "OPENAI_API_KEY": "synthetic-unused"}):
            c.convert(self.base, self.root)
        self.assertEqual(seen["GH_TOKEN"], SUITE)
        for name in ("CODEX_AUTH_JSON", "GITHUB_TOKEN", "CODEX_API_KEY", "OPENAI_API_KEY"):
            self.assertNotIn(name, seen)
        self.assertEqual(seen["ACR_CODEX_LIVE_REQUIRED"], "1")
        self.assertEqual(seen["CODEX_HOME"], str(self.root / "seed"))
        for key in ("GOC", "FFA"):
            self.assertEqual(seen["ACR_CODEX_LIVE_" + key], str(self.root / "fixtures" / key.lower()))
        # This proves the central-to-harness environment only. The ACR proof
        # and future original-suite tests own harness-to-child token isolation.

    def test_omitted_cli_validation_evidence_refuses(self):
        item = self.get("goc/fixture-result.json"); item["operations"].pop(3)
        self.put("goc/fixture-result.json", item); self.assert_refused()

    def test_provider_rerun_or_wrong_deterministic_refusal_refuses(self):
        self.put("goc/rerun.json", {"ok": True, "result": {"current": True, "agentRuns": [{"provider": "codex"}]}})
        self.assert_refused()

    def test_bounded_text_bundle_and_inventory(self):
        for bound in ("MAX_TEXT_TOTAL", "MAX_BUNDLE", "MAX_FILES"):
            with self.subTest(bound=bound), mock.patch.object(c, bound, 1):
                self.assert_refused()

    def test_stale_proof_refuses_before_bundle_creation(self):
        self.put("credential-boundary.json", {**proof(), "run_attempt": "1"})
        self.assert_refused()

    def test_missing_auth_refuses(self):
        (self.root / "seed/auth.json").unlink(); self.assert_refused()

    def test_malformed_auth_refuses(self):
        (self.root / "seed/auth.json").write_text("invalid"); self.assert_refused()

    def test_suite_token_cannot_be_missing_or_journey_fake(self):
        for value in ("", "journey-fixture-token"):
            with mock.patch.dict(os.environ, {"GH_TOKEN": value}):
                self.assert_refused()

    def test_seed_secret_in_text_refuses(self):
        report = self.get("goc/apply.json"); report["result"]["notes"] = SEED
        self.put("goc/apply.json", report); self.assert_refused()

    def test_compressed_git_seed_and_suite_leaks_refuse(self):
        # Decode real bundles: compressed bytes need not expose the sentinel.
        clean_head = git(self.root / "fixtures/goc", "rev-parse", "HEAD")
        for sentinel in (SEED, SUITE):
            with self.subTest(oracle="seed" if sentinel == SEED else "suite"):
                repo = self.root / "fixtures/goc"
                git(repo, "reset", "--hard", clean_head)
                (repo / "agent-plugin.yaml").write_text(sentinel + "\n")
                commit(repo, "credential-bearing generated content")
                probe = self.base / ("probe-" + ("seed" if sentinel == SEED else "suite") + ".bundle")
                git(repo, "bundle", "create", str(probe), "HEAD")
                self.assertNotIn(sentinel.encode(), probe.read_bytes())
                item = self.get("goc/fixture-result.json")
                item.update(producer_sha=git(repo, "rev-parse", "HEAD"), tree_sha=git(repo, "rev-parse", "HEAD^{tree}"))
                self.put("goc/converted-inventory.json", c.inventory(repo, "HEAD"))
                item["inventories"]["converted"]["sha256"] = c.sha((self.evidence / "goc/converted-inventory.json").read_bytes())
                self.put("goc/fixture-result.json", item)
                self.assert_refused()

    def test_scanner_fault_never_exports(self):
        with mock.patch.object(c, "scan", side_effect=OSError("synthetic scanner fault")):
            self.assert_refused()

    def test_existing_text_scanner_failure_never_exports(self):
        original = c.run
        def failing(argv, *args, **kwargs):
            if "assert-no-secret-leak.sh" in str(argv):
                raise c.ToolFailure("synthetic text scanner failure")
            return original(argv, *args, **kwargs)
        with mock.patch.object(c, "run", side_effect=failing):
            self.assert_refused()

    def test_same_tree_new_commit_does_not_satisfy_receipt(self):
        repo = self.root / "fixtures/goc"
        before = git(repo, "rev-parse", "HEAD^{tree}")
        with mock.patch.dict(os.environ, {"GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid", "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid", "GIT_AUTHOR_DATE": "2001-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2001-01-01T00:00:00Z"}):
            git(repo, "commit", "--allow-empty", "-qm", "different producer identity")
        self.assertEqual(before, git(repo, "rev-parse", "HEAD^{tree}"))
        self.assert_refused()

    def test_decoded_size_bound_refuses(self):
        with mock.patch.object(c, "MAX_DECODED", 1):
            self.assert_refused()

    def test_object_count_bound_refuses(self):
        with mock.patch.object(c, "MAX_OBJECTS", 1):
            self.assert_refused()

    def test_bundle_corruption_refuses_verify(self):
        self.seal()
        with (self.output / "goc.bundle").open("ab") as handle:
            handle.write(b"changed")
        with self.assertRaises(c.Refusal):
            c.verify_artifact(self.output, CONTEXT)

    def test_extra_member_and_symlink_refuse_verify(self):
        self.seal(); extra = self.output / "secret.json"; extra.write_text("extra")
        with self.assertRaises(c.Refusal):
            c.verify_artifact(self.output, CONTEXT)
        extra.unlink()
        path = self.output / "evidence/goc/apply.json"; data = path.read_bytes(); path.unlink()
        outside = self.base / "outside.json"; outside.write_bytes(data); path.symlink_to(outside)
        with self.assertRaises(c.Refusal):
            c.verify_artifact(self.output, CONTEXT)

    def test_export_bounds_and_unsafe_zip(self):
        self.seal()
        files = c.members(self.output)
        for mutate in ("unsafe", "duplicate", "symlink", "extra", "oversize"):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                for name, data in files.items():
                    if mutate == "unsafe" and name == "manifest.json":
                        name = "../manifest.json"
                    if mutate == "symlink" and name == "manifest.json":
                        info = zipfile.ZipInfo(name); info.external_attr = (0o120777 << 16); archive.writestr(info, data)
                    else:
                        archive.writestr(name, data)
                if mutate == "extra":
                    archive.writestr("extra.json", b"{}")
                if mutate == "duplicate":
                    import warnings
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", UserWarning)  # The deliberate duplicate is the refusal fixture.
                        archive.writestr("manifest.json", files["manifest.json"])
            with self.subTest(mutate=mutate):
                if mutate == "oversize":
                    with mock.patch.object(c, "MAX_TEXT", 1), self.assertRaises(c.Refusal):
                        c.extract_archive(buffer.getvalue(), self.base / "download")
                else:
                    with self.assertRaises(c.Refusal):
                        c.extract_archive(buffer.getvalue(), self.base / "download")
                self.assertFalse((self.base / "download").exists())

    def test_clean_archive_roundtrip(self):
        self.seal(); buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in c.members(self.output).items():
                archive.writestr(name, data)
        destination = self.base / "download"
        c.extract_archive(buffer.getvalue(), destination)
        self.assertEqual(c.verify_artifact(destination, CONTEXT), c.verify_artifact(self.output, CONTEXT))

    def test_consumer_exact_producer_and_real_source_projection(self):
        manifest = self.seal()
        receipt = consumer(manifest)
        c.consumer_receipt(receipt, manifest, CONTEXT)
        for field, value in (("source", "/tmp/local"), ("key", "FFA")):
            changed = copy.deepcopy(receipt); changed["fixtures"][0][field] = value
            with self.assertRaises(c.Refusal):
                c.consumer_receipt(changed, manifest, CONTEXT)
        for field, value in (("commit", "f" * 40), ("kind", "git"), ("release_id", 999), ("tag", "v2"), ("contentHash", "f" * 64)):
            changed = copy.deepcopy(receipt); changed["fixtures"][0]["consumers"][0][field] = value
            with self.assertRaises(c.Refusal):
                c.consumer_receipt(changed, manifest, CONTEXT)
        changed = copy.deepcopy(receipt); changed["fixtures"][0]["release"]["commit"] = "f" * 40
        with self.assertRaises(c.Refusal):
            c.consumer_receipt(changed, manifest, CONTEXT)
        changed = copy.deepcopy(receipt); changed["producer_run_attempt"] = "1"
        with self.assertRaises(c.Refusal):
            c.consumer_receipt(changed, manifest, CONTEXT)


def consumer(manifest):
    fixtures = []
    for item in manifest["fixtures"]:
        release = {"id": 1234, "tag": "v" + item["version"], "commit": item["producer_sha"], "contentHash": "sha256:" + "d" * 64}
        inv = [{"path": "native/rule.md", "mode": "100644", "sha256": "e" * 64}]
        fixtures.append({"key": item["key"], "source": "github:" + item["repository"] + "@v" + item["version"], "release": release,
                         "consumers": [{"adapter": adapter, "kind": "release", "commit": item["producer_sha"], "release_id": release["id"],
                                        "tag": release["tag"], "contentHash": release["contentHash"], "install": True, "realize": True, "check": True,
                                        "declared_inventory": inv, "native_inventory": inv} for adapter in ("claude-code", "codex", "cursor")],
                         "sha_install": {"source": "github:" + item["repository"] + "@" + item["producer_sha"], "commit": item["producer_sha"],
                                         "contentHash": release["contentHash"], "install": True}})
    return {"schema_version": 1, "result": "passed", "acr_sha": CONTEXT["acr_sha"], "central_sha": CONTEXT["central_sha"],
            "producer_run_id": CONTEXT["run_id"], "producer_run_attempt": CONTEXT["run_attempt"], "host": "linux-amd64", "fixtures": fixtures}


class ProvenanceTests(unittest.TestCase):
    def record(self):
        run = {"id": 123, "run_attempt": 2, "workflow_id": 8, "path": c.WORKFLOW,
               "event": "workflow_dispatch", "status": "completed", "conclusion": "success", "head_sha": CONTEXT["central_sha"],
               "repository": {"full_name": c.CENTRAL}, "head_repository": {"full_name": c.CENTRAL}}
        jobs = [{"name": "convert", "status": "completed", "conclusion": "success", "run_id": 123, "run_attempt": 2, "head_sha": CONTEXT["central_sha"]}]
        artifacts = [{"id": 77, "name": c.artifact_name(CONTEXT), "expired": False, "size_in_bytes": 1024,
                      "workflow_run": {"id": 123, "head_sha": CONTEXT["central_sha"]}, "digest": "sha256:" + "d" * 64}]
        return run, jobs, artifacts, {"path": c.WORKFLOW, "id": 8}

    def test_exact_successful_producer_attempt_passes(self):
        context, artifact = c.provenance(*self.record(), CONTEXT["acr_sha"], "123", "2")
        self.assertEqual(context, CONTEXT)
        self.assertEqual(artifact["id"], 77)

    def test_failed_expired_wrong_attempt_or_workflow_refuses(self):
        for target, field, value in ((0, "event", "push"), (0, "run_attempt", 1), (0, "path", ".github/workflows/fleet-review.yml"),
                                     (0, "conclusion", "failure"), (0, "workflow_id", 99), (1, "conclusion", "skipped"),
                                     (1, "run_attempt", 1), (1, "head_sha", "f" * 40), (2, "expired", True), (2, "digest", None),
                                     (2, "name", "newest-artifact"), (2, "size_in_bytes", c.MAX_ARCHIVE + 1)):
            record = self.record()
            obj = record[target] if target == 0 else record[target][0]
            obj[field] = value
            with self.subTest(field=field), self.assertRaises(c.Refusal):
                c.provenance(*record, CONTEXT["acr_sha"], "123", "2")
        run, jobs, artifacts, workflow = self.record()
        artifacts.append(copy.deepcopy(artifacts[0]))
        with self.assertRaises(c.Refusal):
            c.provenance(run, jobs, artifacts, workflow, CONTEXT["acr_sha"], "123", "2")

    def test_producer_binding_not_current_consume_run(self):
        with mock.patch.dict(os.environ, {**ENV, "ACR_ACCEPT_RUN_ID": "999", "ACR_ACCEPT_RUN_ATTEMPT": "1"}):
            context, _ = c.provenance(*self.record(), CONTEXT["acr_sha"], "123", "2")
            c.validate_proof(proof(), context)
            with self.assertRaises(c.Refusal):
                c.validate_proof(proof(), c.binding())


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = json.loads((HELPER.parents[1] / "workflows/acr-codex-accept.yml").read_text())

    def test_only_seven_dispatch_inputs_and_exact_public_source(self):
        self.assertEqual(set(self.workflow["on"]), {"workflow_dispatch"})
        self.assertEqual(set(self.workflow["on"]["workflow_dispatch"]["inputs"]),
                         {"phase", "acr-sha", "codex-version", "producer-run-id", "producer-run-attempt", "goc-source", "ffa-source"})
        self.assertFalse(self.workflow["concurrency"]["cancel-in-progress"])
        for job in self.workflow["jobs"].values():
            for step in job["steps"]:
                if step.get("uses", "").startswith("actions/checkout@"):
                    self.assertIs(step["with"]["persist-credentials"], False)
                    self.assertIn(step["with"]["ref"], ("${{ github.sha }}", "${{ inputs.acr-sha }}"))
                    if "repository" in step["with"]:
                        self.assertEqual(step["with"]["repository"], c.ACR)
                if "run" in step:
                    self.assertNotIn("${{", step["run"])
                    self.assertTrue(step["run"].startswith("set -euo pipefail\n"))
                self.assertNotIn("continue-on-error", step)

    def enabled(self, expression, statuses, failed):
        # Evaluate the deliberately small committed Actions condition grammar;
        # reject an expression outside it rather than interpreting by keywords.
        if expression is None:
            return not failed
        if expression == "always()":
            return True
        clauses = expression.split(" && ")
        result = True
        for clause in clauses:
            if clause == "success()":
                result = result and not failed
            else:
                import re
                match = re.fullmatch(r"steps\.([a-z]+)\.outcome == 'success'", clause)
                self.assertIsNotNone(match, expression)
                assert match
                result = result and statuses.get(match[1]) == "success"
        return result

    def simulate(self, failed_step):
        statuses = {}; executed = []; failed = False
        for step in self.workflow["jobs"]["convert"]["steps"]:
            identity = step.get("id", step["name"] if "name" in step else step["uses"])
            if self.enabled(step.get("if"), statuses, failed):
                executed.append(identity)
                outcome = "failure" if identity == failed_step else "success"
                statuses[identity] = outcome
                failed |= outcome == "failure"
            else:
                statuses[identity] = "skipped"
        return statuses, executed

    def test_failure_at_every_pre_upload_step_runs_cleanup_and_never_uploads(self):
        steps = self.workflow["jobs"]["convert"]["steps"]
        identities = [s.get("id", s.get("name", s.get("uses"))) for s in steps]
        for identity in identities[:identities.index("upload")]:
            statuses, executed = self.simulate(identity)
            self.assertIn("cleanup", executed)
            self.assertNotIn("upload", executed)
            self.assertEqual(statuses["upload"], "skipped")
        _, executed = self.simulate(None)
        self.assertLess(executed.index("proof"), executed.index("seed"))
        self.assertLess(executed.index("seal"), executed.index("cleanup"))
        self.assertLess(executed.index("cleanup"), executed.index("upload"))

    def test_credentials_are_step_scoped_and_consumer_cannot_seed(self):
        self.assertFalse(any("TOKEN" in key or "AUTH" in key for key in self.workflow["env"]))
        convert = self.workflow["jobs"]["convert"]
        consumer_job = self.workflow["jobs"]["consume"]
        self.assertEqual(convert["permissions"], {"contents": "read", "issues": "read", "pull-requests": "read"})
        self.assertEqual(consumer_job["permissions"], {"contents": "read", "actions": "read"})
        secrets = [(s.get("id"), k) for s in convert["steps"] for k, v in s.get("env", {}).items() if "secrets." in v]
        self.assertEqual(secrets, [("seed", "CODEX_AUTH_JSON")])
        self.assertNotIn("CODEX_AUTH_JSON", json.dumps(consumer_job))
        self.assertEqual([s["id"] for s in convert["steps"] if "GH_TOKEN" in s.get("env", {})], ["conversion", "seal"])
        for name in ("convert", "consume"):
            self.assertEqual(self.workflow["jobs"][name]["needs"], ["preflight"])
            self.assertEqual(self.workflow["jobs"][name]["if"], f"needs.preflight.result == 'success' && inputs.phase == '{name}'")

    def test_cleanup_failure_is_observable(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name) / "acr-accept"; root.mkdir()
            with mock.patch.object(c.shutil, "rmtree", side_effect=OSError("synthetic removal fault")):
                with self.assertRaises(OSError):
                    c.clean(root)
            self.assertTrue(root.exists())
            c.clean(root)
            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
