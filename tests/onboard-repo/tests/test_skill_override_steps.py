"""onboard-repo's upgrade mode appends `--override` to exactly the right steps.

SKILL.md lists the steps whose script command takes `--override`. A step left
off the list runs its script in install mode during an upgrade; a step on the
list whose script rejects `--override` fails with a usage error (PR #830).
"""

import re
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[3] / "skills/onboard-repo"

#: The upgrade-mode instruction naming the steps, e.g. "Steps 1, 2, 3, 6 and 7".
LISTED = re.compile(r"Append `--override` to the script command in Steps ([\d, and]+)")
STEP = re.compile(r"^## Step (\d+) —(.*?)(?=^## |\Z)", re.S | re.M)
SCRIPT = re.compile(r'^bash "\$KS/([\w-]+\.sh)"', re.M)
#: A `case` arm accepting the flag, as each onboard-repo script parses arguments.
ACCEPTS = re.compile(r"^\s*--override\)", re.M)


class OverrideStepsTest(unittest.TestCase):
    def test_listed_steps_are_the_steps_whose_script_accepts_override(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        listed = LISTED.search(text)
        if listed is None:
            self.fail("SKILL.md names no --override steps")
        named = {int(n) for n in re.findall(r"\d+", listed.group(1))}
        accepting = set()
        for number, body in STEP.findall(text):
            for script in SCRIPT.findall(body):
                if ACCEPTS.search((SKILL_DIR / script).read_text(encoding="utf-8")):
                    accepting.add(int(number))
        self.assertTrue(accepting, "no step runs a script that accepts --override")
        self.assertEqual(named, accepting)


if __name__ == "__main__":
    unittest.main()
