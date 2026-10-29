#!/usr/bin/env python3
"""Real CLI-child/filesystem cases for the finite registry authority exception."""

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "core_authority", ROOT / "materialize-core-authority.py"
)
assert SPEC is not None and SPEC.loader is not None
OWNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OWNER)
REPORT = json.loads((ROOT / "tests/fixtures/core157-security-report.json").read_text())
REAL_FETCH_REPORT = OWNER.fetch_report


class CoreAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.policy = self.root / "policy"
        self.policy.mkdir(mode=0o700)
        self.bin = self.root / "bin"
        self.bin.mkdir(mode=0o700)
        self.log = self.root / "commands.jsonl"
        self.metadata_path = self.root / "metadata.json"
        self.metadata = {
            "tile": {
                "fullName": OWNER.PLUGIN,
                "latestVersion": OWNER.VERSION,
                "archived": False,
            },
            "version": {
                "version": OWNER.VERSION,
                "fingerprint": OWNER.FINGERPRINT,
                "moderationPassed": True,
                "moderationStatus": "pass",
                "archived": False,
            },
        }
        self.metadata_path.write_text(json.dumps(self.metadata))
        self.report_path = self.root / "report.json"
        self.report_path.write_text(json.dumps(REPORT))
        self.files = {
            ".tessl-plugin/plugin.json": json.dumps(
                {"name": OWNER.PLUGIN, "version": OWNER.VERSION}
            ),
            "rules/temporal-awareness.md": "Synthetic scoped contract fixture.\n",
            "skills/current-tz/SKILL.md": "Synthetic current-zone fixture.\n",
            "skills/current-tz/scripts/read-current-tz.py": "# Synthetic, never executed.\n",
        }
        self.files_path = self.root / "files.json"
        self.files_path.write_text(json.dumps(self.files))
        self.alias = self.root / "untouched.txt"
        self.alias.write_text("Unchanged synthetic sibling")
        executable = self.bin / "tessl"
        executable.write_text("""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
args = sys.argv[1:]
cwd = Path.cwd()
with open(os.environ["FAKE_TESSL_LOG"], "a") as out:
    out.write(json.dumps({"args": args, "cwd": str(cwd), "files_before": sorted(p.name for p in cwd.iterdir()), "manifest_before": json.loads((cwd / "tessl.json").read_text()) if (cwd / "tessl.json").exists() else None}) + "\\n")
if args[:2] == ["plugin", "info"]:
    print(Path(os.environ["FAKE_TESSL_METADATA"]).read_text())
elif args[0] == "install":
    mode = os.environ.get("FAKE_TESSL_MODE", "valid")
    if mode == "blocked":
        print("Blocked by install policy", file=sys.stderr)
        raise SystemExit(0)
    if mode == "failed":
        print("Synthetic private tool diagnostic must not escape", file=sys.stderr)
        raise SystemExit(1)
    dependencies = {"jbaruch/nanoclaw-core": {"version": "0.1.157"}}
    if mode == "extra-dependency":
        dependencies["unexpected/plugin"] = {"version": "latest"}
    (cwd / "tessl.json").write_text(json.dumps({"dependencies": dependencies}))
    provider = cwd / ".tessl/plugins/jbaruch/nanoclaw-core"
    for name, body in json.loads(Path(os.environ["FAKE_TESSL_FILES"]).read_text()).items():
        path = provider / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    target = provider / "rules/temporal-awareness.md"
    if mode == "tampered":
        target.write_text("Changed rule")
    if mode == "extra-file":
        (provider / "unexpected.md").write_text("Unexpected rule")
    if mode in ("symlink", "fifo"):
        target.unlink()
        if mode == "symlink":
            target.symlink_to(os.environ["FAKE_TESSL_ALIAS"])
        else:
            os.mkfifo(target)
else:
    raise SystemExit(2)
""")
        executable.chmod(0o700)
        curl = self.bin / "curl"
        curl.write_text("""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
with open(os.environ["FAKE_TESSL_LOG"], "a") as out:
    out.write(json.dumps({"args": ["curl", *sys.argv[1:]], "cwd": str(Path.cwd())}) + "\\n")
sys.stdout.write(Path(os.environ["FAKE_CURL_REPORT"]).read_text())
sys.stdout.write("\\n" + os.environ.get("FAKE_CURL_STATUS", "200"))
""")
        curl.chmod(0o700)
        environment = {
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "FAKE_TESSL_LOG": str(self.log),
            "FAKE_TESSL_METADATA": str(self.metadata_path),
            "FAKE_TESSL_FILES": str(self.files_path),
            "FAKE_TESSL_ALIAS": str(self.alias),
            "FAKE_CURL_REPORT": str(self.report_path),
        }
        self.environment = patch.dict(os.environ, environment)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        # Synthetic package bytes isolate filesystem behavior. The unpatched
        # production pins are also exercised by the real registry preparation.
        self.hashes = {
            key: hashlib.sha256(value.encode()).hexdigest()
            for key, value in self.files.items()
        }
        hashes = patch.object(OWNER, "EXPECTED_FILES", self.hashes)
        hashes.start()
        self.addCleanup(hashes.stop)
        self.report = copy.deepcopy(REPORT)
        reports = patch.object(
            OWNER, "fetch_report", side_effect=lambda: copy.deepcopy(self.report)
        )
        self.report_mock = reports.start()
        self.addCleanup(reports.stop)

    def commands(self):
        return (
            [json.loads(line) for line in self.log.read_text().splitlines()]
            if self.log.exists()
            else []
        )

    def installs(self):
        return [row for row in self.commands() if row["args"][0] == "install"]

    def test_preparation_performs_no_install_or_context_write(self):
        result = OWNER.materialize(self.policy, prepare_only=True)
        self.assertEqual(result["status"], "prepared")
        self.assertEqual(self.installs(), [])
        self.assertEqual(list(self.policy.iterdir()), [])

    def test_actual_report_transport_keeps_verified_https_and_refuses_redirect_status(
        self,
    ):
        with patch.object(OWNER, "fetch_report", REAL_FETCH_REPORT):
            result = OWNER.materialize(self.policy, prepare_only=True)
            self.assertEqual(result["status"], "prepared")
            curl = [row for row in self.commands() if row["args"][0] == "curl"]
            self.assertEqual(len(curl), 1)
            args = curl[0]["args"]
            self.assertEqual(args[1:4], ["--disable", "--proto", "=https"])
            self.assertEqual(args[-1], OWNER.REPORT_URL)
            self.assertFalse(
                any(arg in args for arg in ("--insecure", "-k", "--location", "-L"))
            )
            with patch.dict(os.environ, {"FAKE_CURL_STATUS": "302"}):
                with self.assertRaises(OWNER.MaterializationError):
                    OWNER.materialize(self.policy)
            self.assertEqual(self.installs(), [])

    def test_materializes_exact_tree_with_only_one_fresh_scoped_install(self):
        result = OWNER.materialize(self.policy)
        self.assertEqual(result["status"], "materialized")
        self.assertEqual(result["files"], len(self.files))
        calls = self.installs()
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0]["args"],
            [
                "install",
                "jbaruch/nanoclaw-core@0.1.157",
                "--yes",
                "--accept-warnings",
                "--strict",
                "--agent",
                "codex",
            ],
        )
        self.assertEqual(calls[0]["files_before"], ["tessl.json"])
        self.assertEqual(
            calls[0]["manifest_before"], {"mode": "managed", "dependencies": {}}
        )
        self.assertNotEqual(calls[0]["cwd"], str(self.policy))
        self.assertEqual(Path(calls[0]["cwd"]).parent, self.policy.resolve())
        for name, body in self.files.items():
            self.assertEqual(
                (
                    self.policy / ".tessl/plugins/jbaruch/nanoclaw-core" / name
                ).read_text(),
                body,
            )
        self.assertEqual(list(self.policy.glob("core157-*")), [])
        self.assertEqual(self.alias.read_text(), "Unchanged synthetic sibling")

    def test_exact_repeat_reuses_tree_without_another_install(self):
        first = OWNER.materialize(self.policy)
        provider = self.policy / ".tessl/plugins/jbaruch/nanoclaw-core"
        before = provider.stat()
        self.assertEqual(OWNER.materialize(self.policy), first)
        self.assertEqual(
            OWNER.materialize(self.policy, prepare_only=True)["status"], "prepared"
        )
        after = provider.stat()
        self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))
        self.assertEqual(len(self.installs()), 1)
        self.assertEqual(list(self.policy.glob("core157-*")), [])

    def test_exact_repeat_still_requires_current_registry_and_report(self):
        OWNER.materialize(self.policy)
        provider = self.policy / ".tessl/plugins/jbaruch/nanoclaw-core"
        before = OWNER.verify_tree(provider, self.policy)
        self.metadata["tile"]["latestVersion"] = "0.1.158"
        self.metadata_path.write_text(json.dumps(self.metadata))
        with self.assertRaisesRegex(
            OWNER.MaterializationError, "registry_identity_changed"
        ):
            OWNER.materialize(self.policy)
        self.metadata["tile"]["latestVersion"] = OWNER.VERSION
        self.metadata_path.write_text(json.dumps(self.metadata))
        self.report["data"][0]["attributes"]["securityFindings"].append(
            {"code": "W999"}
        )
        with self.assertRaisesRegex(
            OWNER.MaterializationError, "reviewed_security_report_changed"
        ):
            OWNER.materialize(self.policy)
        self.assertEqual(OWNER.verify_tree(provider, self.policy), before)
        self.assertEqual(len(self.installs()), 1)

    def test_surrounding_context_dependencies_are_not_part_of_warning_acceptance(self):
        manifest = self.policy / "tessl.json"
        manifest.write_text(
            json.dumps({"dependencies": {"another/retained": {"version": "1.0.0"}}})
        )
        before = manifest.read_bytes()
        OWNER.materialize(self.policy)
        self.assertEqual(manifest.read_bytes(), before)
        self.assertEqual(
            self.installs()[0]["manifest_before"],
            {"mode": "managed", "dependencies": {}},
        )
        self.assertEqual(len(self.installs()), 1)

    def test_registry_drift_never_accepts_warnings(self):
        mutations = [
            ("tile", "fullName", "unexpected/provider"),
            ("tile", "latestVersion", "0.1.158"),
            ("version", "version", "0.1.158"),
            ("version", "fingerprint", "different"),
            ("version", "moderationPassed", False),
            ("version", "archived", True),
        ]
        for section, key, value in mutations:
            with self.subTest(section=section, key=key):
                metadata = copy.deepcopy(self.metadata)
                metadata[section][key] = value
                self.metadata_path.write_text(json.dumps(metadata))
                with self.assertRaises(OWNER.MaterializationError):
                    OWNER.materialize(self.policy)
                self.assertEqual(self.installs(), [])

    def test_changed_or_additional_security_finding_never_accepts_warnings(self):
        for change in ("new-code", "new-scope", "additional"):
            with self.subTest(change=change):
                self.report = copy.deepcopy(REPORT)
                attributes = self.report["data"][0]["attributes"]
                if change == "new-code":
                    attributes["securityFindings"][0]["code"] = "W999"
                elif change == "new-scope":
                    attributes["securityFindings"][0]["located"]["sourcePath"] = (
                        "skills/current-tz"
                    )
                else:
                    self.report["data"][2]["attributes"]["securityFindings"] = [
                        {"code": "W007"}
                    ]
                with self.assertRaises(OWNER.MaterializationError):
                    OWNER.materialize(self.policy)
                self.assertEqual(self.installs(), [])

    def test_blocked_exit_zero_and_bad_installed_trees_never_materialize(self):
        for mode in (
            "blocked",
            "tampered",
            "extra-file",
            "extra-dependency",
            "symlink",
            "fifo",
        ):
            with (
                self.subTest(mode=mode),
                patch.dict(os.environ, {"FAKE_TESSL_MODE": mode}),
            ):
                with self.assertRaises((OWNER.MaterializationError, OSError)):
                    OWNER.materialize(self.policy)
                self.assertFalse(
                    (self.policy / ".tessl/plugins/jbaruch/nanoclaw-core").exists()
                )
                self.assertEqual(list(self.policy.glob("core157-*")), [])
                self.assertEqual(self.alias.read_text(), "Unchanged synthetic sibling")

    def test_unknown_existing_context_is_not_overwritten(self):
        target = self.policy / ".tessl/plugins/jbaruch/nanoclaw-core"
        target.mkdir(parents=True)
        (target / "held").write_text("Retain original evidence")
        with self.assertRaises(OWNER.MaterializationError):
            OWNER.materialize(self.policy)
        self.assertEqual((target / "held").read_text(), "Retain original evidence")
        self.assertEqual(self.commands(), [])

    def test_report_change_after_install_stops_the_context(self):
        changed = copy.deepcopy(REPORT)
        changed["data"][0]["attributes"]["securityFindings"].append({"code": "W999"})
        self.report_mock.side_effect = [REPORT, changed]
        with self.assertRaises(OWNER.MaterializationError):
            OWNER.materialize(self.policy)
        self.assertEqual(len(self.installs()), 1)

    def test_outer_boundary_keeps_raw_installer_diagnostics_private(self):
        output, errors = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, {"FAKE_TESSL_MODE": "failed"}),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            code = OWNER.main([str(self.policy)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "refused")
        self.assertNotIn("Synthetic private", output.getvalue() + errors.getvalue())


if __name__ == "__main__":
    unittest.main()
