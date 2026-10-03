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
        text = brief().replace("CRITERION 1:", "- CRITERION 1:").replace("CRITERION 2:", "    CRITERION 2:")
        self.assertEqual(report_contract.brief_criteria(text), 2)

    def test_quoted_criterion_examples_do_not_change_n(self):
        text = brief().replace(
            "CRITERION 2: the alternatives are compared\n",
            "CRITERION 2: the alternatives are compared\n"
            "`CRITERION 3: quoted inline`\n"
            "> CRITERION 3: quoted quote\n"
            "```\nCRITERION 3: fenced\n```\n",
        )
        self.assertEqual(report_contract.brief_criteria(text), 2)
        listed = brief().replace("CRITERION 2: the alternatives are compared",
                                 "- `CRITERION 2: the alternatives are compared`")
        self.assertEqual(report_contract.brief_criteria(listed), 1)

    def test_a_fenced_heading_does_not_create_an_acceptance_section(self):
        text = ("# Brief\n\n```\n## Acceptance Criteria\nCRITERION 1: fake\n```\n\n"
                "## Acceptance Criteria\n\nCRITERION 1: the flow is named\nCRITERION 2: the alternatives are compared\n")
        self.assertEqual(report_contract.brief_criteria(text), 2)

    def test_a_fenced_heading_does_not_end_the_criteria_section(self):
        text = ("# Brief\n\n## Acceptance Criteria\n\nCRITERION 1: the flow is named\n"
                "```\n## Next Section\nCRITERION 9: hidden\n```\n"
                "CRITERION 2: the alternatives are compared\n")
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
        for line in ("- VERDICT: approved", "* VERDICT: approved", "  - VERDICT: approved", "    VERDICT: approved"):
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

    def test_a_criterion_line_in_a_report_is_extra(self):
        self.assertIn("extra CRITERION", self.gaps("VERDICT: approved\nCRITERION 1: restated from the brief\n", "reviewer"))
        self.assertIn("extra CRITERION", self.gaps(consultation(*MET, "- CRITERION 1: the flow is named"), "advisor", None, 2))

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


A2_QUOTE = "`CONTRIBUTION declared below as design.`"
A_UNQUOTED = "CONTRIBUTION declared below as `design`."
MET3 = (
    "ACCEPTANCE 1/3: met — inventory tables name paths, triggers, and class",
    "ACCEPTANCE 2/3: met — bounded repair keeps identity checks",
    "ACCEPTANCE 3/3: met — follow-ups and document conflicts named",
)
FORMS = (
    ("VERDICT", "VERDICT: approved", "VERDICT: looks good"),
    ("ACCEPTANCE", "ACCEPTANCE 1/2: met — a", "ACCEPTANCE 1/2: met"),
    ("CONTRIBUTION", "CONTRIBUTION: design", "CONTRIBUTION declared below as design."),
    ("CRITERION", "CRITERION 1: the flow is named", "CRITERION two: the"),
)


def quotes(line):
    return (
        "`{}`".format(line),
        "``{}``".format(line),
        "> {}".format(line),
        "> > {}".format(line),
        "- `{}`".format(line),
        "> - {}".format(line),
        "> `{}`".format(line),
        "```\n{}\n```".format(line),
        "~~~\n{}\n~~~".format(line),
        "```python\n{}\n```".format(line),
        "   ```\n{}\n   ```".format(line),
    )


