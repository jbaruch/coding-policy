"""Round-tier policy and exact launch-argument verification for #324.

The table is operator-owned configuration, never a per-dispatch model
override. Judgment rounds cannot lower the pinned model or effort. Cheap
mechanical work requires the research predicate in full. Model availability
and subscription attribution are separate: see billing.py for the latter.

Only installed, documented CLI kinds have launch adapters. Antigravity's
research column is a future adapter, not an accepted dead config row.
"""

import math
import os
import re
from pathlib import Path, PurePath

from .billing import billing_window, effective_multiplier
from .errors import ConfigError, HerdrError, UsageError


#: The pinned top model per adapter. Review these ids whenever
#: `capability-check` reports the capability table due (`capabilities.INTERVAL`),
#: and bump one only with a CHANGELOG note citing the table entry that moved it;
#: never rewrite this set from a model's own report (#520).
TOP_MODELS = {
    "claude": frozenset({"opus-5", "claude-opus-5"}),
    "codex": frozenset({"gpt-5.6-sol"}),
    "grok": frozenset({"grok-4.6"}),
}
EFFORTS = {
    "claude": frozenset({"low", "medium", "high", "xhigh", "max"}),
    "codex": frozenset({"low", "medium", "high", "xhigh"}),
    "grok": frozenset({"low", "medium", "high"}),
}
NO_EFFORT_MODELS = frozenset({"claude-haiku-4-5", "haiku-4.5"})
JUDGMENT_ROUNDS = frozenset({
    "architect", "reconciliation", "critic", "review",
    "hostile_verify", "recheck", "release_adjudication", "lead",
})
#: `consultation` is evidence gathering that decides nothing, and `test_plan`
#: is pre-development preparation that passes nothing: neither is a gate, so
#: neither carries the judgment floor (#518). A consultation that must settle
#: something is planned on `reconciliation` or `architect` explicitly.
ROUNDS = JUDGMENT_ROUNDS | {"build", "fix", "mechanical", "release_mechanics", "consultation", "test_plan"}
#: Each seat's default is the cheapest round its contract allows. A release
#: worker edits no source and its skill's own gates fail the round loudly, so
#: it defaults to mechanics; `release_adjudication` is requested explicitly
#: when the worker must interpret a dispute (#521).
DEFAULT_ROUNDS = {
    "developer": "build", "tester": "hostile_verify", "reviewer": "review",
    "release": "release_mechanics", "architect": "architect",
    "critic": "critic", "lead": "lead",
    "advisor": "consultation", "investigator": "consultation",
}
#: The judgment round a consultation takes when its recorded evidence says it
#: must settle something, and the default it took before `consultation`
#: existed. Selection reads the evidence from the round context rather than
#: relying on a remembered `--round` override (#518).
CONSULTATION_ESCALATION = {"investigator": "reconciliation", "advisor": "architect"}
#: Round-context evidence that moves each consultation to its judgment round.
ESCALATION_EVIDENCE = {
    "investigator": ("diagnosis_input", "prior_high_miss"),
    "advisor": ("security_trigger",),
}
#: Separates a seat from the slice it owns in a role name (`reviewer#api`). A
#: role name never contains it, so the seat reads back unambiguously (#409).
SEAT_SEPARATOR = "#"


#: The responsibilities a SEAT may fill. Slicing supplies a termination
#: condition for independent verification of a surface. Every other
#: responsibility holds a per-task counter one seat owns -- the developer fix
#: count, the retained-context transition, the release role -- and a slice of
#: one would read as a second worker holding the same count.
SEATABLE_ROLES = frozenset({"reviewer", "tester"})

#: The slice half of a seat. A seat is a CLI key -- the left side of `--brief
#: SEAT=PATH` and `--report SEAT=PATH`, and a line-oriented key
#: `compose-briefs.sh` reads back -- so a name outside this shape plans a seat
#: the round cannot address. `partition` validates its document against the
#: same pattern, so a hand-written seat and a generated one are held to one
#: grammar.
SLICE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


#: What a role name can never carry: `/` escapes the brief's output directory,
#: and `=` and `,` are the separators `--brief ROLE=PATH` and `--roles a,b`
#: split on. `compose-briefs.sh` refuses the same set, so a name `plan` emits
#: always composes.
UNADDRESSABLE_ROLE = re.compile(r"[/=,\x00-\x1f\x7f]")


