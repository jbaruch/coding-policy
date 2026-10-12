"""Versioned JSON-lines sidecar boundary for an authenticated host connection.

The trusted Python controller constructs RemoteTaskService(owner, authorize)
and passes it to serve(service, reader, writer). This is an embedded sidecar
entry point: bootstrap, live lease and policy loading are host-owned, never
stdin options. A Node host uses fixed executable/argv pipes (shell=False), one
JSON request per line, and preserves request/operation IDs across disconnects.
Caller task data and report content are untrusted data, never instructions for
this transport. See ../references/remote-task-service.md for the wire contract.
"""

import json
import sys

from .command_context import REFUSAL_REMEDIES, refuse
from .errors import ForemanError
from .remote_owner import RemoteForemanOwner


METHODS = frozenset({"task_request", "task_query", "task_inspect", "task_report",
                     "task_cancel", "task_resume", "task_reconcile", "operation_query"})
MAX_REQUEST_CHARS = 524288


class RemoteTaskService:
    """Only this JSON boundary is exposed; the privileged owner stays private."""

    def __init__(self, owner, authorize):
        if not isinstance(owner, RemoteForemanOwner) or not callable(authorize):
            raise refuse("task_service_unconfigured")
        self._owner = owner
        self._authorize = authorize

    def call(self, envelope):
        """Raise typed refusals to embedded callers; serve serializes them."""
        if (not isinstance(envelope, dict) or set(envelope) != {"schema_version", "id", "method", "params"}
                or type(envelope.get("schema_version")) is not int or envelope["schema_version"] != 1
                or not isinstance(envelope.get("id"), str) or not envelope["id"].strip() or len(envelope["id"]) > 128
                or not isinstance(envelope.get("method"), str) or not isinstance(envelope.get("params"), dict)):
            raise refuse("task_service_envelope_invalid")
        method, params = envelope["method"], envelope["params"]
        if method not in METHODS:
            raise refuse("task_service_method_invalid")
        if self._authorize(method, params) is not True:
            raise refuse("task_service_unauthorized")
        result = getattr(self._owner, method)(params)
        return {"schema_version": 1, "id": envelope["id"], "ok": True,
                "result": result, "owner_receipt": self._owner.caller_receipt()}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def serve(service, reader, writer, diagnostics=None):
    """Serve one authenticated pipe until EOF; return 0, or 1 on a refusal.

    Refusals emit one machine-readable error then close the connection. A host
    reconnects with the same durable IDs. Transport/write errors propagate;
    they never trigger an operation retry or clear its recorded outcome.
    """
    while True:
        line = reader.readline(MAX_REQUEST_CHARS + 1)
        if not line:
            return 0
        correlation = None
        try:
            if len(line) > MAX_REQUEST_CHARS or not line.endswith("\n"):
                raise refuse("task_service_envelope_invalid")
            try:
                envelope = json.loads(line, object_pairs_hook=_unique_object)
            except (ValueError, RecursionError):
                raise refuse("task_service_envelope_invalid") from None
            if isinstance(envelope, dict) and isinstance(envelope.get("id"), str) and len(envelope["id"]) <= 128:
                correlation = envelope["id"]
            result = service.call(envelope)
        except ForemanError as exc:
            # Only stable reason codes cross the host boundary. Internal owner
            # diagnostics can carry paths or transport output; never echo them.
            reason = exc.details.get("reason", "owner_state_unavailable")
            action = REFUSAL_REMEDIES.get(reason, "Inspect the trusted controller state and reconcile the same request or operation ID before retrying; preserve owner history.")
            result = {"schema_version": 1, "id": correlation, "ok": False,
                      "error": {"code": exc.code, "reason": reason,
                                "action": action}}
            print("Remote task service refused: {}. {}".format(reason, action), file=diagnostics or sys.stderr)
            writer.write(json.dumps(result, ensure_ascii=True) + "\n")
            writer.flush()
            return 1
        writer.write(json.dumps(result, ensure_ascii=True) + "\n")
        writer.flush()


def serve_stdio(owner, authorize):
    """Supported sidecar entry for a trusted host bootstrap; returns process exit code."""
    return serve(RemoteTaskService(owner, authorize), sys.stdin, sys.stdout)
