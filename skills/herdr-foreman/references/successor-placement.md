# Provisional Successor Placement

## Upgrade route

Use the capability-maintenance owner for an authorized version replacement.
Do not start per-role qualification tests or request another operator approval.
Provider identity evidence is not operating adequacy. The existing maintenance
consultation reads the provider's explicit successor relationship and reports
its meaning. The owner checks identities, trusted hosts and quoted bytes; it
does not infer succession from a prefix or classify prose by keywords.

1. Before editing config, identify the exact configured worker kind,
   responsibility, requested round and predecessor row. Preserve effort and
   declared weight. Task authority and contributor exclusion stay separate.
2. Read the provider's catalog/publication. Verify exact predecessor and
   successor IDs, family, versions and the explicit same-family relationship.
   Cite one to three official sources, including any separate identity/alias
   catalog. A model self-report, matching prefixes, a weaker family or a mere
   listing of two models does not establish succession. Report retirement
   rather than inheritance. No architectural or judgment round is implied by
   this factual lookup; actual specialist triggers retain their normal route.
3. Prepare the report below. Run `capability-successor --record <report.json>`
   through SKILL.md Step 2's installed-plugin command. Config must still name
   the predecessor. The owner rereads official sources with bounded HTTPS
   reads, refuses absent quotes or mismatched identity, and snapshots the
   authorized row and original qualification, including missing/unknown facts.
   Explicit negative capability evidence prevents inheritance.
4. Replace only that row's model in operator-owned config. Keep effort and
   declared weight; clear predecessor billing evidence. Supply fresh exact-pair
   installed-CLI launch support and actual account access in
   `tier_routing.evidence`, then measure this account's capacity. A new no-effort
   successor uses explicit `effort: null` and matching supported launch proof;
   no hardcoded model allowlist update is needed. Inheritance supplies none of
   these facts.
5. Plan and apply normally. Selection exposes placement and unchanged
   qualification side by side. Apply rereads current owners before native
   actions. No different role, round, row, effort, worker kind or account can
   borrow the placement. Explicit pins, pinned judge, judgment floors and risk
   escalation do not opt into inheritance.

Report shape, with placeholders rather than live provider claims:

```json
{
  "id": "upgrade-identity",
  "worker": "configured-worker-kind",
  "role": "developer", "round": "build", "tier_row": "build",
  "successor": "exact-successor-id",
  "provenance": {
    "provider": "anthropic",
    "checked_at": "<actual timezone-bearing observation>",
    "relationship": "same_family_successor",
    "predecessor": {"model": "exact-predecessor-id", "family": "provider-family", "version": "old-version", "status": "active"},
    "successor": {"model": "exact-successor-id", "family": "provider-family", "version": "new-version", "status": "active"},
    "citations": [{"ref": "<official HTTPS publication/catalog URL>", "quote": "<verbatim relationship/identity evidence>"}]
  }
}
```

The report carries no qualification verdict or savings claim. The provider/
adapter and trusted-host inventory, read limits, field validation and refusal
contract belong to `skills/herdr-foreman/foreman/successors.py`. A new assignment gets a new ID;
an existing ID's origin cannot be rewritten.

## Maintenance checkpoint

`capability-check` exposes `successors_due` independently of the last table
refresh. Refreshing unrelated entries never postpones a placement's due date.
Preflight carries this detail under its existing capability check. SKILL.md
Step 2 records due maintenance and proceeds with unrelated delivery.

At the next maintenance checkpoint, the existing read-only capability
consultation revisits these placements using published evidence and actual
project outcomes already available. Check provider status and contrary
evidence. Record results through `capability-record`; inspect origins and
history with `capability-show`. Unknown results remain unknown; invent no
token/cost savings. No synchronous role-validation campaign is required.

Maintenance reports may contain `entries`, `recalibrations`, or both:

```json
{
  "recalibrations": [{
    "id": "upgrade-identity", "action": "keep", "verdict": "unknown",
    "source": {"kind": "project", "ref": "<actual recorded outcome/report>", "dated": "<actual YYYY-MM-DD reading>"},
    "provider_status": "active"
  }]
}
```

- `keep` retains provisional placement and records the evidence's outcome.
  An unknown outcome stays unknown, not a new measured qualification.
- `revise` confirms placement from substantive adequate successor capability
  entries covering its existing needs. Include those entries in the report
  when they are not already recorded. A launch smoke result cannot confirm
  adequacy. Confirmation grants no new responsibility, model, effort or
  capability and preserves the origin.
- `withdraw` records negative evidence, retirement or provider status that
  cannot be established. Withdrawal remains visible and vetoes this inherited
  placement even if later evidence marks the pair adequate. A keep cannot
  revive it; a new assignment requires new provenance and still-authorized
  predecessor config.

The owner appends dated history without rewriting origins. Current negative
capability evidence vetoes inheritance before maintenance too. Cadence alone
is never a seat veto. This is a due detector plus foreman maintenance
instruction, not an unattended recalibration scheduler. It creates no automatic
job or always-on service.

## Saved schema and readers

Artifact: `<selected-state>.capabilities.json`. Owner: `herdr-foreman`, through
`skills/herdr-foreman/foreman/capabilities.py` and
`skills/herdr-foreman/foreman/successors.py`. Capability-table schema 2
adds required `successors`, an array. Entry schema stays 1: upgrade does not
rewrite or restamp model/effort/capability evidence. Readers accept schemas
1/2; schema 1 has no placements and remains read-only. The next owner write
migrates its envelope to 2, preserving all entries. Missing/newer, unreadable,
malformed and symlink behavior remains the capability-table contract in
`skills/herdr-foreman/references/model-tiers.md`.

Writer: `capability-successor` creates a placement; `capability-record` appends
maintenance outcomes and updates only entries its report covers. Readers:
`capability-show`, `capability-check`, preflight, plan and apply. Neither writer
rewrites operator-owned config; no reader persists migration.

Each placement carries required `schema_version: 1`, all report fields above,
and these owner fields:

| Field | Meaning |
| ----- | ------- |
| `kind`, `window_group` | Exact adapter and account identity of the authorized spot |
| `authorized_row` | Original parsed row, excluding model-bound billing evidence |
| `needs` | Capability names of the responsibility and requested round |
| `provider_sha256` | SHA-256 of JSON-serialized normalized owner-read source bodies |
| `origin` | Original aggregate `verdict` and untouched `entries`; missing evidence stays missing |
| `assigned_at` | Owner-stamped timezone-bearing assignment timestamp |
| `history` | Append-only maintenance results, initially empty |

Each history record carries `schema_version: 1`, `recorded_at`, `id`, `action`,
`verdict`, `source`, `provider_status`. No field is backfilled with invented
evidence. The owner validates chronology and the originating exact pair.
Due dates derive from assignment or last maintenance checkpoint using the
existing capability interval.

Selection-record schema 3 in plan schemas 17/18 adds per-candidate `placement`:
null when none applies, otherwise `id`, `status`, `origin`, `provenance`,
`assigned_at`, `recalibration_due_at`, `due`, `history`. Qualification keeps
its own `status` and `sources`. Older explanatory records remain readable
without placement; no history is backfilled. Audit data is stripped from
durable dispatch tiers and never supplies launch proof.