class QuotedExampleTest(unittest.TestCase):
    def gaps(self, text, role, specialty=None, criteria=None):
        with self.assertRaises(UsageError) as caught:
            report_contract.report_lines(text, role, specialty, criteria)
        return " | ".join(caught.exception.details["gaps"])

    def test_corrected_artifact_yields_three_met_criteria_and_design(self):
        text = consultation(A2_QUOTE, *MET3, "CONTRIBUTION: design")
        lines = report_contract.report_lines(text, "advisor", None, 3)
        self.assertEqual([row["state"] for row in lines["acceptance"]], ["met", "met", "met"])
        self.assertEqual(lines["contribution"], "design")
        self.assertIsNone(lines["verdict"])
        self.assertEqual(report_contract.declared_contributions(text), {"design"})

    def test_original_unquoted_malformed_sentence_still_refuses(self):
        text = consultation(A_UNQUOTED, *MET3, "CONTRIBUTION: design")
        self.assertIn("malformed CONTRIBUTION", self.gaps(text, "advisor", None, 3))
        self.assertEqual(report_contract.declared_contributions(text), {"design"})

    def test_quoted_valid_and_invalid_forms_of_each_keyword_are_inert(self):
        for keyword, valid, invalid in FORMS:
            for form in quotes(valid) + quotes(invalid):
                with self.subTest(keyword=keyword, form=form):
                    if keyword == "VERDICT":
                        self.assertIn("missing VERDICT", self.gaps(form + "\n", "reviewer"))
                        text = consultation(*MET, form)
                        self.assertIsNone(report_contract.report_lines(text, "advisor", None, 2)["verdict"])
                    elif keyword == "ACCEPTANCE":
                        self.assertIn("missing ACCEPTANCE", self.gaps(consultation(form, MET[1]), "advisor", None, 2))
                    elif keyword == "CONTRIBUTION":
                        text = consultation(*MET, form, "CONTRIBUTION: design")
                        lines = report_contract.report_lines(text, "architect", None, 2)
                        self.assertEqual(lines["contribution"], "design")
                        self.assertEqual(report_contract.declared_contributions(form), set())
                    else:
                        text = consultation(*MET, form)
                        self.assertIsNone(report_contract.report_lines(text, "advisor", None, 2)["verdict"])

    def test_code_only_and_quote_only_reports_still_miss_required_lines(self):
        for form in ("```\nVERDICT: approved\n```\n", "> VERDICT: approved\n", "`VERDICT: approved`\n"):
            with self.subTest(form=form):
                self.assertIn("missing VERDICT", self.gaps(form, "reviewer"))
        for form in ("```\n" + "\n".join(MET) + "\n```\n", "> " + MET[0] + "\n> " + MET[1] + "\n"):
            with self.subTest(form=form):
                self.assertIn("missing ACCEPTANCE", self.gaps(form, "advisor", None, 2))

    def test_active_duplicates_and_n_mismatch_are_unchanged(self):
        self.assertIn("duplicate VERDICT", self.gaps("VERDICT: approved\nVERDICT: approved\n", "reviewer"))
        self.assertIn("duplicate ACCEPTANCE 1", self.gaps(consultation(*MET, MET[0]), "advisor", None, 2))
        self.assertIn("recorded 2", self.gaps(consultation("ACCEPTANCE 1/1: met — only one"), "advisor", None, 2))

    def test_active_malformed_syntax_is_unchanged(self):
        self.assertIn("malformed", self.gaps(consultation(*MET, "VERDICT: looks good"), "advisor", "security", 2))
        self.assertIn("malformed CONTRIBUTION",
                      self.gaps(consultation(*MET, "CONTRIBUTION: design",
                                             "CONTRIBUTION declared below as design."), "architect", None, 2))

    def test_real_declarations_before_and_after_fenced_examples(self):
        text = "VERDICT: approved\n```\nVERDICT: blocking\n```\n"
        self.assertEqual(report_contract.report_lines(text, "reviewer")["verdict"], "approved")
        text = "```\nVERDICT: blocking\n```\nVERDICT: approved\n"
        self.assertEqual(report_contract.report_lines(text, "reviewer")["verdict"], "approved")

    def test_quoted_contributions_add_no_exclusions_while_genuine_survive_gaps(self):
        quoted = "`CONTRIBUTION: implementation`\n> CONTRIBUTION: implementation\n```\nCONTRIBUTION: implementation\n```\n"
        self.assertEqual(report_contract.declared_contributions(quoted), set())
        mixed = quoted + "No verdict.\nCONTRIBUTION: design\n"
        self.assertEqual(report_contract.declared_contributions(mixed), {"design"})
        self.assertEqual(report_contract.declared_contributions("No verdict.\n- CONTRIBUTION: implementation\n"),
                         {"implementation"})

    def test_unmatched_backtick_is_not_a_quote_exemption(self):
        self.assertIn("malformed CONTRIBUTION",
                      self.gaps(consultation(*MET, "`CONTRIBUTION declared below as design."), "advisor", None, 2))
        self.assertEqual(report_contract.report_lines("`VERDICT: approved\n", "reviewer")["verdict"], "approved")

    def test_lazy_quote_continuation_does_not_hide_an_unmarked_declaration(self):
        text = "> quoted intro\nVERDICT: approved\n"
        self.assertEqual(report_contract.report_lines(text, "reviewer")["verdict"], "approved")

    def test_mismatched_or_shorter_fence_does_not_close(self):
        self.assertIn("missing VERDICT", self.gaps("```\nVERDICT: approved\n~~~\n", "reviewer"))
        self.assertIn("missing VERDICT", self.gaps("````\nVERDICT: approved\n```\n", "reviewer"))

    def test_unclosed_fence_hides_declarations_so_required_lines_are_missing(self):
        self.assertIn("missing VERDICT", self.gaps("```\nVERDICT: approved\n", "reviewer"))

    def test_four_space_indent_is_not_a_code_block(self):
        self.assertEqual(report_contract.report_lines("    VERDICT: approved\n", "reviewer")["verdict"], "approved")

    def test_inline_code_in_evidence_stays_active(self):
        text = consultation("ACCEPTANCE 1/2: met — see `foo`", "ACCEPTANCE 2/2: met — bar")
        lines = report_contract.report_lines(text, "advisor", None, 2)
        self.assertEqual(lines["acceptance"][0]["evidence"], "see `foo`")


if __name__ == "__main__":
    unittest.main()
