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
- For plugin/tile/package releases, the duty extends past merge: watch the publish workflow that fires on the merge commit to completion via `gh run watch <publish-run-id> --exit-status`. **That return code IS the authoritative signal** — a publish workflow cannot return `success` without its publish step having pushed the new version to the registry; if it returns non-zero, the publish failed and the new version did not land. As a belt-and-suspenders sanity check, capture the registry's `Latest Version` pre-publish (`tessl tile info <workspace>/<tile>` for Tessl tiles, or the equivalent for other registries) and confirm post-publish that it advanced (registry-latest is now newer than pre-publish-latest). Do **not** compare against a specific expected version: the bump version is set inside the publish workflow's bump step, which runs after the merge SHA's manifest is read, so deriving "your" expected version from the merge SHA's manifest is wrong (it sees the pre-bump value); and in repos that auto-publish on every merge, interleaved merges may advance the registry past your specific version, which is not a failure. The Tessl registry **never rejects a published version**: `moderationPassed: false` means the version still lands and is fetchable but won't surface in `tessl search`; a security finding means `tessl install` requires an override flag to install — neither is rejection. If the registry didn't advance after the watch returned `success`, that's a contradiction; inspect the workflow's logs, but do not invent moderation states. Naively re-running a failed publish can create an extra release when the workflow includes a version-bump step (as `tesslio/patch-version-publish` does); the safer recovery is a follow-up commit that fires a fresh publish on merge

## Protected Branches

- Don't push directly to `main` or `master`
- All changes go through pull requests
