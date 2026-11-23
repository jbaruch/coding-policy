# Historical Central Fleet Publication

## Retirement boundary

The central fleet publisher is retired. No enrollment, scheduled poll, publisher dispatch,
publication, new moderation-recovery run or consumer bookkeeping write is supported.
Use the consumer's reviewed publication workflow for new releases.

Before rollout, disable new publisher admission and reconcile previously admitted
runs with their native outcomes and completion archives. Preserve existing
claims, receipts and evidence tags. Remove only separate publisher-only
repository-write or bypass grants; keep shared fleet reviewer App permissions
unchanged.

The historical resolver, archive, proof and confirmation interfaces remain
supported for existing publications. `fleet-publish-finalize.yml` retains
completion archival for previously admitted runs and explicit later source
proof creation. Neither path publishes or changes consumer history.

The finalizer reuses `FLEET_APP_ID` and `FLEET_APP_PRIVATE_KEY` only for selected
consumer reads, `TESSL_TOKEN` for registry/install/pack reads and the central
workflow credential for create-only evidence tags. `scripts/fleet_publish.py`
retains the Git transport, historical evidence validation and inert package
transforms used by that finalizer and its package verifier.

## Record owner and retention

`skills/release/fleet_publish_record.py` owns schema 1 and its complete field
validation. The retired central publisher wrote the publication records; the
central resolver and confirmation helper read them without migration. The native
artifacts remain `fleet-publish-intent-v1` with only `intent.json`, and
`fleet-publish-receipt-v1` with only `receipt.json`. Uploads are immutable within
the original run. The consumer's claim contains the intent shape; its native
bookkeeping commit remains independently bound.

`skills/release/fleet_publish_archive.py` owns the separate versioned archive
schema, canonical representation and read contract. The source-owned writer,
`scripts/fleet_publish_archive_write.py`, creates central Git records only in
`refs/tags/fleet-publish-v1/`. This namespace is a durable authority of record,
owned by the retired central publisher and its fixed completion finalizer.
Never update, replace or delete a record. Existing identical records permit
verified reuse; conflicts refuse. Consumer stamps and Git author or timestamp
fields cannot supply this authority.

Existing source claims and run intents remain immutable.
After a previously admitted publisher run finishes, `fleet-publish-finalize.yml`
verifies its native run, attempt, protocol and available records, then archives
the actual outcome.
It never executes consumer or triggering-run code, invokes publication, or
converts a failed or uncertain outcome into success. Historical
moderation-recovery records retain their original source claim.

The completion finalizer may be retried to repair interrupted archival while
native evidence remains available. This retries archival only. It validates and
reuses existing matching evidence without replacing its original provenance.
Unsupported reruns of the publisher itself create a durable per-run hold; expiry
of the later native run cannot remove that hold or revive a positive result.

These stores use the gate-store exception in `rules/stateful-artifacts.md`.
Unsupported, ambiguous, malformed, conflicting or unreadable evidence refuses
publication and confirmation. Verify current central Git evidence through the
archive owner; compare available native evidence. A supported durable record may
retain verified historical facts after Actions retention removes artifacts or
run metadata. Missing completion evidence remains a hold, never permission to
repeat an effect. A missing native run alone proves no absence or success.
The archive owner defines accepted historical responses and refusal conditions.

A publisher, poller or finalizer run is a supported producer when its workflow
commit is on the central repository's main history
(`skills/release/fleet_publish_producer.py` `verify_workflow_protocol`). The
`main-append-only` repository ruleset keeps that history from being
force-pushed or deleted; review happens before merge. A historical-source proof
additionally requires the original run's executable graph to match one of the
frozen activated pre-0.8 pinsets (`historical_pinset`). Those sets never change,
and new producers add none. Readers never repair records or infer a new baseline
from registry agreement and a consumer stamp. Preserve native source/run/version
and repair evidence through the retained finalizer.

## Later historical source proof

The existing archive/finalizer owner also accepts the separately discriminated
`fleet-publish-historical-source-proof` schema-1 family. Its fixed create-only
ref is `refs/tags/fleet-publish-v1/runs/<original-run>/1/historical-source-proof`.
`skills/release/fleet_publish_historical.py` owns its full schema, supported
derivation, package inventories and native critical-step contract. The record
binds the original immutable intent/source claim, normalized native attempt and
jobs, the frozen historical executable pinset, complete official source-pack
and exact-version installed inventories, registry observation and actual later
attester workflow/run/attempt/source. It carries no reconstructed original
receipt, artifact digest, publication timestamp or original finalizer identity.
Its schema validator admits only complete normalized proofs that fit the existing
canonical archive encoding budget; see `skills/release/fleet_publish_archive.py`
— `_encoded_archive`. Inventories are never truncated to fit.

Historical proof creation supports public and private consumers through an App
read token scoped to the one repository derived from the original durable
intent. The existing finalizer's `select-consumer` phase validates that original
run and emits only its repository name to the native workflow output. It reads
central records only and grants no publication or ref-write authority. See
`scripts/fleet_publish_archive_write.py` — `historical_selection` and its CLI.

