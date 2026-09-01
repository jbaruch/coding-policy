"""Pure text -> dict parsers for each agent's usage output.

Every function here is a pure function of the pane text it is handed. Nothing
in this module reads the clock, the filesystem, the environment, or a
subprocess -- that is what makes the parsers testable against inline fixture
strings and what keeps `measured_at` a CLI-layer concern.

Every parser returns the same shape::

    {
      "windows": {
        "<label>": {
          "used_pct": float,        # 0..100, how much of the window is spent
          "remaining_pct": float,   # 100 - used_pct
          "resets": str | None,     # verbatim reset text, never normalized
        },
        ...
      },
      "credits": float | None,      # informational, grok only
    }

`resets` stays a verbatim string on purpose: each agent prints a different
human date format in a different timezone, and turning those into timestamps
is reasoning, not parsing.
"""

import re

from .errors import ParseError

# --- claude -----------------------------------------------------------------

# A window header is a line that is exactly "Current session" or
# "Current week (<something>)". The "(<something>)" form covers both
# "(all models)" and a per-model line such as "(Fable)".
_CLAUDE_HEADER_RE = re.compile(r"^(Current session|Current week \(.+\))$")
# "████        8% used" -- the bar glyphs are decoration, only the number counts.
_CLAUDE_USED_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s+used\b")
_CLAUDE_RESETS_RE = re.compile(r"^Resets\s+(.+)$")

# --- codex ------------------------------------------------------------------

# Codex draws a box; strip the frame before matching anything.
_CODEX_FRAME = "│┃|"  # box-drawing light/heavy vertical, ASCII pipe
# "Weekly limit:  [████░░░] 87% left (resets 17:26 on 7 Sep)"
# The bar is optional so a narrow terminal that drops it still parses.
_CODEX_LIMIT_RE = re.compile(
    r"^(?P<label>.+?)\s+limit:\s*"
    r"(?:\[[^\]]*\]\s*)?"
    r"(?P<pct>\d+(?:\.\d+)?)\s*%\s*left"
    r"(?:\s*\(resets\s+(?P<resets>[^)]*)\))?"
)
# "GPT-5.3-Codex-Spark limit:" -- a section header; every limit row after it
# belongs to that model until the next header or the end of the block.
_CODEX_MODEL_RE = re.compile(r"^(?P<model>\S.*?)\s+limit:$")
_CODEX_ACCOUNT_RE = re.compile(r"^Account:\s*(?P<account>.+?)\s*$")
# Window labels codex uses for the *primary* model. A bare line ending in
# "limit:" carrying one of these is a malformed row, not a model header.
_CODEX_WINDOW_LABELS = frozenset({"Weekly", "5h", "Hourly", "Daily", "Monthly"})

# --- grok -------------------------------------------------------------------

_GROK_WEEKLY_RE = re.compile(r"^Weekly limit:\s*(\d+(?:\.\d+)?)\s*%")
_GROK_RESET_RE = re.compile(r"^Next reset:\s*(.+?)\s*$")
_GROK_CREDITS_RE = re.compile(r"^Credits:\s*\$?\s*(-?\d+(?:\.\d+)?)")


def _window(used_pct, resets):
    """Build one window record from a used-percentage and a reset string."""
    used = round(float(used_pct), 6)
    return {
        "used_pct": used,
        "remaining_pct": round(100.0 - used, 6),
        "resets": resets,
    }


def parse_claude_usage(text):
    """Parse the full-screen `/usage` dialog Claude Code renders in its pane.

    Reads the `visible` snapshot: the dialog paints over the viewport, so the
    header/percentage/reset triples appear in reading order.
    """
    windows = {}
    label = None
    pending_used = None
    pending_resets = None

    def flush():
        if label is not None and pending_used is not None:
            windows[label] = _window(pending_used, pending_resets)

    for raw in text.splitlines():
        line = raw.strip()
        header = _CLAUDE_HEADER_RE.match(line)
        if header:
            flush()
            label = header.group(1)
            pending_used = None
            pending_resets = None
            continue
        if label is None:
            continue
        used = _CLAUDE_USED_RE.search(line)
        if used and pending_used is None:
            pending_used = used.group(1)
            continue
        resets = _CLAUDE_RESETS_RE.match(line)
        if resets and pending_used is not None and pending_resets is None:
            pending_resets = resets.group(1).strip()
    flush()

    if not windows:
        raise ParseError(
            "No usage windows found in the claude pane text - send /usage and "
            "read with `herdr agent read <name> --source visible` while the "
            "dialog is open.",
            {"kind": "claude"},
        )
    return {"windows": windows, "credits": None}


