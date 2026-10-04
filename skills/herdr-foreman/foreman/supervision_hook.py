"""Native Claude/Codex Stop contract for an explicitly bound Herdr foreman only.

stdin: native Stop JSON containing cwd and session_id and/or transcript_path.
stdout: {decision:block,reason:...} only for the matching bound foreman with
unhandled events, active observation obligations, or unreadable owner state.
Workers, other native sessions, and never-bound sessions produce no output.
No writes, worker contact, process termination, or acknowledgement occurs.

stop_hook_active does not waive supervision. A genuine user pause remains a
supported boundary. A handoff also needs a matching live reset continuation.
"""

import json
import os
import sys

from . import runnable
from . import foreman_reset
from . import supervision as store
from . import supervision_runtime as runtime
from .errors import ForemanError


def block(reason):
    return {"decision": "block", "reason": "Herdr supervision — " + reason}


def _ineligible_handoff_clause(continuation):
    """Next action for an ineligible current handoff, keyed by continuation state."""
    state = continuation["state"]
    prefix = (" The saved handoff prepares a reset but does not transfer supervision "
              "({}).".format(state))
    if state == "reset_missing":
        return prefix + " Schedule its exact live continuation with `{}`.".format(
            runnable.command("foreman-reset"))
    if state == "reset_deliverer_not_live":
        return prefix + " Reconcile that reset row with `{}`; do not schedule another attempt for this stow.".format(
            runnable.command("foreman-reset-reconcile"))
    if state in ("reset_failed", "reset_interrupted", "reset_delivered", "reset_reconciled"):
        return prefix + " Keep the current turn and foreground watch; do not retry this stow."
    return prefix + " Keep the current turn and foreground watch."


def check(payload, environ, at, *, root=None, probe=runtime.process_identity):
    if not environ.get("HERDR_ENV") or not environ.get("HERDR_PANE_ID") or not isinstance(payload, dict):
        return None
    if not isinstance(payload.get("cwd"), str) or not payload["cwd"]:
        return None
    for kind, field in (("id", "session_id"), ("path", "transcript_path")):
        value = payload.get(field)
        if not isinstance(value, str) or not value:
            continue
        who = store.identity(value, payload["cwd"], environ["HERDR_ENV"], kind=kind, pane_id=environ["HERDR_PANE_ID"])
        path = store.binding_path(who, root)
        try:
            binding = store.read_json(path)
            if binding is None:
                continue
            if (not isinstance(binding, dict) or binding.get("schema_version") != 1
                    or binding.get("identity") != who or not isinstance(binding.get("state_path"), str)
                    or type(binding.get("generation")) is not int or binding["generation"] < 1):
                return block("The exact foreman binding is unreadable. Restore {} and reconcile its saved state before stopping.".format(path))
            data = store.load(binding["state_path"])
            if data["binding"] is None:
                return block("This bound foreman's supervision state is missing. Restore {} before stopping; missing state cannot prove its work was resolved.".format(store.store_path(binding["state_path"])))
            if data["binding"]["identity"] != who:
                if binding["generation"] >= data["binding"]["generation"]:
                    return block("This foreman's native binding handoff is incomplete. Retry `{}` for the same owner state before stopping.".format(
                        runnable.command("supervision-bind")))
                continue
            events = store.pending(data)
            active = [row["id"] for row in data["members"] if row["active"]]
            if not events and not active:
                return None
            if not events and store.current_holds(data, "waiting_for_user"):
                return None
            continuation = None
            if not events and store.current_holds(data, "handoff"):
                continuation = foreman_reset.stop_coverage(binding["state_path"], data, probe=probe)
                if continuation["eligible"]:
                    return None
            health = runtime.health(data, at, probe)
            handoff = ""
            if continuation is not None:
                handoff = _ineligible_handoff_clause(continuation)
            return block("{} active assignment(s), {} unhandled event(s); watcher is {}.{} Run `{}`, reconcile report/ledger evidence, acknowledge handled outcomes, and keep awaiting the foreground `{}` handle. A quiet watch deadline is a checkpoint: start the next foreground watch while authorized work remains. A genuine user-requested pause may use `{}`; a handoff permits Stop only after its matching reset continuation is live. State: {}".format(
                len(active), len(events), health["state"], handoff, runnable.command("supervision-drain"), runnable.command("supervision-watch"),
                runnable.command("supervision-hold"),
                binding["state_path"]))
        except ForemanError as exc:
            return block("Cannot verify this bound foreman's supervision: {} Resume from its saved state before stopping.".format(exc))
    return None


def main():
    # The CLI remains the package's single wall-clock source.
    from .cli import now_iso
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print("herdr-supervision-stop: cannot identify the native session from invalid hook JSON: {}. Restore native hook payload delivery.".format(exc), file=sys.stderr)
        return 0
    bound = False
    try:
        if isinstance(payload, dict) and payload.get("cwd") and os.environ.get("HERDR_ENV") and os.environ.get("HERDR_PANE_ID"):
            for kind, field in (("id", "session_id"), ("path", "transcript_path")):
                if isinstance(payload.get(field), str) and payload[field]:
                    who = store.identity(payload[field], payload["cwd"], os.environ["HERDR_ENV"], kind=kind, pane_id=os.environ["HERDR_PANE_ID"])
                    bound = bound or store.binding_path(who).exists()
        result = check(payload, os.environ, now_iso())
    # outer-boundary-process-contract: native Stop treats nonzero/invalid stdout
    # as no block; emit a structured block for unexpected evaluation errors so
    # a bound foreman's supervision does not silently disappear on a traceback.
    except Exception as exc:
        print("herdr-supervision-stop: evaluation failed ({}); inspect the hook installation.".format(type(exc).__name__), file=sys.stderr)
        if bound:
            print(json.dumps(block("The bound foreman's supervision could not be evaluated. Restore the hook/state installation and reconcile active work before stopping.")))
        return 0
    if result is not None:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
