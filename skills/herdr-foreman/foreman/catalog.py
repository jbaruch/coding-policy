"""The model catalog: listed IDs, scoped availability, and judgment family.

A sidecar beside the capability table. Discovery reads local CLI metadata.
Selection refuses a recorded access failure and a documented retirement.
Listing is not access; `available` is never written here (readiness is a later
probe). Exact version allowlists do not live in this owner.

Saved at `<canonical-state-path>.catalog.json`. Writer: this module, through
`catalog-discover`, `catalog-record` and `catalog-record-access`. Readers:
`catalog-check`, `catalog-show`, the round preflight, `plan`, `apply` and
`start-judge`.
"""

import errno
import hashlib
import json
import os
import re
import stat
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import NoReturn

from . import runnable
from .capabilities import SOURCE_KINDS, SUPPORTING_SOURCES
from .chronology import timestamp
from .diagnostics import stderr_warn as _warn
from .errors import UsageError
from .state import save_state, state_lock

SCHEMA_VERSION = 1
INTERVAL = timedelta(days=7)

PRESENCE = ("listed", "unlisted", "deprecated", "unknown")
AVAILABILITY = ("available", "unavailable", "unknown")
AVAILABILITY_CLASSES = (
    "request_ok", "request_refused", "invalid_id", "quota", "transport",
    "cache_listed", "docs_listed",
)
ACCESS_CLASSES = frozenset({"request_refused", "invalid_id"})
TRANSIENT_CLASSES = frozenset({"quota", "transport"})
ADAPTERS = ("claude", "codex", "grok")

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/[\]-]{0,127}\Z")
DATED = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
SOURCES_DIR = Path(__file__).resolve().parent / "catalog_sources"
DOCS_FILE = SOURCES_DIR / "docs-2026-10-03.json"
SIGNATURES_FILE = SOURCES_DIR / "access-refusal-signatures.json"
SEED_FILE = SOURCES_DIR / "seed-judgment-family.json"

ENTRY_FIELDS = (
    "schema_version", "adapter", "model", "effort", "aliases", "resolved_id", "efforts",
    "catalog_presence", "availability", "availability_class", "judgment_family",
    "scope_fingerprint", "listed_at", "availability_at", "source",
)
SCOPE_FIELDS = (
    "adapter", "cli_version", "fingerprint", "source", "fetched_at", "checked_at",
    "complete",
)
OBSERVATION_FIELDS = (
    "schema_version", "adapter", "model", "effort", "scope_fingerprint",
    "availability_class", "text", "recorded_at", "source",
)
FORBIDDEN_FIELDS = frozenset({
    "api_key", "token", "secret", "password", "credential", "authorization",
    "auth", "cookie", "session_token",
})


def canonical_state(path):
    return Path(path).expanduser().resolve()


def storage_path(path):
    return Path(str(canonical_state(path)) + ".catalog.json")


def _fail(message) -> NoReturn:
    raise UsageError(message, {})


def _name(value, label):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        _fail("{} needs 1-128 letters, digits, dots, underscores, slashes, brackets or hyphens.".format(label))
    return value


def _utc(value):
    return timestamp(value, "Catalog timestamp").astimezone(timezone.utc).isoformat()


def _source(value, label="Catalog source"):
    if not isinstance(value, dict) or set(value) != {"kind", "ref", "dated"}:
        _fail("{} carries kind, ref and dated.".format(label))
    if value["kind"] not in SOURCE_KINDS and value["kind"] not in {"cli_cache", "docs"}:
        _fail("{} kind is one of {}, cli_cache or docs.".format(
            label, ", ".join(SOURCE_KINDS)))
    if not isinstance(value["ref"], str) or not value["ref"].strip():
        _fail("{} names where it was read: a URL, a citation, or a cache path.".format(label))
    if not isinstance(value["dated"], str) or not DATED.fullmatch(value["dated"]):
        _fail("{} is dated YYYY-MM-DD, so a stale reading is visible.".format(label))
    try:
        date.fromisoformat(value["dated"])
    except ValueError:
        _fail("{} date {!r} is not a calendar date.".format(label, value["dated"]))
    return value


def empty():
    return {
        "schema_version": SCHEMA_VERSION,
        "refreshed_at": None,
        "scopes": [],
        "entries": [],
        "observations": [],
    }


def default_cache_paths():
    home = Path.home()
    return {
        "grok": home / ".grok" / "models_cache.json",
        "codex": home / ".codex" / "models_cache.json",
        "claude": home / ".claude" / "cache" / "model-catalog",
    }