def require_seatable(name):
    """Refuse a name that is not a seat this fleet can address.

    `partition_role` and `load_partition` bar an unseatable role and a
    malformed slice inside the partition document; this bars both wherever a
    role name is READ -- `--roles developer#api`, `--assignments {"reviewer#":
    ...}` and any other hand-written map reach the same refusal (#434).
    """
    if isinstance(name, str) and UNADDRESSABLE_ROLE.search(name):
        raise UsageError(
            "Role {!r} cannot be addressed: a name carrying a path separator, '=', ',' or a "
            "control character neither names its brief nor reads back through "
            "`--brief ROLE=PATH` and `--roles a,b`.".format(name),
            {"role": name})
    if not isinstance(name, str) or SEAT_SEPARATOR not in name:
        return name
    base = canonical_role(name)
    if base not in SEATABLE_ROLES:
        raise UsageError(
            "Role {!r} names a seat of {!r}, which holds a per-task counter one "
            "worker owns. Pass {!r} unseated, or seat the slice under {}.".format(
                name, base, base, " or ".join(sorted(SEATABLE_ROLES))),
            {"role": name})
    slice_name = name.split(SEAT_SEPARATOR, 1)[1]
    if not SLICE_NAME.fullmatch(slice_name):
        raise UsageError(
            "Seat {!r} names the slice {!r}, which cannot address it: name a slice "
            "with letters, digits, underscores, dots or hyphens, starting with a "
            "letter or digit. A seat is a CLI key, and any other name does not read "
            "back through `--brief SEAT=PATH`.".format(name, slice_name),
            {"role": name})
    return name


def canonical_role(name):
    """The responsibility a seat fills.

    Several seats of one role are distinct names to the planner and one
    responsibility to everything else -- independence, round tiers,
    requirements, brief templates and the per-role history all resolve through
    here, so a seat inherits its role's contract instead of reading as an
    unknown one (#434).
    """
    if not isinstance(name, str):
        return name
    return name.split(SEAT_SEPARATOR, 1)[0]


