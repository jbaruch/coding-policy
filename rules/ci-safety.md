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

- Narrow exception for dismissing a gating bot's `CHANGES_REQUESTED` that the same bot later superseded with an all-clear re-review
- Applies when the bot cannot `APPROVE` and its stale request keeps the merge `BLOCKED`
- Preconditions are binding: read `skills/release/references/ci-safety-carve-outs.md` Superseded-Bot-Review Dismissal Carve-Out before relying on it
- Every other dismissal still gates

## Judge-Ruled-Review Dismissal Carve-Out

- Narrow exception for dismissing the policy reviewer's latest `CHANGES_REQUESTED` on the PR head
- Applies when a weighing ruling rules every blocking finding in that review `defer` or `decline` under `rules/review-severity.md` Judge-Weighed Finding Carve-Out
- Preconditions are binding: read `skills/release/references/ci-safety-carve-outs.md` Judge-Ruled-Review Dismissal Carve-Out before relying on it
- Every other policy-review `CHANGES_REQUESTED` blocks the merge until resolved through review

## Publish-Pipeline Loop-Prevention Carve-Out

- Narrow exception for `[skip ci]` on a commit the publish workflow pushes to the protected branch
- Applies when that commit would otherwise re-trigger the same publish workflow
- Preconditions are binding: read `skills/release/references/ci-safety-carve-outs.md` Publish-Pipeline Loop-Prevention Carve-Out before relying on it
- Every other commit still follows the rule: no `[skip ci]`, never to skip failing tests or unblock a merge

## Bootstrap-Red Carve-Out

- Narrow exception for merging a PR with a failing required check whose failure is an explicit cache-binding or bootstrap guard, not a test assertion
- Applies when the PR changes a key the CI cache is bound to AND the rebuilt cache can only be seeded from the default branch after merge
- Preconditions are binding: read `skills/release/references/ci-safety-carve-outs.md` Bootstrap-Red Carve-Out before relying on it
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
- The pre-merge watch belongs to the merge decision; a stage that does not merge reads a snapshot instead
- Pre-merge watch mechanics are binding: read `skills/release/references/release-contract.md` Pre-Merge Watch Mechanics before watching or reading a PR's reviews
- For plugin/package releases, the duty extends past merge to each publication
- Publication confirmation is binding: read `skills/release/references/release-contract.md` Publication Confirmation before reporting a release confirmed

## Publish Outcomes

- A red publish run is never proof that publishing was blocked
- A non-zero publish exit is never proof nothing published
- Diagnosis is binding: read `skills/release/references/release-contract.md` Credits Never Block Publishing and A Non-Zero Publish Exit Is Not Proof Nothing Published before diagnosing a publish run

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
- Applies when the edited paths are prose or data artifacts a human audience reads directly, not code and not context artifacts an agent loads
- Preconditions are binding: read `skills/release/references/ci-safety-carve-outs.md` Content-Only Direct-Push Carve-Out before relying on it
- Every other branch / path in the repo still goes through pull requests
