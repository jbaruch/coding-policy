"""Report contract lines: every form the parser accepts and every refusal class it names."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foreman import report_contract
from foreman.errors import UsageError

CRITERIA_BRIEF = """# Brief

## Assignment

- Question: settle it

## Acceptance Criteria

CRITERION 1: the flow is named
CRITERION 2: the alternatives are compared

## Evidence and Capabilities

{inputs}
"""


def brief(inputs="Read the issue."):
    return CRITERIA_BRIEF.format(inputs=inputs)


def consultation(*lines):
    return "Findings.\n" + "\n".join(lines) + "\n"


MET = ("ACCEPTANCE 1/2: met — the flow is in section 1", "ACCEPTANCE 2/2: met — table in section 2")


class BriefCriteriaTest(unittest.TestCase):
    def test_contiguous_block_counts(self):
        self.assertEqual(report_contract.brief_criteria(brief()), 2)

    def test_criterion_injected_into_another_section_is_ignored(self):
        self.assertEqual(report_contract.brief_criteria(brief("CRITERION 3: sneaked in through inputs")), 2)

    def test_gapped_duplicate_malformed_missing_and_doubled_sections_are_refused(self):
        cases = {
            "gap": brief().replace("CRITERION 2:", "CRITERION 3:"),
            "duplicate": brief().replace("CRITERION 2: the alternatives are compared", "CRITERION 1: again"),
            "malformed": brief().replace("CRITERION 2: the", "CRITERION two: the"),
            "empty": brief().replace("CRITERION 1: the flow is named\nCRITERION 2: the alternatives are compared\n", ""),
            "no section": "# Brief\n\nCRITERION 1: outside any section\n",
            "two sections": brief("## Acceptance Criteria\nCRITERION 3: second section"),
        }
        for name, text in cases.items():
            with self.subTest(name), self.assertRaises(UsageError) as caught:
                report_contract.brief_criteria(text)
            self.assertTrue(caught.exception.details["gaps"])

    def test_markup_on_criterion_lines_is_tolerated(self):
        text = brief().replace("CRITERION 1:", "- CRITERION 1:").replace("CRITERION 2:", "> `CRITERION 2:")
        self.assertEqual(report_contract.brief_criteria(text), 2)


class ReportLinesTest(unittest.TestCase):
    def gaps(self, text, role, specialty=None, criteria=None):
        with self.assertRaises(UsageError) as caught:
            report_contract.report_lines(text, role, specialty, criteria)
        return " | ".join(caught.exception.details["gaps"])

    def test_reviewer_and_tester_verdicts(self):
        for role in ("reviewer", "tester"):
            for verdict in ("blocking", "approved"):
                with self.subTest(role=role, verdict=verdict):
                    lines = report_contract.report_lines("Body\nVERDICT: {}\n".format(verdict), role)
                    self.assertEqual(lines, {"verdict": verdict, "acceptance": None, "contribution": None})

    def test_tolerated_markup(self):
        for line in ("- VERDICT: approved", "* VERDICT: approved", "> VERDICT: approved", "`VERDICT: approved`",
                     "  - `VERDICT: approved`  "):
            with self.subTest(line=line):
                self.assertEqual(report_contract.report_lines(line + "\n", "reviewer")["verdict"], "approved")

    def test_consultation_acceptance_with_each_dash(self):
        for dash in ("—", "–", "-"):
            with self.subTest(dash=dash):
                text = consultation("ACCEPTANCE 1/2: met {} a".format(dash), "ACCEPTANCE 2/2: unmet {} b".format(dash))
                lines = report_contract.report_lines(text, "advisor", "ux", 2)
                self.assertEqual(lines["acceptance"], [{"k": 1, "state": "met", "evidence": "a"},
                                                       {"k": 2, "state": "unmet", "evidence": "b"}])
                self.assertIsNone(lines["verdict"])

    def test_verdict_specialties_carry_both(self):
        for specialty in sorted(report_contract.VERDICT_SPECIALTIES):
            with self.subTest(specialty=specialty):
                lines = report_contract.report_lines(consultation(*MET, "VERDICT: blocking"), "advisor", specialty, 2)
                self.assertEqual(lines["verdict"], "blocking")
                self.assertIn("missing VERDICT", self.gaps(consultation(*MET), "advisor", specialty, 2))

    def test_optional_contribution(self):
        lines = report_contract.report_lines(consultation(*MET, "CONTRIBUTION: design"), "architect", None, 2)
        self.assertEqual(lines["contribution"], "design")
        self.assertIn("duplicate CONTRIBUTION",
                      self.gaps(consultation(*MET, "CONTRIBUTION: none", "CONTRIBUTION: none"), "architect", None, 2))
        self.assertIn("malformed CONTRIBUTION", self.gaps(consultation(*MET, "CONTRIBUTION: some"), "architect", None, 2))

    def test_missing(self):
        self.assertIn("missing VERDICT", self.gaps("No verdict here.\n", "reviewer"))
        self.assertIn("missing ACCEPTANCE 2", self.gaps(consultation(MET[0]), "investigator", None, 2))

    def test_duplicate_including_identical_repeat(self):
        self.assertIn("duplicate VERDICT", self.gaps("VERDICT: approved\nVERDICT: approved\n", "reviewer"))
        self.assertIn("duplicate VERDICT", self.gaps("VERDICT: approved\nVERDICT: blocking\n", "tester"))
        self.assertIn("duplicate ACCEPTANCE 1", self.gaps(consultation(*MET, MET[0]), "advisor", None, 2))

    def test_extra(self):
        self.assertIn("extra VERDICT", self.gaps(consultation(*MET, "VERDICT: approved"), "architect", None, 2))
        self.assertIn("extra VERDICT", self.gaps(consultation(*MET, "VERDICT: approved"), "advisor", "accessibility", 2))
        self.assertIn("extra ACCEPTANCE", self.gaps("VERDICT: approved\nACCEPTANCE 1/1: met — x\n", "reviewer"))
        self.assertIn("extra ACCEPTANCE 3", self.gaps(consultation(*MET, "ACCEPTANCE 3/2: met — x"), "advisor", None, 2))

    def test_n_mismatch(self):
        text = consultation("ACCEPTANCE 1/1: met — only one")
        self.assertIn("recorded 2", self.gaps(text, "advisor", None, 2))

    def test_malformed_candidates(self):
        for line in ("VERDICT: looks good", "VERDICT approved", "ACCEPTANCE 1/2: met", "ACCEPTANCE 1/2: met —   ",
                     "ACCEPTANCE 1/2: done — x", "ACCEPTANCE one/2: met — x"):
            with self.subTest(line=line):
                self.assertIn("malformed", self.gaps(consultation(*MET, line), "advisor", "security", 2))

    def test_prose_mentioning_keywords_in_lower_case_is_not_a_candidate(self):
        text = consultation(*MET, "The acceptance criteria and the verdict are discussed above.")
        self.assertEqual(report_contract.report_lines(text, "advisor", None, 2)["verdict"], None)

    def test_declared_contributions_survive_other_gaps(self):
        text = "No verdict.\n- CONTRIBUTION: implementation\nCONTRIBUTION: design\nCONTRIBUTION: lots\n"
        self.assertEqual(report_contract.declared_contributions(text), {"implementation", "design"})
        self.assertEqual(report_contract.declared_contributions("contribution: design\n"), set())

    def test_every_gap_is_named_at_once(self):
        gaps = self.gaps("VERDICT: approved\nVERDICT: approved\nACCEPTANCE 1/1: met — x\n", "reviewer")
        self.assertIn("duplicate VERDICT", gaps)
        self.assertIn("extra ACCEPTANCE", gaps)

    def test_role_and_criteria_preconditions(self):
        for role, criteria in (("developer", None), ("advisor", None), ("advisor", 0), ("reviewer", 2)):
            with self.subTest(role=role, criteria=criteria), self.assertRaises(UsageError):
                report_contract.report_lines("VERDICT: approved\n", role, None, criteria)


if __name__ == "__main__":
    unittest.main()
