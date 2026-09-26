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


class PlanError(ForemanError):
    """The requested roles cannot be assigned from the given snapshot."""

    code = "plan_error"


class UsageError(ForemanError):
    """The invocation itself was wrong (bad --brief, unknown agent, ...)."""

    code = "usage_error"
