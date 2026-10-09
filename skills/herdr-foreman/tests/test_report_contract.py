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
        for line in ("- VERDICT: approved", "* VERDICT: approved",
                     "   VERDICT: approved", "VERDICT: approved`"):
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


FENCE = "```"
#: The shape of the 729 architecture report's fenced example (#737), placeholders included.
PLACEHOLDER = ('TRIGGER_DECLARATION: {"repo":"/absolute/canonical/repo","base_revision":"<full base>",'
               '"head_revision":"<full head>","path":"/absolute/external/triggers.json","sha256":"<artifact SHA-256>"}')
#: A well-formed four-key line: recognised as a binding when standalone, never when quoted.
FOUR_KEY = ('TRIGGER_DECLARATION: {"repo":"/absolute/target/repo","base_revision":"' + "a" * 40 +
            '","path":"/absolute/external/triggers.json","sha256":"' + "b" * 64 + '"}')
#: The prose lines of the 737 advisor report that the gate refused (#737).
PROSE = ("`VERDICT`.", "`CRITERION`, and `TRIGGER_DECLARATION`:",
         "`ACCEPTANCE` lines `met`, `contribution: design`, `verdict: null`. The only")
#: Every way an example stays out of the contract: fenced, quoted, indented, inline code.
QUOTED = {
    "fenced text": lambda line: "{0}text\n{1}\n{0}".format(FENCE, line),
    "fenced json": lambda line: "{0}json\n{1}\n{0}".format(FENCE, line),
    "fenced no info": lambda line: "{0}\n{1}\n{0}".format(FENCE, line),
    "fenced tildes": lambda line: "~~~\n{}\n~~~".format(line),
    "fenced longer closer": lambda line: "{0}\n{1}\n{0}`".format(FENCE, line),
    "fenced indented opener": lambda line: "   {0}\n{1}\n   {0}".format(FENCE, line),
    "blockquote": lambda line: "> " + line,
    "nested blockquote": lambda line: "- > " + line,
    "indented four": lambda line: "    " + line,
    "indented tab": lambda line: "\t" + line,
    "inline code then prose": lambda line: "`" + line + "` is the form.",
    "inline code whole line": lambda line: "`" + line + "`",
    "listed inline code whole line": lambda line: "  - `" + line + "`  ",
    "bullet list fence": lambda line: "- {0}text\n  {1}\n  {0}".format(FENCE, line),
    "ordered list fence": lambda line: "1. {0}text\n   {1}\n   {0}".format(FENCE, line),
    "wide ordered list fence": lambda line: "10. {0}\n    {1}\n    {0}".format(FENCE, line),
    "nested list tildes": lambda line: "- a\n  - ~~~\n    {}\n    ~~~".format(line),
}
EXAMPLES = ("VERDICT: approved", "VERDICT: blocking", "ACCEPTANCE 1/2: met — x", "ACCEPTANCE 9/9: met — x",
            "CONTRIBUTION: design", "CRITERION 1: restated", "CONTRIBUTION: lots", "VERDICT: looks good")


