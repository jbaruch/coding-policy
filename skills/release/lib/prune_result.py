"""Shape checks for the JSON prune-worktrees.sh prints.

`prune-worktrees.sh` owns its result shape (the top-of-file contract).
The session-start hook `hooks/check-leftover-worktrees.sh` checks it here
first, so a malformed result becomes a reported error rather than a crash
part-way through a report.
"""


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def is_str(value):
    return isinstance(value, str)


def entries_ok(entries, fields):
    """Every entry an object whose named fields hold the named types."""
    return isinstance(entries, list) and all(
        isinstance(e, dict) and all(check(e.get(k)) for k, check in fields.items()) for e in entries)


KEPT_BY_REASON = {
    "dirty": {"path": is_str, "age_hours": is_int, "dirty_files": is_int, "command": is_str},
    "unpushed": {"path": is_str, "age_hours": is_int, "unpushed_commits": is_int, "command": is_str},
}
FAILED = {"target": is_str, "error": is_str}


def prune_schema_error(doc):
    """What a prune-worktrees.sh result lacks, or None when it is whole."""
    if not isinstance(doc, dict):
        return "not a JSON object"
    for key in ("worktrees_removed", "worktrees_kept", "branches_deleted", "branches_kept", "failed"):
        if not isinstance(doc.get(key), list):
            return "no {} list".format(key)
    if not entries_ok(doc["worktrees_removed"], {"path": is_str}):
        return "a malformed worktrees_removed entry"
    if not all(is_str(b) for b in doc["branches_deleted"]):
        return "a malformed branches_deleted entry"
    if not entries_ok(doc["worktrees_kept"], {"path": is_str, "reason": is_str}):
        return "a malformed worktrees_kept entry"
    for kept in doc["worktrees_kept"]:
        if not entries_ok([kept], KEPT_BY_REASON.get(kept["reason"], {})):
            return "a malformed {} worktree entry".format(kept["reason"])
    if not entries_ok(doc["branches_kept"], {"branch": is_str, "reason": is_str}):
        return "a malformed branches_kept entry"
    unpushed = [b for b in doc["branches_kept"] if b["reason"] == "unpushed"]
    if not entries_ok(unpushed, {"unpushed_commits": is_int, "age_hours": is_int, "command": is_str}):
        return "a malformed unpushed branch entry"
    if not entries_ok(doc["failed"], FAILED):
        return "a malformed failed entry"
    return None
