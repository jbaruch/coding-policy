"""Round-tier policy and exact launch-argument verification for #324.

The table is operator-owned configuration, never a per-dispatch model
override. Judgment rounds cannot lower the pinned model or effort. Cheap
mechanical work requires the research predicate in full. Model availability
and subscription attribution are separate: see billing.py for the latter.

Only installed, documented CLI kinds have launch adapters. Antigravity's
research column is a future adapter, not an accepted dead config row.
"""

import math
import re
from pathlib import PurePath

from .billing import billing_window, effective_multiplier
from .errors import ConfigError, HerdrError, UsageError


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
    "architect", "reconciliation", "test_plan", "critic", "review",
    "hostile_verify", "recheck", "release_adjudication", "lead",
})
ROUNDS = JUDGMENT_ROUNDS | {"build", "fix", "mechanical", "release_mechanics"}
DEFAULT_ROUNDS = {
    "developer": "build", "tester": "hostile_verify", "reviewer": "review",
    "release": "release_adjudication", "architect": "architect",
    "critic": "critic", "lead": "lead",
}
ROLE_ROUNDS = {
    "developer": frozenset({"build", "fix", "mechanical"}),
    "tester": frozenset({"test_plan", "hostile_verify", "recheck"}),
    "reviewer": frozenset({"architect", "reconciliation", "critic", "review", "recheck"}),
    "release": frozenset({"release_adjudication", "release_mechanics"}),
    "architect": frozenset({"architect", "reconciliation"}),
    "critic": frozenset({"critic"}), "lead": frozenset({"lead"}),
}
MECHANICAL_TASKS = frozenset({
    "rebase", "restack", "apply_exact_patch", "docs_only", "check_rerun",
    "exact_thread_reply", "homogeneous_search_replace", "issue_filing",
})
MECHANICAL_PROOFS = (
    "spec_complete", "no_semantic_decisions", "whole_result_oracle", "exact_plan",
)
MECHANICAL_MAX_FILES = 2
MECHANICAL_MAX_BYTES = 64000
XHIGH_MIN_RISKS = 2
XHIGH_CONTEXT_BYTES = 250000
BUILD_FAILED_GATES = 2
MAX_MECHANICAL_RETRIES = 2
MAX_MECHANICAL_REPAIRS = 1
TIER_FIELDS = frozenset({"model", "effort", "multiplier", "billing_evidence", "qualification"})
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
LAUNCH_SWITCHES = {
    "claude": frozenset({"--dangerously-skip-permissions"}),
    "codex": frozenset({"--full-auto", "--no-alt-screen"}),
    "grok": frozenset({"--always-approve", "--no-subagents", "--no-alt-screen"}),
}
LAUNCH_OPTIONS = {
    "claude": {"--permission-mode": {"default", "acceptEdits", "auto", "dontAsk", "bypassPermissions", "plan"}},
    "codex": {"-a": {"on-request", "never"}, "--ask-for-approval": {"on-request", "never"},
              "-s": {"read-only", "workspace-write", "danger-full-access"},
              "--sandbox": {"read-only", "workspace-write", "danger-full-access"}},
    "grok": {"--permission-mode": {"default", "acceptEdits", "auto", "dontAsk", "bypassPermissions", "plan"}},
}


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
        if not isinstance(entry.get("qualification", []), list):
            _error("Tier qualification must be an array of recorded battery results.")
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
    """The research's whole-result predicate, including its escape conditions."""
    return (
        context.get("task_kind") in MECHANICAL_TASKS
        and all(context.get(key) is True for key in MECHANICAL_PROOFS)
        and context.get("risk_flags") == []
        and (_nonnegative_int(context, "files") <= MECHANICAL_MAX_FILES
             or context.get("homogeneous_enumerated") is True)
        and "files" in context and "input_bytes" in context
        and _nonnegative_int(context, "input_bytes") <= MECHANICAL_MAX_BYTES
        and _nonnegative_int(context, "tool_retries") <= MAX_MECHANICAL_RETRIES
        and _nonnegative_int(context, "repair_rounds") <= MAX_MECHANICAL_REPAIRS
        and not any(context.get(key) for key in (
            "unplanned_file", "semantic_question", "unresolved_conflict", "missing_oracle", "gate_red_after_repair"
        ))
    )


