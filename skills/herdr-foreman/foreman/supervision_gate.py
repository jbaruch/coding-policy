"""Which supervision events need the foreman, and which are noise.

Herdr records every change it observes in a worker, and the foreman acknowledges
all of them. Over 1790 recorded events it acknowledged 1790: every one cost a
full lead turn, and a lead turn ships the foreman's whole conversation.

Much of it carries nothing a lead can act on. `visible_observed` is a sha256 of
the worker's screen, and before any report file exists it says only that a
worker is working; `recheck_due` is the foreman's own deferral coming back.

This is a SCRIPT, not a classifier, and the reason is where the information
lives rather than how the question feels. `{"kind": "visible_observed", "data":
{"sha256": "5f084..."}}` has no meaning to read. Deciding whether the foreman is
needed means joining it against other state -- has a report landed, is the
worker working, has anything changed since the deferral. A join is a script
(rules/script-delegation.md).

DEFAULT-WAKE is the whole safety property. Only named cases are suppressed;
everything else wakes the foreman, including a kind this module has never seen.
That matters: `<key>_observed` kinds are generated from whatever an observation
samples, and the recorded history contains none of the failure kinds --
`watcher_lost`, `observation_error_observed`, `report_error_observed`,
`unavailable_observed`. A gate enumerating what to WAKE on would be silent for
exactly those. Enumerating what to SUPPRESS means an unknown kind costs one lead
turn instead of a missed failure.

Read-only. It decides nothing and writes nothing; the caller acts on the verdict.
"""

import json
from pathlib import Path

from .errors import UsageError

SCHEMA_VERSION = 1

#: Kinds this module suppresses, each under the conditions in `_verdict`.
#: Every other kind wakes the foreman, whether or not it appears here.
SUPPRESSIBLE = ("visible_observed", "recheck_due")

#: How many consecutive unchanged rechecks may be suppressed for one deferral
#: before the foreman sees it anyway. A worker whose observed state never moves is
#: indistinguishable from a stalled one, and a stall is the foreman's to judge.
MAX_QUIET_RECHECKS = 3


def _members(data):
    return {row["id"]: row for row in data.get("members", [])}


def _acknowledged(data):
    return {row["event"]: row for row in data.get("acknowledgements", [])}


def _verdict(event, member, state, quiet_rechecks):
    """`(wake, reason)` for one event against the state observed at that moment.

    `state` is the member's sample as of this event, rebuilt from the
    `<key>_observed` events before it -- never the member's CURRENT sample,
    which would judge a historical event by a later worker.
    """
    kind = event["kind"]
    if kind not in SUPPRESSIBLE:
        return True, "kind is not suppressible"
    if member is None:
        return True, "event has no member to join against"

    if kind == "visible_observed":
        # Before any report file exists, a screen hash is a worker working.
        # After one exists it is not noise: a report FILE is not delivery.
        # Delivery is the file plus the `REPORT: <path>` marker in the worker's
        # final message, and that marker reaches the supervisor only as a screen
        # change. Replayed on 1790 recorded events, suppressing screen hashes
        # unconditionally lost 3 deliveries outright and delayed 5 more by 5 to
        # 49 minutes, because `report_observed` had already fired for the file
        # and nothing fires again for the marker.
        if (state.get("report") or {}).get("present"):
            return True, "a report file exists; its delivery marker arrives as a screen change"
        return False, "screen changed before any report file exists"

    # `recheck_due`: the foreman deferred an event to a later time. If nothing the
    # supervisor observes has moved since, looking again reads the same state.
    if state != event.get("_state_at_deferral"):
        return True, "observed state changed since the deferral"
    seen = quiet_rechecks.get(event["data"].get("event"), 0)
    if seen >= MAX_QUIET_RECHECKS:
        return True, "state unchanged across {} rechecks: a stall is the foreman's to judge".format(seen)
    return False, "observed state unchanged since the deferral"


def evaluate(data):
    """Replay the store's events in order and return a verdict for each.

    Ordering matters twice over: the per-member sample is rebuilt forward from
    `<key>_observed` values, and a deferral's baseline is the sample as of the
    event it defers.
    """
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        raise UsageError("Supervision data must carry an events array; pass a store this build wrote.", {})
    members = _members(data)
    acknowledged = _acknowledged(data)

    samples = {}
    baselines = {}
    quiet_rechecks = {}
    wake, suppressed = [], []

    for event in sorted(data["events"], key=lambda row: row.get("seq", 0)):
        member_id = event.get("member")
        state = dict(samples.get(member_id, {}))
        deferred = event["data"].get("event") if event["kind"] == "recheck_due" else None
        enriched = {**event, "_state_at_deferral": baselines.get(deferred)} if deferred else event

        decision, reason = _verdict(enriched, members.get(member_id), state, quiet_rechecks)
        row = {"event": event["id"], "kind": event["kind"], "member": member_id, "reason": reason}
        if decision:
            wake.append(row)
            if deferred and reason == "observed state changed since the deferral":
                quiet_rechecks[deferred] = 0
        else:
            suppressed.append(row)
            if deferred:
                quiet_rechecks[deferred] = quiet_rechecks.get(deferred, 0) + 1

        # Apply this event's observation AFTER judging it, so a verdict never
        # reads the change it is judging.
        if member_id is not None and event["kind"].endswith("_observed"):
            key = event["kind"][: -len("_observed")]
            samples.setdefault(member_id, {})[key] = event["data"]
        baselines[event["id"]] = dict(samples.get(member_id, {}))

    total = len(wake) + len(suppressed)
    return {"schema_version": SCHEMA_VERSION, "wake": wake, "suppressed": suppressed,
            "counts": {"total": total, "wake": len(wake), "suppressed": len(suppressed),
                       "acknowledged": sum(1 for row in data["events"] if row["id"] in acknowledged)}}


def pending(data):
    """Verdicts for the events the foreman has not acknowledged yet.

    The whole store is replayed, since each verdict reads the state as of its
    own event; only the unacknowledged ones are reported.
    """
    result = evaluate(data)
    done = set(_acknowledged(data))
    open_ids = {row["id"] for row in data["events"] if row["id"] not in done}
    wake = [row for row in result["wake"] if row["event"] in open_ids]
    suppressed = [row for row in result["suppressed"] if row["event"] in open_ids]
    return {"schema_version": SCHEMA_VERSION, "wake": wake, "suppressed": suppressed,
            "counts": {"pending": len(open_ids), "wake": len(wake), "suppressed": len(suppressed)}}


def load(path):
    target = Path(path)
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise UsageError("No supervision store at {}; bind supervision before gating its events.".format(target), {}) from None
    except (OSError, UnicodeDecodeError) as exc:
        raise UsageError("Cannot read the supervision store at {}: {}. Restore a readable UTF-8 file.".format(target, exc), {}) from None
    except json.JSONDecodeError as exc:
        raise UsageError("The supervision store at {} is not valid JSON ({}); restore the owner-written file.".format(target, exc.msg), {}) from None
