"""The model-capability table: what each model can do, sourced and dated.

A dependency the project imports, not a measurement it takes (#480). Entries
carry published knowledge that tier selection reads (`assess`, called from
`select_tier`'s callers), each with the source supporting it and the date that
source was read.

Staleness here is silent. Models ship monthly, a retired entry keeps routing
work to a model that stopped being the right choice, and nothing errors and no
check fails. So the table comes due on a cadence, a consultation reads what is
published, and the lead records the report through this owner (#481).

The table replaces a per-model, per-effort, per-role qualification battery for
the same information. Defect detection is one capability in it, not a separate
regime: this codebase sits inside the distribution published benchmarks measure,
and where a model failed here that failure is itself a `project` source.
"""

import json
import re
from datetime import date, timedelta, timezone
from pathlib import Path
from typing import NoReturn

from .chronology import timestamp
from .diagnostics import stderr_warn as _warn
from .errors import UsageError
from .state import save_state, state_lock

SCHEMA_VERSION = 1

#: The table is read at dispatch time, so a week that dispatches nothing is a
#: week where a stale table is never consulted. The interval is script-owned.
INTERVAL = timedelta(days=7)

#: Source kinds, strongest first.
SOURCE_KINDS = ("benchmark", "evaluation", "project", "vendor")

#: What may support an `adequate` verdict. A vendor's own claim about its own
#: model routes real work on marketing, which is what the hierarchy exists to
#: keep out (#480). `vendor` still records availability, pricing and
#: deprecation, and still supports `inadequate` and `unknown`.
SUPPORTING_SOURCES = frozenset({"benchmark", "evaluation", "project"})

VERDICTS = ("adequate", "inadequate", "unknown")

#: The table's vocabulary, owned here so what a consultation records is what
#: routing reads (#520). `ROUND_CAPABILITIES` names what each round needs a
#: model to do; a judgment round on a rotating worker also needs
#: `JUDGMENT_TIER`, and a consultation needs what its role does.
#: `RECORDED_ONLY` names are facts the table keeps without routing on them.
#: `record` refuses any other name.
JUDGMENT_TIER = "rotating-worker-judgment-tier"
JUDGE_CAPABILITY = "pinned-judge-launch"
ROUND_CAPABILITIES = {
    "review": ("independent-defect-detection",),
    "hostile_verify": ("independent-defect-detection",),
    "recheck": ("independent-defect-detection",),
    "critic": ("independent-defect-detection",),
    "test_plan": ("independent-defect-detection",),
    "architect": ("advisory-synthesis",),
    "reconciliation": ("causal-investigation",),
    "release_adjudication": ("release-adjudication",),
    "lead": (),
    "build": ("implementation",),
    "fix": ("implementation",),
    "mechanical": ("mechanical-execution",),
    "release_mechanics": ("mechanical-execution",),
    "consultation": (),
}
CONSULTATION_CAPABILITIES = {"investigator": "causal-investigation", "advisor": "advisory-synthesis"}
RECORDED_ONLY = frozenset({"context-window-1m", "report-verdict-classification"})
VOCABULARY = frozenset(
    {JUDGMENT_TIER, JUDGE_CAPABILITY} | set(CONSULTATION_CAPABILITIES.values()) | RECORDED_ONLY
    | {name for names in ROUND_CAPABILITIES.values() for name in names})
#: The effort a table row names for a model that takes no effort flag.
DEFAULT_EFFORT = "default"

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}\Z")
DATED = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


def canonical_state(path):
    return Path(path).expanduser().resolve()


def storage_path(path):
    return Path(str(canonical_state(path)) + ".capabilities.json")


def _fail(message) -> NoReturn:
    raise UsageError(message, {})


def _name(value, label):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        _fail("{} needs 1-128 letters, digits, dots, underscores, slashes or hyphens.".format(label))
    return value


def _utc(value):
    return timestamp(value, "Capability timestamp").astimezone(timezone.utc).isoformat()


def empty():
    return {"schema_version": SCHEMA_VERSION, "refreshed_at": None, "entries": []}


