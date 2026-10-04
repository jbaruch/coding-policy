"""Git-owned worktree base receipts and automatic brief provenance.

Herdr owns schema 1 in each worktree's private Git directory. Provisioning
records the original base; composition reads without modifying Git or owner state. A later
fetch never changes the original task base. See state-schema.md.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .errors import ForemanError, UsageError
from .state import default_state_path, load_state_checked

FIELDS = {"schema_version", "path", "branch", "base_ref", "base_revision",
          "fetched_default_ref", "fetched_default_revision"}


def git(path, *args):
    result = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, check=False)
    if result.returncode:
        raise UsageError("Cannot read worktree provenance: git {} failed: {}".format(
            " ".join(args), result.stderr.strip()), {})
    return result.stdout.strip()


def receipt_path(path):
    return Path(git(path, "rev-parse", "--path-format=absolute", "--git-path", "foreman-provision.json"))


def intent_path(shared, path):
    common = Path(git(shared, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    identity = hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()
    return common / ("foreman-provision-" + identity + ".json")


def persist(location, saved):
    """Replace atomically without sharing or stranding a fixed scratch name."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=location.parent,
                                         prefix=location.name + ".", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(saved, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(location)
    except OSError as exc:
        raise UsageError("Cannot persist worktree base provenance: {}; restore writable Git metadata and rerun normal provisioning. Existing work is preserved.".format(exc), {}) from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                print("provision: cannot clean owned scratch {}: {}; normal retries use a new scratch file.".format(temporary, exc), file=sys.stderr)


def prepare(shared, path, branch, base_ref, base_revision, default_ref, default_revision):
    """Persist the original base before any branch or worktree is created."""
    path = str(Path(path).resolve())
    location = intent_path(shared, path)
    saved = None
    if location.exists():
        saved = validate(json.loads(location.read_text(encoding="utf-8")))
    elif Path(path).exists():
        receipt = receipt_path(path)
        # Recover the complete scratch receipt left by older provisioning owners.
        candidate = receipt if receipt.exists() else receipt.with_suffix(".tmp")
        if candidate.exists():
            saved = validate(json.loads(candidate.read_text(encoding="utf-8")))
    if saved is not None:
        if saved["path"] != path or saved["branch"] != branch:
            raise UsageError("Provisioning intent does not match this path and branch; preserve existing work.", {})
        if git(shared, "rev-parse", "--verify", saved["base_revision"] + "^{commit}") != saved["base_revision"]:
            raise UsageError("Original provisioning base is unavailable; preserve existing work.", {})
    else:
        saved = validate({"schema_version": 1, "path": path, "branch": branch,
                          "base_ref": base_ref, "base_revision": base_revision,
                          "fetched_default_ref": default_ref, "fetched_default_revision": default_revision})
    saved.update(fetched_default_ref=default_ref, fetched_default_revision=default_revision)
    persist(location, saved)
    return saved


def validate(record):
    if (not isinstance(record, dict) or set(record) != FIELDS
            or type(record["schema_version"]) is not int or record["schema_version"] != 1):
        raise UsageError("Unsupported worktree provenance; preserve it and update the provisioning owner.", {})
    for key in FIELDS - {"schema_version"}:
        if not isinstance(record[key], str) or not record[key]:
            raise UsageError("Worktree provenance requires non-empty {}.".format(key), {})
    for key in ("base_revision", "fetched_default_revision"):
        if len(record[key]) not in (40, 64) or any(c not in "0123456789abcdef" for c in record[key]):
            raise UsageError("Worktree provenance requires an exact {} commit.".format(key), {})
    return record


def read(path):
    location = receipt_path(path)
    try:
        record = validate(json.loads(location.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise UsageError("Cannot read worktree base receipt {}: {}; provision through the normal owner before composing.".format(location, exc), {}) from None
    if record["path"] != str(Path(path).resolve()) or record["branch"] != git(path, "symbolic-ref", "--short", "HEAD"):
        raise UsageError("Worktree base receipt no longer matches its path and branch; preserve the original task worktree.", {})
    for key in ("base_revision", "fetched_default_revision"):
        if git(path, "rev-parse", "--verify", record[key] + "^{commit}") != record[key]:
            raise UsageError("Worktree base receipt names an unavailable commit.", {})
    if git(path, "merge-base", "--is-ancestor", record["base_revision"], "HEAD"):
        raise UsageError("Worktree does not descend from its recorded task base.", {})
    return record


def record(path, branch, base_ref, base_revision, default_ref, default_revision):
    location = receipt_path(path)
    if location.exists():
        saved = read(path)
        if saved["branch"] != branch or saved["base_revision"] != base_revision:
            raise UsageError("Provisioning cannot change the original worktree base; preserve the recorded task.", {})
        saved.update(fetched_default_ref=default_ref, fetched_default_revision=default_revision)
    else:
        saved = validate({"schema_version": 1, "path": str(Path(path).resolve()), "branch": branch,
                          "base_ref": base_ref, "base_revision": base_revision,
                          "fetched_default_ref": default_ref, "fetched_default_revision": default_revision})
    if git(path, "merge-base", "--is-ancestor", base_revision, "HEAD"):
        raise UsageError("Attached branch does not descend from the supplied task base.", {})
    persist(location, saved)
    # The authoritative worktree receipt now carries the same original base.
    # Retire only our matching pending intent, so normal worktree cleanup can
    # later reuse the path without an obsolete common-directory reservation.
    intent = intent_path(path, path)
    if intent.exists():
        pending = validate(json.loads(intent.read_text(encoding="utf-8")))
        if any(pending[key] != saved[key] for key in ("path", "branch", "base_revision")):
            raise UsageError("Pending provisioning intent changed; preserve existing work.", {})
        intent.unlink()
    return saved


def compose(values):
    if (not isinstance(values, dict) or not isinstance(values.get("shared"), dict)
            or not isinstance(values.get("roles"), dict)
            or any(not isinstance(row, dict) for row in values["roles"].values())):
        raise UsageError("Composition requires shared and role value objects.", {})
    task = values.get("task")
    if not isinstance(task, str) or not task:
        raise UsageError("Packaged briefs require the existing registered task identity in top-level task; no caller-supplied base substitutes.", {})
    state, usable = load_state_checked(values.get("state") or default_state_path(), persist_migration=False)
    registered = state["recovery"]["tasks"].get(task) if isinstance(task, str) else None
    if not usable or registered is None:
        raise UsageError("Packaged briefs require the existing registered task identity in top-level task (and state when non-default); composition reads its original base automatically.", {})
    base = registered["base_revision"]
    shared = values["shared"]
    if any(key in shared for key in ("BASE_REVISION", "BASE_PROVENANCE", "FETCHED_DEFAULT_REVISION")):
        raise UsageError("BASE_REVISION is owner-derived from the registered task; omit the supplied SHA.", {})
    shared["BASE_REVISION"] = base
    for role, inputs in values["roles"].items():
        if "BASE_REVISION" in inputs or "FETCHED_DEFAULT_REVISION" in inputs:
            raise UsageError("Brief provenance is owner-derived; remove per-role base values.", {})
        path = inputs.get("WORKTREE", shared.get("WORKTREE"))
        if path is not None:
            saved = read(path)
            if role == "developer":
                if "BASE_PROVENANCE" in inputs:
                    raise UsageError("BASE_PROVENANCE is owner-derived; omit it from role values.", {})
                inputs["BASE_PROVENANCE"] = "The provisioning owner fetched default `{}` at exact commit `{}`. This task retains its authorized original base `{}`.".format(
                    saved["fetched_default_ref"], saved["fetched_default_revision"], base)
            if saved["base_revision"] != base:
                raise UsageError("Worktree {} was provisioned from a different task base; preserve the registered original task base {}.".format(path, base), {})
    return values


def main():
    try:
        if sys.argv[1:] == ["compose"]:
            result = compose(json.load(sys.stdin))
        elif len(sys.argv) == 8 and sys.argv[1] == "record":
            result = record(*sys.argv[2:])
        elif len(sys.argv) == 9 and sys.argv[1] == "prepare":
            result = prepare(*sys.argv[2:])
        else:
            raise UsageError("Use provision compose, record PATH BRANCH BASE_REF BASE_SHA DEFAULT_REF DEFAULT_SHA, or prepare SHARED PATH BRANCH BASE_REF BASE_SHA DEFAULT_REF DEFAULT_SHA.", {})
        print(json.dumps(result))
        return 0
    except (ForemanError, OSError, ValueError) as exc:
        print("provision: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
