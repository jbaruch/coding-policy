#!/usr/bin/env python3
"""Atomic questions about one worker report, and the verdict composed from them in code.

The questions live in `report-questions.json`: does the report name an open
item, does it accept every such item, does it place every such item out of
its own scope, does its conclusion say nothing blocks. A model answers each
one; `compose` turns the answers into `blocking`, `approved` or
`insufficient_evidence`. The model never sees the policy that combines them.

Deterministic checks run before any semantic answer is used:
  - framing: the report travels as the `report` field of a JSON object between
    marker lines carrying a nonce derived from the report's own sha256, so no
    report can forge its own delimiter; a report that contains its marker is
    refused before any call.
  - evidence: every quote an LLM adapter returns must be a passage of the
    report (whitespace-normalized, at least MIN_EVIDENCE_CHARS non-space
    characters). One quote that is not voids the answer: the label becomes
    `insufficient_evidence`, never a verdict a hallucinated reason backs.

Usage:
  report_verdict.py schema <out>
      Write the JSON schema LLM adapters constrain their answer to.
  report_verdict.py frame <report> <out>
      Write the whole LLM question (prompt, questions, framed report) to <out>
      and print a JSON receipt: {"schema_version": 1, "question": <out>,
      "report_sha256", "nonce", "bytes"}. Exit 2 when the report contains its
      own delimiter or <out> cannot be written.
  report_verdict.py label <answer> <report> <agent> <model> [--fallback <reason>] [--as <path>]
      Check an LLM answer and print the label. Exit 2 when the answer is off
      the schema.
  report_verdict.py jev <report> [--model <id>] [--as <path>]
      Ask Jev (TypeSafe System One, one Noul per question) and print the label.
      Exit 3 when Jev is unavailable -- key unset, service down, report over
      the state budget, answer off contract -- with the reason as the last
      stderr line; the caller decides whether to fall back.

Label (stdout, one JSON object):
  {"schema_version": 2, "report", "sha256", "question", "questions_version",
   "agent", "model", "verdict", "evidence", "reason",
   "answers": {<id>: {"answer": "yes"|"no"|"unclear", "evidence", "p_yes"}},
   "checks": {"evidence_verbatim": true|false|null}, "bands", "fallback",
   "gate": {"level": "block"|"reread"|null, "reason"}}
`p_yes` and `bands` are set for Jev only; `gate` is what foreman/report_gates.py
decides for this label, and the owner recomputes it when recording.

`--as` names the report path a label records when <report> is a snapshot of
it: the answers, the evidence check and the sha256 all read the snapshot's
bytes once, so a report rewritten mid-run never pairs new bytes with old
answers.

Exit 0 on a label, `insufficient_evidence` included. Exit 2 on a usage error
or an off-schema answer. Exit 3 as above.
"""

import hashlib
import json
import sys
from pathlib import Path
from typing import NoReturn

HERE = Path(__file__).resolve().parent
# The directory's own name is never imported: an installed or copied plugin may
# live under any name (#592), so siblings resolve from HERE itself.
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import typesafe_client  # noqa: E402 -- HERE and the skill dir are on sys.path only from here
from foreman import report_gates  # noqa: E402

SCHEMA_VERSION = 2
QUESTIONS = HERE / "report-questions.json"
PROMPT = HERE / "report-verdict.prompt.md"
ANSWERS = ("yes", "no", "unclear")
VERDICTS = ("blocking", "approved", "insufficient_evidence")
#: A quote shorter than this proves nothing: "B1" is a substring of most reports.
MIN_EVIDENCE_CHARS = 8
#: Jev reads 32k tokens of state plus the longest question; a report past this
#: is not sent, and the caller falls back (https://docs.typesafe.ai/models).
JEV_MAX_REPORT_BYTES = 96_000
MARKER = "REPORT-DATA"
UNAVAILABLE = 3


def fail(message, code=2) -> NoReturn:
    sys.stderr.write("report_verdict: {}\n".format(message))
    raise SystemExit(code)


def read_bytes(path):
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        fail("cannot read {}: {}".format(path, exc))


def questions():
    try:
        data = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        fail("cannot read the questions at {}: {}".format(QUESTIONS, exc))
    return data


def question_ids():
    return [row["id"] for row in questions()["questions"]]


def question_hash():
    digest = hashlib.sha256()
    digest.update(read_bytes(PROMPT))
    digest.update(b"\0")
    digest.update(read_bytes(QUESTIONS))
    return digest.hexdigest()


