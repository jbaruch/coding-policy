"""Exception types for the foreman utility.

Every error the CLI can produce inherits from :class:`ForemanError`, so the
outermost entry point catches one specific base type instead of a bare
catch-all. Anything that is *not* a ``ForemanError`` is a bug and propagates.
"""


class ForemanError(Exception):
    """Base class for every expected foreman failure.

    ``code`` is a short machine-readable slug emitted in the stderr JSON so a
    caller can branch on the failure without matching on prose.
    """

    code = "foreman_error"

    def __init__(self, message, details=None):
        super().__init__(message)
        self.message = message
        self.details = details if details is not None else {}

    def to_dict(self):
        return {"error": self.code, "message": self.message, "details": self.details}


class ConfigError(ForemanError):
    """The agent config file is missing, unreadable, or malformed."""

    code = "config_error"


class StateError(ForemanError):
    """The state file is unreadable, malformed, or of an unsupported version."""

    code = "state_error"


class ParseError(ForemanError):
    """Pane text did not contain the usage numbers the parser needs."""

    code = "parse_error"


class HerdrError(ForemanError):
    """The `herdr` CLI failed, or returned something that is not JSON."""

    code = "herdr_error"


class AgentBusyError(ForemanError):
    """Refused to write to an agent whose status is `working` or `blocked`."""

    code = "agent_busy"


class StartShellNotReadyError(HerdrError):
    """Pre-input start evidence is absent or shows captured login-shell children.

    Only the fresh-workspace owner may wait and repeat this observation.
    Existing-pane callers retain the ordinary immediate refusal.
    """


class PlanError(ForemanError):
    """The requested roles cannot be assigned from the given snapshot."""

    code = "plan_error"


class UsageError(ForemanError):
    """The invocation itself was wrong (bad --brief, unknown agent, ...)."""

    code = "usage_error"


def owner_recovery(error, kind, operation, condition, *, outcome="blocked", **evidence):
    """Attach a consumer-facing next operation without changing safety proof.

    This is error output only, not durable state or authorization. Owners may
    replace a lower-level outcome after actual cleanup/persistence succeeds.
    """
    message = error.details.get("failure_message", error.message)
    error.details = {**error.details, "failure_kind": kind, "failure_message": message, "recovery": {
        "outcome": outcome, "operation": operation, "condition": condition,
        "evidence": evidence}}
    error.message = "{} Next owner operation: `{}`. {}".format(message, operation, condition)
    error.args = (error.message,)
    return error


#: `failure_kind` and `wait-report.sh` exit-6 reason for a launched model
#: identifier the account cannot call. Seat-local model maintenance on the SAME
#: provider: the identifier is the suspect, not the provider, so no
#: whole-provider exclusion, capability-list edit or refusal record follows (#733).
IDENTIFIER_UNAVAILABLE_KIND = "model_identifier_unavailable"
IDENTIFIER_UNAVAILABLE_RECOVERY = (
    "The launched model identifier is unavailable to this account. Check the provider's current catalog, the "
    "installed CLI's model and flag spellings, and this account's actual access; repair that exact configured "
    "row (or a verified same-family successor) in the operator's config; validate the repaired row by planning the "
    "seat again; then dispatch the affected seat. The provider, the other callable rows, the task, its original base, its correction "
    "count and its artifacts stay as recorded.")


#: Trusted structured `agent_start` error codes. Only Herdr's own code field
#: counts; provider prose in the same payload never classifies a failure.
IDENTIFIER_ERROR_CODES = frozenset({"model_not_found", "unsupported_model"})
#: `failure_kind` for a native start that failed on a 5xx class server error
#: before any input. A start timeout or transport error may already have sent
#: input, so it stays unclassified. The class takes the bounded retry, never a model retirement.
TRANSIENT_LAUNCH_KIND = "launch_transient"
TRANSIENT_LAUNCH_ERROR_CODES = frozenset({
    "service_unavailable", "bad_gateway", "gateway_timeout", "internal_error", "overloaded"})