def load(path, *, for_write=False):
    """The saved catalog, or an empty one. Readers never create the file."""
    target = storage_path(path)
    try:
        linked = stat.S_ISLNK(os.lstat(target).st_mode)
    except (FileNotFoundError, NotADirectoryError):
        linked = False
    except OSError as exc:
        _fail("Cannot inspect the model catalog at {}: {}. Restore search permission on its "
              "directory; the catalog is left untouched.".format(target, exc))
    linked_message = ("The model catalog at {} is a symlink, not the owner's file. It is left "
                      "untouched: restore the regular file at that path, or remove the link to "
                      "start an empty catalog.".format(target))
    if linked:
        _fail(linked_message)
    try:
        descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return empty()
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            _fail(linked_message)
        _fail("Cannot read the model catalog at {}: {}. Restore a readable UTF-8 file, or "
              "remove it to start an empty catalog.".format(target, exc))
    try:
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            document = json.loads(handle.read())
    except (OSError, UnicodeDecodeError) as exc:
        _fail("Cannot read the model catalog at {}: {}. Restore a readable UTF-8 file, or "
              "remove it to start an empty catalog.".format(target, exc))
    except json.JSONDecodeError as exc:
        _fail("The model catalog at {} is not valid JSON ({}). Restore the owner-written "
              "file rather than editing it by hand.".format(target, exc.msg))
    version = document.get("schema_version") if isinstance(document, dict) else None
    if isinstance(version, int) and not isinstance(version, bool) and version > SCHEMA_VERSION:
        if for_write:
            _fail("The model catalog at {} is schema {}, newer than this build's {}. Update "
                  "the coding-policy plugin before recording; the file is left untouched.".format(
                      target, version, SCHEMA_VERSION))
        _warn("model catalog {} is schema {}, newer than this build's {}; reading it as no "
              "prior state. Update the coding-policy plugin. The file is left untouched.".format(
                  target, version, SCHEMA_VERSION))
        return empty()
    validate(document)
    return document


def validate(document):
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
        _fail("Unsupported catalog schema; update the owner skill before using it.")
    if set(document) != {"schema_version", "refreshed_at", "scopes", "entries", "observations"}:
        _fail("The model catalog carries exactly schema_version, refreshed_at, scopes, entries and observations.")
    if document["refreshed_at"] is not None:
        _utc(document["refreshed_at"])
    for field in ("scopes", "entries", "observations"):
        if not isinstance(document[field], list):
            _fail("The model catalog's {} must be an array.".format(field))
    seen_scopes = set()
    for scope in document["scopes"]:
        key = validate_scope(scope)
        if key in seen_scopes:
            _fail("The model catalog records adapter {} fingerprint {} twice.".format(*key))
        seen_scopes.add(key)
    seen = set()
    for entry in document["entries"]:
        key = validate_entry(entry)
        if key in seen:
            _fail("The model catalog records {} twice; one entry owns one adapter, model, "
                  "effort and fingerprint.".format(" / ".join(str(part) for part in key)))
        seen.add(key)
    for observation in document["observations"]:
        validate_observation(observation)
    return document


def validate_scope(scope):
    if not isinstance(scope, dict) or set(scope) != set(SCOPE_FIELDS):
        _fail("A catalog scope carries exactly {}.".format(", ".join(SCOPE_FIELDS)))
    adapter = _name(scope["adapter"], "Catalog adapter")
    if adapter not in ADAPTERS:
        _fail("A catalog adapter is one of {}.".format(", ".join(ADAPTERS)))
    if not isinstance(scope["cli_version"], str) or not scope["cli_version"].strip():
        _fail("A catalog scope names the CLI version it was read from.")
    fingerprint = scope["fingerprint"]
    if not isinstance(fingerprint, str) or not fingerprint.strip():
        _fail("A catalog scope fingerprint is a non-empty string; missing identity is the literal unknown.")
    _source(scope["source"], "Catalog scope source")
    _utc(scope["fetched_at"])
    _utc(scope["checked_at"])
    if not isinstance(scope["complete"], bool):
        _fail("A catalog scope complete flag is a boolean.")
    return adapter, fingerprint