ROLE_ROUNDS = {
    "developer": frozenset({"build", "fix", "mechanical"}),
    "tester": frozenset({"test_plan", "hostile_verify", "recheck"}),
    "reviewer": frozenset({"architect", "reconciliation", "critic", "review", "recheck"}),
    "release": frozenset({"release_adjudication", "release_mechanics"}),
    "architect": frozenset({"architect", "reconciliation"}),
    "critic": frozenset({"critic"}), "lead": frozenset({"lead"}),
    "advisor": frozenset({"consultation", "architect"}),
    "investigator": frozenset({"consultation", "reconciliation"}),
}
#: A whole-result oracle: the expected result recorded in a form a later check
#: compares against byte for byte. Its EXISTENCE is what licenses a round below
#: its floor, since a wrong result is then caught loudly rather than silently
#: (#480).
#: `digest` carries the expected sha256 of the whole result. `patch` and
#: `fixture` name a file holding the exact patch to apply, or the complete
#: expected output.
ORACLE_KINDS = ("digest", "patch", "fixture")
#: What the retired predicate accepted. Named so a saved round-context written
#: against it is refused with its replacement rather than silently ignored.
RETIRED_CONTEXT_FIELDS = frozenset({
    "task_kind", "spec_complete", "no_semantic_decisions", "whole_result_oracle",
    "exact_plan", "files", "homogeneous_enumerated", "tool_retries", "repair_rounds",
    "unplanned_file", "semantic_question", "unresolved_conflict", "missing_oracle",
    "gate_red_after_repair",
})
ORACLE_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
XHIGH_MIN_RISKS = 2
XHIGH_CONTEXT_BYTES = 250000
BUILD_FAILED_GATES = 2
#: Measured remaining headroom, in percent, at or below which a DISCRETIONARY
#: escalation is declined on a non-judgment round. The audited fleet spent a
#: weekly Codex window at roughly 0.75 points per round, so twenty points is
#: about twenty-five rounds of runway -- enough to finish a task, not enough to
#: spend on an escalation the configured row already covers. A judgment round
#: is never de-escalated, whatever the pressure (#477).
PRESSURE_HEADROOM_PCT = 20.0
TIER_FIELDS = frozenset({"model", "effort", "multiplier", "billing_evidence"})
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
LAUNCH_SWITCHES = {
    "claude": frozenset({"--dangerously-skip-permissions"}),
    "codex": frozenset({"--full-auto", "--no-alt-screen", "--dangerously-bypass-approvals-and-sandbox"}),
    "grok": frozenset({"--always-approve", "--no-subagents", "--no-alt-screen"}),
}
YOLO_FLAGS = {
    "claude": "--dangerously-skip-permissions",
    "codex": "--dangerously-bypass-approvals-and-sandbox",
    "grok": "--always-approve",
}
YOLO_OPTION_VALUES = {
    "--permission-mode": "bypassPermissions",
    "-a": "never", "--ask-for-approval": "never",
    "-s": "danger-full-access", "--sandbox": "danger-full-access",
}
LAUNCH_OPTIONS = {
    "claude": {"--permission-mode": {"default", "acceptEdits", "auto", "dontAsk", "bypassPermissions", "plan"}},
    "codex": {"-a": {"on-request", "never"}, "--ask-for-approval": {"on-request", "never"},
              "-s": {"read-only", "workspace-write", "danger-full-access"},
              "--sandbox": {"read-only", "workspace-write", "danger-full-access"}},
    "grok": {"--permission-mode": {"default", "acceptEdits", "auto", "dontAsk", "bypassPermissions", "plan"}},
}
# Resumed-process proof (#382), verified against the installed CLI help:
# Claude Code 2.1.266 `-r, --resume [value]`; Grok 1.0.24 `-r, --resume
# [<SESSION_ID_OR_TITLE>]`; Codex 0.153.2 `codex resume [OPTIONS] [SESSION_ID]
# [PROMPT]`. Only one explicit session UUID, as a separate token, identifies
# the resumed conversation. Pickers, most-recent selectors, titles or names,
# forks, new --session-id conversations and prompt operands are refused.
# launch_args never accept a resume form; this grammar reads live argv only.
RESUME_OPTIONS = {"claude": frozenset({"-r", "--resume"}), "codex": frozenset(), "grok": frozenset({"-r", "--resume"})}
RESUME_SUBCOMMANDS = {"codex": "resume"}
RESUME_REFUSALS = {
    "claude": frozenset({"-c", "--continue", "--fork-session", "--session-id", "--from-pr", "--teleport"}),
    "codex": frozenset({"--last", "--all", "--include-non-interactive"}),
    "grok": frozenset({"-c", "--continue", "--fork-session", "-s", "--session-id", "--restore-code", "-w", "--worktree"}),
}
SESSION_UUID = re.compile(r"[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}\Z")


class MissingTierError(UsageError):
    """A candidate lacks a required row; other candidates may still qualify."""


def _error(message):
    raise ConfigError(message + " Update the agent's tiers in config.json.", {})


def parse_launch_args(args, kind):
    """Preserve explicit operator permission/UI choices, never tier overrides."""
    if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
        _error("launch_args must be an array of supported permission/UI flags.")
    offset = 0
    seen = set()
    while offset < len(args):
        flag = args[offset]
        if flag in seen:
            _error("Duplicate launch option {!r}.".format(flag))
        seen.add(flag)
        if flag in LAUNCH_SWITCHES.get(kind, frozenset()):
            offset += 1
        elif flag in LAUNCH_OPTIONS.get(kind, {}):
            if offset + 1 >= len(args) or args[offset + 1] not in LAUNCH_OPTIONS[kind][flag]:
                _error("Invalid value for launch option {!r}.".format(flag))
            offset += 2
        else:
            _error("Unsupported launch option {!r}; model, effort, resume and prompt overrides are forbidden.".format(flag))
    return list(args)