class QuotedExampleTest(unittest.TestCase):
    """A report explains the contract without the explanation becoming the contract (#737)."""

    def parse(self, *lines, role="architect", specialty=None):
        return report_contract.report_lines("\n".join(("Findings.",) + lines) + "\n", role, specialty, 2)

    def gaps(self, *lines, role="architect", specialty=None):
        with self.assertRaises(UsageError) as caught:
            self.parse(*lines, role=role, specialty=specialty)
        return " | ".join(caught.exception.details["gaps"])

    def test_every_quoted_example_of_every_keyword_is_inert(self):
        for form, quote in QUOTED.items():
            for example in EXAMPLES:
                with self.subTest(form=form, example=example):
                    lines = self.parse(*MET, quote(example))
                    self.assertEqual((lines["verdict"], lines["contribution"]), (None, None))
                    self.assertEqual([row["k"] for row in lines["acceptance"]], [1, 2])

    def test_quoted_examples_beside_one_operative_line_leave_that_line_the_only_one(self):
        for form, quote in QUOTED.items():
            with self.subTest(form=form):
                lines = self.parse(*MET, "CONTRIBUTION: none", quote("CONTRIBUTION: design"), quote("ACCEPTANCE 1/2: unmet — x"))
                self.assertEqual(lines["contribution"], "none")
                self.assertEqual([row["state"] for row in lines["acceptance"]], ["met", "met"])
            with self.subTest(form=form, role="reviewer"):
                text = "Notes.\n" + quote("VERDICT: blocking") + "\nVERDICT: approved\n"
                self.assertEqual(report_contract.report_lines(text, "reviewer")["verdict"], "approved")

    def test_prose_opening_with_an_inline_keyword_is_not_a_candidate(self):
        # The 737 advisor report: three lines the gate refused as malformed VERDICT / extra CRITERION / malformed ACCEPTANCE.
        lines = self.parse(*MET, *PROSE, "VERDICT: approved", role="advisor", specialty="security")
        self.assertEqual((lines["verdict"], len(lines["acceptance"])), ("approved", 2))
        for line in PROSE:
            with self.subTest(line=line):
                self.assertEqual(self.parse(*MET, line)["acceptance"][0]["state"], "met")

    def test_the_strictness_of_operative_lines_is_unchanged_beside_quoted_ones(self):
        quoted = QUOTED["fenced text"]("VERDICT: approved\nCONTRIBUTION: none")
        self.assertIn("duplicate ACCEPTANCE 1", self.gaps(*MET, MET[0], quoted))
        self.assertIn("extra VERDICT", self.gaps(*MET, "VERDICT: approved", quoted))
        self.assertIn("malformed CONTRIBUTION", self.gaps(*MET, "CONTRIBUTION: lots", quoted))
        self.assertIn("duplicate CONTRIBUTION", self.gaps(*MET, "CONTRIBUTION: none", "CONTRIBUTION: none", quoted))
        self.assertIn("extra CRITERION", self.gaps(*MET, "CRITERION 1: x", quoted))
        self.assertIn("recorded 2", self.gaps("ACCEPTANCE 1/1: met — x", quoted))
        self.assertIn("missing ACCEPTANCE 2", self.gaps(MET[0], quoted))
        self.assertIn("malformed", self.gaps(*MET, "VERDICT approved", quoted))
        self.assertIn("duplicate VERDICT", self.gaps(*MET, "VERDICT: approved", "VERDICT: blocking", quoted,
                                                     role="advisor", specialty="security"))

    def test_an_unclosed_list_fence_hides_what_follows(self):
        text = "- {}text\n  VERDICT: blocking\nVERDICT: approved\n".format(FENCE)
        with self.assertRaises(UsageError) as caught:
            report_contract.report_lines(text, "reviewer")
        self.assertIn("missing VERDICT line", caught.exception.details["gaps"])

    def test_a_list_fence_example_does_not_hide_the_real_line_after_it(self):
        text = "- {0}text\n  VERDICT: blocking\n  {0}\nVERDICT: approved\n".format(FENCE)
        self.assertEqual(report_contract.report_lines(text, "reviewer")["verdict"], "approved")

    def test_operative_lines_after_a_closed_fence_count(self):
        text = "Body.\n{0}\nVERDICT: blocking\n{0}\nVERDICT: approved\n".format(FENCE)
        self.assertEqual(report_contract.report_lines(text, "tester")["verdict"], "approved")

    def test_a_fence_is_closed_only_by_a_matching_unquoted_delimiter(self):
        for closer in ("~~~", "> " + FENCE, FENCE + " text", "``"):
            with self.subTest(closer=closer):
                text = "{0}\nVERDICT: blocking\n{1}\nVERDICT: blocking\n{0}\nVERDICT: approved\n".format(FENCE, closer)
                self.assertEqual(report_contract.report_lines(text, "tester")["verdict"], "approved")

    def test_a_quoted_delimiter_does_not_open_a_fence(self):
        text = "> {0}\nVERDICT: approved\n".format(FENCE)
        self.assertEqual(report_contract.report_lines(text, "tester")["verdict"], "approved")

    def test_inline_triple_backticks_do_not_open_a_fence(self):
        text = "```not a fence``` here\nVERDICT: approved\n"
        self.assertEqual(report_contract.report_lines(text, "tester")["verdict"], "approved")

    def test_an_unclosed_fence_hides_what_follows_and_the_refusal_says_so(self):
        text = "Body.\n{}text\nVERDICT: approved\n".format(FENCE)
        with self.assertRaises(UsageError) as caught:
            report_contract.report_lines(text, "reviewer")
        gaps = caught.exception.details["gaps"]
        self.assertIn("missing VERDICT line", gaps)
        self.assertIn("the code fence opened on line 2 is never closed, so every line after it is illustrative", gaps)

    def test_contract_lines_before_an_unclosed_fence_still_count(self):
        text = "VERDICT: approved\n{}text\nillustration\n".format(FENCE)
        self.assertEqual(report_contract.report_lines(text, "reviewer")["verdict"], "approved")

    def test_a_contribution_declared_only_in_an_example_still_excludes_on_the_refusal_path(self):
        # Over-reading adds an exclusion and never clears one, so this scan stays tolerant of quoting.
        for form, quote in QUOTED.items():
            if form == "inline code then prose":
                continue  # a sentence about the line, not the line
            with self.subTest(form=form):
                self.assertEqual(report_contract.declared_contributions("No lines.\n" + quote("CONTRIBUTION: design") + "\n"),
                                 {"design"})

    def test_brief_criteria_keep_their_tolerant_scan(self):
        text = brief().replace("CRITERION 1:", "```CRITERION 1:")
        self.assertEqual(report_contract.brief_criteria(text), 2)