def validate_entry(entry):
    if not isinstance(entry, dict) or set(entry) != set(ENTRY_FIELDS):
        _fail("A catalog entry carries exactly {}.".format(", ".join(ENTRY_FIELDS)))
    if set(entry) & FORBIDDEN_FIELDS:
        _fail("A catalog entry must not store credentials.")
    if entry["schema_version"] != SCHEMA_VERSION:
        _fail("A catalog entry carries an unsupported schema version.")
    adapter = _name(entry["adapter"], "Catalog adapter")
    if adapter not in ADAPTERS:
        _fail("A catalog adapter is one of {}.".format(", ".join(ADAPTERS)))
    model = _name(entry["model"], "Catalog model")
    effort = entry["effort"]
    if effort is not None:
        effort = _name(effort, "Catalog effort")
    if not isinstance(entry["aliases"], list) or any(not isinstance(item, str) for item in entry["aliases"]):
        _fail("A catalog entry's aliases are an array of strings.")
    if not isinstance(entry["resolved_id"], str) or not entry["resolved_id"].strip():
        _fail("A catalog entry names the resolved CLI id.")
    if not isinstance(entry["efforts"], list) or any(not isinstance(item, str) for item in entry["efforts"]):
        _fail("A catalog entry's efforts are an array of strings.")
    if entry["catalog_presence"] not in PRESENCE:
        _fail("A catalog_presence is one of {}.".format(", ".join(PRESENCE)))
    if entry["availability"] not in AVAILABILITY:
        _fail("A catalog availability is one of {}.".format(", ".join(AVAILABILITY)))
    if entry["availability"] == "available":
        _fail("PR1 never writes available; a readiness probe (PR3) owns that verdict.")
    klass = entry["availability_class"]
    if klass is not None and klass not in AVAILABILITY_CLASSES:
        _fail("A catalog availability_class is one of {} or null.".format(", ".join(AVAILABILITY_CLASSES)))
    if not isinstance(entry["judgment_family"], bool):
        _fail("judgment_family is a boolean.")
    fingerprint = entry["scope_fingerprint"]
    if not isinstance(fingerprint, str) or not fingerprint.strip():
        _fail("A catalog entry fingerprint is a non-empty string; missing identity is the literal unknown.")
    _utc(entry["listed_at"])
    _utc(entry["availability_at"])
    source = _source(entry["source"])
    if entry["judgment_family"] and source["kind"] not in SUPPORTING_SOURCES:
        _fail("judgment_family true rests on a {} source. Vendor copy and cache listing cannot "
              "set it; cite a benchmark, an independent evaluation, or this project's recorded result.".format(
                  source["kind"]))
    return adapter, model, effort, fingerprint


def validate_observation(observation):
    if not isinstance(observation, dict) or set(observation) != set(OBSERVATION_FIELDS):
        _fail("A catalog observation carries exactly {}.".format(", ".join(OBSERVATION_FIELDS)))
    if observation["schema_version"] != SCHEMA_VERSION:
        _fail("A catalog observation carries an unsupported schema version.")
    _name(observation["adapter"], "Catalog adapter")
    if observation["adapter"] not in ADAPTERS:
        _fail("A catalog adapter is one of {}.".format(", ".join(ADAPTERS)))
    _name(observation["model"], "Catalog model")
    if observation["effort"] is not None:
        _name(observation["effort"], "Catalog effort")
    if not isinstance(observation["scope_fingerprint"], str) or not observation["scope_fingerprint"].strip():
        _fail("A catalog observation fingerprint is a non-empty string.")
    if observation["availability_class"] not in AVAILABILITY_CLASSES:
        _fail("A catalog observation class is one of {}.".format(", ".join(AVAILABILITY_CLASSES)))
    if not isinstance(observation["text"], str):
        _fail("A catalog observation records the native text it matched.")
    _utc(observation["recorded_at"])
    _source(observation["source"], "Catalog observation source")
    return observation


def lookup(document, adapter, model, effort, fingerprint):
    """The recorded entry for one key, or None."""
    for entry in document["entries"]:
        if (entry["adapter"], entry["model"], entry["effort"], entry["scope_fingerprint"]) == (
                adapter, model, effort, fingerprint):
            return entry
    return None


def lookup_model(document, adapter, model, fingerprint):
    """The listed (non-deprecated) model row for this fingerprint, or None."""
    row = lookup(document, adapter, model, None, fingerprint)
    if row is None or row["catalog_presence"] == "deprecated":
        return None
    return row


def lookup_deprecated(document, adapter, model):
    """Deprecation is fingerprint-independent: any matching retired row."""
    for entry in document["entries"]:
        if (entry["adapter"] == adapter and entry["model"] == model
                and entry["catalog_presence"] == "deprecated"):
            return entry
    return None


def pair_fingerprint(document, adapter):
    """The latest scope fingerprint for an adapter, or the literal unknown."""
    latest = None
    for scope in document["scopes"]:
        if scope["adapter"] == adapter:
            latest = scope
    if latest is None:
        return "unknown"
    return latest["fingerprint"]


def no_effort_models(document, adapter):
    """Models whose catalog row records an empty effort list."""
    found = []
    for entry in document["entries"]:
        if (entry["adapter"] == adapter and entry["effort"] is None
                and list(entry["efforts"]) == []):
            found.append(entry["model"])
    return frozenset(found)


def catalog_efforts(document, adapter, model):
    row = lookup_model(document, adapter, model, pair_fingerprint(document, adapter))
    if row is None:
        # A fingerprint miss still widens from any listed row for that model.
        for entry in document["entries"]:
            if entry["adapter"] == adapter and entry["model"] == model and entry["effort"] is None:
                return list(entry["efforts"])
        return None
    return list(row["efforts"])


class CatalogRefusal(UsageError):
    """The catalog records the selected pair as unavailable, deprecated, or not a judgment family."""

    code = "catalog_refused"


