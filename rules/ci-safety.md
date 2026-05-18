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
- For plugin/tile/package releases, the duty extends past merge — the authoritative signal is the registry's state, not the workflow's exit code (workflows can succeed without publishing, or fail after publishing)
- Release contract:
  1. Before merge: capture the registry's `Latest Version` as baseline (`tessl tile info <workspace>/<tile>` for Tessl tiles)
  2. After merge: resolve the publish run whose `headSha` matches the merge commit (from `gh pr view <N> --json mergeCommit`, not `git log -1` on main) AND whose `event` is `push` (excluding manual `workflow_dispatch` runs sharing the SHA), then `gh run watch <publish-run-id>` on that exact run
  3. Confirm the registry's `Latest Version` advanced past the baseline
- Do not derive an expected version from the merge SHA's manifest
- Do not compare against a specific expected version
- The Tessl registry never rejects a published version. `moderationPassed: false` affects `tessl search` visibility only; a security finding requires an override flag for `tessl install`. Neither is rejection
- If the registry didn't advance, the publish failed — do not invent moderation states as a hedge explanation
- Naively re-running a failed publish can create an extra release when the workflow includes a version-bump step (e.g., `tesslio/patch-version-publish`) and the run got past it. Safer recovery: a follow-up commit fires a fresh publish on merge

## Protected Branches

- Don't push directly to `main` or `master`
- All changes go through pull requests
