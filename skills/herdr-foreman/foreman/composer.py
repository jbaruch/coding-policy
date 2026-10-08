"""Verify that a slash command was actually consumed by the agent's composer.

Sending a command is not the same as the agent receiving one. Live, `herdr
agent prompt codex /new` pasted `/new` into Codex's composer, the
slash-autocomplete popup swallowed the Enter, and nothing submitted. `agent
wait --until idle` returned immediately -- the agent had been idle the whole
time -- so foreman reported `cleared: true` and pasted the assignment on top
of the unsent text. Codex received `/newNew assignment from the team lead...`
and rejected it twice.

Every failure there was a missing check, not a bad send. So after every slash
command foreman reads the pane's composer row and confirms the text is gone:

* still there -> press Enter once more, re-read
* still there after that -> fail the agent, and never send the assignment
* composer non-empty before a dispatch -> recover once, then re-read

The composer row is the last row starting with the agent's prompt glyph
(`› ` for Codex, `❯ ` for Claude, `│ ❯` for Grok). A configured glyph that is
absent from the read -- covered by a modal or scrolled away -- triggers a
bounded wait and then refuses before input. An agent with no configured glyph
keeps the legacy unchecked path.
"""

import re
import time

from .errors import HerdrError, owner_recovery
from . import runnable
from .parsers import BOX_FRAME
from .probe import stderr_warn

#: How the composer is read. It lives at the bottom of the rendered viewport,
#: so `visible` is the only source that shows it.
COMPOSER_READ_SOURCE = "visible"
COMPOSER_READ_LINES = 20

#: Seconds to let a TUI repaint before re-reading. Injected in tests.
COMPOSER_SETTLE_SEC = 1.0

#: How many times to look for the worker's own prompt before refusing, and how
#: long to wait between looks: 10 s in total. A runtime that has not drawn its
#: composer by then is drawing something else over it (#393).
COMPOSER_VISIBLE_ATTEMPTS = 20
COMPOSER_VISIBLE_INTERVAL = 0.5
FRESH_COMPOSER_ATTEMPTS = 20
FRESH_COMPOSER_INTERVAL = 0.5
FRESH_COMPOSER_STABLE_READS = 2

#: Re-reads allowed while waiting for the screen to change after a clear.
SCREEN_CHANGE_ATTEMPTS = 3

#: Extra Enters foreman will press when a typed slash command is still
#: visible in the composer. Bounded: past this, something is wrong that more
#: keystrokes will not fix.
MAX_EXTRA_ENTERS = 2

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
#: Colon-separated SGR is valid but not decoded by the existing ANSI parser.
#: Such a read cannot safely establish the new painted-composer boundary.
_UNSUPPORTED_SGR_RE = re.compile(r"\x1b\[[0-9;:]*:[0-9;:]*m")
#: Codex's unpainted empty/draft footer styles the shortcut key alone, then
#: resets before its label. Editor text does not style individual key hints.
#: Plain wording, model names and ordinary bold/dim draft rows prove nothing.
_CODEX_SHORTCUT_ROW_RE = re.compile(
    r"^\s*(?:\x1b\[0m)?\x1b\[1m\?(?:\x1b\[0m | \x1b\[0m)for shortcuts(?:\s|$)"
)
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


def _row_background(line, background=False):
    """Return (painted cells present, final background state) for one row.

    Codex paints its composer, including blank padding and recalled-input
    rows, with a background. The model/status footer is outside that box.
    Read SGR parameters rather than color names or footer wording; skip RGB
    and palette operands so a foreground color cannot look like a background.
    """
    line = _OTHER_ESC_RE.sub("", line)
    painted = False
    position = 0
    for match in _SGR_RE.finditer(line):
        if background and match.start() > position:
            painted = True
        codes = [int(part) if part.isdigit() else 0 for part in match.group(1).split(";")]
        index = 0
        while index < len(codes):
            code = codes[index]
            if code in (0, 49):
                background = False
            elif 40 <= code <= 47 or 100 <= code <= 107:
                background = True
            elif code in (38, 48, 58) and index + 1 < len(codes):
                mode = codes[index + 1]
                operands = 2 if mode == 5 else 4 if mode == 2 else 0
                if operands and index + operands < len(codes):
                    if code == 48:
                        background = True
                    index += operands
            index += 1
        position = match.end()
    return painted or (background and position < len(line)), background


