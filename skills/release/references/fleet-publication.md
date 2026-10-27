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
6. The claim carries no source, test or workflow changes. It bypasses no required
   review, skill score, CI, registry or moderation gate. It supplies no authority
   to edit other paths or change repository/App permissions.

The controller's entry points and refusal contracts live in
`scripts/fleet_publish.py`, `scripts/fleet_publish_targets.py` and
`scripts/fleet_publish_workflow.py`. Its queue consumes each merged source
separately. It does not coalesce rapid merges into the latest tip.

## Record owner and retention

`skills/release/fleet_publish_record.py` owns schema 1 and its complete field
validation. The central publisher is the sole writer. The central resolver and
confirmation helper are readers; they never migrate records. Both native
artifacts use the schema: `fleet-publish-intent-v1` contains only `intent.json`,
and `fleet-publish-receipt-v1` contains only `receipt.json`. Uploads are immutable
within the original run, with overwrite disabled. The consumer's source claim
uses the original claimed intent shape; its bookkeeping commit is independently
bound through the terminal receipt and native history.

These artifacts are gate stores under `rules/stateful-artifacts.md`. Unsupported,
expired, ambiguous, missing or unreadable evidence refuses publication and
confirmation; it never means no previous effect. Never remove a claim to resume
a queue. A claim remains a stop record even when artifact retention has expired.
Preserve the native original run/source/version and repair the evidence through
the owning controller before any further effect.

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
