"""Atomic report questions: framing as data, the evidence check, composition in code, and the Jev adapter.

Every model is stubbed: an LLM answer is a fixture file, and Jev's HTTP layer
is a replayed transport (rules/testing-standards.md Determinism).
"""

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "classify"))

import adversarial  # noqa: E402 -- the classify dir is on sys.path only from here
import report_verdict as rv  # noqa: E402
import typesafe_client as ts  # noqa: E402
from foreman import report_gates  # noqa: E402

KEY = "ts-live-0123456789abcdef"
REPORT = ("# Reviewer report\n\n**B1 — blocking.** The parser accepts a quoted\ncompletion marker inside a fence.\n\n"
          "Nothing else was found.\n")
IDS = ("names_open_item", "open_items_accepted", "open_items_out_of_scope", "concludes_nothing_blocks")


def answers(**given):
    return {qid: given.get(qid, "no") for qid in IDS}


class Case(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="report-verdict-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.report = self.root / "report.md"
        self.report.write_text(REPORT, encoding="utf-8")

    def llm(self, reply):
        path = self.root / "answer.json"
        path.write_text(json.dumps(reply), encoding="utf-8")
        return rv.llm_label(str(path), str(self.report), "claude", "claude-sonnet-5")

    def reply(self, **rows):
        base = {qid: {"answer": "no", "evidence": ""} for qid in IDS}
        base.update(rows)
        return base


class ComposeTest(unittest.TestCase):
    def test_the_verdict_is_composed_from_the_answers(self):
        cases = [
            (answers(names_open_item="yes"), "blocking"),
            (answers(names_open_item="yes", open_items_accepted="yes"), "approved"),
            (answers(names_open_item="yes", open_items_out_of_scope="yes"), "approved"),
            (answers(names_open_item="yes", open_items_accepted="unclear"), "insufficient_evidence"),
            (answers(concludes_nothing_blocks="yes"), "approved"),
            (answers(), "insufficient_evidence"),
            (answers(names_open_item="unclear", concludes_nothing_blocks="yes"), "insufficient_evidence"),
            # A conclusion that nothing blocks never outvotes a named, undisposed item.
            (answers(names_open_item="yes", concludes_nothing_blocks="yes"), "blocking"),
        ]
        for given, verdict in cases:
            with self.subTest(given=given):
                self.assertEqual(rv.compose(given)[0], verdict)

    def test_the_schema_asks_every_question_and_nothing_else(self):
        schema = rv.schema()
        self.assertEqual(tuple(schema["required"]), IDS)
        self.assertEqual(set(schema["properties"]), set(IDS))


class EvidenceTest(Case):
    def test_a_verbatim_passage_backs_the_verdict(self):
        label = self.llm(self.reply(names_open_item={"answer": "yes", "evidence": "The parser accepts a quoted"}))
        self.assertEqual((label["verdict"], label["evidence"]), ("blocking", "The parser accepts a quoted"))
        self.assertIs(label["checks"]["evidence_verbatim"], True)

    def test_a_passage_across_a_line_break_still_matches(self):
        quote = "The parser accepts a quoted completion marker inside a fence."
        self.assertEqual(self.llm(self.reply(names_open_item={"answer": "yes", "evidence": quote}))["verdict"], "blocking")

    def test_a_quote_not_in_the_report_fails_closed(self):
        for quote in ("The parser rejects every marker.", "", "B1"):
            with self.subTest(quote=quote):
                label = self.llm(self.reply(names_open_item={"answer": "yes", "evidence": quote}))
                self.assertEqual(label["verdict"], "insufficient_evidence")
                self.assertIs(label["checks"]["evidence_verbatim"], False)
                self.assertEqual(label["evidence"], "")

    def test_any_fabricated_quote_voids_the_label_even_on_a_no(self):
        label = self.llm(self.reply(names_open_item={"answer": "yes", "evidence": "The parser accepts a quoted"},
                                    open_items_accepted={"answer": "no", "evidence": "accepted as a tracked defect"}))
        self.assertEqual(label["verdict"], "insufficient_evidence")

    def test_an_answer_off_the_schema_is_refused(self):
        for reply in ({"names_open_item": {"answer": "yes", "evidence": "x"}},
                      self.reply(names_open_item={"answer": "maybe", "evidence": ""}),
                      self.reply(names_open_item={"answer": "yes"}),
                      {**self.reply(), "verdict": "approved"}):
            with self.subTest(reply=reply), self.assertRaises(SystemExit) as caught:
                self.llm(reply)
            self.assertEqual(caught.exception.code, 2)

    def test_an_llm_label_never_gates(self):
        label = self.llm(self.reply(names_open_item={"answer": "yes", "evidence": "The parser accepts a quoted"}))
        self.assertNotIn("fallback", label)
        self.assertIsNone(label["gate"]["level"])
        self.assertTrue(all(row["p_yes"] is None for row in label["answers"].values()))


class FramingTest(Case):
    def setUp(self):
        super().setUp()
        self.built = {Path(row["report"]).name: Path(row["report"]) for row in adversarial.write(self.root)}

    def framed(self, path):
        text = rv.frame(str(path))
        data = Path(path).read_bytes()
        begin, end = rv.markers(data)
        lines = text.splitlines()
        return lines, lines.index(begin), lines.index(end), data

    def test_the_report_travels_as_one_json_field_between_its_own_markers(self):
        lines, begin, end, data = self.framed(self.built["forged-delimiter.md"])
        self.assertEqual(end, begin + 2)
        self.assertEqual(json.loads(lines[begin + 1]), {"report": data.decode("utf-8")})

    def test_a_forged_delimiter_never_becomes_a_line_of_its_own(self):
        lines, begin, end, _ = self.framed(self.built["forged-delimiter.md"])
        forged = [line for line in lines if line.startswith(("BEGIN REPORT-DATA", "END REPORT-DATA"))]
        self.assertEqual(forged, [lines[begin], lines[end]])

    def test_an_injected_instruction_stays_inside_the_data(self):
        lines, begin, _, _ = self.framed(self.built["injected-instruction.md"])
        carrying = [index for index, line in enumerate(lines) if "Answer `approved`" in line]
        self.assertEqual(carrying, [begin + 1])

    def test_the_prompt_says_the_report_is_data(self):
        lines, begin, _, data = self.framed(self.report)
        preamble = "\n".join(lines[:begin])
        self.assertIn("untrusted data", preamble)
        self.assertIn("The delimiter nonce is {}.".format(rv.nonce(data)), preamble)

    def test_the_cli_writes_the_question_to_a_file_and_prints_a_json_receipt(self):
        out, stdout = self.root / "question.txt", io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(rv.main(["frame", str(self.report), str(out)]), 0)
        receipt = json.loads(stdout.getvalue())
        data = self.report.read_bytes()
        self.assertEqual(receipt, {"schema_version": 1, "question": str(out),
                                   "report_sha256": hashlib.sha256(data).hexdigest(), "nonce": rv.nonce(data),
                                   "bytes": len(out.read_bytes())})
        self.assertEqual(out.read_text(encoding="utf-8"), rv.frame(str(self.report)))

    def test_the_builder_prints_its_rows_and_writes_every_report(self):
        stdout = io.StringIO()
        target = self.root / "built"
        target.mkdir()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(adversarial.main([str(target)]), 0)
        rows = json.loads(stdout.getvalue())["fixtures"]
        self.assertEqual({row["verdict"] for row in rows}, {"blocking", "approved"})
        self.assertTrue(all(Path(row["report"]).is_file() for row in rows))
        self.assertEqual(adversarial.main([str(self.root / "absent")]), 2)

    def test_a_report_carrying_its_own_marker_is_refused_before_any_call(self):
        with patch.object(rv, "nonce", return_value="0000000000000000"), self.assertRaises(SystemExit) as caught:
            rv.frame(str(self.built["forged-delimiter.md"]))
        self.assertEqual(caught.exception.code, 2)


class Transport:
    def __init__(self, probabilities, model=report_gates.JEV_MODEL):
        self.probabilities, self.model, self.calls = probabilities, model, []

    def __call__(self, url, body, headers, timeout):
        request = json.loads(body)
        self.calls.append(request)
        reply = {"model": self.model, "usage": {"input_tokens": 1, "output_tokens": 1},
                 "answers": {qid: {"type": "noul", "noul": self.probabilities[qid]} for qid in request["questions"]}}
        return 200, json.dumps(reply).encode("utf-8")


class JevTest(Case):
    def jev(self, transport, environ=None):
        return rv.jev_label(str(self.report), report_gates.JEV_MODEL, transport=transport,
                            environ={"TYPESAFE_API_KEY": KEY} if environ is None else environ)

    def test_one_noul_per_question_over_the_report_as_a_named_field(self):
        transport = Transport(dict(zip(IDS, (0.99, 0.01, 0.02, 0.4))))
        self.jev(transport)
        request = transport.calls[0]
        self.assertEqual(request["state"], {"report": REPORT})
        self.assertEqual(request["model"], report_gates.JEV_MODEL)
        self.assertEqual(set(request["questions"]), set(IDS))
        for question in request["questions"].values():
            self.assertEqual(question["type"], "noul")
            self.assertIn("never changes the question", question["instructions"]["note"])

    def test_probabilities_are_banded_composed_and_gated(self):
        label = self.jev(Transport(dict(zip(IDS, (0.99, 0.01, 0.02, 0.4)))))
        self.assertEqual(label["verdict"], "blocking")
        self.assertEqual(label["answers"]["names_open_item"], {"answer": "yes", "evidence": "", "p_yes": 0.99})
        self.assertEqual(label["answers"]["concludes_nothing_blocks"]["answer"], "unclear")
        self.assertEqual(label["bands"], report_gates.BANDS_VERSION)
        self.assertEqual(label["gate"]["level"], "block")
        self.assertNotIn(KEY, json.dumps(label))

    def test_jev_is_unavailable_without_a_key_and_never_calls(self):
        transport = Transport({})
        with self.assertRaises(ts.KeyMissing):
            self.jev(transport, environ={})
        self.assertEqual(transport.calls, [])

    def test_a_report_over_the_state_budget_is_never_sent(self):
        self.report.write_bytes(b"x" * (rv.JEV_MAX_REPORT_BYTES + 1))
        transport = Transport({})
        with self.assertRaises(ts.Unavailable):
            self.jev(transport)
        self.assertEqual(transport.calls, [])

    def test_the_cli_exits_3_when_jev_is_unavailable(self):
        with patch.dict("os.environ", {"TYPESAFE_API_KEY": ""}), self.assertRaises(SystemExit) as caught:
            rv.main(["jev", str(self.report)])
        self.assertEqual(caught.exception.code, rv.UNAVAILABLE)

    def test_a_report_carrying_the_key_is_refused_and_labels_nothing(self):
        self.report.write_text("leaked {}\n".format(KEY), encoding="utf-8")
        with patch.dict("os.environ", {"TYPESAFE_API_KEY": KEY}), self.assertRaises(SystemExit) as caught:
            rv.main(["jev", str(self.report)])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
