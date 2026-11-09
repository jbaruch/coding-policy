# Central Fleet Publication

## Enrollment and credentials

The central owner is `jbaruch/coding-policy`. Its scheduled controller discovers
the regular file `.github/fleet-publish-enabled` on an active same-owner
consumer's default branch. The marker's complete supported value is:

```json
{"schema_version":1,"plugin_name":"jbaruch/<plugin>"}
```

Enroll through a reviewed consumer PR. Keep the Tessl manifest, declared
rules/skills, `.tesslignore` and changelog controls. Remove the consumer's
competing Tessl publication workflow in that same PR before enabling the marker.
Do not enroll a mixed-channel publisher in this Tessl-only mode without its
separate channel's reviewed ownership and confirmation flow. This central
repository keeps its existing dual publisher and is not its own consumer.

All publishing credentials remain in the central repository. Reuse its existing
`TESSL_TOKEN`, `FLEET_APP_ID` and `FLEET_APP_PRIVATE_KEY`; never copy them into a
consumer or introduce a consumer dispatch token. `FLEET_PUBLISHER_ENABLED` is a
central Actions variable. Source and workflows may be reviewed while it remains
false. Activation requires the real App installation to permit the workflow's
requested scopes; installation on all repositories alone does not prove those
permissions. Permission minting refuses when a requested grant is unavailable.
Run the reviewed `fleet-publish-grant-probe.yml` manual workflow on the central
default branch before activation. Its fixed repository and metadata-only
contract live in `scripts/fleet_publish_grant_probe.py`. Require the native mint
and whole probe run to succeed, including the action's token-revocation step;
its diagnostic output alone proves no protected write or publication.

Central workflow credentials also own the durable publication ledger. Grant
central Contents write only to the jobs that create its fixed data refs; keep
consumer access in the selected App credential. This grants no central default
branch, workflow, source, permission or consumer-path mutation authority.

The central workflow never checks out or executes consumer code. The projection
owner, `scripts/fleet_publish.py`, copies bounded regular declared package data
and applies central lint, mandatory skill review, changelog stamping, publication
and registry/moderation helpers. Consumer commands, workflows, hooks and symlinks
cannot supply controller code or credentials. A reusable workflow running in a
consumer's context is not this central execution mode.

## Bookkeeping Authority

Every condition is required:

1. A new attempt of the central workflow runs from the central default branch
   with its verified native workflow/source identity. A rerun is refused.
2. The supported enrollment marker and plugin identity are present in the
   consumer's native default history. The selected source is a verified merged
   same-repository PR; independent review and CI gates remain satisfied.
3. The controller owns the publication queue and verifies prior claims against
   native central evidence. A source-controlled claim alone authorizes no
   publication, retry, bookkeeping exclusion or success.
4. The only changed consumer paths are `.github/fleet-publish-source.json`,
   `.tessl-plugin/plugin.json`'s version bookkeeping and `CHANGELOG.md`'s selected
   source stamp. Preserve newer source, manifest metadata and changelog entries.
5. A native Git ref compare-and-swap must succeed before the immutable intent is
   uploaded and read back, and before publication. A competing write or failed
   upload retains the claim as a stop record; never retry the effect implicitly.
   Persist and read back the central source claim before an effect. The original
   source claim remains fixed across moderation recoveries.
6. Wait for the claimed bookkeeping commit's required CI before publication.
   Missing, pending or failing requirements retain the claim and refuse the
   effect. The controller owns the bounded wait and its refusal contract.
7. The claim carries no source, test or workflow changes. It bypasses no required
   review, skill score, CI, registry or moderation gate. It supplies no authority
   to edit other paths or change repository/App permissions.

The controller's entry points and refusal contracts live in
`scripts/fleet_publish.py`, `scripts/fleet_publish_targets.py` and
`scripts/fleet_publish_workflow.py`. Its queue consumes each merged source
separately. It does not coalesce rapid merges into the latest tip.

## Record owner and retention

`skills/release/fleet_publish_record.py` owns schema 1 and its complete field
validation. The central publisher writes the publication records; the central
resolver and confirmation helper read them without migration. The native
artifacts remain `fleet-publish-intent-v1` with only `intent.json`, and
`fleet-publish-receipt-v1` with only `receipt.json`. Uploads are immutable within
the original run. The consumer's claim contains the intent shape; its native
bookkeeping commit remains independently bound.