def _unpainted_codex_footer_start(rows, found_index):
    """Find the padded footer only with Codex's native shortcut-key styling.

    Some terminals provide no composer background. Codex still separates the
    editor from the status/shortcut footer with bottom padding. Only its final
    nonblank, individually styled shortcut row establishes that footer; an
    unstyled read, unknown SGR, missing padding or later text stays ambiguous.
    Choose the LAST blank before that footer, retaining every draft paragraph.
    """
    if any(_UNSUPPORTED_SGR_RE.search(row) for row in rows):
        return None
    tail = len(rows) - 1
    while tail > found_index and not strip_ansi(rows[tail]).strip():
        tail -= 1
    if tail <= found_index or not _CODEX_SHORTCUT_ROW_RE.match(rows[tail]):
        return None
    for index in range(tail - 1, found_index, -1):
        if not strip_ansi(rows[index]).strip():
            return index
    return None


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

    rows = pane_text.splitlines()
    painted_rows = []
    background = False
    for raw in rows:
        painted, background = _row_background(raw, background)
        painted_rows.append(painted)
    found = None
    found_index = None
    found_indent = None
    for index, raw in enumerate(rows):
        visible = strip_ansi(raw)
        if visible.strip().startswith(prefix):
            indent = len(visible) - len(visible.lstrip())
            if found_indent is not None and indent > found_indent:
                continue
            found = raw
            found_index = index
            found_indent = indent
    if found is None:
        return None

    chars = _trim(ansi_chars(found))[len(prefix) :]
    if ignore_dim:
        chars = [pair for pair in chars if not pair[1]]
    parts = ["".join(char for char, _ in _trim(chars, drop=BOX_FRAME))]

    # Codex wraps recalled multiline input onto indented rows below the glyph
    # row. Those rows are still part of the composer: ignoring them can turn a
    # recalled prompt whose first row equals the empty hint into an exact
    # placeholder match. A continuation is more indented than the glyph row;
    # blank rows may be part of recalled input. Modern Codex paints the entire
    # composer box but leaves its footer unpainted; its footer can be just as
    # indented as recalled input. Without that ANSI evidence, retain the
    # conservative indentation scan rather than guessing from footer text.
    visible_found = strip_ansi(found)
    glyph_indent = len(visible_found) - len(visible_found.lstrip())
    painted_composer = (
        prefix == "›"
        and painted_rows[found_index or 0]
        and not _UNSUPPORTED_SGR_RE.search(pane_text)
    )
    footer_start = (
        _unpainted_codex_footer_start(rows, found_index)
        if prefix == "›" and not painted_composer else None
    )
    for index in range((found_index or 0) + 1, len(rows)):
        if footer_start is not None and index >= footer_start:
            break
        raw = rows[index]
        # Unknown SGR still has zero display width. Keep its text as occupied
        # input, without letting an escape before spaces hide the indentation.
        visible = _UNSUPPORTED_SGR_RE.sub("", strip_ansi(raw))
        if not visible.strip():
            continue
        if painted_composer and not painted_rows[index]:
            break
        indent = len(visible) - len(visible.lstrip())
        if indent <= glyph_indent:
            break
        continuation = _trim(ansi_chars(raw), drop=BOX_FRAME)
        if ignore_dim:
            continuation = [pair for pair in continuation if not pair[1]]
        text = "".join(char for char, _ in _trim(continuation, drop=BOX_FRAME))
        if text:
            parts.append(text)
    return "\n".join(parts)


def screen_signature(pane_text, glyph):
    """The pane's content with the composer row and blank rows removed.

    Used to answer "did anything actually happen?" without teaching foreman
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


def command_still_present(pane_text, agent, command):
    """True when `command` is still visible in the composer, in ANY style.

    Dimness is deliberately IGNORED here. Codex renders a slash command in a
    dim/highlight style while its autocomplete popup is open, so the
    `composer_ignore_dim` filter erased the real, unsent `/new` and foreman
    declared it consumed. The brief was then pasted onto it and Codex answered
    "Unrecognized command '/newNew assignment...'".

    So: while foreman is looking for the command it just typed, every
    character counts. Placeholder and dim rules only apply once the command
    text is gone.
    """
    literal = composer_text(pane_text, agent.composer_glyph, ignore_dim=False)
    if not literal:
        return False
    return command.strip() in literal


def checkable(agent):
    """True when this agent's composer can be inspected at all."""
    return bool(agent.composer_glyph and agent.composer_glyph.strip())


