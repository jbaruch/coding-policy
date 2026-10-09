"""Diff-time detection of the four non-exhaustion Team Composition triggers.

The exhaustion trigger is enforced in code: `recovery.require_investigation_before_judge`
refuses a judge dispatch at an exhausted allowance with no assessment. The
architect, security, UX-and-product and documentation triggers shipped as prose
alone (#408), so the operative rule was "the foreman notices, or it does not
happen" -- the condition the triggers were written to replace. A trigger nobody
mechanically evaluates is a check nobody runs (`rules/language-diagnostics.md`).

This module is that evaluation. It reads the consuming repo's own declaration
of its trigger surfaces, classifies the task's diff against them, and refuses
the round when a fired trigger is neither staffed nor answered by a recorded
staffing decision. Every signal is mechanical: a package directory absent from
the base, a changed path in the declared trust-boundary set, an added line
carrying a declared CLI-surface marker, an added file in the declared
user-facing docs set.

The declaration is the repo's, never this module's. One absent-base bootstrap
may classify a reviewed external artifact whose exact digest and installation
path the plan binds; the first pushed head must install identical bytes. Every
existing and later declaration is read from the repository. The architect trigger reads
"above the size the repo states", and a repo that states no size has a trigger
that fires never or always depending on the reader (#415), so
`package_change_lines` is required for every writing round. A validated
read-only round has no repository surface to classify and may proceed without
a declaration (#671); an existing malformed declaration is still refused.

Contract:

* `load_declaration(repo)` reads `<repo>/.herdr/triggers.json`; callers may
  tolerate only its absence for an already validated read-only round or use
  `load_bootstrap_declaration` under the absent-base plan contract.
* `detect(...)` is pure over the declaration and the diff facts.
* `run_command(args, runner=...)` collects those facts through `runner`, a
  callable taking a git argument list and returning its stdout.
* The payload lists every trigger with its evidence; the failure object names
  the fired triggers that are neither staffed nor decided.
"""

import fnmatch
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from . import runnable, engagement, report_contract
from .recovery import receipt
from .state import default_state_path, load_state_checked
from .composition import REQUIREMENTS_SCHEMA_VERSION, parse_requirements
from .errors import UsageError
from .tiers import canonical_role, require_seatable


DECLARATION_SCHEMA_VERSION = 1
DETECTION_SCHEMA_VERSION = 3
DECISIONS_SCHEMA_VERSION = 1

#: Repo-relative location of the consuming repo's trigger declaration.
DECLARATION_FILE = ".herdr/triggers.json"

#: The four triggers `skills/herdr-foreman/references/team-operation.md` Team Composition states
#: outside the exhaustion path, each mapped to the planned responsibility or
#: requirements specialty that staffs it.
TRIGGER_ROLES = {"architect": "architect"}
TRIGGER_SPECIALTIES = {"security": "security", "ux-product": "ux-product",
                       "documentation": "documentation"}
TRIGGERS = tuple(sorted(set(TRIGGER_ROLES) | set(TRIGGER_SPECIALTIES)))

#: Declaration fields, all required. A repo declares an empty list to state
#: that a surface does not exist here; omitting the field states nothing.
GLOB_FIELDS = ("package_roots", "trust_boundary_paths", "cli_spec_paths", "user_doc_paths")
DECLARATION_FIELDS = frozenset({"schema_version", "package_change_lines",
                                "cli_surface_markers", *GLOB_FIELDS})


def _globs(value, label):
    if not isinstance(value, list) or len(value) > 200:
        raise UsageError("Trigger declaration {} must be a list of at most 200 path globs; state [] when the surface does not exist in this repo.".format(label), {})
    for entry in value:
        if not isinstance(entry, str) or not entry.strip() or any(ord(char) < 32 for char in entry):
            raise UsageError("Trigger declaration {} holds an empty or control-character glob; state each surface as a repo-relative path glob.".format(label), {})
    return list(value)


