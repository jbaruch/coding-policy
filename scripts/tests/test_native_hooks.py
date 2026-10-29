"""Native hook copies find their complete release skill, independent of cwd."""

import json
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "skills/release/native-hook.sh"
NATIVE = "acr__jbaruch__coding-policy__release"


def installed_layout(project, host=None):
    if host is None:
        skill = project / ".tessl/plugins/jbaruch/coding-policy/skills/release"
        hook = skill / "native-hook.sh"
    else:
        skill = project / host / "skills" / NATIVE
        hook = project / host / "hooks/acr__jbaruch__coding-policy__fixture/native-hook.sh"
    hook.parent.mkdir(parents=True, exist_ok=True)
    (skill / "hooks").mkdir(parents=True)
    hook.write_text(SOURCE.read_text())
    return skill, hook


class NativeHooksTest(unittest.TestCase):
    def test_tessl_and_recorded_native_layouts_preserve_input_and_quoted_paths(self):
        with tempfile.TemporaryDirectory() as scratch:
            project = Path(scratch) / "project with spaces\n"
            foreign = Path(scratch) / "foreign"
            foreign.mkdir()
            for host in (None, ".codex", ".claude", ".cursor"):
                skill, hook = installed_layout(project, host)
                for event, owner, content in (
                    ("session-start", "session-start.sh", "#!/bin/bash\ncat\n"),
                    ("stop-handoff", "stop-handoff-hygiene.sh", "#!/bin/bash\ncat\n"),
                    ("session-end", "session-end-cleanup.py", "import sys\nsys.stdout.write(sys.stdin.read())\n"),
                ):
                    target = skill / "hooks" / owner
                    target.write_text(content)
                    target.chmod(0o644)
                    result = subprocess.run(["bash", str(hook), event], cwd=foreign,
                                            env={**os.environ, "CDPATH": str(foreign)}, input='{"fixture":true}\n',
                                            capture_output=True, text=True)
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '{"fixture":true}\n', ""))

    def test_cursor_flat_native_entry_runs_after_path_materialization(self):
        plugin = json.loads((ROOT / ".tessl-plugin/plugin.json").read_text())
        entry = plugin["nativeHooks"]["cursor"]["sessionEnd"][0]
        self.assertNotIn("hooks", entry)
        self.assertIsInstance(entry["command"], str)
        with tempfile.TemporaryDirectory() as scratch:
            project = Path(scratch) / "project with spaces\n"
            skill, _ = installed_layout(project)
            (skill / "hooks/session-end-cleanup.py").write_text("import sys\nsys.stdout.write(sys.stdin.read())\n")
            # nativeHooks preserve the native entry; materialization expands only the package path.
            native_entry = {**entry, "command": entry["command"].replace("${TESSL_PLUGIN_DIR}", str(skill.parents[1]))}
            config = project / ".cursor/hooks.json"
            config.parent.mkdir()
            config.write_text(json.dumps({"version": 1, "hooks": {"sessionEnd": [native_entry]}}))
            command = json.loads(config.read_text())["hooks"]["sessionEnd"][0]["command"]
            result = subprocess.run(shlex.split(command), cwd=scratch, input='{"fixture":true}\n',
                                    capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '{"fixture":true}\n', ""))

    def test_missing_python_reports_installation_error_with_bash_available(self):
        with tempfile.TemporaryDirectory() as scratch:
            skill, hook = installed_layout(Path(scratch))
            (skill / "hooks/session-end-cleanup.py").write_text("raise SystemExit(9)\n")
            tools = Path(scratch) / "bin"
            tools.mkdir()
            bash = shutil.which("bash")
            assert bash is not None
            (tools / "bash").symlink_to(bash)
            result = subprocess.run([str(tools / "bash"), str(hook), "session-end"],
                                    env={"PATH": str(tools)}, capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stdout), (1, ""))
            self.assertIn("install python3", result.stderr)
            self.assertIn("PATH", result.stderr)
            self.assertNotIn("command not found", result.stderr)

    def test_python_owner_preserves_error_status_and_diagnostic(self):
        with tempfile.TemporaryDirectory() as scratch:
            skill = Path(scratch) / "release"
            (skill / "hooks").mkdir(parents=True)
            hook = skill / "native-hook.sh"
            hook.write_text(SOURCE.read_text())
            owner = skill / "hooks/session-end-cleanup.py"
            owner.write_text('import sys\nprint("fixture diagnostic", file=sys.stderr)\nsys.exit(7)\n')
            result = subprocess.run(["bash", str(hook), "session-end"], capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (7, "", "fixture diagnostic\n"))

    def test_missing_owner_is_reported_and_unknown_event_is_usage(self):
        with tempfile.TemporaryDirectory() as scratch:
            hook = Path(scratch) / "release/native-hook.sh"
            hook.parent.mkdir()
            hook.write_text(SOURCE.read_text())
            missing = subprocess.run(["bash", str(hook), "session-start"], capture_output=True, text=True)
            self.assertEqual((missing.returncode, missing.stdout), (1, ""))
            self.assertIn("repair the complete release skill", missing.stderr)
            invalid = subprocess.run(["bash", str(hook), "unknown"], capture_output=True, text=True)
            self.assertEqual((invalid.returncode, invalid.stdout), (2, ""))
            self.assertIn("usage", invalid.stderr)

    def test_disappeared_installation_directory_fails_visibly(self):
        with tempfile.TemporaryDirectory() as scratch:
            hook = Path(scratch) / "release/native-hook.sh"
            hook.parent.mkdir()
            hook.write_text(SOURCE.read_text())
            command = 'source "$1"; rm "$1"; rmdir "${1%/*}"; main session-start'
            result = subprocess.run(["bash", "-c", command, "fixture", str(hook)],
                                    capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stdout), (1, ""))
            self.assertIn("restore read/search access", result.stderr)


if __name__ == "__main__":
    unittest.main()
