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


def composer_text(pane_text, glyph):
    """Return what sits in the composer, or None when it is not visible.

    An empty string means the composer is visible and empty. None means no row
    carried the glyph, so nothing can be concluded either way.
    """
    if not glyph:
        return None
    prefix = glyph.rstrip()
    if not prefix:
        return None
    found = None
    for raw in pane_text.splitlines():
        line = raw.strip()
        if line.startswith(prefix):
            found = line[len(prefix) :]
    if found is None:
        return None
    return found.strip().strip(BOX_FRAME).strip()


def screen_signature(pane_text, glyph):
    """The pane's content with the composer row and blank rows removed.

    Used to answer "did anything actually happen?" without teaching teamlead
    what each agent's fresh-session banner looks like.
    """
    prefix = glyph.rstrip() if glyph else ""
    rows = []
    for raw in pane_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if prefix and line.startswith(prefix):
            continue
        rows.append(line)
    return "\n".join(rows)


def checkable(agent):
    """True when this agent's composer can be inspected at all."""
    return bool(agent.composer_glyph and agent.composer_glyph.strip())


def read_pane(client, agent):
    """Read the agent's viewport, or return "" when there is no glyph to find.

    An agent with no `composer_glyph` cannot be checked, so teamlead does not
    spend a read it could not interpret.
    """
    if not checkable(agent):
        return ""
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
        text = read_pane(client, agent)
    held = composer_text(text, agent.composer_glyph)
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
    text = read_pane(client, agent)
    held = composer_text(text, agent.composer_glyph)
    if held:
        raise HerdrError(
            "{}'s composer still holds {!r} after sending {}. Clear it by hand "
            "-- teamlead sends the recover keys once and never twice, because "
            "a second ctrl+c exits Codex -- then run this again.".format(
                agent.name, held, " ".join(agent.recover_keys)
            ),
            {"agent": agent.name, "composer": held},
        )
    return text


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
    before = read_pane(client, agent)
    recovered = bool(composer_text(before, agent.composer_glyph))
    if recovered:
        before = ensure_ready(
            client, agent, sleep=sleep, warn=warn, settle_sec=settle_sec, text=before
        )
    before_signature = screen_signature(before, agent.composer_glyph)

    client.deliver_slash_command(agent.slash_delivery, agent.name, pane_id, command)
    sleep(settle_sec)
    text = read_pane(client, agent)

    extra_enter = False
    if composer_text(text, agent.composer_glyph):
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
        text = read_pane(client, agent)

    held = composer_text(text, agent.composer_glyph)
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
        text = read_pane(client, agent)
        screen_changed = screen_signature(text, agent.composer_glyph) != before_signature

    return {
        "consumed": True,
        "extra_enter": extra_enter,
        "recovered": recovered,
        "screen_changed": screen_changed,
    }