The authorized recovery uses an explicit manual dispatch of the EXISTING
`fleet-publish-finalize.yml`, with only `original_run_id` as input. The workflow
reuses `FLEET_APP_ID` and `FLEET_APP_PRIVATE_KEY`, requesting Contents and Pull
requests read on the derived consumer. It adds no installation permission or
operator-managed credential. Empty selection or failed mint holds; a central
repository token never substitutes for the App token on consumer reads. Central
ledger/run reads keep their separate central credential. Both transports admit
only their fixed repository routes. Normal completion archival remains unchanged.

The maintained pack/install observer lives in
`scripts/fleet_publish_historical_package.py`. It rebuilds the publisher's inert
projection, prior changelog overlay and version/stamp transform, bound to the
original native bookkeeping bytes. Any stamp date is observed artifact content,
not an inferred publication time. Missing or conflicting transform evidence holds.
Its commands use the existing central registry credential only for read/install/
pack; GitHub credentials are excluded. Consumer checkout/code execution,
publication, source stamping and prior-record mutation remain outside this branch.

A later proof cannot override a durable run hold, rerun, failed/uncertain native
receipt, conflicting terminal/native data or unsupported producer graph. The
writer revalidates fresh original native evidence before creating a proof.
Readers compare available native facts and retain positive stored facts after
retention removes native metadata. Unknown POST outcomes require exact readback;
existing identical evidence is read-only reuse, and a conflict remains held.
Never rerun publication to repair missing receipt bytes.

`skills/release/fleet_publish_evidence.py` owns the explicit prior-publication
union. Its native variant preserves the original receipt record; its historical
variant exposes derived common facts and later provenance, without native exit
or receipt fields.

The existing `resolve()` and four-argument confirmation CLI remain receipt-only
and schema 1. Maintained callers can explicitly select the typed
`resolve_evidence()` path. Historical CLI confirmation requires the optional
`--historical-source-proof` fifth argument; successful historical confirmation
is schema 2 with `proof_kind: historical_source_proof`, original run identity
and the actual later `attester`. It runs the same fresh exact-version registry
and moderation gates. A caller must accept both native schema 1 and this explicit
schema 2 before opting in. Unsupported older callers keep their original HOLD.
Historical confirmation uses its validated durable native-success fact and fresh
canonical latest-version, exact-version and moderation reads. It does not require
an expired original Actions run to become readable again. Native confirmation
keeps its live-run gate.
`fleet_publish_wait.py` still selects the exact associated source run and grants
no dispatch or retry from an unknown historical claim.

Install compatible archive and confirmation readers before manual proof
creation. The finalizer observes all previously admitted central runs reaching
their native terminal states before attestation. It reconciles native totals
with complete bounded active-run catalogs, then repeats the observation after
exact immutable readback. Only the current native manual attester may be active.
Missing, ambiguous, changed or over-bound observations refuse attestation,
including an existing-proof no-op. Retain original run metadata through readback.

This is a stable-observation completeness check, not atomic metadata across
requests. The operator keeps mutation admission disabled and preserves run
metadata through readback. Completed-history growth does not drive pagination,
and status-filter lists alone never prove completeness. See
`scripts/fleet_publish_archive_write.py` — `historical_quiescence` for the native
observation contract, and `scripts/fleet_publish.py` — `GitHub.pages` for active
catalog bounds. There is no timestamp cutoff, new variable-read grant or
per-attempt crawl. The finalizer addresses the retired publisher by the numeric
workflow ID bound to the original native run. Resolver identity comes from the
verified native or archived attempt. Neither requires the deleted publisher's
filename lookup to succeed. The finalizer must remain active. No retired
admission variable is required. Never restore dispatch or publication to repair
historical evidence.

## Source-bound selection and confirmation

Use the original captured registry baseline and consumer merge SHA for an
existing central publication, separately from its workflow commit. Resolve its
historical run with the retained read-only helper:

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/release/; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/release/ ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
python3 "$KS/fleet_publish_wait.py" <consumer-repository> <merge-sha>
```

Exit 0 emits the source-bound selection envelope, including `database_id`,
`confirmed` and the supported record. Nonzero emits no selection and a redacted
diagnostic. See `skills/release/fleet_publish_resolver.py` for its CLI, evidence
and refusal contract. A missing intent is not a run id to guess. The wait helper
retains bounded polling for supported pending evidence; it cannot enqueue work.
`fleet_publish_resolver.py` exposes the same selection as a single read-only
snapshot; see the helper docstrings for inputs, outputs and refusal contracts.

Watch the selected run in its real repository:

```bash
gh run watch <central-run-id> --repo jbaruch/coding-policy
```

Confirm using the captured baseline and same consumer/source/run identity:

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/release/; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/release/ ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
python3 "$KS/fleet_publish_confirm.py" <consumer-repository> <merge-sha> <pre> <central-run-id>
```

Exit 0 emits JSON with `confirmed:true`, the exact published `version`, source
and native run identity. Exit 1 is a definitive failed channel gate; exit 2 is
unavailable or unsupported evidence. Neither failure emits a version. The helper
retains all three Tessl conjuncts and checks the exact receipt version against
fresh registry evidence; see its docstring. Do not apply the legacy latest-only
confirmation to an unverified central run.

An existing historical moderation-recovery run may be selected explicitly with
`--run-id`, retaining the original source/baseline. The resolver verifies its
original publication. Never rerun or dispatch publication to recover moderation.
