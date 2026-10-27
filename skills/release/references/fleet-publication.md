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

Only producer versions that write the durable source claim before effects may
supply the corresponding absence proof. Verify the coherent protocol sources
named by `skills/release/fleet_publish_producer.py`; older unactivated protocols
remain unsupported. Readers never repair records or infer a new baseline from
registry agreement and a consumer stamp. Preserve native source/run/version and
repair evidence through the owning controller.

The current local-owner proof supports only the explicit 19-path protocol set.
Before upgrading an owner or executable-pin renewal with activated history,
require a reviewed historical-source proof strategy and regressions over the
previously activated intents and terminal records. Unknown historical owners
remain fail-closed; no automatic backward-compatibility fallback exists. This
maintenance obligation adds no activation prerequisite for the current set.

A moderation recovery is a new central run bound to its original successful
publication. It writes its own claim/bookkeeping and performs registry and
moderation reads only. It must not publish again. A failed publish exit or
`landed_after_error` remains unconfirmed even when the exact version exists.

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