def worker_launch_args(kind, args=()):
    """Require YOLO for new/live workers while preserving supported UI flags.

    Installed CLI --help verifies YOLO_FLAGS. The lead's classifier screens
    assignments; launch mode never grants work beyond their authorized scope.
    Restrictive operator options fail before a worker is stopped. Compatible
    aliases normalize to one flag, preventing contradictory CLI precedence.
    Archive readers keep parse_launch_args/verify_argv's historical contract.
    """
    if kind not in YOLO_FLAGS:
        raise ConfigError("No YOLO launch adapter for {!r}; configure a supported worker kind before starting it.".format(kind), {})
    parsed = parse_launch_args(list(args), kind)
    options = []
    offset = 0
    while offset < len(parsed):
        flag = parsed[offset]
        if flag == "--full-auto" or (
            flag in YOLO_OPTION_VALUES and parsed[offset + 1] != YOLO_OPTION_VALUES[flag]
        ):
            raise ConfigError("launch_args option {!r} conflicts with required YOLO mode; remove restrictive permission/sandbox options from config.json and use {}.".format(flag, YOLO_FLAGS[kind]), {})
        if flag in YOLO_OPTION_VALUES:
            offset += 2
        else:
            if flag != YOLO_FLAGS[kind]:
                options.append(flag)
            offset += 1
    return [YOLO_FLAGS[kind]] + options


def _resume_session(current, value, recovery):
    """Exactly one explicit session UUID; every other selector is ambiguous."""
    if current is not None:
        raise HerdrError("Worker resume arguments carry a second session or a prompt operand; resume with exactly one explicit session UUID and no prompt. " + recovery, {})
    if not isinstance(value, str) or not SESSION_UUID.fullmatch(value):
        raise HerdrError("Worker resume arguments name no single explicit session UUID; a picker, most-recent, title, name or search selector is ambiguous. " + recovery, {})
    return value


def verify_worker_permissions(kind, argv):
    """Prove a legacy worker's YOLO argv without inventing model-tier data.

    Accept only the launch adapter's permission/UI options, model/effort
    arguments, and the documented resume form naming one explicit session
    UUID (RESUME_OPTIONS, RESUME_SUBCOMMANDS). Unknown configuration,
    wrappers, ambiguous or identity-changing resume selectors
    (RESUME_REFUSALS) and prompt operands cannot establish permission proof.
    Codex config overrides are restricted to its documented reasoning-effort
    key so approval overrides cannot hide.
    """
    recovery = ("Inspect its foreground argv, then start a fresh worker with the documented YOLO flags "
                "or restore a retained developer's own native session under references/dispatch-recovery.md before dispatch.")
    if (kind not in YOLO_FLAGS or not isinstance(argv, list) or not argv
            or any(not isinstance(arg, str) for arg in argv) or PurePath(argv[0]).name != kind):
        raise HerdrError("Worker has no usable YOLO process proof. " + recovery, {})
    model_options = {"claude": {"--model", "--effort"}, "codex": {"-m", "--model", "-c", "--config"},
                     "grok": {"-m", "--model", "--reasoning-effort", "--effort"}}[kind]
    subcommand = RESUME_SUBCOMMANDS.get(kind)
    resuming = subcommand is not None and argv[1:2] == [subcommand]
    session = None
    permissions = []
    pairs = {}
    offset = 2 if resuming else 1
    while offset < len(argv):
        flag = argv[offset]
        if flag in RESUME_REFUSALS[kind]:
            raise HerdrError("Worker resume argument {!r} selects no single explicit session or changes its identity; resume with one explicit session UUID. {}".format(flag, recovery), {})
        if flag in RESUME_OPTIONS[kind]:
            session = _resume_session(session, argv[offset + 1] if offset + 1 < len(argv) else None, recovery)
            offset += 2
        elif subcommand is not None and flag == subcommand:
            raise HerdrError("Worker resume subcommand must directly follow the executable; options before it are not a documented resume form. " + recovery, {})
        elif resuming and not flag.startswith("-"):
            session = _resume_session(session, flag, recovery)
            offset += 1
        elif flag in LAUNCH_SWITCHES[kind]:
            permissions.append(flag)
            offset += 1
        elif flag in LAUNCH_OPTIONS[kind] or flag in model_options:
            if offset + 1 >= len(argv) or not argv[offset + 1] or argv[offset + 1].startswith("-"):
                raise HerdrError("Worker has incomplete launch arguments. " + recovery, {})
            value = argv[offset + 1]
            if flag in LAUNCH_OPTIONS[kind]:
                permissions.extend((flag, value))
                pairs[flag] = value
            elif kind == "codex" and flag in {"-c", "--config"} and not re.fullmatch(
                r'model_reasoning_effort=(?:[a-z]+|"[a-z]+")', value
            ):
                raise HerdrError("Worker config override cannot establish YOLO permission proof. " + recovery, {})
            offset += 2
        else:
            raise HerdrError("Worker has unsupported launch arguments for YOLO proof. " + recovery, {})
    if resuming and session is None:
        _resume_session(None, None, recovery)
    try:
        parse_launch_args(permissions, kind)
    except ConfigError as exc:
        raise HerdrError("Worker has invalid permission/UI launch options: {} {}".format(exc.message, recovery), {}) from None
    try:
        worker_launch_args(kind, permissions)
    except ConfigError:
        raise HerdrError("Worker has restrictive permission options. " + recovery, {}) from None
    equivalent = (pairs.get("--permission-mode") == "bypassPermissions" if kind != "codex" else
                  (pairs.get("-a") == "never" or pairs.get("--ask-for-approval") == "never")
                  and (pairs.get("-s") == "danger-full-access" or pairs.get("--sandbox") == "danger-full-access"))
    if YOLO_FLAGS[kind] not in permissions and not equivalent:
        raise HerdrError("Worker launch arguments do not prove YOLO mode. " + recovery, {})