def assess_pair(document, adapter, model, effort, fingerprint, *, judgment=False, judge=False):
    """Leave the configured pair, or refuse unavailable / deprecated / non-family judgment.

    Unknown, unlisted and listed-without-access leave the row, the way
    capability `unknown` does. A missing catalog is unknown. The pinned judge
    skips the family check. No substitute ID is ever returned.
    """
    retired = lookup_deprecated(document, adapter, model)
    if retired is not None:
        source = retired["source"]
        raise CatalogRefusal(
            "The model catalog records {} / {} as deprecated ({} source {}, read {}). "
            "Configure another model for this round; the catalog does not pick a replacement.".format(
                adapter, model, source["kind"], source["ref"], source["dated"]),
            {"adapter": adapter, "model": model, "effort": effort, "reason": "deprecated",
             "source": source})
    pair = lookup(document, adapter, model, effort, fingerprint)
    if pair is None:
        pair = lookup(document, adapter, model, None, fingerprint)
    if pair is not None and pair["availability"] == "unavailable" and pair["availability_class"] in ACCESS_CLASSES:
        raise CatalogRefusal(
            "The model catalog records {} / {} at {} effort unavailable for fingerprint {} "
            "({}). The pair is refused; no substitute ID is selected.".format(
                adapter, model, effort, fingerprint, pair["availability_class"]),
            {"adapter": adapter, "model": model, "effort": effort, "reason": "unavailable",
             "availability_class": pair["availability_class"], "scope_fingerprint": fingerprint})
    if judgment and not judge:
        family = lookup_model(document, adapter, model, fingerprint)
        if family is None:
            for entry in document["entries"]:
                if (entry["adapter"] == adapter and entry["model"] == model
                        and entry["effort"] is None and entry["scope_fingerprint"] == fingerprint):
                    family = entry
                    break
        if family is not None and family["catalog_presence"] == "listed" and not family["judgment_family"]:
            raise CatalogRefusal(
                "The model catalog lists {} / {} but does not record it as a judgment family. "
                "Record a supporting source through `{}` before a judgment round uses it.".format(
                    adapter, model, runnable.command("catalog-record")),
                {"adapter": adapter, "model": model, "effort": effort, "reason": "not_judgment_family",
                 "scope_fingerprint": fingerprint})
    return "ok"


def _due(reference, reason, **extra):
    payload = {"due": True, "last_refreshed_at": reference.isoformat(),
               "next_due_at": (reference + INTERVAL).isoformat(), "reason": reason}
    payload.update(extra)
    return payload


def cadence(document, at, *, caches=None, configured_adapters=ADAPTERS):
    """Whether the catalog is due a refresh, and why."""
    now = timestamp(_utc(at), "Catalog checkpoint")
    last = document.get("refreshed_at")
    if last is None:
        return {"due": True, "last_refreshed_at": None, "next_due_at": None, "reason": "never_refreshed"}
    reference = timestamp(last, "Catalog refresh")
    if reference > now:
        _fail("Catalog checkpoint precedes the saved refresh; use the current UTC checkpoint "
              "without rewriting history.")
    if now >= reference + INTERVAL:
        return _due(reference, "interval_elapsed")
    covered = {scope["adapter"] for scope in document["scopes"] if scope["complete"]}
    missing = [name for name in configured_adapters if name not in covered]
    if missing:
        return _due(reference, "incomplete_scope", incomplete=missing)
    for observation in document.get("observations") or []:
        klass = observation.get("availability_class")
        recorded = observation.get("recorded_at")
        if klass not in ACCESS_CLASSES or not isinstance(recorded, str):
            continue
        when = timestamp(recorded, "Catalog access observation")
        if when > reference:
            return _due(reference, "refusal_recorded")
    if caches:
        for adapter, path in caches.items():
            if path is None:
                continue
            facts = cache_facts(adapter, Path(path))
            if facts is None:
                continue
            stored = next((scope for scope in document["scopes"] if scope["adapter"] == adapter), None)
            if stored is None:
                continue
            if stored["fingerprint"] != facts["fingerprint"]:
                return _due(reference, "identity_changed", adapter=adapter)
            if stored["cli_version"] != facts["cli_version"]:
                return _due(reference, "client_changed", adapter=adapter)
            fetched = facts.get("fetched_at")
            if isinstance(fetched, str) and fetched:
                fetched_at = timestamp(fetched, "Catalog cache fetch")
                checked = timestamp(stored["checked_at"], "Catalog scope check")
                if fetched_at > checked:
                    return _due(reference, "cache_changed", adapter=adapter)
    return {"due": False, "last_refreshed_at": reference.isoformat(),
            "next_due_at": (reference + INTERVAL).isoformat(), "reason": "not_due"}


def _dated_from(value):
    parsed = timestamp(value, "Catalog fetch")
    return parsed.date().isoformat()


