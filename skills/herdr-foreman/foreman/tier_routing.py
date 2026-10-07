"""Operator-opted minimum-adequate pair selection, not a new qualification programme.

Config owns launch/account facts; capabilities owns sourced qualification;
measure owns capacity. None supplies another owner's evidence. Rows are
assessed against this task's requirements. Declared resource weights are
ordering proxies, never a currency or separate-quota claim.
"""

import copy
from datetime import timedelta

from . import capabilities, successors
from .billing import billing_window, effective_multiplier
from .chronology import timestamp
from .errors import ConfigError, UsageError
from .tiers import JUDGMENT_ROUNDS, ROLE_ROUNDS, canonical_role

MODES = frozenset({"minimum_adequate", "pinned"})
FACT_MAX_AGE = capabilities.INTERVAL
CAPACITY_MAX_AGE = timedelta(minutes=15)
EFFORT_ORDER = {None: 0, "low": 1, "medium": 2, "high": 3, "xhigh": 4, "max": 5}


def parse(value, tiers):
    """Validate schema-8 operator input; missing means legacy configured-row routing."""
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {"mode", "evidence"}
            or not isinstance(value["mode"], str) or value["mode"] not in MODES
            or not isinstance(value["evidence"], dict)):
        raise ConfigError("tier_routing needs mode (minimum_adequate or pinned) and an evidence object keyed by configured row.", {})
    for name, proof in value["evidence"].items():
        if name not in tiers or not isinstance(proof, dict) or set(proof) != {"model", "effort", "launch", "access"}:
            raise ConfigError("tier_routing evidence names a configured row and carries model, effort, launch and access.", {})
        if not isinstance(proof["model"], str) or not isinstance(proof["effort"], (str, type(None))):
            raise ConfigError("tier_routing evidence needs the observed model and effort, not an inferred pair.", {})
        for kind, statuses in (("launch", {"supported", "unsupported", "unknown"}),
                               ("access", {"accessible", "unavailable", "unknown"})):
            fact = proof[kind]
            fields = {"status", "ref", "checked_at"} | ({"window_group"} if kind == "access" else set())
            if (not isinstance(fact, dict) or set(fact) != fields
                    or not isinstance(fact["status"], str) or fact["status"] not in statuses
                    or not isinstance(fact["ref"], str) or not fact["ref"].strip()
                    or (kind == "access" and not isinstance(fact["window_group"], str))):
                raise ConfigError("tier_routing {} fact needs status, a non-secret evidence ref, checked_at{}.".format(
                    kind, " and window_group" if kind == "access" else ""),
                                  {"row": name, "fact": kind})
            try:
                timestamp(fact["checked_at"], "Tier-routing fact")
            except UsageError as exc:
                raise ConfigError(exc.message, {"row": name, "fact": kind}) from None
    return copy.deepcopy(value)


def _fresh(value, at, age):
    if value is None or at is None:
        return False
    try:
        delta = timestamp(at, "Tier-routing checkpoint") - timestamp(value, "Tier-routing fact")
    except UsageError:
        return False
    return timedelta(0) <= delta < age


def _fact(proof, kind, row, agent, at):
    fact = proof.get(kind) if isinstance(proof, dict) else None
    status = fact.get("status", "unknown") if isinstance(fact, dict) else "unknown"
    if proof and (proof.get("model"), proof.get("effort")) != (row["model"], row.get("effort")):
        status = "pair_mismatch"
    elif kind == "access" and fact and fact["window_group"] != agent.window_group:
        status = "account_mismatch"
    elif fact and not _fresh(fact["checked_at"], at, FACT_MAX_AGE):
        status = "stale"
    return {"status": status, "evidence": copy.deepcopy(fact)}


def decide(agent, role, tier, needs, table, headroom, measured_at, at, capacity_group=None, worker_kind=None):
    """Return the selected tier plus audit facts; judgment and explicit pins stay fixed."""
    policy = agent.tier_routing
    if policy is None:
        return tier, None
    mode = policy["mode"]
    override = "operator_pin" if mode == "pinned" else None
    if tier["round"] in JUDGMENT_ROUNDS or tier["tier_row"] in JUDGMENT_ROUNDS:
        override = "judgment_floor"
    elif tier["de_escalated"] or (tier["model"], tier.get("effort")) != (
            agent.tiers[tier["tier_row"]]["model"], agent.tiers[tier["tier_row"]].get("effort")):
        override = "risk_escalation"
    capacity_status = "unknown"
    if headroom is not None:
        capacity_status = "available" if headroom > 0 else "exhausted"
        if not _fresh(measured_at, at, CAPACITY_MAX_AGE):
            capacity_status = "stale"
        elif capacity_group != agent.window_group:
            capacity_status = "account_unknown"
    capacity = {"status": capacity_status, "remaining_pct": headroom,
                "measured_at": measured_at, "window_group": capacity_group}
    allowed = ROLE_ROUNDS.get(canonical_role(role), frozenset())
    candidates = []
    for name, row in sorted(agent.tiers.items()):
        proof = policy["evidence"].get(name)
        launch = _fact(proof, "launch", row, agent, at)
        access = _fact(proof, "access", row, agent, at)
        sources = capabilities.evidence(table, row["model"], row.get("effort"), needs)
        try:
            qualification = capabilities.assess(table, row["model"], row.get("effort"), needs)
        except capabilities.InadequateCapability:
            qualification = "inadequate"
        if qualification == "adequate" and not all(
                _fresh(source["dated"] + "T00:00:00Z", at, FACT_MAX_AGE) for source in sources):
            qualification = "stale"
        placement = successors.placement(table, agent, role, tier["round"], name, row, needs, at, worker_kind) if at else None
        inherited = placement is not None and placement["status"] == "provisional" and override is None
        rejected = []
        if name not in allowed or name in JUDGMENT_ROUNDS:
            rejected.append("role_ineligible")
        for kind, status, positive in (("launch", launch["status"], "supported"),
                                       ("access", access["status"], "accessible"),
                                       ("capacity", capacity_status, "available")):
            if status != positive:
                rejected.append(kind + "_" + status)
        if qualification != "adequate" and not inherited:
            rejected.append("qualification_" + qualification)
        if placement and placement["status"] in {"withdrawn", "retired", "inadequate", "future"}:
            rejected.append("placement_" + placement["status"])
        candidates.append({"tier_row": name, "model": row["model"], "effort": row.get("effort"),
                           "resource_weight": effective_multiplier(row), "launch": launch, "access": access,
                           "qualification": {"status": qualification, "sources": sources},
                           "placement": placement,
                           "capacity": capacity, "rejected": rejected})
    chosen = tier
    if mode == "minimum_adequate" and override is None:
        eligible = [row for row in candidates if not row["rejected"]]
        if not eligible:
            raise UsageError("No qualified, accessible supported pair has current capacity for {} on {}; refresh only the cited candidate facts or explicitly pin the configured row.".format(role, agent.name),
                             {"role": role, "agent": agent.name, "routing_candidates": candidates})
        selected = min(eligible, key=lambda row: (row["resource_weight"], EFFORT_ORDER[row["effort"]], row["model"], row["tier_row"]))
        chosen = {**agent.tiers[selected["tier_row"]], "tier_row": selected["tier_row"],
                  **{key: tier[key] for key in ("round", "kind", "pressure_headroom", "de_escalated")}}
        chosen.update(billing_window=billing_window(chosen), effective_multiplier=effective_multiplier(chosen))
    return chosen, {"mode": mode, "override": override, "ordering": "declared_resource_weight_then_effort",
                    "monetary_cost": "unknown", "candidates": candidates}
