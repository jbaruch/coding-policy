"""Native hook copies find their complete release skill, independent of cwd."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "skills/release/native-hook.sh"
OWN_REFERENCE = ".tessl/plugins/jbaruch/coding-policy/skills/release"
NATIVE = "acr__jbaruch__coding-policy__release"


class NativeHooksTest(unittest.TestCase):
    def test_tessl_and_recorded_native_layouts_preserve_input_and_quoted_paths(self):
        with tempfile.TemporaryDirectory() as scratch:
            project = Path(scratch) / "project with spaces\n"
            foreign = Path(scratch) / "foreign"
            foreign.mkdir()
            for host in (None, ".codex", ".claude", ".cursor"):
                if host is None:
                    skill = project / OWN_REFERENCE
                    hook = skill / "native-hook.sh"
                    text = SOURCE.read_text()
                else:
                    skill = project / host / "skills" / NATIVE
                    hook = project / host / "hooks/acr__jbaruch__coding-policy__fixture/native-hook.sh"
                    text = SOURCE.read_text().replace(OWN_REFERENCE, host + "/skills/" + NATIVE)
                hook.parent.mkdir(parents=True, exist_ok=True)
                (skill / "hooks").mkdir(parents=True)
                hook.write_text(text)
                for event, owner in (("session-start", "session-start.sh"), ("stop-handoff", "stop-handoff-hygiene.sh")):
                    target = skill / "hooks" / owner
                    target.write_text("#!/bin/bash\ncat\n")
                    target.chmod(0o644)
                    result = subprocess.run(["bash", str(hook), event], cwd=foreign,
                                            env={**os.environ, "CDPATH": str(foreign)}, input='{"fixture":true}\n',
                                            capture_output=True, text=True)
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '{"fixture":true}\n', ""))

    def test_missing_owner_is_reported_and_unknown_event_is_usage(self):
        with tempfile.TemporaryDirectory() as scratch:
            hook = Path(scratch) / "release/native-hook.sh"
            hook.parent.mkdir()
            hook.write_text(SOURCE.read_text())
            missing = subprocess.run(["bash", str(hook), "session-start"], capture_output=True, text=True)
            self.assertEqual((missing.returncode, missing.stdout), (0, ""))
            self.assertIn("repair the complete release skill", missing.stderr)
            invalid = subprocess.run(["bash", str(hook), "unknown"], capture_output=True, text=True)
            self.assertEqual((invalid.returncode, invalid.stdout), (2, ""))
            self.assertIn("usage", invalid.stderr)


if __name__ == "__main__":
    unittest.main()