def read_pane(client, agent, warn=None):
    """Return `(pane_text, ansi)` for the agent's viewport.

    `ansi` is False when the SGR read failed and foreman fell back to plain
    text. That flag is load-bearing: without intensity information foreman
    cannot tell a runtime's empty-composer placeholder, or a ghost-text
    suggestion, from something a human typed -- so a plain-text read is never
    allowed to authorise a recovery keystroke.

    An agent with no `composer_glyph` cannot be checked, so foreman does not
    spend a read it could not interpret.
    """
    if not checkable(agent):
        return "", False
    try:
        return (
            client.agent_read(
                agent.name,
                source=COMPOSER_READ_SOURCE,
                lines=COMPOSER_READ_LINES,
                fmt="ansi",
            ),
            True,
        )
    except HerdrError as exc:
        (warn or stderr_warn)(
            "`herdr agent read {} --format ansi` failed ({}). Falling "
            "back to a plain-text read: foreman can still refuse, but it will "
            "not send recovery keys on a read it cannot interpret.".format(
                agent.name, exc.message
            )
        )
        return (
            client.agent_read(
                agent.name, source=COMPOSER_READ_SOURCE, lines=COMPOSER_READ_LINES
            ),
            False,
        )


class Composer(object):
    """What the composer holds, and how confidently foreman knows it."""

    __slots__ = ("visible", "content", "dim", "placeholder", "ansi", "literal")

    def __init__(self, visible, content, dim, placeholder, ansi, literal):
        self.visible = visible
        self.content = content
        self.dim = dim
        self.placeholder = placeholder
        self.ansi = ansi
        # Everything drawn on the row, dim included. Only ever used in
        # messages, never to decide whether to send a key.
        self.literal = literal

    @property
    def occupied(self):
        """True only when somebody's own text is sitting there."""
        return bool(self.content)

    def __repr__(self):
        return "Composer(content={!r}, dim={}, placeholder={}, ansi={})".format(
            self.content, self.dim, self.placeholder, self.ansi
        )


def is_placeholder(text, agent):
    """True when `text` is this runtime's empty-composer hint.

    Codex draws `Ask Codex to do anything` whenever nothing is typed. Reading
    that as occupied is what sent `ctrl+c` into an idle Codex and killed the
    process. Matched exactly after trimming, so a placeholder with a real
    command appended is still a real command.
    """
    stripped = (text or "").strip()
    if not stripped:
        return False
    return any(stripped == hint.strip() for hint in agent.composer_placeholders)


def inspect_composer(pane_text, agent, ansi=True):
    """Classify the composer row. Pure: no herdr, no keys, no decisions."""
    literal = composer_text(pane_text, agent.composer_glyph, ignore_dim=False)
    if literal is None:
        return Composer(False, "", False, False, ansi, None)

    lit = composer_text(pane_text, agent.composer_glyph, ignore_dim=True)
    all_dim = bool(literal) and not lit
    # A runtime with an explicit placeholder allowlist gives us a stronger
    # empty-composer signal than styling. Codex renders recalled prompts dim
    # too; dropping every dim row let a recalled brief read as empty and the
    # measurement `/status` was appended and submitted as a new turn (#670).
    # For such runtimes only an exact declared placeholder is empty. Runtimes
    # without that allowlist (Claude's open-ended ghost suggestions) retain
    # the style-based filter.
    if agent.composer_placeholders:
        judged = literal
    else:
        judged = lit if agent.composer_ignore_dim else literal
    placeholder = is_placeholder(literal, agent) or is_placeholder(judged, agent)
    content = "" if placeholder else judged
    return Composer(True, content, all_dim, placeholder, ansi, literal)


