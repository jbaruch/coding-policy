#!/usr/bin/env python3
"""Fail when a tracked text file carries a leftover merge-conflict marker.

A hand-resolved conflict can leave a marker line behind and still pass every
other check: a diff3 base marker shipped in the 0.3.345 CHANGELOG entry that
way (#668). This check reads every file `git ls-files` tracks and refuses a
line that opens with a conflict marker.

A marker line is 7 of `<`, `|` or `>` at column 0, followed by a space or the
end of the line. A bare `=======` is never flagged on its own: Markdown uses it
as a setext heading underline, and every conflict git writes carries at least
one of the other three markers. Files holding a NUL byte are binary and skipped.

Usage:  scripts/check-conflict-markers.py [repo-dir]
  repo-dir  The git checkout to scan. Defaults to this script's repository.
stdout: one JSON object --
          {"clean": true|false, "files_scanned": N,
           "markers": [{"path": "<file>", "line": n, "text": "<marker line>"}]}
stderr: the actionable diagnostic when markers are found or on a setup error.
Exit:   0 clean, 1 markers found, 2 setup error (not a git checkout, git
        absent or failing, a tracked file that cannot be read).
"""

import json
import re
import subprocess
import sys
from pathlib import Path

#: Tracked paths allowed to carry marker lines, by exact repo-relative path.
#: Empty: a fixture that needs a marker builds it at run time.
EXEMPT = frozenset()

MARKER = re.compile(r"^(?:<{7}|\|{7}|>{7})(?: |$)")


def tracked(repo):
    try:
        out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, check=True).stdout
    except FileNotFoundError:
        raise SystemExit(setup_error("git not found on PATH -- install git to run this check"))
    except subprocess.CalledProcessError as err:
        detail = err.stderr.decode("utf-8", "replace").strip()
        raise SystemExit(setup_error("git ls-files failed in {}: {} -- run the check from a git checkout".format(repo, detail)))
    return [name for name in out.decode("utf-8", "surrogateescape").split("\0") if name]


def setup_error(message):
    print("check-conflict-markers: " + message, file=sys.stderr)
    return 2


def scan(repo):
    markers, scanned = [], 0
    for name in tracked(repo):
        if name in EXEMPT:
            continue
        path = repo / name
        if path.is_symlink() or not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError as err:
            raise SystemExit(setup_error("cannot read tracked file {}: {} -- restore read access and rerun".format(name, err.strerror or err)))
        if b"\0" in data:
            continue
        scanned += 1
        for number, line in enumerate(data.decode("utf-8", "replace").splitlines(), start=1):
            if MARKER.match(line):
                markers.append({"path": name, "line": number, "text": line})
    return scanned, markers


def main(argv):
    if len(argv) > 2:
        return setup_error("usage: check-conflict-markers.py [repo-dir]")
    repo = Path(argv[1]) if len(argv) == 2 else Path(__file__).resolve().parents[1]
    scanned, markers = scan(repo)
    print(json.dumps({"clean": not markers, "files_scanned": scanned, "markers": markers}))
    if markers:
        where = ", ".join("{}:{}".format(m["path"], m["line"]) for m in markers)
        print("check-conflict-markers: leftover merge-conflict marker(s) at {} -- finish resolving the conflict "
              "and delete the marker lines".format(where), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