def nonce(data):
    return hashlib.sha256(data).hexdigest()[:16]


def markers(data):
    tag = nonce(data)
    return "BEGIN {} {}".format(MARKER, tag), "END {} {}".format(MARKER, tag)


def compose(answers):
    """(verdict, reason, deciding question) from one answer per atomic question."""
    names = answers["names_open_item"]
    accepted, scoped = answers["open_items_accepted"], answers["open_items_out_of_scope"]
    if names == "yes":
        if accepted == "yes":
            return "approved", "every open item is accepted for this shipment", "open_items_accepted"
        if scoped == "yes":
            return "approved", "every open item lies outside this report's scope", "open_items_out_of_scope"
        if accepted == "no" and scoped == "no":
            return "blocking", "an open item is neither accepted nor out of scope", "names_open_item"
        return "insufficient_evidence", "whether the open item is disposed of is unclear", None
    if names == "no":
        if answers["concludes_nothing_blocks"] == "yes":
            return "approved", "no open item, and the conclusion says nothing blocks", "concludes_nothing_blocks"
        return "insufficient_evidence", "no open item named, and no conclusion that nothing blocks", None
    return "insufficient_evidence", "whether the report names an open item is unclear", None


def normalized(value):
    return " ".join(value.split())


def verbatim(quote, report_text):
    """True when the quote is a passage of the report, whitespace aside."""
    flat = normalized(quote)
    return len(flat.replace(" ", "")) >= MIN_EVIDENCE_CHARS and flat in normalized(report_text)


def schema():
    item = {"type": "object", "additionalProperties": False,
            "properties": {"answer": {"type": "string", "enum": list(ANSWERS)},
                           "evidence": {"type": "string", "description": "A passage of the report, verbatim, for a yes; empty otherwise."}},
            "required": ["answer", "evidence"]}
    ids = question_ids()
    return {"type": "object", "additionalProperties": False,
            "properties": {qid: item for qid in ids}, "required": ids}


def frame(report_path):
    data = read_bytes(report_path)
    begin, end = markers(data)
    text = data.decode("utf-8", errors="replace")
    if "{} {}".format(MARKER, nonce(data)) in text:
        fail("{} contains its own delimiter; read it in full, it was not classified".format(report_path))
    spec = questions()
    lines = [read_bytes(PROMPT).decode("utf-8").rstrip("\n"), "", "Questions:", ""]
    for row in spec["questions"]:
        lines += ["- `{}`: {}".format(row["id"], row["instructions"]),
                  "  yes: {}".format(row["criteria"]["true"]), "  no: {}".format(row["criteria"]["false"])]
    lines += ["", spec["data_note"], "", "The delimiter nonce is {}.".format(nonce(data)), "",
              begin, json.dumps({"report": text}, ensure_ascii=False), end, ""]
    return "\n".join(lines)


def base_label(data, shown, agent, model):
    """The label's receipt, hashed over the exact bytes the answers were about."""
    return {"schema_version": SCHEMA_VERSION, "report": shown,
            "sha256": hashlib.sha256(data).hexdigest(),
            "question": question_hash(), "questions_version": questions()["version"],
            "agent": agent, "model": model, "bands": None, "fallback": None}


def finish(label, answers, checks, verdict, reason, deciding):
    label.update(answers=answers, checks=checks, verdict=verdict, reason=reason,
                 evidence=answers[deciding]["evidence"] if deciding else "")
    gate = report_gates.decide(label)
    label["gate"] = {"level": gate["level"], "reason": gate["reason"]}
    return label