def select_tier(agent, role, round_type=None, context=None, fix_round=None):
    """Resolve one candidate from configuration; no per-call model override."""
    if not agent.tiers:
        if round_type is not None:
            raise UsageError("Agent {} has no tier table; configure it before selecting a round.".format(agent.name), {})
        return None
    context = {} if context is None else context
    if not isinstance(context, dict):
        raise UsageError("Round context must be a JSON object.", {})
    allowed = set(MECHANICAL_PROOFS) | {"task_kind", "risk_flags", "files", "input_bytes", "homogeneous_enumerated",
        "tool_retries", "repair_rounds", "unplanned_file", "semantic_question", "unresolved_conflict",
        "missing_oracle", "gate_red_after_repair", "failed_gates", "prior_high_miss"}
    if set(context) - allowed:
        raise UsageError("Unknown round-context fields: {}; check their spelling.".format(", ".join(sorted(set(context) - allowed))), {})
    boolean_fields = set(MECHANICAL_PROOFS) | {"homogeneous_enumerated", "unplanned_file", "semantic_question",
        "unresolved_conflict", "missing_oracle", "gate_red_after_repair", "prior_high_miss"}
    if any(type(context[key]) is not bool for key in boolean_fields & set(context)):
        raise UsageError("Round-context proof and escape fields must be JSON booleans.", {})
    for key in ("files", "input_bytes", "tool_retries", "repair_rounds", "failed_gates"):
        _nonnegative_int(context, key)
    if fix_round is not None and (type(fix_round) is not int or not 1 <= fix_round <= 5):
        raise UsageError("Fix round must be an integer from 1 through 5; preserve the task counter.", {})
    round_type = round_type or ("fix" if role == "developer" and fix_round else DEFAULT_ROUNDS.get(role))
    if not isinstance(round_type, str) or round_type not in ROLE_ROUNDS.get(role, frozenset()):
        raise UsageError("Round {!r} cannot perform role {!r}; choose its documented round type.".format(round_type, role), {})
    chosen_round = round_type
    if round_type == "build" and _nonnegative_int(context, "failed_gates") >= BUILD_FAILED_GATES:
        chosen_round = "review"
    if fix_round is not None and fix_round >= 4:
        chosen_round = "review"
    if chosen_round not in agent.tiers:
        raise UsageError("Agent {} has no {!r} tier; add the required row before planning.".format(agent.name, chosen_round), {})
    tier = dict(agent.tiers[chosen_round])
    if round_type in {"mechanical", "release_mechanics"} and not mechanical_allowed(context):
        raise UsageError("Mechanical eligibility is unproven or an escape condition fired; use a judgment round with a fresh brief.", {})
    risks = context.get("risk_flags", [])
    if not isinstance(risks, list) or any(not isinstance(flag, str) or not flag for flag in risks):
        raise UsageError("risk_flags must be an array of non-empty names.", {})
    needs_xhigh = (
        len(set(risks)) >= XHIGH_MIN_RISKS
        or _nonnegative_int(context, "input_bytes") > XHIGH_CONTEXT_BYTES
        or context.get("prior_high_miss") is True
        or round_type == "hostile_verify"
    )
    if needs_xhigh:
        # High risk also excludes a lower build/fix model, not only low effort.
        if tier["model"] not in TOP_MODELS[agent.kind]:
            if "review" not in agent.tiers:
                raise UsageError("High-risk work needs the configured review tier.", {})
            tier = dict(agent.tiers["review"])
        if agent.kind in {"claude", "codex"} and tier["effort"] not in {"xhigh", "max"}:
            tier["effort"] = "xhigh"
    return {
        **tier, "round": round_type, "kind": agent.kind,
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
        raise HerdrError("Worker launch argv did not match the requested model and effort; no brief may be sent.", {"kind": kind})
    return {"model": tier["model"], "effort": tier.get("effort"), "argv": list(argv), "source": "launch_argv"}
