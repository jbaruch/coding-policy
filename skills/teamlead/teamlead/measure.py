"""Measure each agent's remaining subscription budget.

The flow per agent is always the same:

1. read live status with `herdr agent get` -- never trust the last snapshot
2. refuse to write to a `working` or `blocked` agent unless --force
3. send the agent's usage slash command
4. wait for its marker to appear with `herdr pane wait-output`, never sleep
5. read the pane and parse it
6. close the dialog when the agent opens one (claude's `/usage` does)

`measured_at` is passed in. Nothing in this module reads the clock: that is a
CLI-layer concern, which is what lets the tests assert on an exact timestamp.
"""

import re

from .errors import HerdrError, ParseError
from .herdr import BUSY_STATES, DEFAULT_MARKER_TIMEOUT_MS, READY_STATES
from .parsers import headroom_pct, parse_usage

MEASURE_SCHEMA_VERSION = 1

#: Lines to pull for an inline report. `visible` reads the viewport and needs
#: no line count, so this applies to the scrollback sources only.
DEFAULT_READ_LINES = 80


def marker_pattern(marker):
    """Turn a literal config marker into a regex herdr's engine accepts."""
    return re.escape(marker)


def skipped_record(agent, status):
    """The record for an agent teamlead declined to interrupt."""
    return {
        "kind": agent.kind,
        "state": status,
        "windows": None,
        "credits": None,
        "headroom_pct": None,
        "skipped": True,
    }


def measure_agent(client, agent, marker_timeout_ms=DEFAULT_MARKER_TIMEOUT_MS, read_lines=DEFAULT_READ_LINES, force=False):
    """Measure one agent and return its record.

    Raises HerdrError or ParseError; the caller decides whether one bad agent
    fails the whole run.
    """
    info = client.agent_get(agent.name)
    status = info.get("agent_status")
    pane_id = info.get("pane_id")

    if status in BUSY_STATES and not force:
        record = skipped_record(agent, status)
        record["pane_id"] = pane_id
        return record

    if not pane_id:
        raise HerdrError(
            "herdr reported no pane for agent {!r} - confirm it is live with "
            "`herdr agent list`.".format(agent.name),
            {"agent": agent.name},
        )

    client.agent_prompt(agent.name, agent.usage_prompt)
    client.pane_wait_output(
        pane_id,
        regex=marker_pattern(agent.usage_marker),
        source=agent.usage_read_source,
        timeout_ms=marker_timeout_ms,
    )
    lines = None if agent.usage_read_source == "visible" else read_lines
    text = client.agent_read(agent.name, source=agent.usage_read_source, lines=lines)

    try:
        parsed = parse_usage(agent.kind, text)
    finally:
        # Close the dialog even when parsing failed, so a bad read never
        # leaves an agent stuck behind a modal it cannot type through.
        if agent.close_keys:
            client.agent_send_keys(agent.name, agent.close_keys)

    windows = parsed["windows"]
    return {
        "kind": agent.kind,
        "state": status,
        "pane_id": pane_id,
        "windows": windows,
        "credits": parsed["credits"],
        "headroom_pct": headroom_pct(windows),
        "skipped": False,
    }


def measure(client, agents, measured_at, marker_timeout_ms=DEFAULT_MARKER_TIMEOUT_MS, read_lines=DEFAULT_READ_LINES, force=False):
    """Measure every agent in `agents` and return the snapshot document.

    A failure on one agent is recorded on that agent's record and does not
    stop the others; the caller inspects `failed_agents` to decide the exit
    code. Only HerdrError and ParseError are absorbed -- anything else is a
    bug and propagates.
    """
    records = {}
    failures = []
    for agent in agents:
        try:
            records[agent.name] = measure_agent(
                client,
                agent,
                marker_timeout_ms=marker_timeout_ms,
                read_lines=read_lines,
                force=force,
            )
        except (HerdrError, ParseError) as exc:
            failures.append(agent.name)
            records[agent.name] = {
                "kind": agent.kind,
                "state": None,
                "windows": None,
                "credits": None,
                "headroom_pct": None,
                "skipped": False,
                "error": {"code": exc.code, "message": exc.message},
            }
    return {
        "schema_version": MEASURE_SCHEMA_VERSION,
        "measured_at": measured_at,
        "agents": records,
        "failed_agents": failures,
    }


def ready_agents(snapshot):
    """Names of agents in the snapshot that were ready for input."""
    return sorted(
        name
        for name, record in snapshot.get("agents", {}).items()
        if record.get("state") in READY_STATES
    )
