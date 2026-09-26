"""The two slice-scope renderers must agree byte for byte.

`compose-briefs.sh` writes the block into the brief and `apply` re-derives it
to check the brief carries it, so a divergence of one character refuses every
seated dispatch. The composer is shell and the checker is Python; nothing but
this test holds them together (#453).
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL))

from foreman.partition import slice_scope  # noqa: E402

CASES = (
    ("reviewer#api", ["src/api/*"], "0123456789ab"),
    ("reviewer#core", ["src/core/*", "src/core/**/*.py"], "abcdef012345"),
    ("tester#cli", ["skills/herdr-foreman/foreman/cli.py"], "fedcba987654"),
)


class SliceScopeParityTest(unittest.TestCase):
    def shell_render(self, seat, paths, digest):
        """`compose-briefs.sh`'s own renderer, sourced and called directly."""
        script = (
            'set -euo pipefail\n'
            'source "$1"\n'
            'slice_scope "$2" "$3" "$4"\n'
        )
        done = subprocess.run(
            ["bash", "-c", script, "bash", str(SKILL / "compose-briefs.sh"),
             seat, json.dumps(paths), digest],
            capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_the_renderers_agree(self):
        for seat, paths, digest in CASES:
            with self.subTest(seat=seat):
                self.assertEqual(self.shell_render(seat, paths, digest),
                                 slice_scope(seat, paths, digest))

    def test_an_unseated_role_renders_no_scope(self):
        self.assertEqual(self.shell_render("reviewer", "[]", ""), "")


if __name__ == "__main__":
    unittest.main()