def _declaration(raw, path, *, authority):
    """Validate declaration bytes and retain the authority that supplied them."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UsageError("Trigger declaration {} is invalid JSON ({}); repair it before composing a round.".format(path, exc.msg), {}) from None
    if not isinstance(payload, dict) or set(payload) != DECLARATION_FIELDS:
        raise UsageError("Trigger declaration {} requires exactly {}; state every surface, using [] for one this repo does not have.".format(
            path, ", ".join(sorted(DECLARATION_FIELDS))), {})
    if payload["schema_version"] != DECLARATION_SCHEMA_VERSION:
        raise UsageError("Trigger declaration {} is schema_version {}; this owner reads version {}.".format(
            path, payload["schema_version"], DECLARATION_SCHEMA_VERSION), {})
    size = payload["package_change_lines"]
    if type(size) is not int or size < 1:
        raise UsageError("Trigger declaration {} needs a positive package_change_lines: the architect trigger fires above the size this repo states, and an unstated size fires never or always depending on the reader.".format(path), {})
    markers = payload["cli_surface_markers"]
    if not isinstance(markers, list) or len(markers) > 50:
        raise UsageError("Trigger declaration {} cli_surface_markers must list at most 50 literal substrings; state [] when this repo declares no CLI spec surface.".format(path), {})
    for marker in markers:
        if not isinstance(marker, str) or not marker.strip() or any(ord(char) < 32 for char in marker):
            raise UsageError("Trigger declaration {} holds an empty or control-character CLI surface marker; each marker is a literal substring an added line carries.".format(path), {})
    declaration = {"schema_version": DECLARATION_SCHEMA_VERSION, "package_change_lines": size,
                   "cli_surface_markers": list(markers)}
    for field in GLOB_FIELDS:
        declaration[field] = _globs(payload[field], field)
    declaration["path"] = str(path)
    declaration["authority"] = authority
    return declaration


def load_declaration(repo, *, required=True):
    """Read and validate the consuming repo's trigger declaration.

    A missing or incomplete declaration is normally refused, never defaulted:
    the architect trigger's size and the other three surfaces are the repo's to
    state, and an invented default would fire on repos it was never measured
    against. A caller that has already validated an explicit no-write plan may
    set ``required=False``; only a genuinely absent file is tolerated, and the
    returned path is sufficient for the all-quiet report.
    """
    root = Path(repo)
    path = root / DECLARATION_FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        if not required and not path.is_symlink():
            return {"path": str(path), "authority": {"kind": "repository", "revision": "worktree"}}
        raise UsageError("No trigger declaration at {} ({}); state this repo's package roots and size, trust-boundary paths, CLI spec surface and user-facing docs paths there before composing a round.".format(path, exc.strerror), {}) from None
    except OSError as exc:
        raise UsageError("No trigger declaration at {} ({}); state this repo's package roots and size, trust-boundary paths, CLI spec surface and user-facing docs paths there before composing a round.".format(path, exc.strerror), {}) from None
    return _declaration(raw, path, authority={"kind": "repository", "revision": "worktree"})


def load_decisions(path):
    """Read the foreman's recorded staffing decisions for fired triggers."""
    if path is None:
        return {}
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise UsageError("Cannot read staffing decisions at {} ({}); write the recorded decision or omit --decisions.".format(path, exc.strerror), {}) from None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UsageError("Staffing decisions {} are invalid JSON ({}); repair the recorded decision.".format(path, exc.msg), {}) from None
    if (not isinstance(payload, dict) or set(payload) != {"schema_version", "decisions"}
            or payload["schema_version"] != DECISIONS_SCHEMA_VERSION
            or not isinstance(payload["decisions"], dict)):
        raise UsageError("Staffing decisions must be a schema_version {} object with a decisions map of trigger to reason.".format(DECISIONS_SCHEMA_VERSION), {})
    decisions = {}
    for name, reason in payload["decisions"].items():
        if name not in TRIGGERS:
            raise UsageError("Staffing decision names unknown trigger {!r}; the recorded triggers are {}.".format(name, ", ".join(TRIGGERS)), {})
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
            raise UsageError("Staffing decision for {} needs its reason in 1-4000 characters; silence is never that decision.".format(name), {})
        decisions[name] = reason.strip()
    return decisions


PLAN_FIELDS = frozenset({"schema_version", "added", "changed", "package_lines", "cli_surface"})
#: A round that writes no repository content says so here. Omitting the field
#: reads as True, so every plan written before it keeps its meaning and only an
#: explicit `false` opens the no-surface path (#471).
PLAN_OPTIONAL_FIELDS = frozenset({"writes_repository", "bootstrap_declaration_sha256"})
#: The responsibilities `skills/herdr-foreman/references/team-operation.md` declares read-only on
#: repository content. A round claiming to write nothing seats these alone.
READ_ONLY_ROLES = frozenset({"advisor", "investigator", "architect"})
#: Only verification and consultation responsibilities may classify an unchanged
#: legacy PR. This door grants no writing or release authority, and takes no
#: staffing-decision overrides. Seats are compared by canonical responsibility.
LEGACY_REVIEW_ROLES = READ_ONLY_ROLES | frozenset({"reviewer", "tester"})
LEGACY_BINDING_FIELDS = frozenset({"repo", "base_revision", "head_revision", "path", "sha256"})


def _full_oid(value):
    return (isinstance(value, str) and len(value) in (40, 64)
            and all(char in "0123456789abcdef" for char in value))


def _legacy_binding(raw):
    """Reject ambiguous duplicate JSON keys in the new closed legacy shape."""
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise UsageError("Legacy TRIGGER_DECLARATION repeats {}; supply one unambiguous five-key binding and reassess the report.".format(key), {})
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique_object)