class DispatchSession(object):
    """Remembers what foreman itself typed during this run.

    Recovery keys clear somebody's input line, and for Codex the key that does
    it also exits the process when the line is empty. So foreman only ever
    clears text it can account for: a command it sent earlier in this run, or
    text the operator explicitly opted in to clearing with --allow-recovery.
    """

    __slots__ = ("sent", "allow_recovery")

    def __init__(self, allow_recovery=False):
        self.sent = set()
        self.allow_recovery = allow_recovery

    def remember(self, text):
        self.sent.add((text or "").strip())

    def typed_by_foreman(self, text):
        return (text or "").strip() in self.sent


def recovery_allowed(agent, composer, session):
    """Return `(allowed, refusal_reason)` for sending the recovery keys.

    Every condition here exists because one of them, absent, killed an agent.
    """
    if not composer.occupied:
        return False, "the composer is not occupied"
    if not composer.ansi:
        return False, (
            "the composer was read as plain text, so foreman cannot tell a "
            "placeholder or a dim suggestion from text somebody typed"
        )
    if composer.dim:
        return False, "dim text is never eligible for automatic recovery"
    if composer.placeholder:
        return False, "the text is this runtime's empty-composer placeholder"
    if not agent.recover_keys:
        return False, (
            "no recover_keys are configured for {} -- Codex ships with none "
            "because ctrl+c on an idle Codex exits the process".format(agent.name)
        )
    if session.typed_by_foreman(composer.content):
        return True, None
    if session.allow_recovery:
        return True, None
    return False, (
        "foreman did not type it, so it is somebody's work in progress; pass "
        "--allow-recovery to clear text foreman did not put there"
    )


def _stuck_composer_error(agent, pane_id, composer, reason):
    return HerdrError(
        "{}'s composer holds input and foreman will not clear it: {}. Look at "
        "pane {} yourself, clear it if it is safe to, then run this again.".format(
            agent.name, reason, pane_id or "(unknown)"
        ),
        {
            "agent": agent.name,
            "pane_id": pane_id,
            "composer_occupied": True,
            "reason": reason,
            "dim": composer.dim,
            "placeholder": composer.placeholder,
            "ansi_read": composer.ansi,
        },
    )


def _wait_for_visible_composer(client, agent, pane_id, text, ansi, sleep, warn):
    """Return `(text, ansi, composer)` once a configured prompt is visible."""
    composer = inspect_composer(text, agent, ansi=ansi)
    if not checkable(agent) or composer.visible:
        return text, ansi, composer
    for attempt in range(1, COMPOSER_VISIBLE_ATTEMPTS + 1):
        if attempt < COMPOSER_VISIBLE_ATTEMPTS:
            sleep(COMPOSER_VISIBLE_INTERVAL)
        text, ansi = read_pane(client, agent, warn=warn)
        composer = inspect_composer(text, agent, ansi=ansi)
        if composer.visible:
            return text, ansi, composer
    raise HerdrError(
        "{}'s prompt is not on screen in pane {} after {} reads; something is drawn over it -- a startup review or permission dialog takes the input meant for the worker. Read the pane, clear what is on it, and retry. Nothing was sent.".format(
            agent.name, pane_id, COMPOSER_VISIBLE_ATTEMPTS),
        {"agent": agent.name, "pane": pane_id, "attempts": COMPOSER_VISIBLE_ATTEMPTS},
    )


def _fresh_startup_error(kind, message, evidence):
    return owner_recovery(HerdrError(message, evidence), kind,
        runnable.command("apply"),
        "The apply owner must prove owned pre-send cleanup and durable not_sent before retrying the unchanged assignment; unknown input remains blocked.")


def startup_pending_error(agent, pane_id):
    """No authority decision: leave this named pre-input UI for its owner."""
    return _fresh_startup_error("startup_dialog_pending",
        "Fresh startup for {} in {} has no proved empty composer; inspect its retained native pane. Nothing was sent.".format(agent.name, pane_id),
        {"agent": agent.name, "pane_id": pane_id})


