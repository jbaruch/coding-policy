---
alwaysApply: true
---

# CI Safety

## Hands Off CI Config

- **Never smuggle CI configuration changes** (workflow files, pipeline configs) into a PR whose stated scope is something else — CI changes affect every contributor and every branch
- A PR whose title and body explicitly scope the work as a CI change **is** the approval artifact; this rule forbids unannounced edits, not CI-scope PRs
- For unplanned CI edits discovered mid-task, stop and ask before touching the workflow files

## Never Skip Tests

- Never add `[skip ci]` to commit messages
- Never disable or skip failing tests to unblock a merge
- If tests fail, fix the tests or fix the code — skipping is not a valid option

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
- For plugin/tile/package releases, the duty extends past merge — and the **authoritative signal is the registry's state**, not the workflow's exit code. Workflows can succeed without running the publish step (conditional skips) or fail after publishing (cleanup/notification steps), so workflow exit code alone is not safe to encode as "did publish land". The contract: capture the registry's `Latest Version` BEFORE the merge as a baseline (`tessl tile info <workspace>/<tile>` for Tessl tiles, or the equivalent for other registries); after merge, wait for the publish workflow to reach a terminal state (`gh run watch <publish-run-id>` — used to time the check, not as the gate); then confirm the registry's `Latest Version` advanced past the pre-publish baseline. Registry-advanced = the new version landed; registry-not-advanced = it didn't, regardless of the workflow's exit code. Do **not** derive a specific expected version from the merge SHA's manifest (the bump runs inside the publish workflow after that SHA is read, so the merge SHA carries the pre-bump value), and do not compare against a specific expected version (interleaved merges in busy repos may advance the registry past your specific version — still success). The Tessl registry **never rejects a published version**: `moderationPassed: false` means the version still lands and is fetchable but won't surface in `tessl search`; a security finding means `tessl install` requires an override flag to install — neither is rejection. If the registry didn't advance, the publish failed; do not invent moderation states as a hedge explanation. Naively re-running a failed publish can create an extra release when the workflow includes a version-bump step (as `tesslio/patch-version-publish` does); the safer recovery is a follow-up commit that fires a fresh publish on merge

## Protected Branches

- Don't push directly to `main` or `master`
- All changes go through pull requests
