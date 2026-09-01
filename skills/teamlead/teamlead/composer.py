"""Verify that a slash command was actually consumed by the agent's composer.

Sending a command is not the same as the agent receiving one. Live, `herdr
agent prompt codex /new` pasted `/new` into Codex's composer, the
slash-autocomplete popup swallowed the Enter, and nothing submitted. `agent
wait --until idle` returned immediately -- the agent had been idle the whole
time -- so teamlead reported `cleared: true` and pasted the assignment on top
of the unsent text. Codex received `/newNew assignment from the team lead...`
and rejected it twice.

Every failure there was a missing check, not a bad send. So after every slash
command teamlead reads the pane's composer row and confirms the text is gone:

* still there -> press Enter once more, re-read
* still there after that -> fail the agent, and never send the assignment
* composer non-empty before a dispatch -> recover once, then re-read

The composer row is the last row starting with the agent's prompt glyph
(`› ` for Codex, `❯ ` for Claude, `│ ❯` for Grok). A glyph that is absent from
the read -- covered by a modal, scrolled away, or simply not configured --
means teamlead cannot see the composer, and an unverifiable composer is
treated as consumed rather than invented into a failure.
"""

import re
import time

from .errors import HerdrError
from .parsers import BOX_FRAME
from .probe import stderr_warn

#: How the composer is read. It lives at the bottom of the rendered viewport,
#: so `visible` is the only source that shows it.
COMPOSER_READ_SOURCE = "visible"
COMPOSER_READ_LINES = 20

#: Seconds to let a TUI repaint before re-reading. Injected in tests.
COMPOSER_SETTLE_SEC = 1.0

#: Re-reads allowed while waiting for the screen to change after a clear.
SCREEN_CHANGE_ATTEMPTS = 3

#: Re-reads allowed while waiting for a sent assignment to appear as a user
#: message in the transcript.
LANDING_ATTEMPTS = 5

#: How long to wait for the agent to leave idle after an assignment. Short on
#: purpose: this asks "did a turn start", not "is the work done".
DEFAULT_START_TIMEOUT_MS = 15000

#: Transcript text meaning the runtime read the message as a slash command.
#: Live, a leading `/` left over from `/clear` turned the assignment into
#: `/New assignment ...` and Claude Code answered "Args from unknown skill".
UNKNOWN_SKILL_MARKERS = ("unknown skill", "Unrecognized command")



# --- ANSI intensity ---------------------------------------------------------
#
# Claude Code pre-fills its input box with a dim ghost-text suggestion after a
# task ("check the other issues (#28, #30) for follow-up work"). Nobody typed
# it, Esc does not remove it, and typing replaces it -- so it is an EMPTY
# composer wearing a costume. A plain-text read cannot tell it from a real
# command; the SGR sequences can.

#: One SGR sequence: ESC [ params m.
_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
#: Any other escape sequence, dropped before spanning: CSI (cursor moves,
#: erases) and OSC strings (titles).
_OTHER_ESC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[A-Za-ln-z]|\x1b[()][B0]")

#: 256-colour indices that render as dim grey: the greyscale ramp plus bright
#: black. A suggestion is drawn in one of these when it is not SGR 2.
_GREY_256 = frozenset(range(232, 256)) | {8}


def strip_ansi(text):
    """Drop every escape sequence, leaving the characters a human sees."""
    return _SGR_RE.sub("", _OTHER_ESC_RE.sub("", text))


def _apply_sgr(params, intensity, grey):
    """Fold one SGR parameter list into the running intensity and colour."""
    codes = []
    for part in params.split(";"):
        part = part.strip()
        codes.append(int(part) if part.isdigit() else 0)
    if not codes:
        codes = [0]

    index = 0
    while index < len(codes):
        code = codes[index]
        if code == 0:
            intensity, grey = "normal", False
        elif code == 1:
            intensity = "bold"
        elif code == 2:
            intensity = "dim"
        elif code == 22:
            intensity = "normal"
        elif code == 39:
            grey = False
        elif code == 90:
            grey = True
        elif 30 <= code <= 37 or 91 <= code <= 97:
            grey = False
        elif code == 38 and index + 1 < len(codes):
            mode = codes[index + 1]
            if mode == 5 and index + 2 < len(codes):
                grey = codes[index + 2] in _GREY_256
                index += 2
            elif mode == 2 and index + 4 < len(codes):
                red, green, blue = codes[index + 2 : index + 5]
                grey = red == green == blue
                index += 4
        index += 1
    return intensity, grey


