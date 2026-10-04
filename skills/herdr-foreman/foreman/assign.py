"""Hand a role and its brief to an agent.

The hard rule this module enforces: foreman never types into an agent that is
`working` or `blocked`. Statuses are checked for *every* target before the
first keystroke is sent, so a run that is going to be refused sends nothing at
all rather than half the assignments.

`working` from herdr is confirmed against the pane first (see
foreman/probe.py) -- herdr's title-derived state goes stale and would
otherwise refuse a genuinely idle agent forever. `blocked` is never probed.

The clear command (`/clear`, `/new`) goes out by whichever mechanism the
agent's config names -- see SLASH_DELIVERIES in foreman/herdr.py, because the
TUIs disagree about what a pasted slash command means. The assignment itself
IS a message, so it always goes through `agent prompt`.

Both are gated on the composer being empty (see foreman/composer.py). Live,
an unsent `/new` sat in Codex's composer and the assignment was pasted onto
the end of it, so Codex received `/newNew assignment from the team lead...`
and rejected it. The assignment is never sent to an agent whose composer still
holds text.

Sending it is not the end either. A leftover `/` turned an assignment into
`/New assignment ...`, Claude Code answered "Args from unknown skill", and no
turn ever started -- while foreman reported the round applied. So each
hand-off is confirmed: the transcript must show the message, the runtime must
not have read it as a command, and the agent must leave idle. Anything less is
reported as `sent_but_not_started` rather than as success.

`--dry-run` builds the same argv lists the live path would execute (the
builders live on the transport) and prints them without running anything.
"""

import errno
import os
import stat
import sys
import time
import hashlib
from pathlib import Path
from functools import partial

from . import report_contract
from . import runnable
from .composer import (
    COMPOSER_SETTLE_SEC,
    DEFAULT_START_TIMEOUT_MS,
    LANDING_ATTEMPTS,
    DispatchSession,
    send_command,
    send_message,
)
from .errors import AgentBusyError, HerdrError, UsageError
from .herdr import (
    READY_STATES,
    BUSY_STATES,
    DEFAULT_SETTLE_TIMEOUT_MS,
    SLASH_DELIVERY_TYPE,
    format_argv,
)
from .composer import COMPOSER_READ_LINES, COMPOSER_READ_SOURCE, checkable
from .probe import PROBE_READ_LINES, PROBE_READ_SOURCE, resolve_status, stderr_warn
from .chronology import latest_assignment
from .recovery import JUDGE_MODES, briefing_bytes, empty_recovery, fresh_transition, task_record, validate_work
from .launch import foreground_agent, restart_worker, verify_running, verify_running_permissions
from .tiers import EFFORT_RANK, canonical_role, launch_flags, require_seatable, still_de_escalated, worker_launch_args
from .report_delivery import marker_columns
from .composition import normalize_requirement, parse_requirements, seat_holds

# Version 3 adds verified model-tier metadata to context and task/fix evidence.
APPLY_SCHEMA_VERSION = 8

RETAIN_CONTEXT_ROUNDS = frozenset({1, 2, 3})
CONSULTATION_ROLES = frozenset({"advisor", "investigator", "architect"})

# Some native clients publish SessionStart only after their first prompt. The
# post-dispatch handshake polls the official integration, never pane labels.
SESSION_CORRELATION_READS = 3

#: States foreman will type into. Anything else is refused, always.
SETTLE_STATES = ("idle", "done")

#: Stand-in for a pane id in `--dry-run`, which resolves no pane because it
#: makes no herdr calls.
PANE_ID_PLACEHOLDER = "PANE-ID-RESOLVED-AT-RUN-TIME"

#: The opening words looked for in the transcript to confirm the assignment
#: landed as a user message. Short enough to survive the runtime re-wrapping
#: it across rows.
ASSIGNMENT_OPENING = "New assignment from the team lead."

ASSIGNMENT_TEMPLATE = (
    "New assignment from the team lead. Your role for this task is {role}. "
    "Read {common} in full, then read {brief} in full, and execute that brief "
    "exactly. Finish with the REPORT line it specifies."
)


def assignment_text(role, common_path, brief_path):
    """The exact prompt sent to an agent. Pure, so tests pin it byte for byte."""
    return ASSIGNMENT_TEMPLATE.format(
        role=role.upper(), common=common_path, brief=brief_path
    )


def tiered_prompt(text, tier, common, brief, contents=None):
    """Hash length-framed dispatch inputs, excluding this metadata footer.

    `contents` is `briefing_bytes`'s map of verified frozen bytes (#565).
    """
    digest = hashlib.sha256()
    parts = [text.encode("utf-8")]
    for path in (common, brief):
        try:
            parts.append(briefing_bytes(path, contents))
        except OSError as exc:
            raise UsageError("Cannot read briefing file {}: {}. Restore readability or correct its --common/--brief path before dispatch.".format(
                path, exc.strerror or str(exc)), {"path": str(path)}) from None
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    prompt_hash = digest.hexdigest()
    return text + " Launch metadata: model={}, effort={}, prompt_hash={}. Include the tier metadata required by COMMON.md in your report.".format(
        tier["model"], tier.get("effort") or "none", prompt_hash), prompt_hash


def normalize_assignments(payload):
    """Accept either `plan` output or a bare `{role: agent}` mapping.

    Version-agnostic on purpose: this reads `assignments` alone, which every
    plan version carries in the same shape. A version-1 plan has no `judge`
    object; so does a version-2 plan whose round assigned no judge seat, and
    both take the same path here (rules/stateful-artifacts.md Cross-Pipeline
    Schema Bumps -- an additive bump a reader absorbs through absence).
    """
    if isinstance(payload, dict) and isinstance(payload.get("assignments"), dict):
        payload = payload["assignments"]
    if not isinstance(payload, dict) or not payload:
        raise UsageError(
            "--assignments needs a JSON object mapping roles to agent names, or "
            "the output of `{plan}` (which nests one under "
            "\"assignments\"). Got: {}.".format(type(payload).__name__, plan=runnable.command("plan")),
            {},
        )
    for role, agent in payload.items():
        if not isinstance(agent, str) or not agent:
            raise UsageError(
                "--assignments maps role {!r} to {!r}; each role must map to an "
                "agent name string.".format(role, agent),
                {"role": role},
            )
        require_seatable(role)

    reject_duplicate_agents(payload)
    return dict(payload)


def reject_duplicate_agents(assignments):
    """Refuse an agent that appears in more than one role.

    Briefing the same pane twice in a round means the second brief overwrites
    the first, so the earlier role is simply not being done -- silently, since
    both hand-offs report success. Checked before any herdr call, and checked
    again inside `apply` in case the mapping arrived some other way.
    """
    roles_by_agent = {}
    for role, agent in assignments.items():
        roles_by_agent.setdefault(agent, []).append(role)
    doubled = {
        agent: roles for agent, roles in roles_by_agent.items() if len(roles) > 1
    }
    if not doubled:
        return
    raise UsageError(
        "One agent is assigned several roles: {}. Each brief would overwrite "
        "the last in the same pane, so the earlier roles would go undone. "
        "Assign one agent per role.".format(
            "; ".join(
                "{} -> {}".format(agent, ", ".join(roles))
                for agent, roles in sorted(doubled.items())
            )
        ),
        {"doubled": {agent: sorted(roles) for agent, roles in doubled.items()}},
    )


#: Where a dispatched brief's checked bytes are frozen, beside the source.
FROZEN_DIR = ".dispatched"


