#!/usr/bin/env python3
"""No-input Herdr transport fixture for public native-error wait regressions."""

import json
import os
from pathlib import Path
import sys


def main():
    args = sys.argv[1:]
    data = json.loads(Path(os.environ["CP724_PANE"]).read_text())
    with Path(os.environ["CP724_CALLS"]).open("a") as stream:
        stream.write(json.dumps(args) + "\n")
    command = " ".join(args[:2])
    if command == data.get("error_on"):
        print(json.dumps({"error": {"code": "fixture_tool_fault"}}), file=sys.stderr)
        return 1
    if command in ("agent get", "pane get"):
        key = "agent" if command == "agent get" else "pane"
        print(json.dumps({"result": {key: data["pane"]}}))
    elif command in ("agent read", "pane read"):
        print(data["ansi"] if "ansi" in args else data["visible"])
    elif command == "pane wait-output":
        print(json.dumps({"error": {"code": "timeout"}}), file=sys.stderr)
        return 1
    else:
        print(json.dumps({"error": {"code": "fixture_forbids_input"}}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