def _dim(intensity, grey):
    """Bold always reads as deliberate, whatever colour it is drawn in."""
    if intensity == "bold":
        return False
    return intensity == "dim" or grey


def ansi_chars(line):
    """Split one line into (character, is_dim) pairs.

    A line with no escape sequences yields every character as not-dim, which
    is what makes the plain-text fallback behave exactly as it did before.
    """
    line = _OTHER_ESC_RE.sub("", line)
    intensity, grey = "normal", False
    chars = []
    position = 0
    for match in _SGR_RE.finditer(line):
        for char in line[position : match.start()]:
            chars.append((char, _dim(intensity, grey)))
        intensity, grey = _apply_sgr(match.group(1), intensity, grey)
        position = match.end()
    for char in line[position:]:
        chars.append((char, _dim(intensity, grey)))
    return chars


def _trim(chars, drop=""):
    """Trim (char, dim) pairs, dropping whitespace plus any chars in `drop`.

    The glyph is matched against a whitespace-trimmed row, never a
    frame-trimmed one: Grok's glyph IS a frame character (`│ ❯`), so stripping
    the frame first would eat the glyph and take a character of the command
    with it.
    """
    start = 0
    end = len(chars)
    while start < end and (chars[start][0].isspace() or chars[start][0] in drop):
        start += 1
    while end > start and (chars[end - 1][0].isspace() or chars[end - 1][0] in drop):
        end -= 1
    return chars[start:end]


def composer_text(pane_text, glyph, ignore_dim=False):
    """Return what sits in the composer, or None when it is not visible.

    An empty string means the composer is visible and empty. None means no row
    carried the glyph, so nothing can be concluded either way.

    With `ignore_dim`, characters rendered dim or in the grey palette are
    dropped before deciding. Claude Code pre-fills its input box with a dim
    ghost-text suggestion; nobody typed it, Esc does not remove it, and the
    next thing typed replaces it, so it is an empty composer. Text rendered at
    normal weight after a suggestion still counts, which is what keeps a real
    command from hiding behind one.

    Pane text with no escape sequences yields no dim characters, so a
    plain-text read behaves exactly as it always did.
    """
    if not glyph:
        return None
    prefix = glyph.rstrip()
    if not prefix:
        return None

    found = None
    for raw in pane_text.splitlines():
        if strip_ansi(raw).strip().startswith(prefix):
            found = raw
    if found is None:
        return None

    chars = _trim(ansi_chars(found))[len(prefix) :]
    if ignore_dim:
        chars = [pair for pair in chars if not pair[1]]
    return "".join(char for char, _ in _trim(chars, drop=BOX_FRAME))


def screen_signature(pane_text, glyph):
    """The pane's content with the composer row and blank rows removed.

    Used to answer "did anything actually happen?" without teaching teamlead
    what each agent's fresh-session banner looks like.
    """
    prefix = glyph.rstrip() if glyph else ""
    rows = []
    for raw in pane_text.splitlines():
        line = strip_ansi(raw).strip()
        if not line:
            continue
        if prefix and line.startswith(prefix):
            continue
        rows.append(line)
    return "\n".join(rows)


def transcript_holds(pane_text, needle):
    """True when `needle` appears in the pane, ignoring styling and wrapping.

    Terminals wrap a long message across rows, so the whole viewport is
    collapsed to one whitespace-normalised string before matching. Only the
    opening words of an assignment are ever looked for.
    """
    flat = " ".join(strip_ansi(pane_text).split())
    return " ".join(needle.split()) in flat


def unknown_skill_error(pane_text):
    """Return the marker showing the message was eaten as a slash command."""
    flat = strip_ansi(pane_text)
    for marker in UNKNOWN_SKILL_MARKERS:
        if marker in flat:
            return marker
    return None


def checkable(agent):
    """True when this agent's composer can be inspected at all."""
    return bool(agent.composer_glyph and agent.composer_glyph.strip())


