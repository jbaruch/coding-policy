"""Resource use per task through acceptance, from the owner records alone (#602).

Read-only. Joins the assignment ledger, the recovery events and the measure
snapshots already in the state file; it records nothing and asks no worker.

A task is `accepted` when a `task_closed` event with outcome `merged` is in
force, `abandoned` for outcome `abandoned`, and `open` otherwise. The closure
time ends the task's span: assignments and events after it are not counted,
and an open task has no end, so its elapsed time and window use are `unknown`.

Each quantity is reported separately and never summed into one cost:

- `tokens` -- no owner record carries token counts, so every field is `unknown`
  (`UNRECORDED` lists them)
- `elapsed_seconds` -- the first applied assignment (or the task's
  registration) to its closure
- `correction_rounds` -- the highest cumulative `fix_round` among the task's
  applied developer rows
- `work` -- applied assignments per responsibility
- `coordination` -- what dispatch cost around the work: rows that never
  started, transport retries, unsent dispatches and recorded provider
  refusals; the foreman's own tokens are `unknown`
- `windows` -- headroom movement of each usage window a task worker drew on,
  between the last snapshot at or before the span's start and the first at or
  after its end

A window's movement is attributed to the task only when nothing else is known
to have drawn on it: an unshared window, and no other task's assignment on it
during the span. Otherwise `attribution` is `unknown`, whatever it moved. A
reset between the two readings (a changed reset text, or remaining headroom
that rose) makes the movement itself `unknown`. Consumers the ledger never
records -- the foreman, the operator's own sessions -- are invisible here, so
an attributed movement is an upper bound set by recorded work, never a saving.
"""

from datetime import timezone

from .chronology import timestamp
from .errors import UsageError
from .recovery import task_closure
from .tiers import canonical_role, measured_pressure

REPORT_SCHEMA_VERSION = 1
UNKNOWN = "unknown"

#: Fields no owner record carries yet. Named in every report so a reader
#: never mistakes a missing number for a zero.
UNRECORDED = ("tokens.uncached_input", "tokens.cached_input", "tokens.output", "coordination.foreman_tokens")

#: Recovery event kinds counted as coordination overhead, keyed by report field.
COORDINATION_EVENTS = {
    "transport_retries": "dispatch_transport_retry",
    "dispatches_not_sent": "dispatch_not_sent",
    "provider_refusals": "provider_refusal_recorded",
}


def _utc(instant):
    return None if instant is None else instant.astimezone(timezone.utc).isoformat()


def _snapshot_time(snapshot, index):
    return timestamp(snapshot.get("measured_at"), "Snapshot {} measured_at".format(index))


def _usable(record):
    """A record's windows when it holds a live reading, else None."""
    if (not isinstance(record, dict) or record.get("skipped") or record.get("error")
            or not isinstance(record.get("windows"), dict) or not record["windows"]):
        return None
    return record["windows"]


def _reading(pair, agent):
    """One agent's usable windows in a `(time, snapshot)` pair, or None."""
    if pair is None:
        return None
    agents = pair[1].get("agents")
    return _usable(agents.get(agent)) if isinstance(agents, dict) else None


def _pool(snapshots, agent):
    """`(pool, shared, members)`: the window an agent reports through, whether another agent shares it, and who does."""
    group = None
    members = set()
    for snapshot in snapshots:
        agents = snapshot.get("agents") if isinstance(snapshot.get("agents"), dict) else {}
        record = agents.get(agent)
        if isinstance(record, dict) and isinstance(record.get("window_group"), str) and record["window_group"]:
            group = record["window_group"]
    if group is None:
        return agent, False, {agent}
    for snapshot in snapshots:
        agents = snapshot.get("agents") if isinstance(snapshot.get("agents"), dict) else {}
        members |= {name for name, record in agents.items()
                    if isinstance(record, dict) and record.get("window_group") == group}
    members.add(agent)
    return group, len(members) > 1, members


def _bracket(snapshots, start, end):
    """The last snapshot at or before `start` and the first at or after `end`."""
    timed = [(_snapshot_time(snapshot, index), snapshot) for index, snapshot in enumerate(snapshots)]
    before = [pair for pair in timed if pair[0] <= start]
    after = [pair for pair in timed if pair[0] >= end]
    return (max(before, key=lambda pair: pair[0]) if before else None,
            min(after, key=lambda pair: pair[0]) if after else None)