def llm_label(answer_path, report_path, agent, model, fallback=None, shown=None):
    try:
        answer = json.loads(Path(answer_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        fail("cannot read the answer {}: {}".format(answer_path, exc))
    ids = question_ids()
    if not isinstance(answer, dict) or set(answer) != set(ids):
        fail("the answer carries exactly one entry per question: {}".format(", ".join(ids)))
    for qid in ids:
        row = answer[qid]
        if (not isinstance(row, dict) or set(row) != {"answer", "evidence"}
                or row["answer"] not in ANSWERS or not isinstance(row["evidence"], str)):
            fail("the answer to {} is outside the schema".format(qid))
    data = read_bytes(report_path)
    label = base_label(data, shown or report_path, agent, model)
    if fallback:
        label["fallback"] = {"from": "jev", "reason": fallback}
    text = data.decode("utf-8", errors="replace")
    answers = {qid: {"answer": answer[qid]["answer"], "evidence": answer[qid]["evidence"], "p_yes": None} for qid in ids}
    for qid in ids:
        quote = answers[qid]["evidence"]
        if (answers[qid]["answer"] == "yes" or quote) and not verbatim(quote, text):
            return finish(label, answers, {"evidence_verbatim": False}, "insufficient_evidence",
                          "the evidence for {} is not a passage of the report".format(qid), None)
    return finish(label, answers, {"evidence_verbatim": True}, *compose({qid: row["answer"] for qid, row in answers.items()}))


def jev_request():
    spec = questions()
    return {row["id"]: {"type": "noul",
                        "instructions": {"note": spec["data_note"], "question": row["instructions"]},
                        "criteria": row["criteria"]}
            for row in spec["questions"]}


def jev_label(report_path, model, transport=None, environ=None, shown=None):
    """The Jev label, or `typesafe_client.TypeSafeError` for the caller to report."""
    data = read_bytes(report_path)
    if len(data) > JEV_MAX_REPORT_BYTES:
        raise typesafe_client.Unavailable("the report is {} bytes, over Jev's {}-byte state budget".format(
            len(data), JEV_MAX_REPORT_BYTES))
    key = typesafe_client.api_key(environ)
    response = typesafe_client.system_one({"report": data.decode("utf-8", errors="replace")}, jev_request(),
                                          model, key=key, transport=transport)
    probabilities = typesafe_client.nouls(response)
    label = base_label(data, shown or report_path, "jev", model)
    label["bands"] = report_gates.BANDS_VERSION
    answers = {qid: {"answer": report_gates.band(p), "evidence": "", "p_yes": p} for qid, p in probabilities.items()}
    return finish(label, answers, {"evidence_verbatim": None},
                  *compose({qid: row["answer"] for qid, row in answers.items()}))


def flags(rest, allowed):
    """`--name value` pairs, each named in `allowed` at most once."""
    if len(rest) % 2:
        fail("every option takes one value")
    options = {}
    for name, value in zip(rest[::2], rest[1::2]):
        if name not in allowed or name in options:
            fail("unknown or repeated argument '{}'".format(name))
        options[name] = value
    return options


def main(argv):
    if len(argv) == 2 and argv[0] == "schema":
        try:
            Path(argv[1]).write_text(json.dumps(schema()), encoding="utf-8")
        except OSError as exc:
            fail("cannot write the schema to {}: {}".format(argv[1], exc))
        return 0
    if len(argv) == 3 and argv[0] == "frame":
        prompt = frame(argv[1])
        try:
            Path(argv[2]).write_text(prompt, encoding="utf-8")
        except OSError as exc:
            fail("cannot write the framed question to {}: {}".format(argv[2], exc))
        data = read_bytes(argv[1])
        print(json.dumps({"schema_version": 1, "question": argv[2], "report_sha256": hashlib.sha256(data).hexdigest(),
                          "nonce": nonce(data), "bytes": len(prompt.encode("utf-8"))}, sort_keys=True))
        return 0
    if argv and argv[0] == "label" and len(argv) >= 5:
        options = flags(argv[5:], {"--fallback", "--as"})
        print(json.dumps(llm_label(argv[1], argv[2], argv[3], argv[4], options.get("--fallback"),
                                   options.get("--as")), sort_keys=True))
        return 0
    if argv and argv[0] == "jev" and len(argv) >= 2:
        options = flags(argv[2:], {"--model", "--as"})
        try:
            label = jev_label(argv[1], options.get("--model", report_gates.JEV_MODEL), shown=options.get("--as"))
        except typesafe_client.InvalidRequest as exc:
            # A refused request -- the report carries the key -- must not reach the fallback vendor.
            fail("Jev refused the request, no fallback: {}".format(exc))
        except typesafe_client.TypeSafeError as exc:
            fail("Jev unavailable: {}".format(exc), UNAVAILABLE)
        print(json.dumps(label, sort_keys=True))
        return 0
    fail("usage: report_verdict.py schema <out> | frame <report> <out> | "
         "label <answer> <report> <agent> <model> [--fallback <reason>] [--as <path>] | "
         "jev <report> [--model <id>] [--as <path>]")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
