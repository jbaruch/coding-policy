"""Shape checks for the JSON the two prune scripts print.

`prune-worktrees.sh` and `prune-remote-branches.sh` own their result shapes
(their top-of-file contracts). Every reader that renders a result checks it
here first, so a malformed result becomes a reported error rather than a
crash part-way through a report: `sweep-worktrees.sh` and the session-start
hook `hooks/check-leftover-worktrees.sh`.
"""


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def is_str(value):
    return isinstance(value, str)


def is_str_or_none(value):
    return value is None or isinstance(value, str)


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


def remote_schema_error(doc):
    """What a prune-remote-branches.sh result lacks, or None when it is whole."""
    if not isinstance(doc, dict):
        return "not a JSON object"
    for key in ("deleted", "questionable", "kept", "failed"):
        if not isinstance(doc.get(key), list):
            return "no {} list".format(key)
    if "could_not_check" not in doc or not is_str_or_none(doc["could_not_check"]):
        return "no could_not_check field"
    if not is_str(doc.get("default_branch")):
        return "no default_branch"
    fields = {"branch": is_str, "ahead": is_int, "age_hours": is_int, "author": is_str, "open_pr": is_str, "delete": is_str}
    if not entries_ok(doc["questionable"], fields):
        return "a malformed questionable entry"
    if not entries_ok(doc["failed"], FAILED):
        return "a malformed failed entry"
    return None