def parse_tiers(raw, kind):
    """Validate an optional per-agent {round: tier} table; never invent rows."""
    if raw is None:
        return {}
    if kind not in TOP_MODELS:
        _error("Tier launches are supported for claude, codex, and grok; {!r} has no adapter.".format(kind))
    if not isinstance(raw, dict) or not raw:
        _error("tiers must be a non-empty object keyed by round type.")
    result = {}
    for round_type, entry in raw.items():
        if round_type not in ROUNDS:
            _error("Unknown round {!r}; use one of {}.".format(round_type, ", ".join(sorted(ROUNDS))))
        if not isinstance(entry, dict) or set(entry) - TIER_FIELDS:
            _error("Tier {!r} must contain only {}.".format(round_type, ", ".join(sorted(TIER_FIELDS))))
        model = entry.get("model")
        if not isinstance(model, str) or not MODEL_ID.fullmatch(model):
            _error("Tier {!r} needs a model identifier, not a flag or command.".format(round_type))
        effort = entry.get("effort")
        if model in NO_EFFORT_MODELS:
            if kind != "claude" or effort is not None:
                _error("Haiku accepts no effort flag; omit effort for its Claude tier.")
        elif not isinstance(effort, str) or effort not in EFFORTS[kind]:
            _error("Tier {!r} needs an explicit effort from {}.".format(round_type, sorted(EFFORTS[kind])))
        if round_type in JUDGMENT_ROUNDS and (
            model not in TOP_MODELS[kind] or effort not in {"high", "xhigh", "max"}
        ):
            _error("Judgment round {!r} must use the pinned top model at high or above.".format(round_type))
        multiplier = entry.get("multiplier", 1.0)
        if isinstance(multiplier, bool) or not isinstance(multiplier, (int, float)):
            _error("Tier multiplier must be a finite positive number.")
        try:
            multiplier = float(multiplier)
        except OverflowError:
            _error("Tier multiplier is too large.")
        if not math.isfinite(multiplier) or multiplier <= 0:
            _error("Tier multiplier must be a finite positive number.")
        result[round_type] = {**entry, "model": model, "effort": effort, "multiplier": multiplier}
    return result


def _nonnegative_int(context, key):
    value = context.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise UsageError("Round context {} must be a non-negative integer.".format(key), {})
    return value


