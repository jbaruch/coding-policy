"""Git-owned worktree base receipts and automatic brief provenance.

Herdr owns schema 1 in each worktree's private Git directory. Provisioning
records the original base; composition reads without modifying Git or owner state. A later
fetch never changes the original task base. See state-schema.md.
"""

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from .errors import ForemanError, UsageError, owner_recovery
from .state import default_state_path, load_state_checked

FIELDS = {"schema_version", "path", "branch", "base_ref", "base_revision",
          "fetched_default_ref", "fetched_default_revision"}


def refuse(kind, message, *, path=None, base_revision=None, **evidence):
    operation = "bash " + shlex.quote(str(Path(__file__).resolve().parents[1] / "provision-worktree.sh"))
    return owner_recovery(UsageError(message, {}), kind, operation,
        "Use the same normal provisioning invocation. The owner fetches recorded commits when available; it must preserve this worktree and original base and stop while identity, commit availability or ancestry remains unproved.",
        path=str(path) if path is not None else None, base_revision=base_revision, **evidence)


def ensure_commit(path, revision, *, fetch=False, worktree=None, base_revision=None):
    selected = worktree or path
    original_base = base_revision or revision
    try:
        resolved = git(path, "rev-parse", "--verify", revision + "^{commit}")
    except UsageError as lookup:
        if not fetch:
            raise refuse("provision_commit_unavailable", "Recorded worktree commit is unavailable.",
                path=selected, base_revision=original_base, commit_revision=revision, lookup=lookup.to_dict()) from lookup
        try:
            git(path, "fetch", "--quiet", "origin", revision)
            resolved = git(path, "rev-parse", "--verify", revision + "^{commit}")
        except UsageError as exc:
            raise refuse("provision_commit_unavailable", "Authorized fetch could not establish the recorded original commit; existing work is preserved.",
                path=selected, base_revision=original_base, commit_revision=revision, cause=exc.to_dict()) from exc
    if resolved != revision:
        raise refuse("provision_commit_unavailable", "Recorded commit did not resolve exactly.", path=selected, base_revision=original_base, commit_revision=revision)


def require_ancestor(path, revision):
    try:
        git(path, "merge-base", "--is-ancestor", revision, "HEAD")
    except UsageError as exc:
        if exc.details.get("recovery", {}).get("evidence", {}).get("git_exit") != 1:
            raise
        raise refuse("provision_ancestry_conflict", "Worktree does not descend from its recorded task base; existing work is preserved.",
            path=path, base_revision=revision) from exc


def git(path, *args):
    result = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, check=False)
    if result.returncode:
        raise refuse("provision_git_failed", "Cannot read worktree provenance: git {} failed: {}".format(
            " ".join(args), result.stderr.strip()), path=path, git_args=list(args), git_exit=result.returncode)
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
        raise refuse("provision_persistence_failed", "Cannot persist worktree base provenance: {}; existing work is preserved.".format(exc), path=saved["path"], base_revision=saved["base_revision"]) from None
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
            raise refuse("provision_identity_changed", "Provisioning intent does not match this path and branch; preserve existing work.", path=path)
        for key in ("base_revision", "fetched_default_revision"):
            ensure_commit(shared, saved[key], fetch=True, worktree=path, base_revision=saved["base_revision"])
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
        raise refuse("provision_identity_changed", "Worktree base receipt no longer matches its path and branch; preserve the original task worktree.", path=path)
    for key in ("base_revision", "fetched_default_revision"):
        ensure_commit(path, record[key], base_revision=record["base_revision"])
    require_ancestor(path, record["base_revision"])
    return record


def record(path, branch, base_ref, base_revision, default_ref, default_revision):
    location = receipt_path(path)
    if location.exists():
        saved = read(path)
        if saved["branch"] != branch or saved["base_revision"] != base_revision:
            raise refuse("provision_base_changed", "Provisioning cannot change the original worktree base; preserve the recorded task.", path=path)
        saved.update(fetched_default_ref=default_ref, fetched_default_revision=default_revision)
    else:
        saved = validate({"schema_version": 1, "path": str(Path(path).resolve()), "branch": branch,
                          "base_ref": base_ref, "base_revision": base_revision,
                          "fetched_default_ref": default_ref, "fetched_default_revision": default_revision})
    require_ancestor(path, base_revision)
    persist(location, saved)
    # The authoritative worktree receipt now carries the same original base.
    # Retire only our matching pending intent, so normal worktree cleanup can
    # later reuse the path without an obsolete common-directory reservation.
    intent = intent_path(path, path)
    if intent.exists():
        pending = validate(json.loads(intent.read_text(encoding="utf-8")))
        if any(pending[key] != saved[key] for key in ("path", "branch", "base_revision")):
            raise refuse("provision_identity_changed", "Pending provisioning intent changed; preserve existing work.", path=path)
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
    except ForemanError as exc:
        if "recovery" not in exc.details:
            exc = owner_recovery(exc, "provision_inputs_unproved", "bash " + shlex.quote(str(Path(__file__).resolve().parents[1] / "provision-worktree.sh")),
                "The owner must use the recorded task/worktree inputs and supported provenance before retrying normal provisioning; preserve existing work.")
        print(json.dumps(exc.to_dict()), file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(json.dumps(refuse("provision_evidence_unreadable", "Cannot read provisioning evidence: {}.".format(exc)).to_dict()), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
