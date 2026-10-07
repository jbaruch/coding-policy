"""Why each planned assignment got its model and effort, as plan data (#602).

`plan` writes one record per assignment under the plan's `selection` key. The
record explains a choice `select_tier`, `tier_routing` and the planner already
made; this record never changes it. A value the owner records cannot
establish is the literal `unknown`, never a guess:

- an untiered worker runs whatever model is already live, so its model, effort,
  round, capability evidence, cost and escalation are all `unknown`
- a tier row with no isolated billing evidence has an unknown cost
  (`billing.billing_window`), and a cheaper candidate is cheaper only on the
  declared multiplier the billing owner lets it keep

Pure: the capability table, the agent's configured rows and the round inputs
arrive as arguments.
"""

from . import capabilities
from .billing import UNKNOWN, billing_window, effective_multiplier
from .tiers import JUDGMENT_ROUNDS, ROLE_ROUNDS, TOP_MODELS, canonical_role, escalation_conditions

#: Selection record version, stamped on every record so a reader can tell the
#: shape it holds apart from the plan document's own version.
SELECTION_SCHEMA_VERSION = 2


def _verdict(table, model, effort, needs):
    """`adequate`, `unknown` or `inadequate` for one row, never raising."""
    try:
        return capabilities.assess(table, model, effort, needs)
    except capabilities.InadequateCapability:
        return "inadequate"


def cheaper_candidates(agent, role, tier, needs, table):
    """Every configured row this role can run that costs less than `tier`, with its verdict.

    Sorted by row name. `sources` cites the table entries behind the verdict;
    an `inadequate` or `unknown` row is why the cheaper row cannot stand in, and
    an `adequate` one explains the legacy configured-row route. Opted-in
    minimum routing's complete candidate facts are in `routing` (#695).
    """
    cost = tier["effective_multiplier"]
    allowed = ROLE_ROUNDS.get(canonical_role(role), frozenset())
    found = []
    for name, row in sorted(agent.tiers.items()):
        if name not in allowed:
            continue
        if (row["model"], row.get("effort")) == (tier["model"], tier.get("effort")) or effective_multiplier(row) >= cost:
            continue
        found.append({
            "tier_row": name, "model": row["model"], "effort": row.get("effort"),
            "effective_multiplier": effective_multiplier(row), "billing_window": billing_window(row),
            "verdict": _verdict(table, row["model"], row.get("effort"), needs),
            "sources": capabilities.evidence(table, row["model"], row.get("effort"), needs),
        })
    return found


def cheaper_adequate(agent, role, tier, needs, table):
    """The first cheaper row the table records adequate, in the plan-schema-10 shape, or None."""
    for row in cheaper_candidates(agent, role, tier, needs, table):
        if row["verdict"] == "adequate":
            return {key: row[key] for key in ("model", "effort", "tier_row", "sources")}
    return None


def _evidence(table, model, effort, needs):
    """One line per needed capability: the table's verdict and source, or `unknown` with no source."""
    rows = []
    for name in needs:
        entry = capabilities.lookup(table, model, effort or capabilities.DEFAULT_EFFORT, name)
        rows.append({"capability": name, "verdict": entry["verdict"] if entry else UNKNOWN,
                     "source": dict(entry["source"]) if entry else None})
    return rows


def _floor(role, tier):
    """What bars a cheaper row from this seat regardless of its verdict, or None."""
    if role == "judge":
        return "pinned_judge"
    if tier["tier_row"] in JUDGMENT_ROUNDS or tier["round"] in JUDGMENT_ROUNDS:
        return "judgment_round"
    return None


def _untiered(name, requirement):
    return {
        "schema_version": SELECTION_SCHEMA_VERSION, "agent": name, "tiered": False,
        "required_capabilities": {"model": UNKNOWN, "worker": requirement},
        "model": UNKNOWN, "effort": UNKNOWN, "round": UNKNOWN, "tier_row": UNKNOWN,
        "capability": UNKNOWN, "evidence": UNKNOWN,
        "cost": {"billing_window": UNKNOWN, "effective_multiplier": UNKNOWN, "known": False},
        "cheaper": {"floor": None, "candidates": UNKNOWN},
        "escalation": UNKNOWN,
        "routing": UNKNOWN,
    }


def records(assignments, tiers, agents_by_name, requirements, rounds, fix_round, table):
    """`{seat: record}` for every assignment in a plan.

    `tiers` is the plan's `tiers` map (absent or null per seat for an untiered
    worker), `requirements` its normalized specialist requirements, `rounds`
    its round inputs, and `table` the capability table the plan read.
    """
    out = {}
    for role, name in assignments.items():
        requirement = (requirements or {}).get(role)
        worker_needs = list(requirement["required_capabilities"]) if requirement else []
        tier = (tiers or {}).get(role)
        if tier is None:
            out[role] = _untiered(name, worker_needs)
            continue
        base = canonical_role(role)
        needs = capabilities.required(base, tier["round"], JUDGMENT_ROUNDS)
        agent = agents_by_name.get(name)
        window = tier.get("billing_window", UNKNOWN)
        floor = _floor(role, tier)
        if role == "judge" or agent is None:
            # The pinned judge has no rotating rows to compare, and its seat
            # has no substitute.
            candidates = []
        else:
            candidates = cheaper_candidates(agent, role, tier, needs, table)
            if floor == "judgment_round":
                # A judgment round runs the pinned top model at high or above
                # (`parse_tiers`), so no cheaper row can hold it whatever the
                # table says of that row.
                top = TOP_MODELS.get(agent.kind, frozenset())
                candidates = [dict(row, barred_by_floor=row["model"] not in top
                                   or row["effort"] not in {"high", "xhigh", "max"}) for row in candidates]
        context = ((rounds or {}).get(role) or {}).get("context") or {}
        out[role] = {
            "schema_version": SELECTION_SCHEMA_VERSION, "agent": name, "tiered": True,
            "required_capabilities": {"model": list(needs), "worker": worker_needs},
            "model": tier["model"], "effort": tier.get("effort"),
            "round": tier["round"], "tier_row": tier["tier_row"],
            "capability": tier.get("capability", UNKNOWN),
            "evidence": _evidence(table, tier["model"], tier.get("effort"), needs),
            "cost": {"billing_window": window, "effective_multiplier": tier.get("effective_multiplier", UNKNOWN),
                     "known": window != UNKNOWN},
            "cheaper": {"floor": floor, "candidates": candidates},
            "escalation": escalation_conditions(role, tier, context, fix_round),
            "routing": tier.get("routing", UNKNOWN),
        }
    return out