class TriggerBindingTest(unittest.TestCase):
    """Only a standalone TRIGGER_DECLARATION line is operative evidence (#737)."""

    def test_a_standalone_line_is_the_one_binding(self):
        text = "\n".join(("Findings.",) + MET + (FOUR_KEY,)) + "\n"
        self.assertEqual(report_contract.trigger_bindings(text), [FOUR_KEY[len("TRIGGER_DECLARATION: "):]])

    def test_quoted_copies_are_no_binding_whatever_they_contain(self):
        for form, quote in QUOTED.items():
            for line in (PLACEHOLDER, FOUR_KEY, "TRIGGER_DECLARATION: not-json", "TRIGGER_DECLARATION: {}"):
                with self.subTest(form=form, line=line[:40]):
                    self.assertEqual(report_contract.trigger_bindings("\n".join(MET) + "\n" + quote(line) + "\n"), [])

    def test_list_prefixed_indented_and_inline_lines_are_no_binding(self):
        for line in ("- " + FOUR_KEY, "* " + FOUR_KEY, " " + FOUR_KEY, "`" + FOUR_KEY + "`"):
            with self.subTest(line=line[:20]):
                self.assertEqual(report_contract.trigger_bindings(line + "\n"), [])

    def test_an_example_beside_an_actual_line_leaves_one_binding(self):
        for form, quote in QUOTED.items():
            with self.subTest(form=form):
                text = "{}\n{}\n".format(quote(PLACEHOLDER), FOUR_KEY)
                self.assertEqual(report_contract.trigger_bindings(text), [FOUR_KEY[len("TRIGGER_DECLARATION: "):]])

    def test_two_actual_lines_stay_two_bindings_for_the_owner_to_refuse(self):
        text = "{0}\n{1}\n{0}\n".format(FOUR_KEY, QUOTED["fenced text"](PLACEHOLDER))
        self.assertEqual(len(report_contract.trigger_bindings(text)), 2)

    def test_a_malformed_actual_line_is_still_recognised_for_the_owner_to_refuse(self):
        self.assertEqual(report_contract.trigger_bindings("TRIGGER_DECLARATION: not-json\n"), ["not-json"])

    def test_the_prefix_must_be_exact(self):
        for line in ("TRIGGER_DECLARATION:{}", "TRIGGER_DECLARATION {}", "trigger_declaration: {}"):
            with self.subTest(line=line):
                self.assertEqual(report_contract.trigger_bindings(line + "\n"), [])


if __name__ == "__main__":
    unittest.main()