`skills/release/fleet_publish_archive.py` owns the separate versioned archive
schema, canonical representation and read contract. The source-owned writer,
`scripts/fleet_publish_archive_write.py`, creates central Git records only in
`refs/tags/fleet-publish-v1/`. This namespace is a durable authority of record,
owned by the central publisher and its fixed completion finalizer. Never update,
replace or delete a record to resume a queue. Existing identical records permit
verified reuse; conflicts refuse. Consumer stamps and Git author or timestamp
fields cannot supply this authority.

Before publication, the writer records the original source claim and run intent.
After the publisher run finishes, `fleet-publish-finalize.yml` verifies its native
run, attempt, protocol and available records, then archives the actual outcome.
It never executes consumer or triggering-run code, invokes publication, or
converts a failed or uncertain outcome into success. A moderation recovery gets
its own run record and retains the original source claim.

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
and repair evidence through the owning controller.

A moderation recovery is a new central run bound to its original successful
publication. It writes its own claim/bookkeeping and performs registry and
moderation reads only. It must not publish again. A failed publish exit or
`landed_after_error` remains unconfirmed even when the exact version exists.

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

Historical proof creation supports public and private enrolled consumers through
an App read token scoped to the one repository derived from the original durable
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
or receipt fields. The controller and scheduled poll use that boundary only for
confirmed prior-source continuity. Their ordinary next-source, ancestry,
review/CI, registry baseline, version and effect-CAS gates remain required.
Native moderation recovery still requires its actual original receipt.

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

Publish/install the compatible archive, controller and confirmation readers
before any manual proof creation. Verify their exact installed source and both
current reviews/full CI. Bind the original proof and selected next-source
coordinates in the existing authorized recovery task before effects.

The manual branch requires the existing `FLEET_PUBLISHER_ENABLED` flag to be
disabled. That flag alone does not prove quiescence: the existing owner must
observe all previously admitted central poll/publication runs reach their native
terminal states before proof creation. The fixed finalizer reconciles native
total/completed observations with complete bounded active-run catalogs for both
fixed workflow IDs/paths, before proof creation or existing-proof lookup and
again after exact immutable readback. Only the exact current native manual
attester may be active. Direct native original-run and current-attester fences
retain their exact identities and attempts; initial creation still requires
available original native evidence. Missing, ambiguous, changed or over-bound
observations refuse attestation, including an existing-proof no-op.

This is a stable-observation completeness check, not atomic metadata across
requests. The operator keeps mutation admission disabled and preserves run
metadata through readback. Completed-history growth does not drive pagination,
and status-filter lists alone never prove completeness. See
`scripts/fleet_publish_archive_write.py` — `historical_quiescence` for the native
observation contract, and `scripts/fleet_publish.py` — `GitHub.pages` for active
catalog bounds. There is no timestamp cutoff, new variable-read grant or
per-attempt crawl. Existing provider admission gates the disabled flag in the
pinned workflow; the operator must keep it disabled through one explicit
attestation and exact immutable readback. Resume only the selected
normal source flow under the existing owner and all ordinary gates. Prepare
these steps together before execution; an unknown effect outcome authorizes no
retry. This adds no credential, grant, approval store or user permission flow.

## Source-bound selection and confirmation

Capture the pre-merge registry baseline through the existing canonical helper.
After the merge, use the actual consumer merge SHA, not the central workflow's
`github.sha`. Run the central resolver instead of the consumer-push resolver:

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/release/; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/release/ ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
python3 "$KS/fleet_publish_wait.py" <consumer-repository> <merge-sha>
```

Exit 0 emits the source-bound selection envelope, including `database_id`,
`confirmed` and the supported record. Nonzero emits no selection and a redacted
diagnostic. See `skills/release/fleet_publish_resolver.py` for its CLI, evidence
and refusal contract. A missing intent is not a run id to guess. The wait helper owns enqueue polling and retries only supported pending states.
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

For moderation recovery, resolve the selected new run explicitly with `--run-id`
and retain the original source/baseline. Never rerun a successful publication or
dispatch another publication to recover moderation. The resolver follows and
verifies the original publication rather than trusting a nested origin claim.