def load(path, *, for_write=False):
    """The saved table, or an empty one. Readers never create the file.

    A table stamped with a newer schema than this build owns was written by a
    newer owner. A reader treats it as no usable prior state and says so; a
    writer refuses, so an older build never overwrites what it cannot read
    (rules/stateful-artifacts.md Migration Policy).
    """
    target = storage_path(path)
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty()
    except (OSError, UnicodeDecodeError) as exc:
        _fail("Cannot read the capability table at {}: {}. Restore a readable UTF-8 file, or "
              "remove it to start an empty table.".format(target, exc))
    except json.JSONDecodeError as exc:
        _fail("The capability table at {} is not valid JSON ({}). Restore the owner-written "
              "file rather than editing it by hand.".format(target, exc.msg))
    version = document.get("schema_version") if isinstance(document, dict) else None
    if isinstance(version, int) and not isinstance(version, bool) and version > SCHEMA_VERSION:
        if for_write:
            _fail("The capability table at {} is schema {}, newer than this build's {}. Update "
                  "the coding-policy plugin before recording; the file is left untouched.".format(
                      target, version, SCHEMA_VERSION))
        _warn("capability table {} is schema {}, newer than this build's {}; reading it as no "
              "prior state. Update the coding-policy plugin. The file is left untouched.".format(
                  target, version, SCHEMA_VERSION))
        return empty()
    validate(document)
    return document


def validate(document):
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
        _fail("Unsupported capability-table schema; update the owner skill before using it.")
    if set(document) != {"schema_version", "refreshed_at", "entries"}:
        _fail("The capability table carries exactly schema_version, refreshed_at and entries.")
    if document["refreshed_at"] is not None:
        _utc(document["refreshed_at"])
    if not isinstance(document["entries"], list):
        _fail("The capability table's entries must be an array.")
    seen = set()
    for entry in document["entries"]:
        key = validate_entry(entry)
        if key in seen:
            _fail("The capability table records {} twice; one entry owns one model, effort and "
                  "capability.".format(" / ".join(key)))
        seen.add(key)
    return document


def validate_entry(entry):
    """One row: what this model at this effort can do, and what says so."""
    required = {"schema_version", "model", "effort", "capability", "verdict", "source", "recorded_at"}
    if not isinstance(entry, dict) or set(entry) != required:
        _fail("A capability entry carries exactly {}.".format(", ".join(sorted(required))))
    if entry["schema_version"] != SCHEMA_VERSION:
        _fail("A capability entry carries an unsupported schema version.")
    key = (_name(entry["model"], "Capability model"),
           _name(entry["effort"], "Capability effort"),
           _name(entry["capability"], "Capability name"))
    if entry["verdict"] not in VERDICTS:
        _fail("A capability verdict is one of {}.".format(", ".join(VERDICTS)))
    source = entry["source"]
    if not isinstance(source, dict) or set(source) != {"kind", "ref", "dated"}:
        _fail("A capability source carries kind, ref and dated.")
    if source["kind"] not in SOURCE_KINDS:
        _fail("A capability source kind is one of {}.".format(", ".join(SOURCE_KINDS)))
    if not isinstance(source["ref"], str) or not source["ref"].strip():
        _fail("A capability source names where it was read: a URL, a citation, or an issue reference.")
    if not isinstance(source["dated"], str) or not DATED.fullmatch(source["dated"]):
        _fail("A capability source is dated YYYY-MM-DD, so a stale reading is visible.")
    try:
        date.fromisoformat(source["dated"])
        calendar = True
    except ValueError:
        calendar = False
    if not calendar:
        _fail("A capability source's date {!r} is not a calendar date.".format(source["dated"]))
    if entry["verdict"] == "adequate" and source["kind"] not in SUPPORTING_SOURCES:
        _fail("An `adequate` verdict for {} / {} / {} rests on a {} source. A vendor's claim about "
              "its own model routes real work on marketing; cite a benchmark, an independent "
              "evaluation, or this project's own recorded result.".format(*key, source["kind"]))
    _utc(entry["recorded_at"])
    return key


def cadence(document, at, *, existing_work=False):
    """Whether the table is due a refresh, and when the next one falls."""
    now = timestamp(_utc(at), "Capability checkpoint")
    last = document.get("refreshed_at")
    if last is None:
        return {"due": bool(existing_work), "last_refreshed_at": None, "next_due_at": None,
                "reason": "never_refreshed" if existing_work else "no_recorded_work"}
    reference = timestamp(last, "Capability refresh")
    if reference > now:
        _fail("Capability checkpoint precedes the saved refresh; use the current UTC checkpoint "
              "without rewriting history.")
    due = now >= reference + INTERVAL
    return {"due": due, "last_refreshed_at": reference.isoformat(),
            "next_due_at": (reference + INTERVAL).isoformat(),
            "reason": "interval_elapsed" if due else "not_due"}


