#!/usr/bin/env python3
"""Pull a classifier's answer out of each vendor's envelope.

Usage:
    extract-answer.py claude <raw> <answer>
    extract-answer.py grok   <raw> <answer>

`claude` and `grok` read that CLI's raw output and write the bare answer object
to <answer>. Codex needs no mode here: `--output-last-message` already writes
the bare object. Checking the answer and stamping the label is
`report_verdict.py label`, the one check every adapter shares.

Exit 0 on success. Exit 1 when a vendor envelope carries no usable answer.
Exit 2 on a usage error. Diagnostics go to stderr.
"""

import json
import sys
from typing import NoReturn


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


def main(argv):
    if len(argv) == 3 and argv[0] in ("claude", "grok"):
        extract = from_claude if argv[0] == "claude" else from_grok
        write_json(argv[2], extract(read_json(argv[1])))
        return 0
    fail("usage: extract-answer.py claude|grok <raw> <answer>", code=2)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
