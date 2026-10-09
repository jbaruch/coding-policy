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


_REMEDY_GROUPS = (
    (("invalid_attestation_identity", "invalid_saved_profile_id", "invalid_controller_lease",
      "invalid_loaded_policy_receipt", "remote_attestation_missing", "remote_prefix_or_attestation_missing"),
     "Have the trusted controller issue a valid receipt for the configured saved profile, exact identities, current lease and loaded policy."),
    (("invalid_command_context", "untyped_command_context", "unknown_context_mode",
      "native_context_has_remote_authority", "remote_authority_on_native_context",
      "executable_override_of_bound_context", "command_not_in_bound_context", "owner_command_context_changed"),
     "Construct one typed native or attested-remote context in trusted configuration; do not override its executable or routing."),
    (("remote_authority_missing", "unverified_remote_owner", "remote_owner_not_attached", "remote_owner_needs_attested_context"),
     "Attach the verified RemoteForemanOwner for this exact attested context before using the client."),
    (("remote_current_lease_reader_missing", "remote_current_lease_unavailable"),
     "Configure and restore the trusted runtime's independent live lease reader; keep dispatch disabled while it is unavailable."),
    (("remote_current_lease_revoked",),
     "Stop this stale controller and preserve its original history. Keep deployment disabled until an owner-controlled handoff is available; do not reuse its revoked receipt or erase its store."),
    (("unsupported_remote_operation", "remote_effect_classification_changed"),
     "Use a supported bound Herdr operation; correct the owner integration rather than forwarding arbitrary commands or local process signals."),
    (("loaded_policy_missing", "loaded_policy_unreadable"),
     "Restore readable installed rules, foreman skill and plugin manifest, then load them before issuing the receipt."),
    (("loaded_policy_receipt_changed", "loaded_policy_changed_after_attachment"),
     "Restore the originally attested policy or keep deployment disabled until an owner-controlled policy handoff is available. Preserve the original store and history; do not reinitialize it for new policy bytes."),
    (("remote_identity_read_failed", "remote_identity_transport_unavailable", "remote_server_not_ready"),
     "Check connectivity and server readiness through the SAME saved machine profile; restore that endpoint before retrying an observation."),
    (("remote_identity_read_invalid", "duplicate_remote_status_fact", "remote_snapshot_missing", "remote_snapshot_invalid"),
     "Inspect the selected Herdr server's status/snapshot response and repair its command-surface compatibility before dispatch."),
    (("remote_session_changed", "remote_workspace_or_foreman_changed"),
     "Restore or explicitly reconcile the original session, workspace and thin-anchor terminal; do not retarget the receipt."),
    (("remote_foreman_seat_has_another_brain", "another_foreman_brain_forbidden", "remote_thin_foreman_seat_is_not_a_worker"),
     "Keep the thin anchor non-LLM and untouched; launch or address a separate worker pane instead."),
    (("remote_owner_store_not_absolute",),
     "Configure an absolute canonical owner-store path in trusted controller configuration."),
    (("remote_owner_store_not_private",),
     "Restore the controller-owned regular owner directory to exact mode 0700 before accessing it."),
    (("remote_owner_record_not_private",),
     "Restore the original controller-owned regular owner file to exact mode 0600; do not replace it with a symlink."),
    (("remote_owner_store_unavailable",),
     "Restore access to the original private owner directory without discarding its intents or request history."),
    (("remote_owner_record_missing_or_unreadable", "remote_owner_record_lost_restore_original_intents_and_requests",
      "remote_owner_receipt_or_schema_changed", "remote_pending_intent_invalid",
      "remote_reconciliation_record_invalid", "remote_task_request_record_invalid"),
     "Restore the original canonical owner record, current receipt and supported schema with all intents and request history; never recreate an empty record over prior ownership."),
    (("remote_owner_state_path_changed", "remote_supervision_binding_changed"),
     "Use the original state path and matching remote supervision binding; reconcile ownership history before changing mode or store identity."),
    (("remote_reconciliation_needs_observed_evidence",),
     "Observe the SAME remote identities and provide actual evidence plus an applied/not_applied outcome to the trusted owner reconciliation API."),
    (("remote_reconciliation_intent_changed",),
     "Query the current pending intent and reconcile that exact operation ID; do not replay a different or already reconciled operation."),
    (("task_request_fields_invalid",),
     "Submit only nonempty string request_id (at most 128 characters) and task (at most 65536 characters); remove all execution and routing fields."),
    (("task_request_identity_conflict",),
     "Reuse the request ID only with identical task bytes, or submit a genuinely new authorized task with a new request ID."),
    (("task_query_fields_invalid",),
     "Submit exactly one string task_id returned by this owner's task request API."),
    (("task_not_found",),
     "Query a task ID returned by this same owner's request API; verify the selected owner rather than inventing a task ID."),
    (("remote_policy_task_state_unreadable", "remote_policy_task_state_lost"),
     "Restore the original readable task ledger and registration history before resuming; do not requeue or redispatch accepted work."),
    (("remote_legacy_task_registration_unproved",),
     "Recover the original task ledger proving this legacy request's registration before migration; its queued field alone cannot prove that it was never dispatched."),
    (("native_foreman_tier_verifier_in_remote_context",),
     "Supply authenticated controller-runtime model-tier proof before enabling the remote round; the native pane verifier cannot prove it."),
    (("native_controller_operation_in_remote_context",),
     "Use the controller host runtime's policy-preserving continuation or home management; do not reset or launch a foreman in the thin anchor."),
)
REFUSAL_REMEDIES = {reason: remedy for reasons, remedy in _REMEDY_GROUPS for reason in reasons}


def refuse(reason):
    remedy = REFUSAL_REMEDIES.get(reason, "Correct the internal refusal-reason mapping before enabling this operation.")
    return RemoteContextError("Remote operation refused ({}). {}".format(reason, remedy), {"reason": reason})


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


def remote_receipt(context: HerdrCommandContext) -> RemoteForemanAttestation:
    """Prove the remote receipt's type at each privileged integration boundary."""
    if context.mode != "attested-remote" or context.attestation is None:
        raise refuse("remote_attestation_missing")
    return context.attestation


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