def load_plan(path):
    """Read the surfaces a round intends to touch, before it has a diff.

    The triggers gate work *before* implementation, and a task's first round has
    nothing committed to read: a diff-only detector reports every trigger quiet
    on exactly the round the architect and security triggers exist for (#415).
    The foreman declares the intended surfaces here, and the same declaration
    classifies them. Later rounds keep reading the diff, which is evidence
    rather than intent.
    """
    if path is None:
        return None
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise UsageError("Cannot read planned surfaces at {} ({}); write the plan or omit --planned.".format(path, exc.strerror), {}) from None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UsageError("Planned surfaces {} are invalid JSON ({}); repair the plan.".format(path, exc.msg), {}) from None
    if (not isinstance(payload, dict) or set(payload) - PLAN_OPTIONAL_FIELDS != PLAN_FIELDS
            or payload["schema_version"] != DECLARATION_SCHEMA_VERSION):
        raise UsageError("Planned surfaces must be a schema_version {} object with added, changed, package_lines and cli_surface, and may carry writes_repository and bootstrap_declaration_sha256; state [] or {{}} for one this round has none of.".format(
            DECLARATION_SCHEMA_VERSION), {})
    plan: dict[str, Any] = {"schema_version": DECLARATION_SCHEMA_VERSION}
    for field in ("added", "changed", "cli_surface"):
        plan[field] = _globs(payload[field], "planned " + field)
    lines = payload["package_lines"]
    if not isinstance(lines, dict):
        raise UsageError("Planned package_lines maps a package directory to the lines this round will change there; state {} when none.", {})
    for name, count in lines.items():
        if not isinstance(name, str) or not name.strip() or type(count) is not int or count < 0:
            raise UsageError("Planned package_lines needs a package directory and a non-negative line count; state the size this round will change.", {})
    plan["package_lines"] = dict(lines)
    writes = payload.get("writes_repository", True)
    if type(writes) is not bool:
        raise UsageError("Planned writes_repository is a JSON boolean; state false only for a round that writes no repository content.", {})
    plan["writes_repository"] = writes
    if not writes and (plan["added"] or plan["changed"] or plan["cli_surface"] or plan["package_lines"]):
        raise UsageError("A plan declaring writes_repository false names no surface; empty added, changed, cli_surface and package_lines, or declare the surfaces the round will touch.", {})
    digest = payload.get("bootstrap_declaration_sha256")
    if digest is not None and (not isinstance(digest, str) or len(digest) != 64
                               or any(char not in "0123456789abcdef" for char in digest)):
        raise UsageError("Planned bootstrap_declaration_sha256 must be the reviewed trigger artifact's lowercase SHA-256 hex digest.", {})
    if not writes and digest is not None:
        raise UsageError("A no-write plan cannot install a trigger declaration; remove bootstrap_declaration_sha256.", {})
    plan["bootstrap_declaration_sha256"] = digest
    return plan


def load_revision_declaration(run, repo, revision, *, required=True):
    """Read the declaration from a named commit, never adjacent checkout bytes."""
    path = Path(repo) / DECLARATION_FILE
    present = run(["ls-tree", "--name-only", revision, "--", DECLARATION_FILE]).strip()
    if not present:
        if not required:
            return {"path": str(path), "authority": {"kind": "repository", "revision": revision}}
        raise UsageError("No trigger declaration at {} in {}; install the reviewed bootstrap declaration before classifying this pushed head.".format(path, revision), {})
    raw = run(["show", "{}:{}".format(revision, DECLARATION_FILE)])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return _declaration(raw, path, authority={"kind": "repository", "revision": revision,
                                              "sha256": digest})


