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

## Install, Don't Skip

- If a test needs an external tool or dependency, install it in CI
- "It's hard to install" is not a reason to skip tests — figure out the installation

## Branch Naming

- Use the convention: `<type>/<description>` (e.g., `feat/add-auth`, `fix/null-pointer`, `chore/update-deps`)
- Keep branch names lowercase with hyphens

## Always Watch CI

- After every push, watch the CI run to completion — never assume it will pass
- Use `gh run watch` or equivalent to monitor the run in real time
- If CI fails, inspect the logs immediately, fix the issue, and push again
- A task is not done until CI is green
- For plugin/tile/package releases, the duty extends past merge — confirm both the resolved run's conclusion and the registry advance; neither signal alone is authoritative
- Release contract:
  1. Before merge: capture the registry's `Latest Version` as baseline
  2. After merge: resolve the publish run by merge-commit `headSha` + `push` event filter
  3. Watch the resolved run to terminal state
  4. Confirm the conjunction (both required, in this order):
     - The resolved run's `conclusion` is `success`
     - The registry's `Latest Version` advanced past the baseline
- See `skills/release/SKILL.md` Step 7 for the agent-executable form of the release contract
- Do not derive an expected version from the merge SHA's manifest
- Do not compare against a specific expected version
- The Tessl registry never rejects a published version. `moderationPassed: false` affects `tessl search` visibility only; a security finding requires an override flag for `tessl install`. Neither is rejection
- If either conjunct fails, the publish is not confirmed — do not invent moderation states as a hedge explanation
- Naively re-running a failed publish can create an extra release when the workflow includes a version-bump step (e.g., `tesslio/patch-version-publish`) and the run got past it. Safer recovery: a follow-up commit fires a fresh publish on merge

## Protected Branches

- Don't push directly to `main` or `master`
- All changes go through pull requests

## Content-Only Direct-Push Carve-Out

- Narrow exception for content-only edits to a fully-enumerable path set
- Applies when the edited paths are prose / data artifacts a human audience reads directly — not code, not context artifacts an agent loads (rules, skills, scripts, manifests, workflow files, configuration)
- The push may go directly to `main` / `master` without a PR review cycle
- Preconditions (each consuming repo, all required):
  1. Repo documents an authority-of-record rule in its own tile naming the carve-out — the exact path globs, why those paths qualify as content not code/context, and what policy review the direct-push does NOT carry
  2. Carve-out scopes to one or more named path globs — never a broad wildcard like `**/*.md`. Globs that would match `rules/**/*.md`, `skills/**/*.md`, `*.yml` workflow files, `tile.json`, `package.json`, or any executable/loaded artifact are mis-scoped
  3. Push-time enforcement rejects the entire ref update when any path changed by the push lies outside the carve-out globs (GitHub push ruleset with path restriction, pre-receive hook, or equivalent server-side gate that blocks the ref update). Post-push CI checks do NOT qualify
- Every other branch / path in the repo still goes through pull requests