def mechanical_allowed(context):
    """Whether a whole-result oracle licenses a round below its floor.

    One question, answered from the artifact rather than from an assertion: is
    the expected whole result written down somewhere a later check can compare
    against? Where it is, a wrong result is loud, and the round may run cheap.

    The retired predicate asked instead for a task named in a closed list of
    eight, ten hand-typed booleans nothing validated, and two size caps standing
    in for difficulty. It fired zero times in 702 recorded assignments and could
    not fire (#480).
    """
    oracle = context.get("oracle")
    if not isinstance(oracle, dict) or set(oracle) - {"kind", "value", "path"}:
        return False
    kind = oracle.get("kind")
    if not isinstance(kind, str):
        return False
    if kind == "digest":
        value = oracle.get("value")
        return isinstance(value, str) and bool(ORACLE_DIGEST.match(value)) and "path" not in oracle
    if kind in {"patch", "fixture"}:
        path = oracle.get("path")
        if not isinstance(path, str) or not path.strip() or "value" in oracle:
            return False
        # A plan is replayed at apply, possibly from another directory. A
        # relative path would resolve to a different file, or none.
        if not Path(path).is_absolute():
            return False
        # The file, not a claim about it. A declared oracle nobody wrote is the
        # silent failure this gate exists to refuse.
        candidate = Path(path)
        return candidate.is_file() and os.access(candidate, os.R_OK)
    return False


def measured_pressure(headroom):
    """Headroom as a float when it is a usable measurement, else None.

    A snapshot is a file on disk and its headroom can be absent, null or
    garbage. Unknown pressure must never READ as abundant capacity, and it must
    never read as scarcity either: it resolves the tier exactly as an
    unmeasured fleet always has.
    """
    if headroom is None or isinstance(headroom, bool) or not isinstance(headroom, (int, float)):
        return None
    try:
        value = float(headroom)
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


def select_tier(agent, role, round_type=None, context=None, fix_round=None, headroom=None):
    """Resolve one candidate from configuration; no per-call model override.

    `headroom` is the worker's measured remaining percentage. It can only
    DECLINE a discretionary escalation on a non-judgment round -- it never
    lowers a configured row, and never touches a judgment round, whose pinned
    model and effort no per-round input lowers (#477).
    """
    if not agent.tiers:
        if round_type is not None:
            raise UsageError("Agent {} has no tier table; configure it before selecting a round.".format(agent.name), {})
        return None
    context = {} if context is None else context
    if not isinstance(context, dict):
        raise UsageError("Round context must be a JSON object.", {})
    allowed = {"oracle", "risk_flags", "input_bytes", "failed_gates", "prior_high_miss",
               "diagnosis_input", "security_trigger"}
    retired = set(context) & RETIRED_CONTEXT_FIELDS
    if retired:
        raise UsageError(
            "Round-context fields {} were the retired mechanical predicate: a closed list of task "
            "names, hand-typed proofs and size caps. Declare an `oracle` instead, naming where the "
            "expected whole result is written down.".format(", ".join(sorted(retired))), {})
    if set(context) - allowed:
        raise UsageError("Unknown round-context fields: {}; check their spelling.".format(", ".join(sorted(set(context) - allowed))), {})
    for flag in ("prior_high_miss", "diagnosis_input", "security_trigger"):
        if flag in context and type(context[flag]) is not bool:
            raise UsageError("Round-context {} must be a JSON boolean.".format(flag), {})
    for key in ("input_bytes", "failed_gates"):
        _nonnegative_int(context, key)
    # The owner checks the task's recorded allowance before tier selection.
    # Selection preserves the true cumulative number for authorized recovery.
    if fix_round is not None and (type(fix_round) is not int or fix_round < 1):
        raise UsageError("Fix round must be a positive integer; preserve the task counter.", {})
    base = canonical_role(role)
    evidence = [flag for flag in ESCALATION_EVIDENCE.get(base, ()) if context.get(flag) is True]
    if evidence and round_type == "consultation":
        raise UsageError("Round-context {} requires {} to settle this, on {!r}; drop the `consultation` round request.".format(
            ", ".join(evidence), base, CONSULTATION_ESCALATION[base]), {})
    if round_type is None and base in CONSULTATION_ESCALATION and (evidence or "consultation" not in agent.tiers):
        # Evidence escalates deterministically. A tier table written before
        # config schema 4 has no `consultation` row and keeps the judgment
        # round it always defaulted to (config.py enforces the row from 4).
        round_type = CONSULTATION_ESCALATION[base]
    round_type = round_type or ("fix" if base == "developer" and fix_round else DEFAULT_ROUNDS.get(base))
    if not isinstance(round_type, str) or round_type not in ROLE_ROUNDS.get(base, frozenset()):
        raise UsageError("Round {!r} cannot perform role {!r}; choose its documented round type.".format(round_type, role), {})
    chosen_round = round_type
    if round_type == "build" and _nonnegative_int(context, "failed_gates") >= BUILD_FAILED_GATES:
        chosen_round = "review"
    if fix_round is not None and fix_round >= 4:
        chosen_round = "review"
    if chosen_round not in agent.tiers:
        raise MissingTierError("Agent {} has no {!r} tier; add the required row before planning.".format(agent.name, chosen_round), {})
    tier = dict(agent.tiers[chosen_round])
    # Only a developer's mechanical round needs an oracle. A release round's
    # loud failure is the release skill's own gates, and it has no pre-written
    # whole result to compare against (#521).
    if round_type == "mechanical" and not mechanical_allowed(context):
        raise UsageError("Mechanical eligibility is unproven or an escape condition fired; use a judgment round with a fresh brief.", {})
    risks = context.get("risk_flags", [])
    if not isinstance(risks, list) or any(not isinstance(flag, str) or not flag for flag in risks):
        raise UsageError("risk_flags must be an array of non-empty names.", {})
    needs_xhigh = (
        len(set(risks)) >= XHIGH_MIN_RISKS
        or _nonnegative_int(context, "input_bytes") > XHIGH_CONTEXT_BYTES
        or context.get("prior_high_miss") is True
    )
    # Pressure reaches tier RESOLUTION, not only the planner's worker ranking:
    # a cheaper pair was the only thing headroom could buy before, and a seat's
    # own round was always resolved at full price (#477).
    pressure = measured_pressure(headroom)
    de_escalated = bool(
        needs_xhigh and pressure is not None and pressure <= PRESSURE_HEADROOM_PCT
        and round_type not in JUDGMENT_ROUNDS and chosen_round not in JUDGMENT_ROUNDS
    )
    if de_escalated:
        # The configured row is the floor and it still runs. What is declined is
        # the discretionary step ABOVE it, which is the only part of the
        # selection the operator did not write down.
        needs_xhigh = False
    if needs_xhigh:
        # High risk also excludes a lower build/fix model, not only low effort.
        if tier["model"] not in TOP_MODELS[agent.kind]:
            if "review" not in agent.tiers:
                raise MissingTierError("Agent {} has no review tier for high-risk work; add and qualify its review row before assigning it this round.".format(agent.name), {})
            tier = dict(agent.tiers["review"])
            chosen_round = "review"
        if agent.kind in {"claude", "codex"} and tier["effort"] not in {"xhigh", "max"}:
            tier["effort"] = "xhigh"
    return {
        **tier, "round": round_type, "tier_row": chosen_round, "kind": agent.kind,
        "pressure_headroom": pressure, "de_escalated": de_escalated,
        "billing_window": billing_window(tier), "effective_multiplier": effective_multiplier(tier),
    }