def validate_bootstrap_binding(raw):
    """Validate the specialist's structured evidence at ordinary assessment."""
    try:
        binding = json.loads(raw)
    except json.JSONDecodeError:
        raise UsageError("TRIGGER_DECLARATION evidence must be a JSON object naming repo, base_revision, path and sha256.", {}) from None
    if (not isinstance(binding, dict) or set(binding) != {"repo", "base_revision", "path", "sha256"}
            or any(not isinstance(value, str) for value in binding.values())
            or not Path(binding["repo"]).is_absolute() or not Path(binding["path"]).is_absolute()
            or len(binding["base_revision"]) not in (40, 64)
            or any(char not in "0123456789abcdef" for char in binding["base_revision"])
            or len(binding["sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in binding["sha256"])):
        raise UsageError("TRIGGER_DECLARATION requires absolute repo/path, exact base commit and SHA-256 of reviewed bytes.", {})
    artifact = Path(binding["path"])
    if artifact.resolve().is_relative_to(Path(binding["repo"]).resolve()):
        raise UsageError("Reviewed declaration artifact must be outside the target repository.", {})
    evidence, body = receipt(str(artifact))
    if evidence["sha256"] != binding["sha256"]:
        raise UsageError("Reviewed declaration artifact changed before assessment; the consultation must review its actual bytes.", {})
    _declaration(body, artifact, authority={"kind": "bootstrap"})
    return binding


def accepted_bootstrap(repo, base, artifact, digest, state_path):
    """Read an existing accepted consultation's immutable artifact evidence.

    The specialist report supplies the exact binding, not an operator approval
    flag. Its ordinary assessment binds those report bytes and delivered role.
    No state is written and no native call is made during classification.
    """
    state, usable = load_state_checked(state_path or default_state_path(), persist_migration=False)
    if not usable:
        raise UsageError("Bootstrap consultation history is unreadable; restore the existing owner ledger.", {})
    expected = {"repo": str(Path(repo).resolve()), "base_revision": base,
                "path": str(Path(artifact).resolve()), "sha256": digest}
    for record in reversed(state["specialist_assessments"]):
        if record["role"] not in engagement.CONSULTATION_ROLES or not engagement.investigated(record):
            continue
        try:
            evidence, body = receipt(record["report"])
        except UsageError:
            # Missing historical reports establish no authority; they do not
            # impose unrelated restoration work on a first integration.
            continue
        if evidence != record["report_evidence"]:
            continue
        bindings = report_contract.trigger_bindings(body)
        if len(bindings) != 1:
            continue
        try:
            binding = json.loads(bindings[0])
        except json.JSONDecodeError:
            continue
        if binding == expected:
            return record["id"]
    raise UsageError("The first trigger declaration has no accepted consultation bound to these exact artifact bytes, repository and task base. Dispatch the read-only declaration consultation and assess its delivered report through the normal owner path.", {})


def validate_trigger_binding(raw):
    """Read the two closed report-line shapes without widening writing bootstrap.

    Four keys retain the original writing contract. Five keys bind one legacy
    head; both reuse the same artifact validation during normal assessment.
    The line is report content, not a new stored record or owner approval.
    """
    try:
        binding = json.loads(raw)
    except json.JSONDecodeError:
        return validate_bootstrap_binding(raw)
    if not isinstance(binding, dict) or set(binding) != LEGACY_BINDING_FIELDS:
        return validate_bootstrap_binding(raw)
    binding = _legacy_binding(raw)
    if not _full_oid(binding["head_revision"]):
        raise UsageError("Legacy TRIGGER_DECLARATION requires a full head_revision commit OID; assess the exact pushed head, not a branch or abbreviation.", {})
    base_binding = {key: value for key, value in binding.items() if key != "head_revision"}
    validate_bootstrap_binding(json.dumps(base_binding))
    if any(binding[key] != str(Path(binding[key]).resolve()) for key in ("repo", "path")):
        raise UsageError("Legacy TRIGGER_DECLARATION requires canonical absolute repo/path; consult and assess those exact paths.", {})
    return binding


def accepted_legacy_review(expected, task, state_path):
    """Find unchanged, report-sourced authority for one exact task/head tuple.

    This reader writes no state and preserves the existing older-state
    migration refusal. Unrelated historical gaps create no authority and no
    restoration obligation. Diagnostics name the missing binding dimensions.
    """
    state, usable = load_state_checked(state_path or default_state_path(), persist_migration=False)
    if not usable:
        raise UsageError("Legacy consultation history is unreadable; restore the existing owner ledger before classification.", {})
    mismatched = set()
    for record in reversed(state["specialist_assessments"]):
        if record["role"] not in engagement.CONSULTATION_ROLES:
            continue
        if record["task"] != task:
            mismatched.add("task")
            continue
        if not engagement.investigated(record):
            mismatched.add("assessment acceptance")
            continue
        try:
            evidence, body = receipt(record["report"])
        except UsageError:
            mismatched.add("report")
            continue
        if evidence != record["report_evidence"]:
            mismatched.add("report")
            continue
        bindings = report_contract.trigger_bindings(body)
        if len(bindings) != 1:
            mismatched.add("report binding")
            continue
        try:
            binding = _legacy_binding(bindings[0])
        except (json.JSONDecodeError, UsageError):
            mismatched.add("report binding")
            continue
        if not isinstance(binding, dict) or set(binding) != LEGACY_BINDING_FIELDS:
            mismatched.add("five-key binding")
            continue
        differences = {key for key in LEGACY_BINDING_FIELDS if binding[key] != expected[key]}
        if differences:
            mismatched.update(differences)
            continue
        return record["id"], evidence
    dimensions = sorted(mismatched or {"assessment"})
    raise UsageError("Legacy review has no accepted consultation for this exact task, repository, base, head and artifact/report bytes (mismatch: {}). Obtain a new bounded five-key consultation assessment for the exact full head and rerun detection; do not change the PR to install a declaration.".format(
        ", ".join(dimensions)), {"mismatched": dimensions})


def load_legacy_review_declaration(args, plan, run):
    """Load the read-only, task/head-bound external declaration (#729).

    Require immutable subjects and an empty no-write plan before reading any
    authority. Both commits must predate the declaration. Every mismatch fails
    before diff classification; success returns canonical roles and commits.
    """
    task, head = getattr(args, "task", None), getattr(args, "head", None)
    if (not isinstance(task, str) or not task.strip() or not isinstance(head, str) or not _full_oid(args.base)
            or not _full_oid(head) or plan is None or plan["writes_repository"]):
        raise UsageError("--legacy-review-declaration requires --task, literal full --base and --head OIDs, an empty no-write --planned record and read-only --roles. Preserve the unchanged PR and assess its exact full head.", {})
    if getattr(args, "bootstrap_declaration", None) is not None or getattr(args, "decisions", None) is not None:
        raise UsageError("Legacy review accepts neither --bootstrap-declaration nor --decisions; use its accepted external binding and staff every fired trigger.", {})
    raw_roles = (getattr(args, "roles", None) or "").split(",")
    for role in raw_roles:
        require_seatable(role)
    roles = [canonical_role(role) for role in raw_roles]
    if not set(roles) <= LEGACY_REVIEW_ROLES:
        raise UsageError("Legacy review requires read-only verification/consultation --roles; remove writing, release, judge, empty or unknown responsibilities before classification.", {})
    for revision in (args.base, head):
        if run(["rev-parse", "--verify", revision + "^{commit}"]).strip() != revision:
            raise UsageError("Legacy revision {} did not resolve to its exact commit; use its full commit OID and obtain a matching assessment.".format(revision), {})
        if run(["ls-tree", "--name-only", revision, "--", DECLARATION_FILE]).strip():
            route = "committed in-repository authority" if revision == args.base else "the byte-identical writing bootstrap"
            raise UsageError("Legacy review requires both revisions to lack {}; {} contains it. Use {} instead.".format(
                DECLARATION_FILE, revision, route), {})
    artifact = Path(args.legacy_review_declaration)
    repo = Path(args.repo).resolve()
    if artifact.resolve().is_relative_to(repo):
        raise UsageError("Legacy review declaration must remain outside the target repository; restore the assessed external artifact.", {})
    evidence, body = receipt(str(artifact.resolve()))
    expected = {"repo": str(repo), "base_revision": args.base, "head_revision": head,
                "path": evidence["path"], "sha256": evidence["sha256"]}
    declaration = _declaration(body, artifact.resolve(), authority={"kind": "legacy_review"})
    assessment, report_evidence = accepted_legacy_review(expected, task, getattr(args, "state", None))
    declaration["authority"].update(expected, task=task, assessment=assessment, report_evidence=report_evidence)
    return declaration, roles


def load_bootstrap_declaration(repo, base, artifact, plan, run, state_path=None):
    """Load the one-time reviewed declaration bound to an absent base and plan."""
    if plan is None or not plan["writes_repository"]:
        raise UsageError("--bootstrap-declaration requires a writing --planned record that binds the reviewed artifact's installation.", {})
    if DECLARATION_FILE not in plan["added"]:
        raise UsageError("The bootstrap plan must add {}; include it in planned added paths.".format(DECLARATION_FILE), {})
    expected = plan["bootstrap_declaration_sha256"]
    if expected is None:
        raise UsageError("The bootstrap plan must bind the reviewed artifact in bootstrap_declaration_sha256 before dispatch.", {})
    base_entry = run(["ls-tree", "--name-only", base, "--", DECLARATION_FILE]).strip()
    if base_entry:
        raise UsageError("The recorded base {} already contains {}; the in-repo declaration is sole authority and the bootstrap is stale.".format(
            base, DECLARATION_FILE), {})
    artifact_path = Path(artifact)
    try:
        data = artifact_path.read_bytes()
    except OSError as exc:
        raise UsageError("Cannot read reviewed bootstrap declaration at {} ({}); restore the accepted artifact before classification.".format(
            artifact_path, exc.strerror), {}) from None
    if os.path.commonpath([str(Path(repo).resolve()), str(artifact_path.resolve())]) == str(Path(repo).resolve()):
        raise UsageError("Reviewed bootstrap declaration {} must remain outside the target repository until the developer installs it.".format(
            artifact_path), {})
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise UsageError("Reviewed bootstrap declaration {} is {} but the plan binds {}; restore the reviewed bytes or return to classification with a new accepted plan.".format(
            artifact_path, actual, expected), {})
    try:
        raw = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UsageError("Reviewed bootstrap declaration {} is not UTF-8 ({}); restore the accepted JSON artifact.".format(
            artifact_path, exc.reason), {}) from None
    assessment = accepted_bootstrap(repo, run(["rev-parse", "--verify", base + "^{commit}"]).strip(), artifact, actual, state_path)
    return _declaration(raw, artifact_path, authority={"kind": "bootstrap", "artifact": str(artifact_path),
                                                      "sha256": actual, "assessment": assessment})


def load_requirements(path, *, roles=None, task=None):
    """Collect the specialties a requirements file staffs, for trigger cover."""
    if path is None:
        if roles is not None:
            parse_requirements(None, roles, task)
        return set()
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise UsageError("Cannot read requirements at {} ({}); pass the same file Step 6 gives `{}`.".format(
            path, exc.strerror, runnable.command("plan")), {}) from None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UsageError("Requirements {} are invalid JSON ({}); repair the file before planning.".format(path, exc.msg), {}) from None
    if (not isinstance(payload, dict) or set(payload) != {"schema_version", "assignments"}
            or payload["schema_version"] != REQUIREMENTS_SCHEMA_VERSION
            or not isinstance(payload["assignments"], dict)):
        raise UsageError("Requirements must be a schema_version {} object with an assignments map; use the documented requirements file.".format(REQUIREMENTS_SCHEMA_VERSION), {})
    if roles is not None:
        # Legacy triggers must be staffed by actual read-only seats, not an
        # unseated or writing requirement that the normal planner would refuse.
        resolved = parse_requirements(payload, roles, task)
        return {record["specialty"] for record in resolved.values()}
    found = set()
    for record in payload["assignments"].values():
        if isinstance(record, dict) and isinstance(record.get("specialty"), str):
            found.add(record["specialty"])
    return found


def matches(path, globs):
    """True when `path` matches any path glob. `*` spans path separators.

    A declared surface is a subtree -- `docs/*` covers everything under `docs`
    -- so the path sets deliberately span separators.
    """
    return any(fnmatch.fnmatchcase(path, glob) for glob in globs)


def package_matches(directory, roots):
    """True when `directory` is one of the declared package roots.

    A package root names a directory, not a subtree, so `*` matches within one
    path segment here: `skills/*` is every skill, never a directory nested
    inside one.
    """
    segments = directory.split("/")
    for glob in roots:
        parts = glob.split("/")
        if len(parts) == len(segments) and all(
                fnmatch.fnmatchcase(segment, part) for segment, part in zip(segments, parts)):
            return True
    return False


def package_of(path, roots):
    """The declared package directory holding `path`, or None.

    The nearest matching ancestor wins, so a nested declared root claims its
    own files rather than leaving them with the outer one.
    """
    parts = path.split("/")
    for cut in range(len(parts) - 1, 0, -1):
        candidate = "/".join(parts[:cut])
        if package_matches(candidate, roots):
            return candidate
    return None


def detect(declaration, changes, churn, base_packages, planned_lines=None):
    """Classify one diff against the declaration. Pure.

    `changes` maps a repo-relative path to its git status letter under
    `--no-renames`, so a rename reads as a delete plus an add. `churn` maps a
    path to its added-plus-deleted line count; a binary path contributes 0.
    `base_packages` maps each candidate package directory to whether the base
    revision held it -- the one fact the diff cannot supply. `planned_lines`
    seeds a package's changed-line count from the round's declared plan, for
    work that has not been written yet.
    """
    fired = {}
    packages = dict(planned_lines or {})
    for path in changes:
        package = package_of(path, declaration["package_roots"])
        if package is not None:
            packages.setdefault(package, 0)
            packages[package] += churn.get(path, 0)
    new_packages = sorted(name for name in packages if not base_packages.get(name, True))
    grown = sorted(name for name, lines in packages.items()
                   if base_packages.get(name, True) and lines > declaration["package_change_lines"])
    if new_packages or grown:
        fired["architect"] = [
            *({"signal": "new_package", "evidence": name} for name in new_packages),
            *({"signal": "package_change_lines", "evidence": "{} ({} lines > {})".format(name, packages[name], declaration["package_change_lines"])}
              for name in grown),
        ]
    boundary = sorted(path for path in changes if matches(path, declaration["trust_boundary_paths"]))
    if boundary:
        fired["security"] = [{"signal": "trust_boundary_path", "evidence": path} for path in boundary]
    documents = sorted(path for path, status in changes.items()
                       if status == "A" and matches(path, declaration["user_doc_paths"]))
    if documents:
        fired["documentation"] = [{"signal": "added_user_document", "evidence": path} for path in documents]
    return fired


def cli_surface(declaration, changes, added_lines):
    """The added CLI-surface lines, keyed by the spec path that carries them."""
    found = []
    for path in sorted(changes):
        if not matches(path, declaration["cli_spec_paths"]):
            continue
        for line in added_lines.get(path, ()):
            if any(marker in line for marker in declaration["cli_surface_markers"]):
                found.append({"signal": "added_cli_surface", "evidence": path + ": " + line.strip()[:200]})
    return found


def report(declaration, base, head, fired, roles, specialties, decisions):
    """Assemble the detection document and its unaddressed-trigger failure."""
    roles, triggers, unaddressed = set(roles), [], []
    for name in TRIGGERS:
        signals = fired.get(name, [])
        addressed = None
        if signals:
            role = TRIGGER_ROLES.get(name)
            specialty = TRIGGER_SPECIALTIES.get(name)
            if role is not None and role in roles:
                addressed = "role:" + role
            elif specialty is not None and specialty in specialties:
                addressed = "specialty:" + specialty
            elif name in decisions:
                addressed = "decision"
            else:
                unaddressed.append(name)
        triggers.append({"trigger": name, "fired": bool(signals), "signals": signals,
                         "addressed": addressed, "decision": decisions.get(name)})
    payload = {"schema_version": DETECTION_SCHEMA_VERSION, "declaration": declaration["path"],
               "declaration_authority": declaration["authority"],
               "base": base, "head": head, "triggers": triggers,
               "fired": [name for name in TRIGGERS if fired.get(name)],
               "unaddressed": unaddressed,
               "unused_decisions": sorted(name for name in decisions if not fired.get(name))}
    failure = None
    if unaddressed:
        failure = {"error": "unaddressed_trigger",
                   "message": "Triggers {} fired and this round neither staffs their consultation nor records a staffing decision; consult the profile in skills/herdr-foreman/references/specialists.md or record the decision and its reason.".format(", ".join(unaddressed)),
                   "details": {"unaddressed": unaddressed}}
    return payload, failure


def _records(text):
    """Split git's NUL-delimited output, dropping the trailing empty record."""
    return [record for record in text.split("\0") if record != ""]


def parse_name_status(text):
    records = _records(text)
    if len(records) % 2:
        raise UsageError("git diff --name-status returned an odd number of NUL-delimited fields; rerun the detection against a clean checkout.", {})
    return {records[index + 1]: records[index][:1] for index in range(0, len(records), 2)}


def parse_numstat(text):
    churn = {}
    for record in _records(text):
        added, deleted, path = record.split("\t", 2)
        # `-` is git's binary marker; a binary file has no line count to add.
        churn[path] = (0 if added == "-" else int(added)) + (0 if deleted == "-" else int(deleted))
    return churn


def parse_added_lines(text):
    """Added lines of a single-file unified diff.

    The caller asks for one path at a time and already holds it, so no file
    header is read. git quotes a path carrying a quote, a backslash or a
    non-ASCII byte (`+++ "b/src/a\\"b.py"`), and a parser keyed on that header
    drops the file and its added command, flag or refusal with it (#415).
    Reading only what follows a hunk header is immune to that quoting: a
    content line reading `++ x` arrives as `+++ x` inside a hunk and is an
    added line, never a header.
    """
    added, in_hunk = [], False
    for line in text.split("\n"):
        if line.startswith("@@"):
            in_hunk = True
        elif in_hunk and line.startswith("+"):
            added.append(line[1:])
    return added


def read_lines(repo, path):
    """The working tree's lines for an untracked file.

    A file git is not tracking has no diff to read, so its own bytes are the
    added lines. A binary file legitimately yields none; an unreadable one is a
    failure, never an empty result.
    """
    try:
        return Path(repo, path).read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return []
    except OSError as exc:
        raise UsageError("Cannot read untracked file {} in {} ({}); remove it or commit it before detecting the triggers.".format(
            path, repo, exc.strerror), {}) from None


def collect_untracked(run, repo, changes, churn, added_lines):
    """Fold the working tree's untracked files into the diff facts.

    `git diff` reports tracked changes only, so a whole new package or a new
    user-facing document -- the very shapes the architect and documentation
    triggers exist for -- would fire nothing while they sit untracked (#415).
    They are added files by definition, and every line in them is an added
    line. A comparison against a pushed head needs none of this: an untracked
    file is in no commit.
    """
    for path in _records(run(["ls-files", "--others", "--exclude-standard", "-z"])):
        lines = read_lines(repo, path)
        changes[path] = "A"
        churn[path] = len(lines)
        added_lines[path] = lines


def git_runner(repo):
    """A runner executing git in `repo` and returning stdout."""

    def run(arguments):
        completed = subprocess.run(["git", "-C", str(repo), *arguments],
                                   capture_output=True, check=False)
        try:
            stdout = completed.stdout.decode("utf-8")
            stderr = completed.stderr.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UsageError("git {} returned non-UTF-8 output in {} ({}); use UTF-8 repository metadata and trigger declarations.".format(
                " ".join(arguments), repo, exc.reason), {}) from None
        if completed.returncode != 0:
            raise UsageError("git {} failed in {} ({}): {}".format(
                " ".join(arguments), repo, completed.returncode, stderr.strip() or "no diagnostic"), {})
        return stdout
    return run


def run_command(args, runner=None):
    """Detect the triggers for one task's diff and gate on the unaddressed set."""
    plan = load_plan(getattr(args, "planned", None))
    writes = plan is None or plan["writes_repository"]
    run = runner if runner is not None else git_runner(args.repo)
    head = getattr(args, "head", None)
    declaration_path = Path(args.repo) / DECLARATION_FILE
    bootstrap = None
    artifact = getattr(args, "bootstrap_declaration", None)
    legacy = getattr(args, "legacy_review_declaration", None)
    legacy_roles = None
    legacy_declaration = None
    if legacy is not None:
        legacy_declaration, legacy_roles = load_legacy_review_declaration(args, plan, run)
    base_has_declaration = bool(run(["ls-tree", "--name-only", args.base, "--", DECLARATION_FILE]).strip()) if writes else False
    if writes and not base_has_declaration:
        if artifact is None:
            raise UsageError("The recorded base lacks a trigger declaration; the first writing round requires --bootstrap-declaration and its accepted consultation, including when --head or the worktree already contains a declaration.", {})
        bootstrap = load_bootstrap_declaration(
            args.repo, args.base, artifact, plan, run, getattr(args, "state", None))
    elif artifact is not None:
        # A stale bootstrap cannot override a committed authority.
        bootstrap = load_bootstrap_declaration(
            args.repo, args.base, artifact, plan, run, getattr(args, "state", None))
    if legacy_declaration is not None:
        declaration = legacy_declaration
    elif head:
        declaration = load_revision_declaration(run, args.repo, head, required=writes)
    elif declaration_path.exists() or declaration_path.is_symlink():
        declaration = load_declaration(args.repo, required=writes)
        if bootstrap is not None:
            data = declaration_path.read_bytes()
            declaration["authority"]["sha256"] = hashlib.sha256(data).hexdigest()
    elif bootstrap is not None:
        declaration = bootstrap
    else:
        declaration = load_declaration(args.repo, required=writes)
    if bootstrap is not None and declaration is not bootstrap:
        expected = bootstrap["authority"]["sha256"]
        if declaration["authority"].get("sha256") != expected:
            raise UsageError("The first installed declaration differs from the accepted bootstrap artifact; restore the byte-identical installation or return to classification.", {})
        declaration["authority"]["bootstrap_sha256"] = expected
        declaration["authority"]["bootstrap_assessment"] = bootstrap["authority"]["assessment"]
    decisions = load_decisions(getattr(args, "decisions", None))
    specialties = load_requirements(getattr(args, "requirements", None),
        roles=args.roles.split(",") if legacy is not None else None,
        task=getattr(args, "task", None))
    roles = legacy_roles if legacy_roles is not None else [role for role in (getattr(args, "roles", None) or "").split(",") if role]
    # `base...head` diffs from the merge base, so "absent from the base" is
    # read at that same commit rather than at the branch point's namesake.
    left = run(["merge-base", args.base, head]).strip() if head else args.base
    span = [args.base + "..." + head] if head else [args.base]
    common = ["diff", "--no-renames", *span]
    changes = parse_name_status(run([*common, "--name-status", "-z"]))
    churn = parse_numstat(run([*common, "--numstat", "-z"]))
    untracked = {}
    # Untracked files are a working tree's added surface. A round that writes
    # nothing has no surface for them to belong to, so they are never read:
    # an unreadable or oversized scratch file in the shared checkout must not
    # refuse an investigation it plays no part in (#499).
    if not head and writes:
        collect_untracked(run, args.repo, changes, churn, untracked)
    planned_lines = {}
    if plan is not None:
        for path in plan["added"]:
            changes.setdefault(path, "A")
        for path in plan["changed"]:
            changes.setdefault(path, "M")
        planned_lines = plan["package_lines"]
        for name in planned_lines:
            if not package_matches(name, declaration["package_roots"]):
                raise UsageError("Planned package_lines names {}, which is not one of this repo's declared package roots; name a package root or widen the declaration.".format(name), {})
        for path in plan["cli_surface"]:
            if not matches(path, declaration["cli_spec_paths"]):
                raise UsageError("Planned cli_surface names {}, which is outside this repo's declared CLI spec paths; name a spec path or widen the declaration.".format(path), {})
            # A planned CLI surface is a planned change to that file, so it is
            # classified against every surface the declaration names. A spec
            # path that is also a trust boundary fires security too; validating
            # it and classifying nothing let UX and product answer for both.
            changes.setdefault(path, "M")
    if not writes and legacy is None:
        # An investigation touches no repository surface, so it has nothing to
        # declare and every trigger is quiet by construction. The claim is
        # checkable rather than asserted: the seats are the read-only ones, and
        # a tracked diff on the tree contradicts it (#471). Untracked files were
        # never collected, so `changes` holds the tracked diff alone.
        tracked = sorted(changes)
        if tracked:
            raise UsageError("This round declares writes_repository false, but its tracked diff is not empty: {}. Evidence outranks intent -- declare the surfaces the work touches, or classify the round that produced them.".format(
                ", ".join(tracked[:5])), {})
        if not roles:
            raise UsageError("A round declaring writes_repository false names the responsibilities it seats with --roles, so the read-only claim is checkable.", {})
        if not set(roles) <= READ_ONLY_ROLES:
            raise UsageError("Only {} write no repository content; this round seats {} and cannot declare writes_repository false.".format(
                ", ".join(sorted(READ_ONLY_ROLES)), ", ".join(sorted(set(roles) - READ_ONLY_ROLES))), {})
        return report(declaration, args.base, head or "worktree", {}, roles, specialties, decisions)
    # An empty plan classifies exactly as much as an absent one, so the guard
    # reads the combined inputs rather than the plan's presence: a vacuous
    # success here is the silence the triggers exist to end (#415).
    elif writes and not changes and not planned_lines and not (plan is not None and plan["cli_surface"]):
        raise UsageError("This round classifies nothing: its diff is empty and no planned surface is declared. Name the paths, package sizes or CLI surfaces the work will touch in --planned before the developer is dispatched. A round that writes no repository content declares writes_repository false instead.", {})
    # A package declared only by its planned size has no path in `changes`, and
    # a candidate missing here reads as one the base already held -- so a
    # planned new package would fire nothing (#415).
    candidates = sorted({package for package in
                         (package_of(path, declaration["package_roots"]) for path in changes)
                         if package is not None} | set(planned_lines))
    base_packages = {name: bool(run(["ls-tree", "--name-only", left, "--", name + "/"]).strip())
                     for name in candidates}
    fired = detect(declaration, changes, churn, base_packages, planned_lines)
    spec_paths = sorted(path for path in changes if matches(path, declaration["cli_spec_paths"]))
    tracked_specs = [path for path in spec_paths if path not in untracked]
    if spec_paths and declaration["cli_surface_markers"]:
        added = dict(untracked)
        for path in tracked_specs:
            added[path] = parse_added_lines(run([*common, "--unified=0", "--", path]))
        surface = cli_surface(declaration, changes, added)
        if surface:
            fired["ux-product"] = surface
    if plan is not None and plan["cli_surface"]:
        fired.setdefault("ux-product", []).extend(
            {"signal": "planned_cli_surface", "evidence": path} for path in plan["cli_surface"])
    payload, failure = report(declaration, args.base, head or "worktree", fired, roles, specialties, decisions)
    if legacy is not None:
        payload["task"] = args.task
        if failure is not None:
            failure["message"] = "Legacy review triggers {} are unstaffed; add the required role or requirements specialty and rerun detection. --decisions cannot answer a legacy trigger.".format(
                ", ".join(payload["unaddressed"]))
    return payload, failure


def register_command(sub, common):
    parser = sub.add_parser(
        "detect-triggers", parents=[common],
        help="Classify a task's diff against the repo's declared Team Composition trigger surfaces.")
    parser.add_argument("--repo", required=True, metavar="DIR",
                        help="Repository holding " + DECLARATION_FILE + " and the commits to compare.")
    parser.add_argument("--base", required=True, metavar="REF", help="The task's recorded base revision.")
    parser.add_argument("--head", metavar="REF", help="Pushed head; omit to read the working tree.")
    parser.add_argument("--task", help="Exact consultation task identity; required for legacy review.")
    parser.add_argument("--roles", metavar="ROLE[,ROLE...]", help="Roles this round plans, as given to `plan`.")
    parser.add_argument("--requirements", metavar="FILE", help="The requirements file this round gives `plan`.")
    parser.add_argument("--decisions", metavar="FILE", help="Recorded staffing decisions for fired triggers.")
    parser.add_argument("--planned", metavar="FILE",
                        help="Surfaces this round will touch, for a pre-implementation round with no diff yet.")
    parser.add_argument("--bootstrap-declaration", metavar="FILE",
                        help="Reviewed first trigger declaration; accepted only when the recorded base lacks one and --planned binds its exact installation.")
    parser.add_argument("--legacy-review-declaration", metavar="FILE",
                        help="Accepted external declaration for one unchanged legacy PR; requires exact task/full base/head, no-write plan and read-only seats. No bootstrap, decisions or release authority.")