def _hash_identity(*parts):
    material = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _read_json_nofollow(path):
    target = Path(path)
    try:
        linked = stat.S_ISLNK(os.lstat(target).st_mode)
    except FileNotFoundError:
        return None
    except OSError as exc:
        _fail("Cannot inspect CLI cache {}: {}.".format(target, exc))
    if linked:
        _fail("CLI cache {} is a symlink; pass the owner's regular file, not a redirect.".format(target))
    try:
        descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            _fail("CLI cache {} is a symlink; pass the owner's regular file, not a redirect.".format(target))
        _fail("Cannot read CLI cache {}: {}.".format(target, exc))
    try:
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            return json.loads(handle.read())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("CLI cache {} is not readable UTF-8 JSON: {}.".format(target, exc))


def newest_regular_file(directory):
    """The newest regular file under `directory`; skip symlinks."""
    root = Path(directory)
    try:
        names = os.listdir(root)
    except FileNotFoundError:
        return None
    except OSError as exc:
        _fail("Cannot list Claude catalog directory {}: {}.".format(root, exc))
    newest = None
    newest_mtime = None
    for name in names:
        candidate = root / name
        try:
            info = os.lstat(candidate)
        except OSError:
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            continue
        if newest_mtime is None or info.st_mtime > newest_mtime:
            newest, newest_mtime = candidate, info.st_mtime
    return newest


def parse_grok_cache(path):
    data = _read_json_nofollow(path)
    if data is None:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("models"), dict):
        _fail("Grok cache {} is missing a models object.".format(path))
    identity = data.get("identity")
    if not isinstance(identity, str) or not identity.strip():
        identity = "unknown"
    models = []
    for model_id, row in data["models"].items():
        info = row.get("info") if isinstance(row, dict) else None
        efforts = []
        if isinstance(info, dict):
            raw = info.get("reasoning_efforts") or []
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, dict) and isinstance(item.get("id"), str):
                        efforts.append(item["id"])
                    elif isinstance(item, str):
                        efforts.append(item)
        models.append({"model": model_id, "aliases": [], "efforts": efforts})
    fetched = data.get("fetched_at")
    if not isinstance(fetched, str):
        fetched = None
    revision = data.get("cache_revision")
    if not isinstance(revision, str) or not revision:
        revision = data.get("etag") if isinstance(data.get("etag"), str) else None
    version = data.get("grok_version")
    return {
        "adapter": "grok",
        "cli_version": version if isinstance(version, str) and version else "unknown",
        "fingerprint": identity,
        "fetched_at": fetched,
        "revision": revision,
        "origin": data.get("origin") or "https://cli-chat-proxy.grok.com/v1/models",
        "models": models,
    }


def parse_codex_cache(path):
    data = _read_json_nofollow(path)
    if data is None:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        _fail("Codex cache {} is missing a models array.".format(path))
    identity = data.get("identity")
    if not isinstance(identity, str) or not identity.strip():
        identity = "unknown"
    models = []
    for row in data["models"]:
        if not isinstance(row, dict) or not isinstance(row.get("slug"), str):
            continue
        efforts = []
        levels = row.get("supported_reasoning_levels") or []
        if isinstance(levels, list):
            for item in levels:
                if isinstance(item, dict) and isinstance(item.get("effort"), str):
                    efforts.append(item["effort"])
                elif isinstance(item, str):
                    efforts.append(item)
        models.append({"model": row["slug"], "aliases": [], "efforts": efforts})
    fetched = data.get("fetched_at")
    if not isinstance(fetched, str):
        fetched = None
    version = data.get("client_version")
    return {
        "adapter": "codex",
        "cli_version": version if isinstance(version, str) and version else "unknown",
        "fingerprint": identity,
        "fetched_at": fetched,
        "origin": "codex models_cache",
        "models": models,
    }


