"""Native UserPromptSubmit: verify a planned reset before model execution.

Only the exact guarded reset prompt participates. Normal prompts are silent.
The existing reset owner record and exact foreground identities authorize the
input; its envelope is merely a locator. The hook atomically rebinds existing
supervision to the native payload's new session, without acknowledging events,
resuming holds or accepting tasks. An old session, replacement, changed stow,
dead/unclaimed deliverer or replay blocks the prompt. No new durable store.
"""

import json
import os
import re
import sys

from . import foreman_reset as reset, memory, supervision as store, supervision_runtime as runtime
from .errors import ForemanError, UsageError
from .state import save_state, state_lock


def refusal(cause="invalid_native_payload"):
    return {"decision": "block", "reason": "Herdr reset input was not verified ({}). Preserve its reset record, restore the native hook/owner state, and continue foreground supervision; never repeat an uncertain reset input.".format(cause)}


def check(payload, environ, at, *, client=None, root=None, probe=runtime.process_identity):
    prompt = payload.get("prompt") if isinstance(payload, dict) else None
    # Claude frames long bracketed pastes in its native hook payload. Remove
    # only that complete matching frame; the inner input still matches the
    # owner's regenerated prompt byte-for-byte. Extra content stays refused.
    if isinstance(prompt, str):
        framed = re.fullmatch(r'<pasted_content id="([A-Za-z0-9_-]+)">\n(.*)\n</pasted_content id="\1">',
                              prompt.strip(), flags=re.DOTALL)
        if framed is not None:
            prompt = framed.group(2)
    marker = "\n" + reset.RESET_RECEIPT_PREFIX
    if not isinstance(prompt, str) or marker not in prompt:
        return None
    try:
        try:
            receipt = json.loads(prompt.split(marker, 1)[1])
        except ValueError:
            return refusal("invalid_reset_receipt_json")
        if (not isinstance(receipt, dict) or set(receipt) != {"schema_version", "state", "stow", "native_session", "foreground"}
                or type(receipt["schema_version"]) is not int or receipt["schema_version"] != 1
                or not isinstance(receipt["state"], str) or not os.path.isabs(receipt["state"])
                or not isinstance(receipt["stow"], str) or not reset._valid_session(receipt["native_session"])
                or not isinstance(receipt["foreground"], list) or not receipt["foreground"]):
            return refusal("invalid_reset_receipt")
        pane = environ.get("HERDR_PANE_ID")
        if not pane or not environ.get("HERDR_ENV"):
            return refusal("missing_herdr_environment")
        kind = receipt["native_session"]["kind"]
        value = payload.get("session_id" if kind == "id" else "transcript_path")
        who = store.identity(value, payload.get("cwd"), environ["HERDR_ENV"], pane_id=pane, kind=kind)
        if {key: who[key] for key in ("kind", "value")} == receipt["native_session"]:
            return refusal("old_native_session")
        path = reset.record_path(receipt["state"])
        with state_lock(path):
            document, _ = reset._load(path, migrate_legacy=False)
            row = reset._row(document, {"pane_id": pane, "stow": receipt["stow"]})
            if (row is None or row["status"] != "delivering" or row["accepted_session"] is not None
                    or row["native_session"] != receipt["native_session"]
                    or row["foreground"] != receipt["foreground"]
                    or not reset._alive(row["process"], probe)
                    or prompt != reset.guarded_resume(row["stow"], receipt["state"], row["native_session"], receipt["foreground"], options=row["options"])):
                return refusal("reset_claim_or_input_changed")
            native = client or runtime.read_client(binary=row["options"].get("herdr_bin"))
            live = reset._foreman_record(native, pane)
            if live.get("agent") not in reset.SESSION_AGENTS or reset.foreground_processes(native, pane) != receipt["foreground"]:
                return refusal("foreground_replaced")

            def validate(data):
                bound = (data["binding"] or {}).get("identity", {})
                if (any(bound.get(key) != who[key] for key in ("cwd", "herdr_env", "pane_id"))
                        or {key: bound.get(key) for key in ("kind", "value")} != row["native_session"]):
                    raise UsageError("The reset's original binding already changed; preserve it and inspect the reset.", {})
                stow = memory.show(receipt["state"], at, row["stow"])["record"]
                reset.preflight(stow, data, pane)

            store.bind(receipt["state"], who, at, root=root, before_bind=validate)
            row["accepted_session"] = {key: who[key] for key in ("kind", "value")}
            save_state(path, document)
        return None
    except ForemanError as exc:
        # Error codes are bounded project constants, never provider/pane text.
        return refusal("owner_verification_" + exc.code)
    except (OSError, ValueError, TypeError):
        return refusal("owner_state_unusable")


def main():
    from .cli import now_iso
    try:
        payload = json.load(sys.stdin)
    except (ValueError, UnicodeDecodeError):
        print(json.dumps(refusal()))
        return 0
    # outer-boundary-process-contract: native prompt hooks ignore nonzero or
    # invalid stdout; emit structured blocking JSON for unexpected errors so
    # propagation cannot silently admit an unverified continuation prompt.
    try:
        result = check(payload, os.environ, now_iso())
    except Exception:
        print("herdr-reset-input: verification failed; restore the hook installation and inspect the reset owner record.", file=sys.stderr)
        result = refusal()
    if result is not None:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