def launch_flags(kind, tier):
    """The documented flags, shared by dry-run and real execution."""
    model, effort = tier["model"], tier.get("effort")
    if kind == "claude":
        return ["--model", model] + (["--effort", effort] if effort else [])
    if kind == "codex":
        return ["-m", model] + (["-c", "model_reasoning_effort=" + effort] if effort else [])
    if kind == "grok":
        return ["--model", model] + (["--reasoning-effort", effort] if effort else [])
    raise UsageError("No tier launch adapter for {!r}; add and verify one first.".format(kind), {})


def verify_argv(kind, tier, argv, launch_args=()):
    """Prove exact launch arguments; reject duplicates, overrides and resumes.

Returned argv is a process argument vector, never transcript text. Only the
canonical executable and requested tier flags are accepted here; preserving
additional operator launch options is handled by the caller's exact argv
comparison. Shell command strings, nested wrappers, and lookalike model or
effort tokens do not establish proof.
"""
    if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) for arg in argv):
        raise HerdrError("Worker launch returned no argv array; read the process before dispatch.", {})
    if PurePath(argv[0]).name != kind or argv[1:] != list(launch_args) + launch_flags(kind, tier):
        raise HerdrError("Worker launch argv did not match the requested model, effort and launch options; start a fresh worker with the required arguments before dispatch. No brief may be sent.", {"kind": kind})
    return {"model": tier["model"], "effort": tier.get("effort"), "argv": list(argv), "source": "launch_argv"}
