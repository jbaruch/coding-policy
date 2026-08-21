---
alwaysApply: true
---

# CI Safety

## Hands Off CI Config

- **Never smuggle CI configuration changes** (workflow files, pipeline configs) into a PR whose stated scope is something else
- A PR whose title and body explicitly scope the work as a CI change **is** the approval artifact; this rule forbids unannounced edits, not CI-scope PRs
- For unplanned CI edits discovered mid-task, stop and ask before touching the workflow files

## Never Skip Tests

- Never add `[skip ci]` to commit messages
- Never disable or skip failing tests to unblock a merge
- If tests fail, fix the tests or fix the code

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

## Publish-Pipeline Loop-Prevention Carve-Out

- Narrow exception for `[skip ci]` on a commit the publish workflow pushes to the protected branch
- Applies when that commit would otherwise re-trigger the same publish workflow (infinite publish loop)
- Preconditions (all required):
  1. Commit is authored by the CI bot inside the publish workflow — never a human- or agent-authored PR commit
  2. Sole purpose is the workflow's own release bookkeeping — manifest version bump, CHANGELOG version stamp — carrying no source or test changes that need CI validation
  3. `[skip ci]` rides only on the commit pushed back to the protected branch, solely to stop self-retrigger
- Every other commit still follows the rule: no `[skip ci]`, never to skip failing tests or unblock a merge

## Bootstrap-Red Carve-Out

- Narrow exception for merging a PR with a failing required check whose failure is an explicit cache-binding or bootstrap guard, not a test assertion
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
- Every other failing check still blocks the merge: fix the tests or fix the code

## Install, Don't Skip

- If a test needs an external tool or dependency, install it in CI
- "It's hard to install" is not a reason to skip tests — figure out the installation

## Branch Naming

- Use the convention: `<type>/<description>` (e.g., `feat/add-auth`, `fix/null-pointer`, `chore/update-deps`)
- `<type>-<issue-number>` is an accepted alternative where the repo's existing branches already use it (e.g., `fix-111`)
- Keep branch names lowercase with hyphens
- Flag naming before a PR exists — merged branches are precedent, not violations

## Always Watch CI

- After every push, watch the CI run to completion — never assume it will pass
- Use `gh run watch` or equivalent to monitor the run in real time
- If CI fails, inspect the logs immediately, fix the issue, and push again
- A task is not done until CI is green
- Watch the event, not a stopwatch: bind the watch to the terminal signal it awaits — a run's `conclusion`, a review verdict posted, a moderation `pass` — never to an agent-chosen elapsed time
- Poll interval and give-up budget are script-owned constants (the `*_INTERVAL_SEC` / `*_BUDGET_SEC` env vars in the release watch scripts), never numbers an agent picks per run
- Never wrap a watch in an invented wall-clock `timeout` — no blanket minute count exists in this policy to cite; a watcher gives up only at its own documented budget
- Watch only the fields the gate reads. For PR reviews that is each gating bot's latest review state resolved by bot login, CI status, and merge state — not the appearance of inline comments, and not a hand-picked run / comment / check id
- A bot review is complete when its verdict posts (state leaves `none`), zero inline comments included — never wait for comments to appear
- For a reviewer workflow, the run `conclusion` reports only that the workflow finished, never that the review happened
- A fail-open gate can report `success` having reviewed nothing
- Gate a reviewer workflow on its posted verdict, not the check's color
- Build and publish runs still gate on `conclusion`
- Do not promote a reviewer's check to a required branch-protection gate while a fail-open path exists
- The pre-merge review watch runs ONLY through `skills/release/watch-pr-reviews.sh` (see `skills/release/SKILL.md` Step 5) — never a hand-rolled poll loop
- `watch-pr-reviews.sh` is the sole correct resolver of the gate fields above
- The gating reviewer's bot login varies by repository — resolved inside `poll-pr-reviews.sh`
- A non-gating check that reds and surfaces as the watcher's `ci_failure` result is not a reason to hand-roll
- On a `ci_failure` result, read the returned snapshot and act on the check that actually failed
- The gating signal is not always a PR status check
- A run started out of band (a `workflow_dispatch` reseed, a manual job) may not surface in the PR's `statusCheckRollup`
- Identify the run that gates the outcome and bind the watch to its `conclusion`
- A failed PR check that no event re-triggers stays red until an explicit `gh run rerun --failed` once its cause is fixed
- For plugin/package releases, the duty extends past merge — confirm the resolved run's conclusion, the registry advance, and the moderation clear; no single signal is authoritative
- Release contract:
  1. Before merge: capture the registry's `Latest Version` as baseline
  2. After merge: resolve the publish run by merge-commit `headSha` + `push` event filter
  3. Watch the resolved run to terminal state
  4. Confirm the conjunction (all required, in this order):
     - The resolved run's `conclusion` is `success`
     - The registry's `Latest Version` advanced past the baseline
     - The published version's moderation state has cleared
  5. Wait for the moderation clear with exponential backoff before reporting the release confirmed
