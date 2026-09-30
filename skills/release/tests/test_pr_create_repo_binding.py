"""The skills that push a branch to origin open its PR in origin's repository.

`release` Step 2 and `onboard-repo` Step 8 push with `git push ... origin` and
then run `gh pr create`. A bare `gh pr create` targets `gh repo set-default`,
so a fork whose default is its upstream parent could open the PR in a
repository the operator does not own (#666).
"""

import re
import unittest
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[2]

#: (skill, step number) pairs whose step opens a PR for a branch pushed to origin.
PR_STEPS = (("release", 2), ("onboard-repo", 8))

COMMAND = re.compile(r"`(gh pr create[^`]*)`")
RESOLVE = 'python3 "$CP/skills/release/origin-repo.py" .'


def step(text, number):
    """The body of `## Step <number> — ...`, up to the next H2."""
    match = re.search(r"^## Step {} —.*?(?=^## |\Z)".format(number), text, re.S | re.M)
    if match is None:
        raise AssertionError("no Step {}".format(number))
    return match.group(0)


class PrCreateRepoBindingTest(unittest.TestCase):
    def test_each_pr_step_resolves_origin_then_names_it(self):
        for skill, number in PR_STEPS:
            with self.subTest(skill=skill):
                # Whitespace-normalized: a Markdown line wrap never changes an instruction.
                body = " ".join(step((SKILLS / skill / "SKILL.md").read_text(encoding="utf-8"), number).split())
                found = COMMAND.findall(body)
                self.assertTrue(found, "{} Step {} has no gh pr create command".format(skill, number))
                self.assertIn(RESOLVE, body)
                for cmd in found:
                    self.assertIn("--repo <repo>", cmd)
                    self.assertLess(body.index(RESOLVE), body.index(cmd))


if __name__ == "__main__":
    unittest.main()