def lookup(document, model, effort, capability):
    """The recorded entry for one model, effort and capability, or None."""
    for entry in document["entries"]:
        if (entry["model"], entry["effort"], entry["capability"]) == (model, effort, capability):
            return entry
    return None


def required(role, round_type, judgment_rounds):
    """The capabilities a round asks of the model that runs it, in vocabulary order."""
    if round_type == "judge":
        return (JUDGE_CAPABILITY,)
    names = list(ROUND_CAPABILITIES[round_type])
    if round_type == "consultation":
        names.append(CONSULTATION_CAPABILITIES[role])
    if round_type in judgment_rounds:
        names.insert(0, JUDGMENT_TIER)
    return tuple(names)


class InadequateCapability(UsageError):
    """The table records the selected model and effort as inadequate for this round."""

    code = "capability_inadequate"


def assess(document, model, effort, capabilities):
    """`adequate` or `unknown` for one model and effort; an `inadequate` entry refuses.

    Unknown never selects a different model and never lowers a floor: a
    missing table, a missing entry and an `unknown` verdict all leave the
    configured row in place and say so. Only an entry resting on a supporting
    source counts as `adequate`, whatever the file claims.
    """
    effort = effort or DEFAULT_EFFORT
    verdicts = []
    for name in capabilities:
        entry = lookup(document, model, effort, name)
        if entry is None:
            verdicts.append("unknown")
            continue
        if entry["verdict"] == "inadequate":
            source = entry["source"]
            raise InadequateCapability(
                "The capability table records {} at {} effort as inadequate for {} ({} source {}, read {}). Configure "
                "another model for this round, or record newer evidence through capability-record.".format(
                    model, effort, name, source["kind"], source["ref"], source["dated"]),
                {"model": model, "effort": effort, "capability": name, "source": source})
        supported = entry["verdict"] == "adequate" and entry["source"]["kind"] in SUPPORTING_SOURCES
        verdicts.append("adequate" if supported else "unknown")
    return "adequate" if verdicts and all(v == "adequate" for v in verdicts) else "unknown"


def record(path, data, at):
    """Write a consultation's report into the table, replacing what it covers.

    The consultation is read-only on repository content and returns a report at
    the path its brief names; the lead records it here. A refresh replaces the
    entries it carries and leaves every other row untouched, so one report about
    two models does not retire the rest of the table.
    """
    if not isinstance(data, dict) or set(data) != {"entries"}:
        _fail("A capability record carries `entries` alone: the rows this refresh covers.")
    if not isinstance(data["entries"], list) or not data["entries"]:
        _fail("A capability refresh records at least one entry; an empty report refreshes nothing.")
    stamped = []
    covered = set()
    reported = {"model", "effort", "capability", "verdict", "source"}
    for entry in data["entries"]:
        # The writer stamps the version and the time; a report never supplies them.
        if not isinstance(entry, dict) or set(entry) != reported:
            _fail("A reported capability entry carries exactly {}.".format(", ".join(sorted(reported))))
        if entry["capability"] not in VOCABULARY:
            _fail("Capability {!r} is not one routing reads or the table keeps; use one of {}.".format(
                entry["capability"], ", ".join(sorted(VOCABULARY))))
        row = {**entry, "schema_version": SCHEMA_VERSION, "recorded_at": _utc(at)}
        key = validate_entry(row)
        if key in covered:
            _fail("This refresh records {} twice; one entry owns one model, effort and "
                  "capability.".format(" / ".join(key)))
        covered.add(key)
        stamped.append(row)
    target = storage_path(path)
    with state_lock(target):
        document = load(path, for_write=True)
        kept = [row for row in document["entries"]
                if (row["model"], row["effort"], row["capability"]) not in covered]
        document["entries"] = sorted(kept + stamped,
                                     key=lambda row: (row["model"], row["effort"], row["capability"]))
        document["refreshed_at"] = _utc(at)
        validate(document)
        save_state(target, document)
    return document
