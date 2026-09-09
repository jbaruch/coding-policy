"""Bounded offline catch-up, with obligations ahead of saved progress."""

import copy

from . import attention
from .chronology import timestamp
from .errors import UsageError


def _page(rows, offset, limit):
    selected = rows[offset:offset + limit]
    return {"total": len(rows), "offset": offset, "limit": limit, "returned": len(selected),
            "omitted": len(rows) - len(selected), "next_offset": offset + limit if offset + limit < len(rows) else None,
            "items": selected}


def _short(value, limit=600):
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _source_link(source):
    ref = source["ref"]
    label = source["kind"].replace("_", " ")
    if ref.startswith(("https://", "http://", "/")) and not any(char in ref for char in "\n\r"):
        target = ref.replace("<", "%3C").replace(">", "%3E")
        return "[{}](<{}>)".format(label, target)
    return "{}: {}".format(label, _short(ref, 2000))


def _render(result):
    lines = ["# Needs your attention", ""]
    queue = result["attention"]
    if not queue["total"]:
        lines.append("No actionable obligations are recorded in this queue.")
    for row in queue["items"]:
        seen = "previously presented; still open" if row["last_presented_at"] else "not yet recorded as presented"
        lines.extend(["- **{}** (`{}`, {}, priority {}; {})".format(_short(row["title"], 300), row["id"], row["kind"], row["priority"], seen),
                      "  {}".format(_short(row["context"])),
                      "  Consequence: {}".format(_short(row["consequence"])),
                      "  Resolves when: {}".format(_short(row["resolution_condition"]))])
        if row["task"]:
            lines.append("  Task: {}".format(_short(row["task"])))
        if row["options"]:
            lines.append("  Choices: {}".format("; ".join(_short(value, 200) for value in row["options"])))
        if row["recommendation"]:
            lines.append("  Recommendation: {}".format(_short(row["recommendation"])))
        lines.append("  Sources: {}".format("; ".join(_source_link(source) for source in row["sources"])))
        if row["resurfaced"]:
            lines.append("  Deferral expired at {}.".format(row["deferred_until"]))
    if queue["omitted"]:
        lines.append("\n**{} actionable obligations are outside this page.** {}".format(queue["omitted"],
                     "Continue with --offset {}.".format(queue["next_offset"]) if queue["next_offset"] is not None else "Read earlier pages with --offset 0."))
    result["attention_markdown"] = "\n".join(lines) + "\n" if queue["total"] else ""
    lines.extend(["", "## Deferred", ""])
    deferred = result["deferred"]
    for row in deferred["items"]:
        lines.append("- `{}`: {} — resurfaces {}.".format(row["id"], _short(row["title"], 300), row["deferred_until"]))
    if not deferred["total"]:
        lines.append("No future deferrals recorded.")
    if deferred["omitted"]:
        lines.append("{} deferred entries are outside this page; use the deferred pagination fields.".format(deferred["omitted"]))
    lines.extend(["", "## Known progress", ""])
    for row in result["progress"]["items"]:
        lines.append("- {} [{} as recorded at {}]: {}".format(_short(row["task"]), row["assessment"], row["at"], _short(row["summary"])))
    if not result["progress"]["total"]:
        lines.append("No lead-recorded progress matches this view; completion is unknown.")
    if result["progress"]["omitted"]:
        lines.append("{} progress records are outside this page; use the progress pagination fields.".format(result["progress"]["omitted"]))
    if result["closed"] is not None:
        lines.extend(["", "## Recorded resolutions", ""])
        for row in result["closed"]["items"]:
            lines.append("- `{}`: {} — {} at {}.".format(row["id"], _short(row["title"], 300), row["status"], row["updated_at"]))
        if result["closed"]["omitted"]:
            lines.append("{} closed entries are outside this page; use the closed pagination fields.".format(result["closed"]["omitted"]))
    lines.extend(["", "## Saved sources", "", "Queue: `{}`".format(result["attention_path"]),
                  "Retrospective index: `{}`".format(result["retrospective_index"])])
    for source in result["sources"]:
        lines.append("- {}: {}".format(source["kind"], _short(source["ref"], 2000)))
    lines.extend(["", "Saved records are last-known evidence. A progress assessment of verified means verified when recorded; revalidate the task ledger and its sources before treating work as currently accepted. This view contacts no worker and grants no task acceptance or authority."])
    return "\n".join(lines) + "\n"


def catch_up(path, at, *, task=None, limit=10, offset=0, since=None, include_closed=False):
    if type(limit) is not int or not 1 <= limit <= 50 or type(offset) is not int or offset < 0:
        raise UsageError("Catch-up needs --limit 1–50 and a nonnegative --offset; follow next_offset to read remaining obligations.", {})
    now = timestamp(at, "Catch-up checkpoint")
    cutoff = timestamp(since, "Catch-up since") if since is not None else None
    document, entries, progress = attention.load(path)
    if document["events"] and timestamp(document["events"][-1]["at"], "Latest attention event") > now:
        raise UsageError("Catch-up checkpoint precedes saved events; use the current UTC checkpoint without rewriting history.", {})
    actionable, deferred, closed = [], [], []
    for value in entries.values():
        if task is not None and value["task"] != task:
            continue
        row = copy.deepcopy(value)
        row["resurfaced"] = row["status"] == "deferred" and timestamp(row["deferred_until"], "Resurface time") <= now
        row["effective_status"] = "open" if row["resurfaced"] else row["status"]
        if row["effective_status"] == "open":
            actionable.append(row)
        elif row["effective_status"] == "deferred":
            deferred.append(row)
        elif cutoff is None or timestamp(row["updated_at"], "Resolution time") >= cutoff:
            closed.append(row)
    actionable.sort(key=lambda row: (-row["priority"], row["created_at"], row["id"]))
    deferred.sort(key=lambda row: (row["deferred_until"], -row["priority"], row["id"]))
    closed.sort(key=lambda row: (row["updated_at"], row["id"]), reverse=True)
    progress = [row for row in progress if (task is None or row["task"] == task)
                and (cutoff is None or timestamp(row["at"], "Progress time") >= cutoff)]
    progress.sort(key=lambda row: (row["at"], row["id"]), reverse=True)
    result = {"schema_version": attention.SCHEMA_VERSION, "state_path": str(attention.canonical_state(path)),
              "attention_path": str(attention.storage_path(path)), "checked_at": attention._utc(at),
              "task": task, "since": since, "attention": _page(actionable, offset, limit),
              "deferred": _page(deferred, offset, limit), "progress": _page(progress, offset, limit),
              "closed": _page(closed, offset, limit) if include_closed else None,
              "retrospective_index": str(attention.canonical_state(path)) + ".retrospectives/index.json", "sources": []}
    sources = []
    for section in ("attention", "deferred", "progress", "closed"):
        page = result[section]
        if page is not None:
            for row in page["items"]:
                for source in row["sources"]:
                    if source not in sources:
                        sources.append(copy.deepcopy(source))
    result["sources"] = sources
    result["markdown"] = _render(result)
    return result
