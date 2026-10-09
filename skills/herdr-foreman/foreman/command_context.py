"""Immutable, privileged Herdr routing; never parsed from a task request.

Native clients keep their existing environment and executable behavior. Remote
clients use exactly one saved-machine selector and an owner-issued receipt.
The remote authority holds its controller lease through each effect and owns
durable indeterminate-outcome reconciliation, not the task caller.
"""

from dataclasses import dataclass
import re
from typing import Literal

from .errors import HerdrError


class RemoteContextError(HerdrError):
    """Remote ownership could not be proved before an effect."""

    code = "remote_context_refused"


class RemoteIndeterminateError(HerdrError):
    """A remote effect may have happened; no fresh mutation is authorized."""

    code = "remote_effect_indeterminate"


def refuse(reason):
    return RemoteContextError(
        "Remote foreman context is unproved ({}). Restore the configured owner receipt, "
        "same remote identities and current controller lease before dispatch; do not "
        "retarget, invent HERDR_ENV or repeat uncertain input.".format(reason),
        {"reason": reason})


@dataclass(frozen=True)
class RemoteForemanAttestation:
    """Controller-owned identity receipt, not caller-supplied authorization."""

    controller_id: str
    service_principal: str
    machine_profile_id: str
    remote_session: str
    workspace_id: str
    foreman_pane_id: str
    foreman_terminal_id: str
    lease_epoch: int
    policy_version: str
    policy_digest: str

    def __post_init__(self):
        for value in (self.controller_id, self.service_principal, self.remote_session,
                      self.workspace_id, self.foreman_pane_id, self.foreman_terminal_id):
            if not isinstance(value, str) or not value.strip() or any(ord(ch) < 32 for ch in value):
                raise refuse("invalid_attestation_identity")
        if (not isinstance(self.machine_profile_id, str)
                or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.machine_profile_id) is None):
            raise refuse("invalid_saved_profile_id")
        if type(self.lease_epoch) is not int or self.lease_epoch < 1:
            raise refuse("invalid_controller_lease")
        if (not isinstance(self.policy_version, str)
                or re.fullmatch(r"\d+\.\d+\.\d+", self.policy_version) is None
                or not isinstance(self.policy_digest, str)
                or re.fullmatch(r"[a-f0-9]{64}", self.policy_digest) is None):
            raise refuse("invalid_loaded_policy_receipt")


@dataclass(frozen=True)
class HerdrCommandContext:
    """One executable plus an exact argv prefix; no shell-string interpretation."""

    executable: str
    argv_prefix: tuple[str, ...] = ()
    mode: Literal["native", "attested-remote"] = "native"
    attestation: RemoteForemanAttestation | None = None

    def __post_init__(self):
        if (not isinstance(self.executable, str) or not self.executable.strip()
                or "\0" in self.executable or type(self.argv_prefix) is not tuple):
            raise refuse("invalid_command_context")
        if self.mode == "native":
            if self.argv_prefix or self.attestation is not None:
                raise refuse("native_context_has_remote_authority")
        elif self.mode == "attested-remote":
            if (not isinstance(self.attestation, RemoteForemanAttestation)
                    or self.argv_prefix != ("--machine", self.attestation.machine_profile_id)):
                raise refuse("remote_prefix_or_attestation_missing")
        else:
            raise refuse("unknown_context_mode")

    def argv(self, *command):
        return [self.executable, *self.argv_prefix, *command]


# Unknown commands are refused, never optimistically treated as reads.
REMOTE_READS = frozenset({
    ("status", "server"), ("api", "snapshot"),
    ("agent", "get"), ("agent", "list"), ("agent", "read"), ("agent", "wait"),
    ("pane", "get"), ("pane", "read"), ("pane", "layout"),
    ("pane", "process-info"), ("pane", "wait-output"),
})
REMOTE_EFFECTS = frozenset({
    ("agent", "start"), ("agent", "prompt"), ("agent", "send-keys"),
    ("pane", "send-text"), ("pane", "send-keys"), ("pane", "split"),
    ("pane", "close"), ("pane", "rename"), ("workspace", "create"),
})


def remote_command(context, argv):
    prefix = context.argv()
    if argv[:len(prefix)] != prefix:
        raise refuse("command_not_in_bound_context")
    command = argv[len(prefix):]
    operation = tuple(command[:2])
    if operation not in REMOTE_READS | REMOTE_EFFECTS:
        raise refuse("unsupported_remote_operation")
    return command, operation in REMOTE_EFFECTS