- See `skills/release/SKILL.md` Step 7 for the agent-executable form of the release contract
- Do not derive an expected version from the merge SHA's manifest
- Do not compare against a specific expected version
- Moderation gates `tessl install` after publish — a freshly published version can be install-blocked until its moderation state reaches `pass`
- The moderation wait uses exponential backoff to a bounded budget (see `skills/release/verify-moderation-cleared.sh`); a still-pending or blocked state at budget exhaustion is an unconfirmed release, surfaced as a failure, never reported as success
- A security finding is distinct from moderation: an advisory only suggests review, a blocking finding requires an override flag for `tessl install`
- If any conjunct fails, the publish is not confirmed — query the real moderation state, never invent one to hedge a failed publish
- Naively re-running a failed publish can create an extra release when the workflow includes a version-bump step (e.g., `tesslio/patch-version-publish`) and the run got past it. Safer recovery: a follow-up commit fires a fresh publish on merge

## Credits Never Block Publishing

- The tessl publish step never consumes credits
- The publish step never blocks on org credit state
- The artifact lands regardless of the credit balance
- Credits can still fail a review or eval step and turn the run red (see `rules/context-artifacts.md` Credit-Outage Review Carve-Out)
- A red run (credit-caused or not) is never proof that publishing was blocked
- Whether the artifact published is answered by the registry advance plus moderation `pass`, independent of the run's color
- A red run whose artifact landed means a step other than the publish failed, not a blocked publish
- Never blame a red publish run on credits without confirming the artifact landing and naming the failing step from the logs
- A publish that did not land is the agent's own diagnosis, never a deflection to `tessl credits`

## Checks Not Starting

- When pushed checks sit in `queued` and no `github-actions` run is created, check the PR's merge state before assuming an Actions outage
- Diagnose with `gh pr view <N> --json mergeable,mergeStateStatus` — `CONFLICTING` / `DIRTY` is the cause
- The tell: third-party check suites (Copilot, SonarQube, reviewers) sit `queued` while no `github-actions` suite is created
- Inspect suites with `gh api repos/<owner>/<repo>/commits/<sha>/check-suites -q '.check_suites[] | "\(.app.slug) \(.status) \(.conclusion)"'`
- Fix: merge the base into the PR branch (or rebase the branch onto the base), resolve conflicts, push — the `github-actions` suite runs and `mergeStateStatus` flips to `UNSTABLE` / `CLEAN`

## Protected Branches

- Don't push directly to `main` or `master` (except under the Content-Only Direct-Push Carve-Out below)
- All changes go through pull requests (same exception applies)

## Content-Only Direct-Push Carve-Out

- Narrow exception for content-only edits within an explicit, narrowly scoped path-glob set
- Applies when the edited paths are prose / data artifacts a human audience reads directly — not code, not context artifacts an agent loads (rules, skills, scripts, manifests, workflow files, configuration)
- The push may go directly to `main` or `master` without a PR review cycle
- Preconditions (each consuming repo, all required):
  1. Repo documents an authority-of-record rule in its own plugin naming the carve-out — the exact path globs, why those paths qualify as content not code/context, and what policy review the direct-push does NOT carry
  2. Carve-out scopes to one or more named path globs — never a broad wildcard like `**/*.md`. Globs that would match `rules/**`, `skills/**`, workflow files (`*.yml` or `*.yaml`), `.tessl-plugin/plugin.json`, `package.json`, or any executable/loaded artifact (regardless of extension) are mis-scoped
  3. Push-time enforcement keeps any out-of-glob change from landing on the protected branch via direct push (allowlist semantics), satisfied by form A or form B. Post-push CI checks satisfy neither
     - Form A — server-side gate: a GitHub push ruleset with path restriction, a pre-receive hook, or an equivalent server-side gate rejects the ref update
     - Form B — client-side content-only diff gate: permitted only where the platform cannot express server-side allowlist enforcement (e.g., github.com personal repos)
       - The publishing tool runs the gate as a deterministic script (per `rules/script-delegation.md`), not agent judgment
       - The gate enumerates the paths the push would change on the protected branch and direct-pushes only when every one matches a declared content glob
       - Any out-of-glob path forces an automatic branch + PR fallback — never an operator-say-so override
       - The authority-of-record rule (precondition 1) names the gate script
- Every other branch / path in the repo still goes through pull requests
