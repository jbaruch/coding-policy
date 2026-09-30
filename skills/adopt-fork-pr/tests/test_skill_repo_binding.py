"""Every agent-run `gh pr` instruction in adopt-fork-pr's SKILL.md names origin's repo.

`adopt.sh` binds its `gh pr` calls to origin (#655). The skill's own
classification and inspection instructions must too, or the user approves one
repository's PR #N while `adopt.sh` acts on another's (#656).
"""

import re
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "SKILL.md"

#: A `gh pr <subcommand>` command in inline code — an instruction the agent runs.
COMMAND = re.compile(r"`(gh pr [^`]*)`")


def commands(text):
    return COMMAND.findall(text)


def step(text, number):
    """The body of `## Step <number> — ...`, up to the next H2."""
    match = re.search(r"^## Step {} —.*?(?=^## |\Z)".format(number), text, re.S | re.M)
    if match is None:
        raise AssertionError("SKILL.md has no Step {}".format(number))
    return match.group(0)


class SkillRepoBindingTest(unittest.TestCase):
    def setUp(self):
        self.text = SKILL.read_text(encoding="utf-8")

    def test_every_gh_pr_command_carries_repo(self):
        found = commands(self.text)
        self.assertGreaterEqual(len(found), 2, "expected the Step 1 and Step 2 gh pr view commands")
        for cmd in found:
            self.assertIn("--repo <repo>", cmd, "gh pr command without --repo: {}".format(cmd))

    def test_step_1_resolves_origin_before_classifying(self):
        body = step(self.text, 1)
        self.assertIn('python3 "$CP/skills/release/origin-repo.py" .', body)
        self.assertLess(body.index("origin-repo.py"), body.index("gh pr view"))

    def test_just_inspect_binds_its_gh_pr_calls(self):
        body = step(self.text, 4)
        self.assertRegex(body, r"\*\*Just inspect\*\*[^\n]*`--repo <repo>`[^\n]*`gh pr`")


if __name__ == "__main__":
    unittest.main()