def _settle_fresh_composer(client, agent, pane_id, observe, sleep, warn):
    """Read only; decorative startup evidence never authorizes input itself."""
    original = observe()
    stable = 0
    composer = Composer(False, "", False, False, False, None)
    for attempt in range(FRESH_COMPOSER_ATTEMPTS):
        text, ansi = read_pane(client, agent, warn=warn)
        composer = inspect_composer(text, agent, ansi=ansi)
        if observe() != original:
            raise _fresh_startup_error("startup_identity_changed", "Fresh worker changed pane, process or tier during startup; nothing was sent.", {"pane_id": pane_id})
        # The native hook-review menu uses the same glyph as Codex's composer.
        # Recognizing its UI preserves the target, not permission to trust it.
        rows = [row.strip() for row in strip_ansi(text).splitlines()]
        if agent.kind == "codex" and "Hooks need review" in rows and any("1. Review hooks" in row for row in rows):
            raise startup_pending_error(agent, pane_id)
        if not ansi or not composer.visible:
            if not ansi:
                raise _fresh_startup_error("startup_evidence_missing", "Fresh startup lacks ANSI composer evidence; nothing was sent.", {"pane_id": pane_id, "ansi_read": ansi})
            # Startup paint can precede the composer. Wait without keys, but
            # leave a persistent overlay available for the owner's contextual
            # Runtime Dialogs decision instead of closing it on the first read.
            stable = 0
            if attempt + 1 < FRESH_COMPOSER_ATTEMPTS:
                sleep(FRESH_COMPOSER_INTERVAL)
                continue
            raise startup_pending_error(agent, pane_id)
        # Keep the runtime's existing empty-input classification. Claude's
        # dynamic dim hints have no static allowlist; Codex recalled input
        # remains occupied under its explicit placeholder policy.
        exact = not composer.occupied
        # Only a single placeholder row with decorative non-word marks may
        # settle. Recalled paragraphs and arbitrary dim input remain drafts.
        decorated = (composer.dim and "\n" not in (composer.literal or "") and any(
            (composer.literal or "").strip().startswith(hint.strip())
            and (composer.literal or "").strip()[len(hint.strip()):].strip()
            and all(char.isspace() or char in "✦✧✶✷✸✹✺✻✼✽✾✿⋆*" for char in
                    (composer.literal or "").strip()[len(hint.strip()):])
            for hint in agent.composer_placeholders))
        if not exact and not decorated:
            raise _fresh_startup_error("startup_input_occupied", "Fresh startup contains a draft or dialog; no input was sent.",
                {"pane_id": pane_id, "composer_occupied": True, "dim": composer.dim, "placeholder": composer.placeholder, "ansi_read": composer.ansi})
        stable = stable + 1 if exact else 0
        if stable >= FRESH_COMPOSER_STABLE_READS:
            return text
        if attempt + 1 < FRESH_COMPOSER_ATTEMPTS:
            sleep(FRESH_COMPOSER_INTERVAL)
    raise _fresh_startup_error("startup_settle_timeout", "Fresh composer did not settle within its read-only startup bound; nothing was sent.",
                    {"pane_id": pane_id, "attempts": FRESH_COMPOSER_ATTEMPTS,
                     "dim": composer.dim, "placeholder": composer.placeholder, "ansi_read": composer.ansi})


