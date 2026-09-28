"""The one check for text a brief or a report marker must carry intact (#578).

A report path travels into a brief and back out as the worker's
`REPORT: <path>` line; a policy path, a slice glob or a declared gate renders
into a brief's Markdown, one value per line. A character in
UNRENDERABLE_CATEGORIES breaks that line, reorders or hides text, or fails path
resolution. Every caller asks this module, from Python directly and from shell
through the command form below, so the set cannot drift between copies.

Contract (command form, `python3 -m foreman.renderable [--code-span]` with the
skill directory on PYTHONPATH):
  stdin : one JSON value, a string or an array of strings.
  stdout: one JSON object, {"renderable": bool, "offenders": ["<char>", ...]}.
          `offenders` lists each distinct unrenderable character, sorted; a
          value that is not a string or an array of strings is not renderable
          and names no offenders.
  exit  : 0 renderable, 1 not renderable, 2 usage error or stdin not JSON.
  `--code-span` also refuses a backtick, which closes the code span a value
  renders in.
"""

import json
import sys
import unicodedata

#: Unicode general categories no rendered value may carry: controls (Cc: C0,
#: DEL and C1), format characters such as bidi overrides (Cf), surrogates (Cs),
#: line and paragraph separators (Zl, Zp: U+2028, U+2029), private-use (Co) and
#: unassigned (Cn) code points. A class, not a list of characters, so a new
#: separator is covered.
UNRENDERABLE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp", "Co", "Cn"})


def offenders(value, code_span=False):
    """The distinct characters in `value` a rendered line cannot carry, sorted."""
    return sorted({char for char in value
                   if unicodedata.category(char) in UNRENDERABLE_CATEGORIES
                   or (code_span and char == "`")})


def renderable(value, code_span=False):
    """True when `value` is a string that stays intact on one rendered line."""
    return isinstance(value, str) and not offenders(value, code_span)


def main(argv, stdin, stdout, stderr):
    args = list(argv)
    code_span = "--code-span" in args
    if [arg for arg in args if arg != "--code-span"] or args.count("--code-span") > 1:
        stderr.write("renderable: usage: python3 -m foreman.renderable [--code-span] < value.json\n")
        return 2
    try:
        value = json.load(stdin)
    except json.JSONDecodeError as exc:
        stderr.write("renderable: stdin is not JSON ({}) -- pipe one JSON string or array of strings.\n".format(exc))
        return 2
    values = value if isinstance(value, list) else [value]
    if not all(isinstance(item, str) for item in values):
        stdout.write(json.dumps({"renderable": False, "offenders": []}) + "\n")
        return 1
    found = sorted({char for item in values for char in offenders(item, code_span)})
    stdout.write(json.dumps({"renderable": not found, "offenders": found}) + "\n")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdin, sys.stdout, sys.stderr))