def parse_claude_catalog(path):
    """`path` is a catalog JSON file, or the model-catalog directory."""
    target = Path(path)
    try:
        info = os.lstat(target)
    except FileNotFoundError:
        return None
    except OSError as exc:
        _fail("Cannot inspect Claude catalog {}: {}.".format(target, exc))
    if stat.S_ISLNK(info.st_mode):
        _fail("Claude catalog {} is a symlink; pass the owner's regular file or directory, not a redirect.".format(target))
    if stat.S_ISDIR(info.st_mode):
        target = newest_regular_file(target)
        if target is None:
            return None
    data = _read_json_nofollow(target)
    if data is None:
        return None
    if not isinstance(data, dict):
        _fail("Claude catalog {} is not a JSON object.".format(target))
    raw_catalog = data.get("catalog")
    catalog_body = raw_catalog if isinstance(raw_catalog, dict) else {}
    raw_config = catalog_body.get("config")
    config = raw_config if isinstance(raw_config, dict) else {}
    raw_rows = config.get("models")
    rows = raw_rows if isinstance(raw_rows, list) else []
    org_id = config.get("id")
    org = org_id if isinstance(org_id, str) else target.name.split("-")[0]
    models = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        efforts = []
        raw_thinking = row.get("thinking")
        thinking = raw_thinking if isinstance(raw_thinking, dict) else {}
        raw_options = thinking.get("effort_options")
        options = raw_options if isinstance(raw_options, list) else []
        for item in options:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                efforts.append(item["id"])
        aliases = []
        for key in ("short_name", "name"):
            value = row.get(key)
            if isinstance(value, str) and value and value not in aliases:
                aliases.append(value)
        models.append({"model": row["id"], "aliases": aliases, "efforts": efforts})
    fetched = data.get("fetchedAt")
    if isinstance(fetched, int):
        fetched = datetime.fromtimestamp(fetched / 1000, tz=timezone.utc).isoformat()
    elif not isinstance(fetched, str):
        fetched = None
    fingerprint = _hash_identity(org, target.name)
    return {
        "adapter": "claude",
        "cli_version": str(data.get("version") or "unknown"),
        "fingerprint": fingerprint,
        "fetched_at": fetched,
        "origin": str(target),
        "models": models,
    }


def cache_facts(adapter, path):
    parsers = {"grok": parse_grok_cache, "codex": parse_codex_cache, "claude": parse_claude_catalog}
    return parsers[adapter](path)


def cache_identity(adapter, path):
    parsed = cache_facts(adapter, path)
    if parsed is None:
        return None
    return parsed["fingerprint"]


def load_docs(path=None):
    target = Path(path) if path is not None else DOCS_FILE
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("Cannot read catalog docs fixture {}: {}.".format(target, exc))
    if not isinstance(document, dict) or not isinstance(document.get("sources"), list):
        _fail("Catalog docs fixture {} needs a sources array.".format(target))
    return document


def load_signatures(path=None):
    target = Path(path) if path is not None else SIGNATURES_FILE
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("Cannot read access-refusal signatures {}: {}.".format(target, exc))
    if not isinstance(document, dict) or not isinstance(document.get("signatures"), list):
        _fail("Access-refusal signatures {} need a signatures array.".format(target))
    return document


def match_output(text, adapter=None, signatures=None):
    """The first matching access/quota/transport signature, or None."""
    if not isinstance(text, str) or not text:
        return None
    document = signatures if signatures is not None else load_signatures()
    for row in document["signatures"]:
        if adapter is not None and row.get("adapter") not in {adapter, "*"}:
            continue
        pattern = row.get("pattern")
        if not isinstance(pattern, str):
            continue
        matched = re.search(pattern, text, re.IGNORECASE)
        if matched is None:
            continue
        model = None
        if matched.lastindex:
            model = matched.group(1)
        return {"class": row["class"], "model": model, "adapter": row.get("adapter")}
    return None


def _entry(adapter, model, *, effort, fingerprint, presence, availability, klass,
           family, listed_at, availability_at, source, aliases=None, resolved_id=None,
           efforts=None):
    return {
        "schema_version": SCHEMA_VERSION,
        "adapter": adapter,
        "model": model,
        "effort": effort,
        "aliases": list(aliases or []),
        "resolved_id": resolved_id or model,
        "efforts": list(efforts or []),
        "catalog_presence": presence,
        "availability": availability,
        "availability_class": klass,
        "judgment_family": family,
        "scope_fingerprint": fingerprint,
        "listed_at": listed_at,
        "availability_at": availability_at,
        "source": source,
    }


def _scope(adapter, cli_version, fingerprint, source, fetched_at, checked_at, complete):
    return {
        "adapter": adapter,
        "cli_version": cli_version,
        "fingerprint": fingerprint,
        "source": source,
        "fetched_at": fetched_at,
        "checked_at": checked_at,
        "complete": complete,
    }


def _merge_entry(existing, incoming):
    """Keep access observations across rediscovery; never write available."""
    if existing is None:
        return incoming
    merged = dict(incoming)
    merged["judgment_family"] = existing["judgment_family"] if existing["judgment_family"] else incoming["judgment_family"]
    if existing["availability"] == "unavailable" and existing["availability_class"] in ACCESS_CLASSES:
        merged["availability"] = existing["availability"]
        merged["availability_class"] = existing["availability_class"]
        merged["availability_at"] = existing["availability_at"]
    if existing["catalog_presence"] == "deprecated":
        merged["catalog_presence"] = "deprecated"
        merged["source"] = existing["source"]
    return merged


