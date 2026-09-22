#!/usr/bin/env python3
"""Pull a classifier's answer out of each vendor's envelope, and stamp the label.

Usage:
    extract-answer.py claude <raw> <answer>
    extract-answer.py grok   <raw> <answer>
    extract-answer.py label  <answer> <report> <prompt> <agent> <model>

`claude` and `grok` read that CLI's raw output and write the bare answer object
to <answer>. Codex needs no mode here: `--output-last-message` already writes
the bare object.

`label` checks the answer against the enum -- the guarantee every adapter shares,
whatever its vendor's own schema enforcement did -- and prints the label with the
report hash, question hash, agent and model it came from.

Exit 0 on success. Exit 1 when a vendor envelope carries no usable answer. Exit
2 when the answer falls outside the enum. Diagnostics go to stderr.
"""

import hashlib
import json
import sys
from typing import NoReturn

VERDICTS = ("blocking", "approved", "insufficient_evidence")


def fail(message, code=1) -> NoReturn:
    sys.stderr.write("extract-answer: {}\n".format(message))
    raise SystemExit(code)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        fail("cannot read {}: {}".format(path, exc))


def write_json(path, value):
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
    except OSError as exc:
        fail("cannot write {}: {}".format(path, exc))


def from_claude(raw):
    """The last `result` event's `structured_output`, unless the run errored."""
    events = raw if isinstance(raw, list) else [raw]
    results = [event for event in events if isinstance(event, dict) and event.get("type") == "result"]
    if not results:
        fail("claude emitted no result event")
    last = results[-1]
    if last.get("is_error"):
        fail("claude reported an error: {}".format(last.get("result") or last.get("subtype")))
    answer = last.get("structured_output")
    if not isinstance(answer, dict):
        fail("claude's result carries no structured_output")
    return answer


def from_grok(raw):
    """Exactly one JSON object in `text`.

    One turn gives one object. More than one means the model kept going, and a
    verdict picked out of several is not the answer to the question asked.
    """
    text = raw.get("text", "") if isinstance(raw, dict) else ""
    text = text.strip()
    if not text:
        fail("grok returned no text")
    try:
        answer, end = json.JSONDecoder().raw_decode(text)
    except ValueError:
        fail("grok's text is not a JSON answer")
    if text[end:].strip():
        fail("grok returned more than one answer")
    return answer


def digest(path):
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError as exc:
        fail("cannot hash {}: {}".format(path, exc))


def label(answer_path, report, prompt, agent, model):
    answer = read_json(answer_path)
    if not isinstance(answer, dict) or answer.get("verdict") not in VERDICTS:
        fail("the answer is outside the schema's enum", code=2)
    return {"schema_version": 1, "report": report, "sha256": digest(report),
            "question": digest(prompt), "agent": agent, "model": model,
            "verdict": answer["verdict"], "evidence": answer.get("evidence", "")}


def main(argv):
    if len(argv) == 3 and argv[0] in ("claude", "grok"):
        extract = from_claude if argv[0] == "claude" else from_grok
        write_json(argv[2], extract(read_json(argv[1])))
        return 0
    if len(argv) == 6 and argv[0] == "label":
        print(json.dumps(label(*argv[1:6]), sort_keys=True))
        return 0
    fail("usage: extract-answer.py claude|grok <raw> <answer> | label <answer> <report> <prompt> <agent> <model>", code=2)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
