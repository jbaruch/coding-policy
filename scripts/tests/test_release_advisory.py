"""Advisory replies never become new work or merge prerequisites."""

import unittest


class ReleaseAdvisoryTest(unittest.TestCase):
    def test_release_advisory_contract(self):
        import sys
        from pathlib import Path
        root = Path(__file__).resolve().parents[2] / "skills"
        release = "\n".join((root / path).read_text() for path in ("release/SKILL.md", "release/references/step-6-feedback.md", "release/references/step-7-delivery.md"))
        mechanics = (root / "release/references/SCRIPTING.md").read_text()
        rules = root.parent / "rules"
        boy_scout = (rules / "boy-scout.md").read_text()
        severity = (rules / "review-severity.md").read_text()
        advisory_directive = next(line for line in boy_scout.splitlines() if line.startswith("- **Advisory**"))
        risk_directive = next(line for line in boy_scout.splitlines() if line.startswith("- **Unrelated blocking risk**"))
        assert "current conversation" in risk_directive
        assert "rules/review-severity.md" in advisory_directive
        assert "task report" not in advisory_directive and "round log" not in advisory_directive
        assert "Advisory → note it directly in the existing review conversation" in severity
        assert "Acknowledged — advisory noted" in release
        assert "never simulate a team report" in release
        assert "outside merge prerequisites" in release
        assert "outside the merge predicate" in release and "outside the merge predicate" in mechanics
        assert "Every inline comment must also be read" in release
        for content in (release, mechanics, boy_scout):
            for contradiction in ("Reply on EVERY thread", "every thread has a reply",
                                  "no review thread is unresolved", "existing follow-up references"):
                assert contradiction not in content, contradiction


if __name__ == "__main__":
    unittest.main()