def discover(path, at, caches=None, docs=None):
    """Read injected or default CLI caches plus dated docs; write listed rows.

    Missing cache: that adapter's scope is incomplete, no IDs invented.
    Observations stay append-only. Availability from an access refusal is kept.
    """
    at_utc = _utc(at)
    dated = timestamp(at_utc, "Catalog discover").date().isoformat()
    caches = dict(default_cache_paths() if caches is None else caches)
    parsers = {"grok": parse_grok_cache, "codex": parse_codex_cache, "claude": parse_claude_catalog}
    docs_document = load_docs(docs)
    target = storage_path(path)
    with state_lock(target):
        document = load(path, for_write=True)
        scopes = []
        incoming = []
        for adapter in ADAPTERS:
            cache_path = caches.get(adapter)
            parsed = parsers[adapter](cache_path) if cache_path is not None else None
            if parsed is None:
                scopes.append(_scope(
                    adapter, "unknown", "unknown",
                    {"kind": "cli_cache", "ref": str(cache_path) if cache_path else "missing", "dated": dated},
                    at_utc, at_utc, False))
                continue
            fetched = parsed["fetched_at"] or at_utc
            try:
                fetched_utc = _utc(fetched)
            except UsageError:
                fetched_utc = at_utc
            fingerprint = parsed["fingerprint"] or "unknown"
            scopes.append(_scope(
                adapter, str(parsed["cli_version"]), fingerprint,
                {"kind": "cli_cache", "ref": parsed["origin"], "dated": _dated_from(fetched_utc)},
                fetched_utc, at_utc, True))
            for model in parsed["models"]:
                incoming.append(_entry(
                    adapter, model["model"], effort=None, fingerprint=fingerprint,
                    presence="listed", availability="unknown", klass="cache_listed",
                    family=False, listed_at=fetched_utc, availability_at=fetched_utc,
                    source={"kind": "cli_cache", "ref": parsed["origin"], "dated": _dated_from(fetched_utc)},
                    aliases=model.get("aliases") or [], resolved_id=model["model"],
                    efforts=model.get("efforts") or []))
        for source in docs_document["sources"]:
            adapter = source["adapter"]
            scope = next((row for row in scopes if row["adapter"] == adapter), None)
            if scope is None or not scope["complete"]:
                continue
            fingerprint = scope["fingerprint"]
            doc_source = {"kind": "docs", "ref": source["ref"], "dated": source["dated"]}
            for model in source.get("models") or []:
                if lookup({"entries": incoming}, adapter, model, None, fingerprint) is None:
                    incoming.append(_entry(
                        adapter, model, effort=None, fingerprint=fingerprint,
                        presence="listed", availability="unknown", klass="docs_listed",
                        family=False, listed_at=at_utc, availability_at=at_utc,
                        source=doc_source, aliases=source.get("aliases") or [], resolved_id=model,
                        efforts=[]))
            for retired in source.get("deprecated") or []:
                model = retired["model"] if isinstance(retired, dict) else retired
                incoming.append(_entry(
                    adapter, model, effort=None, fingerprint=fingerprint,
                    presence="deprecated", availability="unknown", klass="docs_listed",
                    family=False, listed_at=at_utc, availability_at=at_utc,
                    source=doc_source, aliases=[], resolved_id=model, efforts=[]))
        kept = []
        covered = {(row["adapter"], row["model"], row["effort"], row["scope_fingerprint"]) for row in incoming}
        by_key = {(row["adapter"], row["model"], row["effort"], row["scope_fingerprint"]): row for row in incoming}
        for existing in document["entries"]:
            key = (existing["adapter"], existing["model"], existing["effort"], existing["scope_fingerprint"])
            if key in by_key:
                by_key[key] = _merge_entry(existing, by_key[key])
            elif existing["effort"] is not None or existing["availability"] == "unavailable" or existing["judgment_family"]:
                kept.append(existing)
            elif existing["catalog_presence"] == "deprecated" and key not in covered:
                kept.append(existing)
        entries = list(by_key.values()) + kept
        entries.sort(key=lambda row: (row["adapter"], row["model"], row["effort"] or "", row["scope_fingerprint"]))
        document["scopes"] = scopes
        document["entries"] = entries
        document["refreshed_at"] = at_utc
        validate(document)
        save_state(target, document)
    return document