def read_pane(client, agent, warn=None):
    """Read the agent's viewport, or return "" when there is no glyph to find.

    Reads with `--format ansi`, because the SGR sequences are the only way to
    tell a dim ghost-text suggestion from a command somebody typed. A herdr
    that cannot produce them falls back to plain text, which reads every
    character as deliberate -- the older, stricter behaviour.

    An agent with no `composer_glyph` cannot be checked, so teamlead does not
    spend a read it could not interpret.
    """
    if not checkable(agent):
        return ""
    try:
        return client.agent_read(
            agent.name,
            source=COMPOSER_READ_SOURCE,
            lines=COMPOSER_READ_LINES,
            fmt="ansi",
        )
    except HerdrError as exc:
        (warn or stderr_warn)(
            "teamlead: `herdr agent read {} --format ansi` failed ({}). Falling "
            "back to a plain-text read, which cannot recognise a dim "
            "suggestion.".format(agent.name, exc.message)
        )
        return client.agent_read(
            agent.name, source=COMPOSER_READ_SOURCE, lines=COMPOSER_READ_LINES
        )


def ensure_ready(client, agent, sleep=time.sleep, warn=None, settle_sec=COMPOSER_SETTLE_SEC, text=None):
    """Return pane text once the composer is empty, recovering once if needed.

    Recovery sends `agent.recover_keys` EXACTLY once. For Codex that is a
    single `ctrl+c`, which clears the composer and leaves the session running;
    a second `ctrl+c` would exit Codex, which is why this never loops.

    Pass `text` to reuse a read the caller already made. Raises HerdrError when
    the composer still holds text afterwards, having sent nothing else.
    """
    warn = warn or stderr_warn
    if text is None:
        text = read_pane(client, agent, warn=warn)
    held = composer_text(text, agent.composer_glyph, agent.composer_ignore_dim)
    if not held:
        return text

    if not agent.recover_keys:
        raise HerdrError(
            "{}'s composer already holds {!r} and no recover_keys are "
            "configured for it. Clear the composer by hand, then run this "
            "again.".format(agent.name, held),
            {"agent": agent.name, "composer": held},
        )

    warn(
        "teamlead: {}'s composer holds {!r} before dispatch. Sending {} once "
        "to clear it.".format(agent.name, held, " ".join(agent.recover_keys))
    )
    client.agent_send_keys(agent.name, agent.recover_keys)
    sleep(settle_sec)
    text = read_pane(client, agent, warn=warn)
    held = composer_text(text, agent.composer_glyph, agent.composer_ignore_dim)
    if held:
        raise HerdrError(
            "{}'s composer still holds {!r} after sending {}. If that is a dim "
            "ghost-text suggestion rather than typed text, it is not really "
            "occupied: set composer_ignore_dim true for {} so teamlead reads "
            "the intensity instead of the characters. Otherwise clear it by "
            "hand -- teamlead sends the recover keys once and never twice, "
            "because a second ctrl+c exits Codex -- then run this again.".format(
                agent.name, held, " ".join(agent.recover_keys), agent.name
            ),
            {
                "agent": agent.name,
                "composer": held,
                "composer_ignore_dim": agent.composer_ignore_dim,
            },
        )
    return text


def send_message(client, agent, text, landing_needle, sleep=time.sleep, warn=None, settle_sec=COMPOSER_SETTLE_SEC, attempts=LANDING_ATTEMPTS, start_timeout_ms=DEFAULT_START_TIMEOUT_MS):
    """Paste a real message and confirm the agent actually took it.

    Sending is not starting. Live, an assignment pasted onto a leftover `/`
    became `/New assignment ...`; Claude Code answered "Args from unknown
    skill", no turn began, and teamlead reported success. So after the paste:

    1. the transcript must show the message as a user message, and
    2. `unknown skill` / `Unrecognized command` must NOT appear, and
    3. the agent must leave idle, or the transcript must show the prompt.

    Returns `{"landed": bool, "started": bool}`. Raises HerdrError when the
    runtime swallowed the message as a command -- that one is not a slow
    start, it is a wrong send.
    """
    warn = warn or stderr_warn
    ensure_ready(client, agent, sleep=sleep, warn=warn, settle_sec=settle_sec)
    client.agent_prompt(agent.name, text)

    landed = False
    complaint = None
    pane = ""
    for _ in range(max(1, attempts)):
        sleep(settle_sec)
        pane = read_pane(client, agent, warn=warn)
        if not pane:
            break
        complaint = unknown_skill_error(pane)
        if complaint:
            break
        if transcript_holds(pane, landing_needle):
            landed = True
            break

    if complaint:
        raise HerdrError(
            "{} read the assignment as a slash command ({!r} in its "
            "transcript), so no turn started. A leftover `/` in the composer "
            "does this. Send `herdr agent send-keys {} esc` once, confirm the "
            "composer is empty, then re-run this assignment.".format(
                agent.name, complaint, agent.name
            ),
            {"agent": agent.name, "marker": complaint},
        )

    started = _left_idle(client, agent, start_timeout_ms, warn)
    if not started and not landed:
        warn(
            "teamlead: {} was sent its assignment but neither left idle nor "
            "showed it in the transcript. Reported as sent_but_not_started; "
            "check the pane before assuming it is working.".format(agent.name)
        )
    return {"landed": landed, "started": started}