def ensure_ready(client, agent, pane_id=None, session=None, sleep=time.sleep, warn=None, settle_sec=COMPOSER_SETTLE_SEC, text=None, ansi=True, before_input=None, recovery_state=None, startup_observe=None):
    """Return pane text once the worker's own composer is on screen and empty.

    Recovery keys are sent only when every condition in `recovery_allowed`
    holds, and then EXACTLY once. Anything else refuses and names the pane:
    a keystroke into a composer foreman cannot account for is how an idle
    Codex got killed. `before_input` revalidates the caller's live guard before
    recovery keys, after the composer read that could have observed a switch.

    The composer must be VISIBLE before its content is judged. An absent
    composer holds no content, so a modal drawn over the prompt read as an
    empty one: Codex's startup review dialog took a whole assignment that way,
    and the apply returned `sent_but_not_started` with no model turn and no
    assignment in the transcript (#393). A prompt that has not appeared yet is
    waited out, bounded; one that never appears refuses before any input.
    """
    warn = warn or stderr_warn
    if startup_observe is not None:
        return _settle_fresh_composer(client, agent, pane_id, startup_observe, sleep, warn)
    session = session if session is not None else DispatchSession()
    if text is None:
        text, ansi = read_pane(client, agent, warn=warn)
    text, ansi, composer = _wait_for_visible_composer(
        client, agent, pane_id, text, ansi, sleep, warn
    )
    if not composer.occupied:
        return text

    allowed, reason = recovery_allowed(agent, composer, session)
    if not allowed:
        raise _stuck_composer_error(agent, pane_id, composer, reason)

    warn(
        "{}'s composer holds input foreman is allowed to recover. Sending {} "
        "once to clear it.".format(
            agent.name, " ".join(agent.recover_keys)
        )
    )
    if before_input is not None:
        before_input()
    client.agent_send_keys(agent.name, agent.recover_keys)
    if recovery_state is not None:
        recovery_state["sent"] = True
    sleep(settle_sec)
    text, ansi = read_pane(client, agent, warn=warn)
    text, ansi, composer = _wait_for_visible_composer(
        client, agent, pane_id, text, ansi, sleep, warn
    )
    if composer.occupied:
        raise _stuck_composer_error(
            agent,
            pane_id,
            composer,
            "it survived the one recovery keystroke foreman is willing to "
            "send (never two: a second ctrl+c exits Codex)",
        )
    return text


def send_message(client, agent, text, landing_needle, pane_id=None, session=None, sleep=time.sleep, warn=None, settle_sec=COMPOSER_SETTLE_SEC, attempts=LANDING_ATTEMPTS, start_timeout_ms=DEFAULT_START_TIMEOUT_MS, before_input=None, before_prompt=None, startup_observe=None):
    """Paste a real message and confirm the agent actually took it.

    Sending is not starting. Live, an assignment pasted onto a leftover `/`
    became `/New assignment ...`; Claude Code answered "Args from unknown
    skill", no turn began, and foreman reported success. So after the paste:

    1. the transcript must show the message as a user message, and
    2. `unknown skill` / `Unrecognized command` must NOT appear, and
    3. the agent must leave idle, or the transcript must show the prompt.

    Returns `{"landed": bool, "started": bool}`. Raises HerdrError when the
    runtime swallowed the message as a command -- that one is not a slow
    start, it is a wrong send. `before_input` runs before recovery and prompt
    input, including after the last composer read.
    """
    warn = warn or stderr_warn
    session = session if session is not None else DispatchSession()
    ensure_ready(
        client,
        agent,
        pane_id=pane_id,
        session=session,
        sleep=sleep,
        warn=warn,
        settle_sec=settle_sec,
        before_input=before_input,
        startup_observe=startup_observe,
    )
    if before_input is not None:
        before_input()
    if before_prompt is not None:
        before_prompt()
    client.agent_prompt(agent.name, text)

    landed = False
    complaint = None
    pane = ""
    for _ in range(max(1, attempts)):
        sleep(settle_sec)
        pane, _ansi = read_pane(client, agent, warn=warn)
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
            "{} was sent its assignment but neither left idle nor "
            "showed it in the transcript. Reported as sent_but_not_started; "
            "check the pane before assuming it is working.".format(agent.name)
        )
    return {"landed": landed, "started": started}


def _left_idle(client, agent, timeout_ms, warn):
    """True when the agent moved to `working` within the timeout.

    A timeout is an answer, not a failure: an agent that finished a short turn
    before foreman looked is indistinguishable from one that never started,
    which is exactly why the transcript check runs first.
    """
    try:
        client.agent_wait(agent.name, until=("working",), timeout_ms=timeout_ms)
    except HerdrError:
        return False
    return True