def _codex_last_block(text):
    """Return the lines of the last `/status` block in `text`.

    Blocks are delimited by the `Account:` row codex prints first. With no
    such row the whole text is treated as a single block, so a snapshot that
    scrolled the account line away still parses.
    """
    lines = [line.strip().strip(_CODEX_FRAME).strip() for line in text.splitlines()]
    start = 0
    for index, line in enumerate(lines):
        if _CODEX_ACCOUNT_RE.match(line):
            start = index
    return lines[start:]


def parse_codex_usage(text):
    """Parse the boxed `/status` block Codex prints inline.

    Codex reports **% left** (remaining), the inverse of Claude and Grok.
    Rows following a `<model> limit:` section header are attributed to that
    model rather than to the primary one.
    """
    windows = {}
    model = None
    for line in _codex_last_block(text):
        limit = _CODEX_LIMIT_RE.match(line)
        if limit:
            label = limit.group("label").strip()
            resets = limit.group("resets")
            key = "{} limit".format(label) if model is None else "{} {} limit".format(model, label)
            remaining = round(float(limit.group("pct")), 6)
            windows[key] = {
                "used_pct": round(100.0 - remaining, 6),
                "remaining_pct": remaining,
                "resets": resets.strip() if resets else None,
            }
            continue
        header = _CODEX_MODEL_RE.match(line)
        if header and header.group("model") not in _CODEX_WINDOW_LABELS:
            model = header.group("model")

    if not windows:
        raise ParseError(
            "No `<label> limit: ... N% left` rows found in the codex pane text - "
            "send /status and read with "
            "`herdr agent read <name> --source recent-unwrapped --lines 80`.",
            {"kind": "codex"},
        )
    return {"windows": windows, "credits": None}


def parse_grok_usage(text):
    """Parse the inline `/usage` report Grok prints.

    Grok's `Weekly limit: N%` is percent **used**. The last report in the
    snapshot wins, so a pane holding several `/usage` runs yields the newest.
    `Credits` is captured as an informational number and never feeds headroom.
    """
    windows = {}
    credits = None
    for raw in text.splitlines():
        line = raw.strip()
        weekly = _GROK_WEEKLY_RE.match(line)
        if weekly:
            windows["Weekly limit"] = _window(weekly.group(1), None)
            continue
        reset = _GROK_RESET_RE.match(line)
        if reset and "Weekly limit" in windows:
            windows["Weekly limit"]["resets"] = reset.group(1)
            continue
        found_credits = _GROK_CREDITS_RE.match(line)
        if found_credits:
            credits = round(float(found_credits.group(1)), 6)

    if not windows:
        raise ParseError(
            "No `Weekly limit: N%` line found in the grok pane text - send "
            "/usage and read with "
            "`herdr agent read <name> --source recent-unwrapped --lines 60`.",
            {"kind": "grok"},
        )
    return {"windows": windows, "credits": credits}


PARSERS = {
    "claude": parse_claude_usage,
    "codex": parse_codex_usage,
    "grok": parse_grok_usage,
}


def parse_usage(kind, text):
    """Dispatch to the parser for `kind`.

    Raises ParseError for an agent kind this build does not know how to read.
    """
    parser = PARSERS.get(kind)
    if parser is None:
        raise ParseError(
            "No usage parser for agent kind {!r} - supported kinds are {}. "
            "Fix the `kind` field in the agent's config entry.".format(
                kind, ", ".join(sorted(PARSERS))
            ),
            {"kind": kind},
        )
    return parser(text)


def headroom_pct(windows):
    """Smallest remaining_pct across `windows`, or None when there are none.

    Headroom is the binding constraint: an agent with 100% of its weekly
    budget left but 2% of its 5-hour budget left has 2% of headroom.
    """
    if not windows:
        return None
    return min(window["remaining_pct"] for window in windows.values())