def _windows(task, agents_used, snapshots, assignments, start, end):
    if end is None or start is None:
        return [{"pool": UNKNOWN, "agents": sorted(agents_used), "window": UNKNOWN, "before_pct": UNKNOWN,
                 "after_pct": UNKNOWN, "consumed_pct": UNKNOWN, "attribution": UNKNOWN,
                 "reason": "task_open" if end is None else "no_start"}] if agents_used else []
    before, after = _bracket(snapshots, start, end)
    pools = {}
    for agent in sorted(agents_used):
        pool, shared, members = _pool(snapshots, agent)
        entry = pools.setdefault(pool, {"agents": [], "shared": shared, "members": members})
        entry["agents"].append(agent)
    out = []
    for pool, entry in sorted(pools.items()):
        concurrent = any(
            row.get("agent") in entry["members"] and row.get("task") != task
            and start <= timestamp(row.get("at"), "Assignment {} chronology".format(index)) <= end
            for index, row in enumerate(assignments))
        readings = None
        for agent in entry["agents"]:
            b, a = _reading(before, agent), _reading(after, agent)
            if b is not None and a is not None:
                readings = (b, a)
                break
        base = {"pool": pool, "agents": entry["agents"]}
        if readings is None:
            reason = "no_snapshot_before" if before is None else "no_snapshot_after" if after is None else "unmeasured"
            out.append({**base, "window": UNKNOWN, "before_pct": UNKNOWN, "after_pct": UNKNOWN,
                        "consumed_pct": UNKNOWN, "attribution": UNKNOWN, "reason": reason})
            continue
        b, a = readings
        for name in sorted(set(b) | set(a)):
            start_w, end_w = b.get(name), a.get(name)
            # A snapshot is a file on disk; a hand-edited or truncated reading
            # is unmeasured, never a number to subtract.
            before_pct = measured_pressure(start_w.get("remaining_pct")) if isinstance(start_w, dict) else None
            after_pct = measured_pressure(end_w.get("remaining_pct")) if isinstance(end_w, dict) else None
            reason = None
            consumed = UNKNOWN
            if before_pct is None or after_pct is None:
                reason = "unmeasured"
            elif (start_w.get("resets") is None or start_w.get("resets") != end_w.get("resets")
                  or after_pct > before_pct):
                reason = "window_reset"
            else:
                consumed = before_pct - after_pct
                if entry["shared"]:
                    reason = "shared_window"
                elif concurrent:
                    reason = "concurrent_work"
            out.append({**base, "window": name,
                        "before_pct": UNKNOWN if before_pct is None else before_pct,
                        "after_pct": UNKNOWN if after_pct is None else after_pct,
                        "consumed_pct": consumed,
                        "attribution": "task" if reason is None else UNKNOWN,
                        "reason": reason})
    return out


def _task_report(task, store, assignments, snapshots):
    closure = task_closure(store, assignments, task)
    status = "open" if closure is None else ("accepted" if closure["details"]["outcome"] == "merged" else "abandoned")
    end = None if closure is None else timestamp(closure["at"], "Task {!r} closure time".format(task))
    rows = []
    for index, row in enumerate(assignments):
        if row.get("task") != task:
            continue
        at = timestamp(row.get("at"), "Assignment {} chronology".format(index))
        if end is None or at <= end:
            rows.append((at, row))
    applied = [(at, row) for at, row in rows if row.get("status") == "applied"]
    registered = store["tasks"].get(task)
    starts = [at for at, _row in applied]
    if not starts and registered is not None:
        starts = [timestamp(registered["at"], "Task {!r} registration time".format(task))]
    start = min(starts) if starts else None
    work = {}
    for _at, row in applied:
        role = canonical_role(row.get("role"))
        work[role] = work.get(role, 0) + 1
    events = [event for event in store["events"] if event.get("task") == task
              and (end is None or timestamp(event.get("at"), "Event {} time".format(event.get("sequence"))) <= end)]
    coordination: dict[str, int | str] = {field: sum(1 for event in events if event.get("kind") == kind)
                                          for field, kind in COORDINATION_EVENTS.items()}
    coordination["unstarted_assignments"] = sum(1 for _at, row in rows if row.get("status") != "applied")
    coordination["foreman_tokens"] = UNKNOWN
    return {
        "task": task, "status": status,
        "started_at": _utc(start), "ended_at": _utc(end),
        "elapsed_seconds": UNKNOWN if end is None or start is None else (end - start).total_seconds(),
        "tokens": {"uncached_input": UNKNOWN, "cached_input": UNKNOWN, "output": UNKNOWN},
        "correction_rounds": max((row.get("fix_round") or 0 for _at, row in applied
                                  if canonical_role(row.get("role")) == "developer"), default=0),
        "work": dict(sorted(work.items())),
        "coordination": coordination,
        "windows": _windows(task, {row.get("agent") for _at, row in applied}, snapshots, assignments, start, end),
    }


def report(state, task=None):
    """`{"schema_version", "unrecorded", "tasks": [...]}` for every task, or the one named."""
    store, assignments = state["recovery"], state["assignments"]
    known = sorted(set(store["tasks"]) | {row["task"] for row in assignments if row.get("task")})
    if task is not None:
        if task not in known:
            raise UsageError("Task {!r} has no registration or assignment in this state; check its identity against "
                             "the ledger's task list.".format(task), {"task": task, "known": known})
        known = [task]
    snapshots = [snapshot for snapshot in state.get("snapshots", []) if isinstance(snapshot, dict)]
    return {"schema_version": REPORT_SCHEMA_VERSION, "unrecorded": list(UNRECORDED),
            "tasks": [_task_report(name, store, assignments, snapshots) for name in known]}
