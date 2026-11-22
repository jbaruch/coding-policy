# CI Safety Carve-Outs

The full preconditions of each `rules/ci-safety.md` carve-out, moved out of the always-loaded rule (#642). They bind as rule content: the rule keeps each carve-out's trigger line and requires reading its section here before relying on it. Each section carries only the moved detail; the trigger, applies-when and reset lines stay in the rule.

## Superseded-Bot-Review Dismissal Carve-Out

- Narrow exception for dismissing superseded legacy bot review gates.
- coding-policy and consumer policy Apps approve natively; their ordinary change requests are never dismissed by this carve-out.
- Preconditions (all required):
  1. Run `skills/release/dismiss-stale-reviews.sh` with its existing owner, repository and PR arguments. No hand dismissal is authorized.
  2. The request belongs to a legacy bot on the script's `GATING_BOTS` allowlist, never a human or an approving App. On `jbaruch/coding-policy`, only the legacy workflow identity is eligible for ordinary dismissal; external callers retain the existing same-bot contract.
  3. The same bot posted a later all-clear under the script's decision contract, or the coding-policy identity migration below applies. An unmarked dismissal or pending review is never an all-clear.
- Identity migration applies only to an OPEN PR in `jbaruch/coding-policy` whose latest `github-actions[bot]` review requests changes. The latest `coding-policy-fleet-reviewer[bot]` review must be APPROVED on the current PR head and submitted later than that legacy review. Only the legacy identity's requests may be dismissed; an App comment, ruled dismissal, older-head approval or quality approval cannot authorize migration.
- The helper preserves its argument, JSON output, exit-code and API-failure contracts for declared external callers. Decision predicates and identity lists live in its header.
- After migration, use native App approvals for ordinary re-reviews. Retain the helper for open legacy gates, external callers and the ruled sweep below.
- Every other change request still gates until resolved through review or the ruled carve-out below.

## Judge-Ruled-Review Dismissal Carve-Out

- Preconditions (all required):
  1. The dismissal runs through `skills/release/dismiss-ruled-review.sh` — coverage predicate, authority lines and rule-id floors in its header
  2. The ruling is an operator ruling quoting the operator's answer verbatim, or a judge's collected report whose sha256 matches the cited digest, limited to `decline` and `defer` on their stated grounds
  3. No check on the head is failing
  4. The script posted the ruled findings to the task's follow-up issue before dismissing
  5. The dismissal message carries the script's `JUDGE-RULED:` marker
- A hand dismissal of a gating policy review is not sanctioned
- A hand dismissal carrying the marker violates this carve-out
- The release scripts trust the marker and never detect a hand-written one
- After a ruled dismissal, `skills/release/dismiss-stale-reviews.sh` sweeps the same policy identity's earlier `CHANGES_REQUESTED` reviews, the fleet reviewer included
- The merge after a ruled dismissal complies with `Never Skip Tests`

## Publish-Pipeline Loop-Prevention Carve-Out

- Applies when that commit would otherwise re-trigger the same publish workflow (infinite publish loop)
- Preconditions (all required):
  1. Commit is authored by the CI bot inside the publish workflow — never a human- or agent-authored PR commit
  2. Sole purpose is the workflow's own release bookkeeping — manifest version bump, CHANGELOG version stamp — carrying no source or test changes that need CI validation
  3. `[skip ci]` rides only on the commit pushed back to the protected branch, solely to stop self-retrigger

## Bootstrap-Red Carve-Out

- Applies when the PR changes a key the CI cache is bound to (an interaction hash, a schema fingerprint) AND the rebuilt cache can only be seeded from the default branch after merge
- Pre-merge gates (all required):
  1. The failing check's output names the guard explicitly (e.g., `InteractionMismatchError`) and shows zero test assertions executed
  2. The consuming repo documents the merge-then-re-seed procedure in its own plugin or contributor docs
  3. The repo owner approves the merge explicitly — review approval or a recorded dismissal of the blocking review — with a recorded commitment to the post-merge obligations
- Post-merge obligations (both required):
  4. The re-seed runs immediately after merge
  5. The green re-seed result is verified and recorded on the PR or its tracking issue
- A PR matching both Applies-when criteria and all three pre-merge gates is mergeable, and reviewers judge it on those gates rather than on the red check alone
- Open post-merge obligations block the next use of this carve-out — complete and record them first

## Content-Only Direct-Push Carve-Out

- Applies when the edited paths are prose / data artifacts a human audience reads directly — not code, not context artifacts an agent loads (rules, skills, scripts, manifests, workflow files, configuration)
- The push may go directly to `main` or `master` without a PR review cycle
- Preconditions (each consuming repo, all required):
  1. Repo documents an authority-of-record rule in its own plugin naming the carve-out — the exact path globs, why those paths qualify as content not code/context, and what policy review the direct-push does NOT carry
  2. Carve-out scopes to one or more named path globs — never a broad wildcard like `**/*.md`. Globs that would match `rules/**`, `skills/**`, workflow files (`*.yml` or `*.yaml`), `.tessl-plugin/plugin.json`, `package.json`, or any executable/loaded artifact (regardless of extension) are mis-scoped
  3. Push-time enforcement keeps any out-of-glob change from landing on the protected branch via direct push (allowlist semantics), satisfied by form A or form B. Post-push CI checks satisfy neither
     - Form A — server-side gate: a GitHub push ruleset with path restriction, a pre-receive hook, or an equivalent server-side gate rejects the ref update
     - Form B — client-side content-only diff gate: permitted only where the platform cannot express server-side allowlist enforcement (e.g., github.com personal repos)
       - The publishing tool runs the gate as a deterministic script (per `rules/script-delegation.md`), never agent judgment and never a bounded classification
       - The gate enumerates the paths the push would change on the protected branch and direct-pushes only when every one matches a declared content glob
       - Any out-of-glob path forces an automatic branch + PR fallback — never an operator-say-so override
       - The authority-of-record rule (precondition 1) names the gate script