def _directory_search_flag():
    """The open flag that reaches a directory for lookups alone, needing search permission, not read.

    A path walk by name needs only search (x) on each ancestor; opening each
    one `O_RDONLY` would refuse an execute-only ancestor that the kernel's own
    lookup passes (#562 review). Python exposes `O_PATH` on Linux and no name
    for Darwin's `O_SEARCH`, whose `O_EXEC` bit is `sys/fcntl.h`'s 0x40000000.
    `O_RDONLY` is the fallback where neither exists.
    """
    for name in ("O_SEARCH", "O_PATH"):
        if hasattr(os, name):
            return getattr(os, name)
    if sys.platform == "darwin":
        return 0x40000000
    return os.O_RDONLY


_SEARCH = _directory_search_flag()


def freeze_decision(assignments, is_replay):
    """`source` when every assigned role replays a dispatch recorded under these source paths, else `frozen`.

    `is_replay(role, agent)` answers whether the source paths resolve to an
    APPLIED recorded dispatch: the same id and fingerprint (task, role, agent,
    fix round, correction plan, work, round options and brief bytes), or an
    older form of it the ledger still carries. Such a replay returns its saved
    receipt and sends nothing, so the source paths never reach a worker.
    Anything that would send -- a new dispatch, or a retry of a row never
    sent -- freezes, and so does anything short of the complete identity. A
    batch mixing replays with new roles is refused, so no new dispatch escapes
    the freeze (#460).
    """
    replays = {role for role, name in assignments.items() if is_replay(role, name)}
    if not replays:
        return "frozen"
    if replays != set(assignments):
        raise UsageError("Roles {} replay dispatches recorded under their source briefs, and {} are new. Apply them in "
                         "separate calls: a replay keeps its recorded paths, and a new dispatch reads frozen copies."
                         .format(", ".join(sorted(replays)), ", ".join(sorted(set(assignments) - replays))), {})
    return "source"


class FrozenPaths(dict):
    """Frozen copy paths by key, with `contents`: each copy's path mapped to the bytes its freeze verified.

    A consumer reading the copy again by name would follow an ancestor swapped
    for a link after the freeze, so the dispatch's identity and its sent
    prompt could be computed from other bytes than the ones verified (#565).
    Consumers take `contents` through `recovery.briefing_bytes` instead.
    """

    def __init__(self, paths, contents):
        super().__init__(paths)
        self.contents = contents


def freeze_paths(paths):
    """Copy each brief, and the common brief, to a content-addressed file nothing rewrites.

    Preflight checks read a brief, and the worker reads it again minutes after
    send. A source edited in between would reach the worker unchecked (#460).
    A new dispatch uses the frozen copies as its paths throughout: its checks,
    its identity, the prompt it sends and the recovery that later rebuilds
    that prompt all read the same bytes. The name carries the content's
    sha256, so the same brief freezes to the same file and a retry is unchanged.
    The copy sits under the source directory's canonical path: `read_frozen`
    refuses a link anywhere on the way, so an alias could never be read back (#554).
    Returns a `FrozenPaths` carrying the verified bytes to every later reader (#565).
    """
    frozen, contents = {}, {}
    for key, source in paths.items():
        # Canonical first, then one descriptor for that directory, held
        # through both the read and the write: a path resolved twice would let
        # an alias retargeted, or the directory replaced, in between place one
        # directory's bytes under another's `.dispatched` (#554).
        directory = Path(os.path.realpath(Path(source).parent))
        canonical = directory / Path(source).name
        held = _open_directory_unlinked(canonical, _SOURCE_OPEN_FAILED, _SOURCE_LINKED, brief=source)
        try:
            data = _read_source(held, canonical, source)
            digest = hashlib.sha256(data).hexdigest()
            target = directory / FROZEN_DIR / "{}.{}{}".format(Path(source).stem, digest[:16], Path(source).suffix)
            _write_frozen(held, target, data, source)
        finally:
            _release(held, directory)
        # Inspected apart from the write's handlers: an error raised inside a
        # handler never reaches a sibling handler, so it would escape as a
        # traceback (#460). A fresh copy is read back too, through the same
        # link-refusing walk the gate uses (#554).
        _require_frozen_copy(target, digest)
        frozen[key] = str(target)
        contents[str(target)] = data
    return FrozenPaths(frozen, contents)


def _close(fd):
    """Close `fd`, returning the failure's text instead of raising it, or None.

    POSIX releases the descriptor even when close reports an error, so the
    caller never closes it again. Returning keeps a close inside `finally`
    from replacing a failure already on its way out.
    """
    try:
        os.close(fd)
    except OSError as exc:
        return exc.strerror or str(exc)
    return None


def _release(fd, path):
    """Close a read-only or directory descriptor, warning on stderr when the close fails.

    Nothing was written through such a descriptor, so a failed close loses
    nothing and the operation it served stands; raising would turn that into
    a traceback, or bury the error the caller is already reporting.
    """
    failure = _close(fd)
    if failure is not None:
        stderr_warn("Could not close the descriptor for {}: {}. Nothing was written through it; if this repeats, "
                    "check the filesystem holding it.".format(path, failure))


def _write_frozen(held, target, data, source):
    """Create `target` exclusively under `held`, the source directory's descriptor, without following a link.

    An existing `target` is left untouched for `_require_frozen_copy` to judge.
    Creating by pathname would follow an ancestor swapped for a link after the
    canonical lookup, and write the brief into another source's directory
    before any read-back could refuse it (#554). A `FROZEN_DIR` that is a link
    would place copies somewhere nothing keeps immutable (#460).
    """
    directory = target.parent
    try:
        try:
            os.mkdir(FROZEN_DIR, dir_fd=held)
        except FileExistsError:
            pass
        frozen_dir = os.open(FROZEN_DIR, _SEARCH | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=held)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise UsageError("Frozen-brief directory {} is a link or not a directory; move it aside and re-run so "
                             "the freeze writes real copies beside the source.".format(directory),
                             {"path": str(directory)}) from None
        raise UsageError("Cannot create {} beside brief {}: {}. Make its directory writable and re-run.".format(
            directory, source, exc.strerror or str(exc)), {"path": str(directory)}) from None
    try:
        _create_exclusive(frozen_dir, target, data, source)
    finally:
        _release(frozen_dir, directory)


def _create_exclusive(frozen_dir, target, data, source):
    """Write `data` to a new `target.name` under `frozen_dir`, removing it again if the write does not complete.

    A partial copy left at the content-addressed name would be refused as
    never-rewritten on every retry. A failed `close` counts as a failed
    write: it is where a delayed ENOSPC, EDQUOT or EIO surfaces.
    """
    failed = "Cannot freeze brief {} at {}: {}. Make its directory writable and re-run."
    try:
        fd = os.open(target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o666, dir_fd=frozen_dir)
    except FileExistsError:
        return
    except OSError as exc:
        raise UsageError(failed.format(source, target, exc.strerror or str(exc)), {"path": str(target)}) from None
    reason = None
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
    except OSError as exc:
        reason = exc.strerror or str(exc)
    finally:
        closing = _close(fd)
    reason = reason or closing
    if reason is None:
        return
    try:
        os.unlink(target.name, dir_fd=frozen_dir)
    except OSError as exc:
        raise UsageError(("Cannot freeze brief {} at {}: {}, and the partial copy could not be removed ({}). Delete "
                          "it, make its directory writable and re-run.").format(
                              source, target, reason, exc.strerror or str(exc)), {"path": str(target)}) from None
    raise UsageError(failed.format(source, target, reason), {"path": str(target)})


_FROZEN_OPEN_FAILED = "Cannot open frozen brief {0}: {1}. Restore its readability or move it aside and re-run."
_FROZEN_LINKED = ("Frozen brief {0} passes through {1}, which is a link or not a directory. Dispatch again with this "
                  "build, which freezes under the brief's canonical directory.")


