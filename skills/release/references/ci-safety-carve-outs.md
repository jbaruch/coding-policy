# CI Safety Carve-Outs

The full preconditions of each `rules/ci-safety.md` carve-out, moved out of the always-loaded rule (#642). They bind as rule content: the rule keeps each carve-out's trigger line and requires reading its section here before relying on it. Each section carries only the moved detail; the trigger, applies-when and reset lines stay in the rule.

## Superseded-Bot-Review Dismissal Carve-Out

- Narrow exception for dismissing a review gate.
- Not a bypass when the gate is a bot's `CHANGES_REQUESTED` that the same bot later superseded with an all-clear re-review
- Applies when a gating bot that cannot `APPROVE` (`github-actions[bot]` — GitHub returns HTTP 422) re-reviews clean but cannot post the `APPROVED` verdict that would supersede its earlier `CHANGES_REQUESTED` — the stale request keeps the merge `BLOCKED` until dismissed
- Preconditions (all required):
  1. The dismissed review is a `CHANGES_REQUESTED` from a gating bot on the allowlist (`GATING_BOTS` in `skills/release/dismiss-stale-reviews.sh`), never a human reviewer — a human can `APPROVE`, so a human's supersession goes through re-request-and-approve, never dismissal
  2. The same bot posted a later all-clear on the PR — the accepting verdict states are the script's decision predicate (`dismiss-stale-reviews.sh` header), not restated here; a `DISMISSED` or `PENDING` latest state is not an all-clear
- Deterministic form is `skills/release/dismiss-stale-reviews.sh` — it enforces both preconditions and is the recommended path; decision predicate and allowlisted bot logins live in the script header, not restated here (`rules/script-as-black-box.md`)
- A hand dismissal meeting both preconditions is equally sanctioned — merging after it is not a `Never Skip Tests` violation; the gate was satisfied and cleaned up, not skipped
- Every other dismissal still gates: a bot `CHANGES_REQUESTED` no all-clear superseded, or any human reviewer's change request, blocks the merge until resolved through review

## Judge-Ruled-Review Dismissal Carve-Out

- Preconditions (all required):
  1. The dismissal runs through `skills/release/dismiss-ruled-review.sh` — coverage predicate and rule-id floors in its header
  2. No check on the head is failing
  3. The script posted the ruled findings to the task's follow-up issue before dismissing
  4. The dismissal message carries the script's `JUDGE-RULED:` marker
- A hand dismissal of a gating policy review is not sanctioned
- A hand dismissal carrying the marker violates this carve-out
- The release scripts trust the marker and never detect a hand-written one
- After a ruled dismissal, `skills/release/dismiss-stale-reviews.sh` sweeps the same policy identity's earlier `CHANGES_REQUESTED` reviews, the fleet reviewer included
- Merging after a ruled dismissal is not a `Never Skip Tests` violation

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
- Reviewers treat a PR as mergeable when it matches both Applies-when criteria and meets all three pre-merge gates — do not request changes on the red check alone
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