def send_command(client, agent, pane_id, command, session=None, sleep=time.sleep, warn=None, settle_sec=COMPOSER_SETTLE_SEC, screen_attempts=SCREEN_CHANGE_ATTEMPTS, max_extra_enters=MAX_EXTRA_ENTERS, before_input=None, after_submit=None):
    """Send a slash command and confirm the composer consumed it.

    Returns::

        {
          "consumed": True,          # always -- anything else raises
          "extra_enters": int,       # Enters beyond the configured count
          "recovered": bool,         # the composer had to be cleared first
          "screen_changed": bool,    # the pane's content actually changed
        }

    Raises HerdrError when the command is still sitting in the composer after
    a second Enter. The caller must not send anything further to that agent.
    `before_input` guards recovery, command delivery and each extra Enter.
    """
    warn = warn or stderr_warn
    session = session if session is not None else DispatchSession()
    before, ansi = read_pane(client, agent, warn=warn)
    recovery_state = {"sent": False}
    # Slash commands need the same visibility gate as real messages even when
    # the first classification looks empty. A clipped or modal-covered prompt
    # is not permission to type into an unknown input surface.
    before = ensure_ready(
        client,
        agent,
        pane_id=pane_id,
        session=session,
        sleep=sleep,
        warn=warn,
        settle_sec=settle_sec,
        text=before,
        ansi=ansi,
        before_input=before_input,
        recovery_state=recovery_state,
    )
    recovered = recovery_state["sent"]
    before_signature = screen_signature(before, agent.composer_glyph)

    # Remembered before it is sent, so a command that fails to submit is one
    # foreman can account for -- and therefore one it may clear later.
    session.remember(command)
    client.deliver_slash_command(
        agent.slash_delivery,
        agent.name,
        pane_id,
        command,
        enter_count=agent.slash_enter_count,
        before_input=before_input,
        after_submit=after_submit,
    )
    sleep(settle_sec)
    text, ansi = read_pane(client, agent, warn=warn)

    # Phase 1 -- is the command still there, in ANY style? Dimness plays no
    # part: Codex styles an open autocomplete popup dim, and filtering that
    # out is what let an unsent /new pass as consumed.
    extra_enters = 0
    while command_still_present(text, agent, command) and extra_enters < max_extra_enters:
        extra_enters += 1
        warn(
            "{} still shows {!r} in its composer. Pressing Enter "
            "again ({} of {}).".format(
                agent.name, command, extra_enters, max_extra_enters
            )
        )
        if before_input is not None:
            before_input()
        client.pane_send_keys(pane_id, ["enter"])
        if after_submit is not None:
            after_submit()
        sleep(settle_sec)
        text, ansi = read_pane(client, agent, warn=warn)

    if command_still_present(text, agent, command):
        raise HerdrError(
            "{!r} is still sitting unsent in {}'s composer after {} Enters. "
            "Nothing further was sent -- pasting a brief onto it is how Codex "
            "came to read `/newNew assignment...`. Look at pane {}, submit the "
            "command by hand, and check whether {} needs a higher "
            "slash_enter_count.".format(
                command,
                agent.name,
                agent.slash_enter_count + extra_enters,
                pane_id or "(unknown)",
                agent.name,
            ),
            {
                "agent": agent.name,
                "command": command,
                "pane_id": pane_id,
                "enters_sent": agent.slash_enter_count + extra_enters,
            },
        )

    # Phase 2 -- the command is gone; NOW placeholder and dim rules decide
    # whether anything else is sitting there.
    held = inspect_composer(text, agent, ansi=ansi)
    # A consumed clear can leave a transient startup frame painted as editor
    # content. Observe only: no key may clear an occupied post-command draft.
    for _ in range(max(0, screen_attempts)):
        if not held.occupied:
            break
        sleep(settle_sec)
        text, ansi = read_pane(client, agent, warn=warn)
        held = inspect_composer(text, agent, ansi=ansi)
    if held.occupied:
        raise HerdrError(
            "{} consumed {!r}, but its composer now holds input. Nothing "
            "further was sent. Look at pane {} before assigning to it.".format(
                agent.name, command, pane_id or "(unknown)"
            ),
            {"agent": agent.name, "command": command, "composer_occupied": True},
        )

    screen_changed = screen_signature(text, agent.composer_glyph) != before_signature
    attempts = 0
    while not screen_changed and attempts < screen_attempts:
        attempts += 1
        sleep(settle_sec)
        text, _ansi = read_pane(client, agent, warn=warn)
        screen_changed = screen_signature(text, agent.composer_glyph) != before_signature

    return {
        "consumed": True,
        "extra_enters": extra_enters,
        "recovered": recovered,
        "screen_changed": screen_changed,
    }
