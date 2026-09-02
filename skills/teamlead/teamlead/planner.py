"""Deterministic role-to-agent assignment.

A pure function of (roles, snapshot, previous role counts). Same inputs, same
output, every time -- no clock, no filesystem, no herdr. Planning never touches
an agent.

Roles arrive heaviest-first: `developer` burns the most tokens, `reviewer` the
least. Agents are ranked by headroom -- the smallest remaining percentage
across all their usage windows -- so the agent with the most budget left gets
the heaviest role.

Ordering, in full:

1. Agents with a known headroom sort before agents whose headroom is null
   (busy, unmeasured, or unparseable).
2. Known-headroom agents sort by headroom descending.
3. A headroom tie is broken toward the agent that has held *the role being
   assigned* the fewest times, which stops one agent from owning `developer`
   forever.
4. Any remaining tie is broken by agent name, ascending.
5. Null-headroom agents sort by name alone. They are the fallback pool, and
   the ledger has nothing useful to say about an agent nobody could measure.
"""

import math

from .diagnostics import stderr_warn
from .errors import PlanError

PLAN_SCHEMA_VERSION = 1


def _sort_key(name, headroom, role, counts):
    """Rank one candidate for one role. Lower sorts first."""
    if headroom is None:
        return (1, 0.0, 0, name)
    return (0, -float(headroom), counts.get(role, {}).get(name, 0), name)


def _headroom_of(name, record, warn):
    """This agent's headroom as a float, or None when it is not a usable number.

    A snapshot is a file on disk: hand-edited, written by an older build, or
    truncated mid-write. `float()` on whatever it happens to hold crashes the
    plan, and a crash here loses the whole round over one bad field. Anything
    that is not a finite number reads as unknown instead, which already has a
    defined place in the ordering -- last, and named in the rationale.
    """
    if not isinstance(record, dict):
        warn(
            "snapshot entry for {!r} is {}, not an object; treating its "
            "headroom as unknown.".format(name, type(record).__name__)
        )
        return None

    value = record.get("headroom_pct")
    if value is None:
        return None

    # bool is a subclass of int, and `true` is not 100% headroom.
    if isinstance(value, bool):
        warn(
            "headroom_pct for {!r} is {!r}, not a number; treating it as "
            "unknown.".format(name, value)
        )
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        warn(
            "headroom_pct for {!r} is {!r}, which is not a number; treating it "
            "as unknown.".format(name, value)
        )
        return None

    # NaN compares false against everything, which would make the sort order
    # depend on input order -- and this planner is documented deterministic.
    if math.isnan(number) or math.isinf(number):
        warn(
            "headroom_pct for {!r} is {!r}, which cannot be ordered; treating "
            "it as unknown.".format(name, value)
        )
        return None
    return number


def plan(roles, snapshot, counts=None, snapshot_ref=None, warn=None):
    """Assign `roles` (heaviest first) to the agents in `snapshot`.

    `counts` is `{role: {agent: times_held}}` from the state ledger; omit it
    to plan with no history. `snapshot_ref` is echoed back so a caller can tell
    which measurement the plan was built on.

    Raises PlanError when there are no roles, no agents, or fewer agents than
    roles -- silently dropping a role would hide work nobody is doing.
    """
    counts = counts or {}
    roles = list(roles)
    if not roles:
        raise PlanError(
            "No roles to assign - pass --roles with a comma-separated list, "
            "heaviest first, e.g. --roles developer,tester,reviewer.",
            {},
        )
    duplicates = sorted({role for role in roles if roles.count(role) > 1})
    if duplicates:
        raise PlanError(
            "Role {} appears more than once in --roles - each role is assigned "
            "to exactly one agent.".format(", ".join(duplicates)),
            {"duplicates": duplicates},
        )

    agents = (snapshot or {}).get("agents") or {}
    if not agents:
        raise PlanError(
            "Snapshot contains no agents - run `teamlead measure` first, or "
            "pass --snapshot pointing at a snapshot that has an `agents` object.",
            {},
        )
    if len(agents) < len(roles):
        raise PlanError(
            "Cannot assign {} roles across {} agent(s) - measure more agents or "
            "pass fewer roles.".format(len(roles), len(agents)),
            {"roles": roles, "agents": sorted(agents)},
        )

    warn = warn or stderr_warn
    headrooms = {
        name: _headroom_of(name, record, warn) for name, record in agents.items()
    }
    remaining = set(headrooms)
    assignments = {}
    rationale = []

    for role in roles:
        ranked = sorted(remaining, key=lambda name: _sort_key(name, headrooms[name], role, counts))
        chosen = ranked[0]
        remaining.discard(chosen)
        assignments[role] = chosen
        rationale.append(_explain(role, chosen, ranked, headrooms, counts))

    # The loop discards each choice from `remaining`, so a duplicate is
    # unreachable today. Asserted anyway: `apply` refuses a plan that repeats
    # an agent, and a refactor that broke this would surface there, one herdr
    # session later, as a refusal nobody could trace back to here.
    if len(set(assignments.values())) != len(assignments):
        raise PlanError(
            "Planner produced the same agent for several roles ({}) - this is a "
            "bug in the planner, not in the input; report it with the snapshot "
            "that triggered it.".format(assignments),
            {"assignments": assignments},
        )

    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "assignments": assignments,
        "rationale": rationale,
        "snapshot_ref": snapshot_ref
        if snapshot_ref is not None
        else {"source": None, "measured_at": (snapshot or {}).get("measured_at")},
    }


def _explain(role, chosen, ranked, headrooms, counts):
    """One human-readable sentence per assignment, in ranking order."""
    held = counts.get(role, {}).get(chosen, 0)
    headroom = headrooms[chosen]
    if headroom is None:
        reason = "no headroom reading (busy, skipped, or unparseable)"
    else:
        reason = "{:g}% headroom".format(headroom)
    field = ", ".join(
        "{}={}".format(name, "null" if headrooms[name] is None else "{:g}%".format(headrooms[name]))
        for name in ranked
    )
    return "{} -> {} ({}; held this role {}x before; field was {})".format(
        role, chosen, reason, held, field
    )