def record(path, data, at):
    """Write a consultation's family flags into matching model rows."""
    if not isinstance(data, dict) or set(data) != {"entries"}:
        _fail("A catalog record carries `entries` alone: the family flags this refresh covers.")
    if not isinstance(data["entries"], list) or not data["entries"]:
        _fail("A catalog family record needs at least one entry.")
    at_utc = _utc(at)
    stamped = []
    reported = {"adapter", "model", "judgment_family", "source"}
    for entry in data["entries"]:
        if not isinstance(entry, dict) or set(entry) != reported:
            _fail("A reported catalog family entry carries exactly {}.".format(", ".join(sorted(reported))))
        adapter = _name(entry["adapter"], "Catalog adapter")
        model = _name(entry["model"], "Catalog model")
        if not isinstance(entry["judgment_family"], bool):
            _fail("judgment_family is a boolean.")
        source = _source(entry["source"])
        if entry["judgment_family"] and source["kind"] not in SUPPORTING_SOURCES:
            _fail("judgment_family true rests on a {} source. Vendor copy and cache listing cannot "
                  "set it.".format(source["kind"]))
        stamped.append((adapter, model, entry["judgment_family"], source))
    target = storage_path(path)
    with state_lock(target):
        document = load(path, for_write=True)
        entries = list(document["entries"])
        for adapter, model, family, source in stamped:
            matched = False
            for index, row in enumerate(entries):
                if row["adapter"] == adapter and row["model"] == model and row["effort"] is None:
                    updated = dict(row)
                    updated["judgment_family"] = family
                    updated["source"] = source
                    updated["listed_at"] = row["listed_at"]
                    validate_entry(updated)
                    entries[index] = updated
                    matched = True
            if not matched:
                fingerprint = pair_fingerprint(document, adapter)
                row = _entry(
                    adapter, model, effort=None, fingerprint=fingerprint,
                    presence="unknown", availability="unknown", klass=None,
                    family=family, listed_at=at_utc, availability_at=at_utc,
                    source=source, aliases=[], resolved_id=model, efforts=[])
                validate_entry(row)
                entries.append(row)
        document["entries"] = sorted(entries, key=lambda row: (
            row["adapter"], row["model"], row["effort"] or "", row["scope_fingerprint"]))
        document["refreshed_at"] = at_utc
        validate(document)
        save_state(target, document)
    return document


def record_access(path, data, at):
    """Record a scoped access, quota or transport observation.

    Access and invalid-id write `unavailable`. Quota and transport append
    history and leave availability unchanged. PR1 never writes `available`.
    """
    if not isinstance(data, dict):
        _fail("A catalog access record is a JSON object.")
    required = {"adapter", "model", "effort", "fingerprint", "class"}
    optional = required | {"text", "source"}
    if set(data) - optional or not required <= set(data):
        _fail("A catalog access record carries adapter, model, effort, fingerprint and class.")
    adapter = _name(data["adapter"], "Catalog adapter")
    if adapter not in ADAPTERS:
        _fail("A catalog adapter is one of {}.".format(", ".join(ADAPTERS)))
    model = _name(data["model"], "Catalog model")
    effort = data["effort"]
    if effort is not None:
        effort = _name(effort, "Catalog effort")
    fingerprint = data["fingerprint"]
    if not isinstance(fingerprint, str) or not fingerprint.strip():
        _fail("Access observations are scoped to a fingerprint; missing identity is the literal unknown.")
    klass = data["class"]
    if klass not in AVAILABILITY_CLASSES:
        _fail("A catalog access class is one of {}.".format(", ".join(AVAILABILITY_CLASSES)))
    if klass == "request_ok":
        _fail("PR1 never writes available; a readiness probe (PR3) owns request_ok.")
    text = data.get("text") if isinstance(data.get("text"), str) else ""
    source = data.get("source") or {
        "kind": "project",
        "ref": "catalog-record-access",
        "dated": timestamp(_utc(at), "Catalog access").date().isoformat(),
    }
    source = _source(source, "Catalog access source")
    at_utc = _utc(at)
    observation = {
        "schema_version": SCHEMA_VERSION,
        "adapter": adapter,
        "model": model,
        "effort": effort,
        "scope_fingerprint": fingerprint,
        "availability_class": klass,
        "text": text,
        "recorded_at": at_utc,
        "source": source,
    }
    validate_observation(observation)
    target = storage_path(path)
    with state_lock(target):
        document = load(path, for_write=True)
        document["observations"] = list(document["observations"]) + [observation]
        if klass in ACCESS_CLASSES:
            existing = lookup(document, adapter, model, effort, fingerprint)
            row = _entry(
                adapter, model, effort=effort, fingerprint=fingerprint,
                presence=existing["catalog_presence"] if existing else "unknown",
                availability="unavailable", klass=klass, family=existing["judgment_family"] if existing else False,
                listed_at=existing["listed_at"] if existing else at_utc,
                availability_at=at_utc, source=source,
                aliases=existing["aliases"] if existing else [],
                resolved_id=existing["resolved_id"] if existing else model,
                efforts=existing["efforts"] if existing else [])
            validate_entry(row)
            document["entries"] = [
                item for item in document["entries"]
                if (item["adapter"], item["model"], item["effort"], item["scope_fingerprint"])
                != (adapter, model, effort, fingerprint)
            ] + [row]
            document["entries"].sort(key=lambda item: (
                item["adapter"], item["model"], item["effort"] or "", item["scope_fingerprint"]))
        document["refreshed_at"] = document["refreshed_at"] or at_utc
        validate(document)
        save_state(target, document)
    return document


def config_candidate_path(config_path, at):
    """PR2 writes a dated candidate beside config. PR1 has no writer."""
    _fail("Config reconciliation is not in this build; the catalog owner does not rewrite {}.".format(
        config_path))