def _left_idle(client, agent, timeout_ms, warn):
    """True when the agent moved to `working` within the timeout.

    A timeout is an answer, not a failure: an agent that finished a short turn
    before teamlead looked is indistinguishable from one that never started,
    which is exactly why the transcript check runs first.
    """
    try:
        client.agent_wait(agent.name, until=("working",), timeout_ms=timeout_ms)
    except HerdrError:
        return False
    return True


def send_command(client, agent, pane_id, command, sleep=time.sleep, warn=None, settle_sec=COMPOSER_SETTLE_SEC, screen_attempts=SCREEN_CHANGE_ATTEMPTS):
    """Send a slash command and confirm the composer consumed it.

    Returns::

        {
          "consumed": True,          # always -- anything else raises
          "extra_enter": bool,       # a second Enter was needed
          "recovered": bool,         # the composer had to be cleared first
          "screen_changed": bool,    # the pane's content actually changed
        }

    Raises HerdrError when the command is still sitting in the composer after
    a second Enter. The caller must not send anything further to that agent.
    """
    warn = warn or stderr_warn
    before = read_pane(client, agent, warn=warn)
    recovered = bool(composer_text(before, agent.composer_glyph, agent.composer_ignore_dim))
    if recovered:
        before = ensure_ready(
            client, agent, sleep=sleep, warn=warn, settle_sec=settle_sec, text=before
        )
    before_signature = screen_signature(before, agent.composer_glyph)

    client.deliver_slash_command(agent.slash_delivery, agent.name, pane_id, command)
    sleep(settle_sec)
    text = read_pane(client, agent, warn=warn)

    extra_enter = False
    if composer_text(text, agent.composer_glyph, agent.composer_ignore_dim):
        # Codex opens an autocomplete popup on `/`, where the first Enter only
        # accepts the completion. A second Enter submits, and an extra Enter
        # on an empty composer is harmless.
        extra_enter = True
        warn(
            "teamlead: {} still shows {!r} in its composer after {!r}. Pressing "
            "Enter once more.".format(agent.name, command, command)
        )
        client.pane_send_keys(pane_id, ["enter"])
        sleep(settle_sec)
        text = read_pane(client, agent, warn=warn)

    held = composer_text(text, agent.composer_glyph, agent.composer_ignore_dim)
    if held:
        raise HerdrError(
            "{!r} is still sitting unsent in {}'s composer (it shows {!r}) "
            "after two Enters. Nothing further was sent. Clear the composer "
            "with `herdr agent send-keys {} {}` -- once only -- submit the "
            "command by hand, and check whether {} needs "
            "slash_delivery \"type\" instead of \"paste\".".format(
                command,
                agent.name,
                held,
                agent.name,
                " ".join(agent.recover_keys) if agent.recover_keys else "<recover-key>",
                agent.name,
            ),
            {"agent": agent.name, "command": command, "composer": held},
        )

    screen_changed = screen_signature(text, agent.composer_glyph) != before_signature
    attempts = 0
    while not screen_changed and attempts < screen_attempts:
        attempts += 1
        sleep(settle_sec)
        text = read_pane(client, agent, warn=warn)
        screen_changed = screen_signature(text, agent.composer_glyph) != before_signature

    return {
        "consumed": True,
        "extra_enter": extra_enter,
        "recovered": recovered,
        "screen_changed": screen_changed,
    }