def _open_directory_unlinked(target, open_failed=_FROZEN_OPEN_FAILED, linked=_FROZEN_LINKED, brief=None):
    """A descriptor for absolute `target`'s directory, reached without following a link.

    Each component is opened relative to the one before it, refusing a
    symlink. A symlinked ancestor would let one recorded path read another
    source's frozen copy once the link is retargeted (#554); the lexical
    checks in `read_frozen` cannot see that, and a resolve-then-open would
    race the retarget. `open_failed` and `linked` are the refusal texts,
    formatted positionally with the target, then the failure or the offending
    component, then `brief`; paths are arguments, never template text.
    """
    parts = Path(target).parent.parts
    try:
        fd = os.open(parts[0], _SEARCH | os.O_DIRECTORY)
    except OSError as exc:
        raise UsageError(open_failed.format(target, exc.strerror or str(exc), brief), {"path": str(target)}) from None
    try:
        for index, name in enumerate(parts[1:], 2):
            try:
                child = os.open(name, _SEARCH | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise UsageError(linked.format(target, Path(*parts[:index]), brief),
                                     {"path": str(target)}) from None
                raise UsageError(open_failed.format(target, exc.strerror or str(exc), brief),
                                 {"path": str(target)}) from None
            _release(fd, Path(*parts[:index - 1]))
            fd = child
        opened, fd = fd, None
        return opened
    finally:
        if fd is not None:
            _release(fd, target)


_SOURCE_OPEN_FAILED = ("Cannot read briefing file {2} to freeze it for dispatch ({0}): {1}. Restore readability "
                       "or correct its --common/--brief path before dispatch.")
_SOURCE_LINKED = ("Briefing file {2} ({0}) passes through {1}, which became a link or not a directory while it was "
                  "frozen. Stop whatever is moving its directory and dispatch again.")


def _read_source(held, canonical, given):
    """The bytes of the brief named `canonical.name` under `held`, its directory's descriptor.

    The brief file itself may be a link: its bytes are what the copy freezes.
    Non-blocking, so a FIFO planted as a brief is refused rather than hung on.
    """
    try:
        fd = os.open(canonical.name, os.O_RDONLY | os.O_NONBLOCK, dir_fd=held)
    except OSError as exc:
        raise UsageError(_SOURCE_OPEN_FAILED.format(canonical, exc.strerror or str(exc), given),
                         {"path": str(given)}) from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise UsageError(_SOURCE_OPEN_FAILED.format(canonical, "not a regular file", given), {"path": str(given)})
        chunks = []
        chunk = os.read(fd, 1 << 16)
        while chunk:
            chunks.append(chunk)
            chunk = os.read(fd, 1 << 16)
    except OSError as exc:
        raise UsageError(_SOURCE_OPEN_FAILED.format(canonical, exc.strerror or str(exc), given),
                         {"path": str(given)}) from None
    finally:
        _release(fd, canonical)
    return b"".join(chunks)


def _read_unlinked_regular(target):
    """The bytes of `target`, refused unless it is an unlinked regular file.

    A symlink could point back at a mutable file and a hard link shares its
    inode, so rewriting that file rewrites either. The checks and the read go
    through one descriptor opened without following a link, and non-blocking
    so a FIFO planted there is refused rather than hung on. `target` is
    absolute; its directory is reached by `_open_directory_unlinked`.
    """
    target = Path(target)
    parent = _open_directory_unlinked(target)
    try:
        fd = os.open(target.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise UsageError("Frozen brief {} is a link or not a regular file; move it aside and re-run so the "
                             "freeze writes a real copy.".format(target), {"path": str(target)}) from None
        raise UsageError("Cannot open frozen brief {}: {}. Restore its readability or move it aside and re-run."
                         .format(target, exc.strerror or str(exc)), {"path": str(target)}) from None
    finally:
        _release(parent, target.parent)
    try:
        # On the raw descriptor, before any file object: wrapping a directory
        # fails first and would hide that it is not a regular file.
        status = os.fstat(fd)
        regular = stat.S_ISREG(status.st_mode) and status.st_nlink == 1
        chunks = []
        while regular:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            chunks.append(chunk)
    except OSError as exc:
        raise UsageError("Cannot read frozen brief {}: {}. Restore its readability or move it aside and re-run."
                         .format(target, exc.strerror or str(exc)), {"path": str(target)}) from None
    finally:
        _release(fd, target)
    if not regular:
        raise UsageError("Frozen brief {} is a link or not a regular file; move it aside and re-run so the "
                         "freeze writes a real copy.".format(target), {"path": str(target)})
    return b"".join(chunks)


def _require_frozen_copy(target, digest):
    """Accept an existing frozen brief only when it is an unlinked regular file holding `digest`."""
    if hashlib.sha256(_read_unlinked_regular(target)).hexdigest() != digest:
        raise UsageError("Frozen brief {} exists with other content; it is never rewritten. Move it aside "
                         "and re-run.".format(target), {"path": str(target)})


def read_frozen(path):
    """The bytes a dispatch recorded at `path`, refused unless they are an intact frozen copy.

    Intact: an absolute path with no `..` component and no symlinked
    component, directly under `FROZEN_DIR`, an unlinked regular file, and
    holding the content its name's digest names, so the bytes read are the
    bytes sent. A `..` would let a lexical `.dispatched` parent name a file in
    some other directory (#534), and so would a retargeted ancestor link (#554).
    """
    target = Path(path)
    anchored = target.is_absolute() and ".." not in target.parts and target.parent.name == FROZEN_DIR
    data = _read_unlinked_regular(target) if anchored else None
    if data is None or hashlib.sha256(data).hexdigest()[:16] not in target.name.split("."):
        raise UsageError("Dispatched brief {} is not an intact frozen copy, so what the worker read cannot be "
                         "shown. Dispatch again with this build.".format(target), {"path": str(target)})
    return data


def resolve_paths(assignments, briefs, common):
    """Turn the brief/common inputs into absolute paths, or explain what is missing."""
    missing_briefs = [role for role in assignments if role not in briefs]
    if missing_briefs:
        raise UsageError(
            "No --brief given for role {} - pass --brief {}=/path/to/brief.md "
            "for every role in --assignments.".format(
                ", ".join(sorted(missing_briefs)), sorted(missing_briefs)[0]
            ),
            {"roles": sorted(missing_briefs)},
        )

    resolved = {"common": os.path.abspath(common)}
    unreadable = [] if os.path.isfile(resolved["common"]) else [resolved["common"]]
    for role in assignments:
        path = os.path.abspath(briefs[role])
        resolved[role] = path
        if not os.path.isfile(path):
            unreadable.append(path)
    if unreadable:
        raise UsageError(
            "These brief files do not exist: {} - create them, or fix the "
            "--common / --brief paths. The agents are told to read these "
            "paths, so a missing file wastes a whole assignment round.".format(
                ", ".join(unreadable)
            ),
            {"missing": unreadable},
        )
    return resolved


def pane_label(role, task=None, model=""):
    """`<role> · <model> #<task>`, dropping whichever parts are absent.

    Role first, and the agent's name is deliberately NOT in it: the workspace
    row already carries the name, so repeating it in the pane row spends the
    sidebar's width saying the same thing twice. The separator is a middle dot
    because roles and model names both carry hyphens.

    Pure, so the shape is testable without a herdr session.
    """
    label = str(role)
    if model:
        label = "{} · {}".format(label, model)
    if task:
        marker = str(task) if str(task).startswith("#") else "#{}".format(task)
        label = "{} {}".format(label, marker)
    return label


def validate_agents(assignments, agents_by_name):
    """Refuse an assignment naming an unknown agent, or one used twice."""
    reject_duplicate_agents(assignments)
    for role, name in assignments.items():
        if name not in agents_by_name:
            raise UsageError(
                "Assignment for role {!r} names agent {!r}, which is not in the "
                "config - configured agents are {}.".format(
                    role, name, ", ".join(sorted(agents_by_name))
                ),
                {"role": role, "agent": name},
            )


def refuse_reserved(assignments, task, reserved):
    """Refuse a developer reserved to another task, read at dispatch time.

    `reserved` is `{agent: task}` from `recovery.developer_reservations`. A
    plan's holds can be stale by dispatch time, so the send re-reads them
    (#483).
    """
    held = seat_holds(list(assignments), task, reserved or {}, {})
    for role, name in assignments.items():
        if name in held["exclude"].get(role, []):
            raise UsageError("Assigned worker {} is reserved as developer for {}. Replan, or close that task with `{}` before reusing its developer.".format(
                name, reserved[name], runnable.command("close-task")), {"agent": name, "task": reserved[name]})


def validate_context_mode(assignments, no_clear, retain_context, task, fix_round, *, recovery=None, history=None, plan_id=None, work=None, retain_specialist=False, requirements=None, assignment_scoped=False, fresh=False):
    """Validate the explicit context choice before any herdr operation."""
    parse_requirements(
        {"schema_version": 1, "assignments": requirements} if requirements else None,
        list(assignments), task,
    )
    if no_clear and retain_context:
        raise UsageError("Choose --no-clear or --retain-context, never both.", {})
    if retain_specialist:
        if no_clear or retain_context:
            raise UsageError("Choose --retain-specialist alone; omit --no-clear and --retain-context.", {})
        if len(assignments) != 1 or not set(assignments) <= CONSULTATION_ROLES:
            raise UsageError("--retain-specialist requires one advisor, investigator or architect assignment; use the normal fresh role or developer fix path for other work.", {})
        if fix_round is not None or plan_id is not None or work is not None:
            raise UsageError("A retained consultation cannot carry correction parameters. Dispatch implementation through the original developer task and fix allowance.", {})
        if not isinstance(task, str) or not task.strip():
            raise UsageError("Pass the original --task with --retain-specialist; a warm session cannot establish its task identity.", {})
        role = next(iter(assignments))
        requirement = (requirements or {}).get(role)
        if normalize_requirement(requirement, role) != requirement:
            raise UsageError("Retained consultation requires its normalized specialty, required_capabilities, independent and engagement requirements; restore the original engagement before dispatch.", {})
    if task is not None and (not isinstance(task, str) or not task.strip()):
        raise UsageError("Pass a non-empty --task label, or omit it.", {})
    if isinstance(task, str) and task != task.strip():
        raise UsageError(
            "New --task labels must have no leading or trailing whitespace. "
            "A padded legacy identity requires an explicit operator recovery decision; "
            "do not retry the padded label, trim or merge history, or reset its fix count.", {}
        )
    if fix_round is not None and (
        isinstance(fix_round, bool) or not isinstance(fix_round, int)
        or fix_round < 1
    ):
        raise UsageError(
            # The number is the task's CUMULATIVE attempt, and the allowance
            # it has to fit inside belongs to the current approach, which
            # `validate_work` reads (#462). A fixed upper bound here named a
            # cap that stopped being the whole rule once a bounded plan or an
            # approved new direction could raise it.
            "A fix round is a positive integer naming this task's cumulative attempt; the allowance it spends is checked against the current approach.", {}
        )
    if fix_round is not None and (not isinstance(task, str) or not task.strip()):
        raise UsageError("Pass --task with --fix-round to identify the task.", {})
    store = recovery if recovery is not None else empty_recovery()
    validate_work(store, history or [], task, fix_round, plan_id, work, implementation="developer" in assignments)
    if assignment_scoped and fresh and "developer" in assignments and fix_round is not None:
        task_record(store, task)
    transition = fresh_transition(store, history or [], task, fix_round)
    if retain_context and (
        set(assignments) != {"developer"} or fix_round not in RETAIN_CONTEXT_ROUNDS
    ):
        raise UsageError(
            "--retain-context requires one developer assignment and --fix-round 1, 2 or 3.", {}
        )
    if ("developer" in assignments and fix_round in RETAIN_CONTEXT_ROUNDS and not retain_context
            and not (assignment_scoped and fresh)):
        if transition is None:
            raise UsageError("Early developer fixes require --retain-context or a recorded fresh handoff. Use `{}` for a verified automatic role clear, or follow dispatch-recovery.md for other causes; never reset the task.".format(runnable.command("recover-role-clear")), {})
        task_record(store, task)
        if no_clear:
            raise UsageError("A replacement developer session requires an automatic clear; omit --no-clear.", {})
    if no_clear and fix_round is not None and fix_round not in RETAIN_CONTEXT_ROUNDS:
        raise UsageError("Fresh fix rounds require an automatic clear; omit --no-clear.", {})
    return transition if not retain_context and "developer" in assignments and not (assignment_scoped and fresh) else None


def validate_fix_history(assignments, history, task, fix_round):
    """A worker change cannot reset or skip the task's confirmed fix count."""
    if "developer" not in assignments or task is None:
        return
    prior = [row for row in (history or []) if row.get("task") == task
             and row.get("role") == "developer" and row.get("status") == "applied"]
    completed = max((row.get("fix_round") or 0 for row in prior), default=0)
    if fix_round is None:
        if prior:
            raise UsageError("Task {!r} already has a developer assignment; do not reset its counter. Use its next fix or replay the original dispatch identity.".format(task), {})
        return
    if not prior or fix_round != completed + 1:
        raise UsageError(
            "Task {!r} requires its preceding confirmed developer assignment and next "
            "fix number {}; do not skip or reset the counter.".format(task, completed + 1), {}
        )


def retained_tier(agent, wanted, previous_tier):
    """The tier a retained fix round runs at: the planned row at the kept effort.

    A retained session never switches model or raises effort. It keeps the
    preceding round's verified effort, and with it that round's cost fields.
    `de_escalated` then reports the running tier, never the plan: a kept effort
    can reach what the plan's declined escalation wanted (#591).
    """
    if (not isinstance(previous_tier, dict) or not previous_tier.get("verified")
            or previous_tier.get("model") != wanted["model"]
            or EFFORT_RANK.get(previous_tier.get("effort"), -1) < EFFORT_RANK.get(wanted.get("effort"), 0)):
        raise UsageError("Retained context cannot switch model or raise effort. Recover the task through an explicit fresh-round decision without resetting its counter.", {})
    tier = {**wanted, "effort": previous_tier.get("effort")}
    if previous_tier.get("effort") != wanted.get("effort"):
        for key, default in (("multiplier", 1.0), ("effective_multiplier", 1.0), ("billing_window", "unknown")):
            tier[key] = previous_tier.get(key, default)
        if "de_escalated" in wanted:
            tier["de_escalated"] = still_de_escalated(agent, wanted, previous_tier.get("effort"))
    return tier


def validate_retained_history(assignments, history, task, fix_round):
    """Only the same agent's last confirmed task/role can retain context.

    The ledger is a necessary history check, never proof of a live pane:
    check_all_ready and composer checks still run before sending the brief.
    """
    name = assignments["developer"]
    latest = latest_assignment(history or [], agent=name)
    prior = latest[1] if latest is not None else None
    if prior is None or (
        prior.get("role") != "developer" or prior.get("task") != task
        or prior.get("status") != "applied"
        or (prior.get("fix_round") or 0) + 1 != fix_round
    ):
        raise UsageError(
            "Cannot retain {}'s context: the ledger must show its preceding confirmed "
            "developer round for task {!r}. Report lost context; do not reset the "
            "task's fix counter.".format(name, task), {}
        )
    return prior


def validate_specialist_history(assignments, history, task, requirements, tiers):
    """Retain only the latest unchanged consultation with recorded tier proof."""
    role, name = next(iter(assignments.items()))
    latest = latest_assignment(history or [], agent=name)
    prior = latest[1] if latest is not None else None
    if (prior is None or prior.get("status") != "applied"
            or prior.get("role") != role or prior.get("task") != task
            or prior.get("requirements") != requirements[role]
            or prior.get("fix_round") is not None):
        raise UsageError("Cannot retain {}: its latest confirmed assignment must preserve the same task, consultation role and engagement requirements. Use a fresh brief with retrospective coverage for a changed engagement.".format(name), {"agent": name, "role": role, "task": task})
    previous_tier = prior.get("tier")
    wanted = (tiers or {}).get(role)
    if (not isinstance(previous_tier, dict) or not isinstance(previous_tier.get("verified"), dict)
            or not isinstance(wanted, dict)
            or any(previous_tier.get(key) != wanted.get(key) for key in ("kind", "model", "effort"))):
        raise UsageError("Cannot retain {}: the consultation needs its recorded verified model and effort unchanged. Use the fresh assignment and retrospective path to establish or change its tier.".format(name), {"agent": name})
    return prior


def native_context_session(info, kind):
    """Read the official integration's native session reference, never a label.

    Herdr's agent.get contract exposes agent_session {source, agent, kind,
    value}; the integration updates it on native SessionStart, including
    clear/new. A worker name or pane alone is not a conversation identity.
    https://herdr.dev/docs/socket-api/#agent-state-reporting
    """
    ref = info.get("agent_session")
    pane = info.get("pane_id")
    if not isinstance(ref, dict) or not isinstance(pane, str) or not pane:
        return None
    if (ref.get("source") != "herdr:" + kind or ref.get("agent") != kind
            or ref.get("kind") not in ("id", "path")
            or not isinstance(ref.get("value"), str) or not ref["value"].strip()):
        return None
    return {"pane_id": pane, **{key: ref[key] for key in ("source", "agent", "kind", "value")}}


def verify_live_retention(prior, current, name):
    """Refuse an absent or changed native session before any terminal write."""
    if current is None or prior.get("context_session") != current:
        raise HerdrError(
            "Cannot retain {}: live native session continuity is unproven or changed. "
            "Check `herdr integration status` and the worker's session; report lost "
            "context without resetting the fix counter. No brief was sent.".format(name),
            {"agent": name},
        )


def correlate_dispatch_session(client, agent, pane_id, previous, before_prompt, *,
                               cleared, warn, sleep, settle_sec, grok_new=False):
    """Bind a confirmed first prompt to the native session it started.

    A pre-clear identity is stale even when it arrives again after dispatch.
    A different identity from the one observed immediately before the prompt
    cannot prove continuity either. Failure here does not undo a sent prompt.
    """
    if grok_new and previous is None:
        warn("{} was dispatched after a Grok clear without a recorded pre-clear native ID. "
             "Continuity remains unproven and stale-ID recovery is unavailable. Wait for its report; "
             "if delivery remains unconfirmed, record the report as unavailable and notify the operator "
             "of the missing pre-clear evidence. Keep review/release gates unsatisfied; "
             "do not resend or reconstruct evidence."
             .format(agent.name))
        return None
    for attempt in range(SESSION_CORRELATION_READS):
        try:
            current = native_context_session(client.agent_get(agent.name), agent.kind)
        except HerdrError as exc:
            warn("{} was dispatched but its native session could not be correlated: {}. "
                 "Wait for its report; do not send the assignment again.".format(agent.name, exc))
            return None
        if current is not None and current["pane_id"] != pane_id:
            warn("{} changed panes after dispatch; session binding is unavailable. "
                 "Wait for its report and recover the recorded assignment before retaining.".format(agent.name))
            return None
        if current is not None and (not cleared or current != previous):
            if before_prompt is not None and current != before_prompt:
                warn("{} changed native sessions across the dispatch; continuity is unproven. "
                     "Wait for its report and use the recorded recovery path; do not resend.".format(agent.name))
                return None
            return current
        if attempt + 1 < SESSION_CORRELATION_READS:
            sleep(settle_sec)
    warn("{} has no verified post-clear native session reference after dispatch. "
         "The sent assignment remains recorded; wait for its report, check `herdr integration status`, "
         "and recover that assignment before retaining context. Do not resend it.".format(agent.name))
    return None


def build_steps(client, assignments, agents_by_name, paths, panes=None, no_clear=False, settle_timeout_ms=DEFAULT_SETTLE_TIMEOUT_MS, start_timeout_ms=DEFAULT_START_TIMEOUT_MS, track_context=False, tiers=None, requirements=None, contents=None):
    """Build the per-role command plan. Pure with respect to herdr: nothing runs.

    This is what `--dry-run` prints, and what the live path walks. `panes` maps
    agent name to pane id; `--dry-run` has resolved no panes, so its rendering
    carries PANE_ID_PLACEHOLDER where the live path substitutes the real id.
    `contents` carries a frozen dispatch's verified bytes to the prompt hash.
    """
    validate_agents(assignments, agents_by_name)
    panes = panes or {}
    steps = []
    for role, name in assignments.items():
        agent = agents_by_name[name]
        launch_args = worker_launch_args(agent.kind, agent.launch_args)
        pane_id = panes.get(name) or PANE_ID_PLACEHOLDER
        text = assignment_text(role, paths["common"], paths[role])
        composer_reads = (
            [
                client.argv_agent_read(
                    name,
                    source=COMPOSER_READ_SOURCE,
                    lines=COMPOSER_READ_LINES,
                    fmt="ansi",
                )
            ]
            if checkable(agent)
            else []
        )
        commands = [client.argv_agent_get(name)]
        # Runs only when the `agent get` above reports `working`; see
        # foreman/probe.py.
        conditional = [
            (
                client.argv_agent_read(name, source=PROBE_READ_SOURCE, lines=PROBE_READ_LINES),
                "herdr reports the agent as working; confirms it against the "
                "pane footer before refusing",
            )
        ]
        tier = (tiers or {}).get(role)
        prompt_hash = None
        if tier:
            text, prompt_hash = tiered_prompt(text, tier, paths["common"], paths[role], contents)
            commands.extend(composer_reads)
            commands.append(client.argv_pane_process_info(pane_id))
            if not no_clear:
                commands.append(["kill", "-TERM", "VERIFIED-IDLE-FOREGROUND-PID"])
                commands.append(client.argv_pane_process_info(pane_id))
                commands.append(client.argv_agent_start(name, agent.kind, pane_id,
                    launch_args + launch_flags(agent.kind, tier)))
        else:
            # Initial all-target preflight, then this role's own boundary.
            commands.append(client.argv_pane_process_info(pane_id))
            commands.append(client.argv_pane_process_info(pane_id))
            conditional.append((client.argv_pane_process_info(pane_id),
                                "immediately before each recovery keystroke or extra Enter"))
        if not tier and not no_clear:
            commands.extend(composer_reads)
            for command in client.argv_deliver_slash_command(
                agent.slash_delivery, name, pane_id, agent.clear_prompt,
                enter_count=agent.slash_enter_count,
            ):
                commands.append(client.argv_pane_process_info(pane_id))
                commands.append(command)
            commands.extend(composer_reads)
            commands.append(
                client.argv_agent_wait(name, until=SETTLE_STATES, timeout_ms=settle_timeout_ms)
            )
            if agent.recover_keys:
                conditional.append(
                    (
                        client.argv_agent_send_keys(name, agent.recover_keys),
                        "the composer already holds text before dispatch; sent "
                        "exactly once, never twice",
                    )
                )
            conditional.append(
                (
                    client.argv_pane_send_keys(pane_id, ["enter"]),
                    "the clear command is still in the composer after the "
                    "first Enter (Codex's autocomplete popup eats it)",
                )
            )
        if track_context and (role == "developer" or role in CONSULTATION_ROLES and (requirements or {}).get(role) is not None):
            commands.append(client.argv_agent_get(name))
        commands.extend(composer_reads)
        # The assignment is real message text, so pasting it is correct.
        commands.append(client.argv_pane_process_info(pane_id))
        commands.append(client.argv_agent_prompt(name, text))
        # Sending is not starting: confirm it landed as a user message.
        commands.extend(composer_reads)
        commands.append(
            client.argv_agent_wait(name, until=("working",), timeout_ms=start_timeout_ms)
        )
        step = {
                "role": role,
                "agent": name,
                "kind": agent.kind,
                "pane_id": pane_id,
                "brief": paths[role],
                "common": paths["common"],
                "prompt": text,
                "tier": tier,
                "commands": [{"argv": argv, "shell": format_argv(argv)} for argv in commands],
                "conditional_commands": [
                    {"argv": argv, "shell": format_argv(argv), "when": when}
                    for argv, when in conditional
                ],
            }
        if tier:
            step["prompt_hash"] = prompt_hash
        steps.append(step)
    return steps


def brief_markers(brief_path, contents=None):
    """Every bare `REPORT: <path>` line the brief tells its worker to emit.

    `contents` is `briefing_bytes`'s map of verified frozen bytes (#565).
    """
    try:
        text = briefing_bytes(brief_path, contents).decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise UsageError("Cannot read brief {} to measure its REPORT marker: {}. Restore it or correct "
                         "its --brief path before dispatch.".format(brief_path, exc), {"path": str(brief_path)}) from None
    return [line[len("REPORT: "):] for line in text.splitlines() if line.startswith("REPORT: ")]


def refuse_wrapping_markers(client, steps, agents_by_name, reports, contents=None):
    """Refuse, before any input, a report marker the target pane would wrap.

    A wrapped `REPORT: <path>` row cannot be told from two authored rows, so
    delivery could never be confirmed (`report_delivery.decorated_row`). The
    live pane width decides; the brief-time length cap in compose-briefs.sh
    cannot know which pane a worker sits in. The brief's own marker lines are
    what the worker emits, so those are measured, and an expected `--report`
    the brief does not assign is refused rather than measured in their place.
    """
    if reports is None:
        return
    too_narrow, mismatched = {}, {}
    for step in steps:
        markers = brief_markers(step["brief"], contents)
        report = reports.get(step["role"])
        if report is not None and report not in markers:
            mismatched[step["role"]] = {"report": report, "brief": step["brief"]}
            continue
        if not markers:
            continue
        needed = max(marker_columns(agents_by_name[step["agent"]].kind, marker) for marker in markers)
        width = client.pane_width(step["pane_id"])
        if needed > width:
            too_narrow[step["agent"]] = {"role": step["role"], "pane_width": width, "needed": needed}
    if mismatched:
        raise UsageError(
            "The --report path for {} is not the `REPORT: <path>` line its brief assigns - the worker "
            "would emit the brief's marker, not this one. Pass the brief's exact path, or recompose "
            "the brief, then re-run. Nothing was sent.".format(", ".join(sorted(mismatched))),
            {"mismatched": mismatched},
        )
    if too_narrow:
        raise UsageError(
            "The REPORT marker would wrap in {} - the worker could finish and the wait could never "
            "confirm delivery. Widen the pane to the needed columns, or recompose the brief with a "
            "shorter reports directory, then re-run. Nothing was sent.".format(
                ", ".join("{} ({} columns, needs {})".format(name, row["pane_width"], row["needed"])
                          for name, row in sorted(too_narrow.items()))),
            {"too_narrow": too_narrow},
        )


def require_criteria(steps, contents=None):
    """Refuse, before any input, a consultation brief without a contiguous `CRITERION 1..N` block.

    The criteria count a consultation report answers is read from the brief
    this dispatch sends, never from the report (`foreman/report_contract.py`,
    #625). `contents` is `briefing_bytes`'s map of verified frozen bytes.
    """
    for step in steps:
        if canonical_role(step["role"]) not in CONSULTATION_ROLES:
            continue
        try:
            text = briefing_bytes(step["brief"], contents).decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise UsageError("Cannot read brief {} to count its acceptance criteria: {}. Restore it or correct its "
                             "--brief path before dispatch.".format(step["brief"], exc), {"path": str(step["brief"])}) from None
        try:
            report_contract.brief_criteria(text)
        except UsageError as exc:
            raise UsageError("Brief for {} ({}): {}".format(step["role"], step["brief"], exc.message),
                             {**exc.details, "role": step["role"]}) from None


def check_all_ready(client, assignments, agents_by_name, warn=None):
    """Read every target's live status before anything is sent.

    Returns `{agent: {"state": ..., "herdr_state": ..., "state_source": ...}}`.
    Raises AgentBusyError -- having sent nothing -- when any target is
    `working` or `blocked`. There is no override: skills/herdr-foreman/references/team-operation.md
    Dispatch Safety is unconditional, and a keystroke into a working agent
    lands in the middle of somebody's turn.
    """
    validate_agents(assignments, agents_by_name)
    statuses = {}
    for name in assignments.values():
        info = client.agent_get(name)
        herdr_status = info.get("agent_status")
        status, source = resolve_status(client, agents_by_name[name], herdr_status, warn=warn)
        statuses[name] = {
            "state": status,
            "herdr_state": herdr_status,
            "state_source": source,
            "pane_id": info.get("pane_id"),
            "context_session": native_context_session(info, agents_by_name[name].kind),
        }
    busy = {
        name: record["state"]
        for name, record in statuses.items()
        if record["state"] in BUSY_STATES
    }
    if busy:
        raise AgentBusyError(
            "Refusing to interrupt {} - wait for them to reach idle or done, "
            "then run this again.".format(
                ", ".join("{} ({})".format(name, status) for name, status in sorted(busy.items()))
            ),
            {"busy": busy},
        )
    return statuses


def apply(client, assignments, agents_by_name, paths, at, no_clear=False, settle_timeout_ms=DEFAULT_SETTLE_TIMEOUT_MS, on_assigned=None, warn=None, sleep=time.sleep, settle_sec=COMPOSER_SETTLE_SEC, landing_attempts=LANDING_ATTEMPTS, start_timeout_ms=DEFAULT_START_TIMEOUT_MS, allow_recovery=False, task=None, retain_context=False, fix_round=None, judge_mode=None, history=None, tiers=None, recovery=None, plan_id=None, work=None, on_prepare=None, on_before_send=None, on_result=None, retrospective_guard=None, retain_specialist=False, requirements=None, reserved=None, reports=None, contents=None, fresh=False, assignment_scoped=False):
    """Hand each agent its brief using the selected context mode.

    `contents` carries a frozen dispatch's verified bytes, keyed by frozen
    path, to every brief read below (#565).

    `on_assigned(role, agent, at, status, context)` is called after each hand-off so the
    caller records it in the state ledger as it goes -- an interrupted run
    still leaves a truthful record of what was actually sent. The status rides
    along because a round that went out and never started is worth recording
    and must not count as experience of the role.
    """
    validate_agents(assignments, agents_by_name)
    transition = validate_context_mode(assignments, no_clear, retain_context, task, fix_round,
                                       recovery=recovery, history=history, plan_id=plan_id, work=work,
                                       retain_specialist=retain_specialist, requirements=requirements,
                                       assignment_scoped=assignment_scoped, fresh=fresh)
    # Every judge dispatch declares its mode, whichever caller reaches here; an
    # undeclared mode is refused, never defaulted (#478).
    if any(canonical_role(role) == "judge" for role in assignments) and judge_mode not in JUDGE_MODES:
        raise UsageError("A judge dispatch declares its mode, one of {}; pass judge_mode.".format(
            " | ".join(JUDGE_MODES)), {"judge_mode": judge_mode})
    validate_fix_history(assignments, history, task, fix_round)
    prior = validate_retained_history(assignments, history, task, fix_round) if retain_context else None
    tiers = dict(tiers or {})
    specialist_prior = validate_specialist_history(assignments, history, task, requirements, tiers) if retain_specialist else None
    if prior is not None and tiers.get("developer"):
        tiers["developer"] = retained_tier(agents_by_name[assignments["developer"]], tiers["developer"], prior.get("tier"))
    skip_clear = fresh or no_clear or retain_context or retain_specialist
    clear_reason = "retained" if retain_context or retain_specialist else "hand" if no_clear else "automatic"
    # Resolve the sink once. Every helper below defaults it too, but this
    # function calls it directly on the label path, and a None there would
    # raise instead of warning -- exactly when something already went wrong.
    warn = warn or stderr_warn
    # Status first: it is the refusal gate, and it is also where the pane ids
    # the clear command needs come from.
    statuses = check_all_ready(client, assignments, agents_by_name, warn=warn)
    if prior is not None:
        name = assignments["developer"]
        verify_live_retention(prior, statuses[name]["context_session"], name)
    if specialist_prior is not None:
        name = next(iter(assignments.values()))
        verify_live_retention(specialist_prior, statuses[name]["context_session"], name)
    refuse_reserved(assignments, task, reserved)
    steps = build_steps(
        client,
        assignments,
        agents_by_name,
        paths,
        panes={name: record.get("pane_id") for name, record in statuses.items()},
        no_clear=skip_clear,
        settle_timeout_ms=settle_timeout_ms,
        start_timeout_ms=start_timeout_ms,
        track_context=task is not None,
        tiers=tiers,
        requirements=requirements,
        contents=contents,
    )
    # Prove every pre-existing legacy worker before any role receives input.
    # The foreman starts these workers manually; apply never guesses a tier
    # or silently restarts a retained session to repair its permission mode.
    for step in steps:
        if requirements is not None and step["role"] in requirements:
            step["requirements"] = requirements[step["role"]]
        if step["pane_id"] == PANE_ID_PLACEHOLDER:
            raise UsageError("Herdr reported no pane for {!r}; inspect `herdr agent list` before dispatch so live YOLO arguments can be verified.".format(step["agent"]), {})
        if not step["tier"]:
            verify_running_permissions(client, agents_by_name[step["agent"]], step["pane_id"])
    refuse_wrapping_markers(client, steps, agents_by_name, reports, contents)
    require_criteria(steps, contents)

    if retrospective_guard is not None:
        retrospective_guard.preflight(steps, statuses)

    # One session per run. Recovery keys clear somebody's input line, and for
    # Codex the key that does it exits the process when the line is empty, so
    # foreman only clears text it can account for.
    session = DispatchSession(allow_recovery=allow_recovery)
    applied = []
    for step in steps:
        name = step["agent"]
        agent = agents_by_name[name]
        if retrospective_guard is not None:
            retrospective_guard.before(step)
        if on_prepare is not None:
            on_prepare(step, statuses[name])
        cleared = fresh
        clear_reason = "retained" if retain_context or retain_specialist else "hand" if no_clear else "automatic"
        if retrospective_guard is not None and isinstance(getattr(retrospective_guard, "retries", None), dict) and name in retrospective_guard.retries:
            clear_reason = "reconciled_not_sent"
        tier = tiers.get(step["role"])
        tier_record = None
        def before_input():
            if tier:
                verify_running(client, agent, step["pane_id"], tier)
            else:
                verify_running_permissions(client, agent, step["pane_id"])
            if retrospective_guard is not None:
                retrospective_guard.before(step)
            if specialist_prior is not None:
                live = check_all_ready(client, {step["role"]: name}, agents_by_name, warn=warn)
                verify_live_retention(specialist_prior, live[name]["context_session"], name)

        startup_identity = None
        def startup_observe():
            nonlocal startup_identity
            before_input()
            live = client.agent_get(name)
            proof = (verify_running(client, agent, step["pane_id"], tier) if tier
                     else foreground_agent(client, step["pane_id"], agent.kind))
            identity = (live.get("pane_id"), live.get("agent_session"), proof)
            if (live.get("pane_id") != step["pane_id"] or live.get("agent_status") not in READY_STATES
                    or type(proof.get("pid")) is not int or proof["pid"] <= 0
                    or (startup_identity is not None and identity != startup_identity)):
                raise HerdrError("Fresh worker changed its startup identity; nothing was sent.", {"pane_id": step["pane_id"]})
            startup_identity = identity
            return identity

        if not tier:
            # Earlier roles and their callbacks may replace a later worker.
            # Composer operations also recheck immediately before each input.
            before_input()
        if tier:
            proof = (verify_running(client, agent, step["pane_id"], tier) if skip_clear
                     else restart_worker(client, agent, step["pane_id"], tier, sleep=sleep,
                                         before_transition=partial(retrospective_guard.before, step) if retrospective_guard else None,
                                         before_start=partial(retrospective_guard.before_launch, step) if retrospective_guard else None))
            tier_record = {**tier, "launch_args": worker_launch_args(agent.kind, agent.launch_args), "verified": proof,
                           "prompt_hash": step["prompt_hash"]}
            # A fresh launch or retained-tier proof can become stale while the
            # composer is read. Verify the selected live tier before input.
            cleared = fresh or not skip_clear
            if cleared and retrospective_guard is not None:
                live_proof = verify_running(client, agent, step["pane_id"], tier)
                retrospective_guard.after_transition(step, launch_proof=live_proof)
        elif not skip_clear:
            pane_id = step["pane_id"]
            outcome = send_command(
                client,
                agent,
                pane_id,
                agent.clear_prompt,
                session=session,
                sleep=sleep,
                warn=warn,
                settle_sec=settle_sec,
                before_input=before_input,
                after_submit=partial(retrospective_guard.after_transition, step) if retrospective_guard else None,
            )
            client.agent_wait(name, until=SETTLE_STATES, timeout_ms=settle_timeout_ms)
            # `cleared` means the command was consumed AND the screen changed:
            # a fresh Codex session draws its banner, Claude empties the
            # transcript, Grok redraws session_start. Consumed but unchanged is
            # reported honestly rather than assumed.
            # A clear that changed nothing did not clear anything. Gating,
            # not advisory: briefing an agent that still holds the last task's
            # context is the failure the clear exists to prevent.
            if not outcome["screen_changed"]:
                raise HerdrError(
                    "{} consumed {} but its screen did not change, so the "
                    "context was not cleared -- a fresh session redraws (Codex "
                    "prints its banner, Claude Code empties the transcript). "
                    "Nothing further was sent. Look at pane {}, clear it by "
                    "hand, or pass --no-clear if that is what you want.".format(
                        name, agent.clear_prompt, step["pane_id"] or "(unknown)"
                    ),
                    {"agent": name, "clear_prompt": agent.clear_prompt},
                )
            cleared = True
            # The clear's redraw races the next paste; a leftover `/` is what
            # made Claude Code read the assignment as a slash command.
            sleep(settle_sec)
            if retrospective_guard is not None:
                retrospective_guard.after_transition(step)
        context_session = None
        tracks_session = task is not None and (step["role"] == "developer"
            or step["role"] in CONSULTATION_ROLES and (requirements or {}).get(step["role"]) is not None)
        if tracks_session:
            # Query again after the clear, or immediately before retaining.
            # A pre-clear reference cannot stand in for the new conversation.
            context_session = native_context_session(client.agent_get(name), agent.kind)
            if prior is not None:
                verify_live_retention(prior, context_session, name)
            elif specialist_prior is not None:
                verify_live_retention(specialist_prior, context_session, name)
            elif cleared and context_session == statuses[name]["context_session"]:
                context_session = None
        grok_new = agent.kind == "grok" and cleared and not tier
        if grok_new and statuses[name]["context_session"] is None:
            # A /new redraw does not repair Herdr's stale native ID (#365).
            # Preserve null proof even if the old ID was absent before clear.
            context_session = None
        # send_message re-checks the composer, pastes, and confirms the
        # message actually landed as a user message rather than as a command.
        def before_prompt():
            if fresh and assignment_scoped:
                startup_observe()
            if on_before_send is not None:
                before = {"cleared": cleared, "clear_reason": clear_reason,
                      "context_session": context_session, "tier": tier_record,
                      "transition": transition if step["role"] == "developer" else None}
                # Only a judge dispatch carries its mode (#478).
                if canonical_role(step["role"]) == "judge":
                    before["judge_mode"] = judge_mode
                on_before_send(step, before)
                before_input()
                if fresh and assignment_scoped:
                    startup_observe()
        landing = send_message(
            client,
            agent,
            step["prompt"],
            ASSIGNMENT_OPENING,
            pane_id=step["pane_id"],
            session=session,
            sleep=sleep,
            warn=warn,
            settle_sec=settle_sec,
            attempts=landing_attempts,
            start_timeout_ms=start_timeout_ms,
            before_input=before_input,
            before_prompt=before_prompt,
            startup_observe=startup_observe if fresh and assignment_scoped else None,
        )
        if tracks_session and prior is None and specialist_prior is None:
            context_session = (correlate_dispatch_session(
                client, agent, step["pane_id"], statuses[name]["context_session"], context_session,
                cleared=cleared, warn=warn, sleep=sleep, settle_sec=settle_sec, grok_new=grok_new,
            ) if landing["landed"] or landing["started"] else None)
        if grok_new and step["role"] != "developer":
            warn("{} received a fresh Grok assignment. Wait for its report; if delivery is unconfirmed, "
                 "stale-ID recovery through `{recover}` requires the recorded pre-clear native ID, original plan and native updates. "
                 "Without that ID, stale-ID recovery is unavailable: record the report as unavailable "
                 "and notify the operator of the missing pre-clear evidence. Keep review/release gates "
                 "unsatisfied; never rerun completed work.".format(name, recover=runnable.command("recover-report")))
        checked = statuses.get(name, {})
        record = {
            "role": step["role"],
            "agent": name,
            "state_before": checked.get("state"),
            "herdr_state_before": checked.get("herdr_state"),
            "state_source": checked.get("state_source"),
            "pane_id": checked.get("pane_id"),
            "cleared": cleared,
            "clear_reason": clear_reason,
            "task": task,
            "fix_round": fix_round,
            "context_session": context_session,
            "tier": tier_record,
            "landed": landing["landed"],
            "started": landing["started"],
            "status": "applied"
            if (landing["landed"] or landing["started"])
            else "sent_but_not_started",
            "brief": step["brief"],
            "common": step["common"],
            "at": at,
            "context_transition": transition if step["role"] == "developer" else None,
        }
        if assignment_scoped:
            record["assignment_scoped"] = True
        if "requirements" in step:
            record["requirements"] = step["requirements"]
        # The mode belongs to the judge seat alone, and the ledger is where an
        # adjudication and a diagnosis stay distinguishable afterwards (#478).
        # Only a judge result carries the key, so every other saved dispatch
        # result keeps the shape it had before recovery store 12.
        if canonical_role(step["role"]) == "judge":
            record["judge_mode"] = judge_mode
        # Persist the dispatch outcome before optional UI work. A broken pipe
        # during pane relabeling must never erase a confirmed handoff.
        if on_assigned is not None:
            context = {key: record[key] for key in ("cleared", "clear_reason", "task", "fix_round", "context_session", "tier")}
            context["judge_mode"] = record.get("judge_mode")
            on_assigned(step["role"], name, at, record["status"], context)
        if on_result is not None:
            on_result(record)
        # A sidebar of w1 w2 w3 tells the operator nothing. Label the pane with
        # who is doing what, but only once the hand-off is CONFIRMED: a label
        # claiming a role nobody started is worse than no label. Cosmetic, so a
        # failure warns and the dispatch stands.
        if record["status"] == "applied" and record.get("pane_id"):
            label = pane_label(
                step["role"], task, getattr(agents_by_name[name], "model_label", "")
            )
            try:
                client.pane_rename(record["pane_id"], label)
                record["pane_label"] = label
            except HerdrError as exc:
                warn(
                    "could not label {}'s pane {} as {!r}: {} - the assignment "
                    "landed, only the sidebar name did not.".format(
                        name, record["pane_id"], label, exc
                    )
                )
                record["pane_label"] = None
        else:
            record["pane_label"] = None

        applied.append(record)

    return {
        "schema_version": APPLY_SCHEMA_VERSION,
        "dry_run": False,
        "applied_at": at,
        "applied": applied,
    }


def dry_run(client, assignments, agents_by_name, paths, no_clear=False, settle_timeout_ms=DEFAULT_SETTLE_TIMEOUT_MS, retain_context=False, task=None, fix_round=None, tiers=None, recovery=None, history=None, plan_id=None, work=None, retain_specialist=False, requirements=None, reserved=None, fresh=False, assignment_scoped=False):
    """Print the plan without contacting herdr at all.

    Deliberately makes zero herdr calls, including the status check: a dry run
    against busy agents must show the plan rather than refuse it. The live
    `apply` re-checks status for real before sending anything.
    """
    transition = validate_context_mode(assignments, no_clear, retain_context, task, fix_round,
                                       recovery=recovery, history=history, plan_id=plan_id, work=work,
                                       retain_specialist=retain_specialist, requirements=requirements,
                                       assignment_scoped=assignment_scoped, fresh=fresh)
    if assignment_scoped and fresh:
        validate_fix_history(assignments, history, task, fix_round)
    if retain_specialist:
        validate_specialist_history(assignments, history, task, requirements, tiers)
    refuse_reserved(assignments, task, reserved)
    result = {
        "schema_version": APPLY_SCHEMA_VERSION,
        "dry_run": True,
        "sent": False,
        "clear_reason": "retained" if retain_context or retain_specialist else "hand" if no_clear else "automatic",
        "assignment_scoped": assignment_scoped,
        "task": task,
        "fix_round": fix_round,
        "context_transition": transition,
        "steps": build_steps(
            client,
            assignments,
            agents_by_name,
            paths,
            no_clear=fresh or no_clear or retain_context or retain_specialist,
            settle_timeout_ms=settle_timeout_ms,
            track_context=task is not None,
            tiers=tiers,
            requirements=requirements,
        ),
    }
    for step in result["steps"]:
        if requirements is not None and step["role"] in requirements:
            step["requirements"] = requirements[step["role"]]
    require_criteria(result["steps"])
    return result
