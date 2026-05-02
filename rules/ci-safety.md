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
- For plugin/tile/package releases, the duty extends past merge: watch the publish workflow that fires on the merge commit to completion (not just "check it triggered" — fire-and-forget scoring is zero), AND verify the new version is live in the registry (`tessl tile info <workspace>/<tile>` for Tessl tiles, or the equivalent registry endpoint for other artifact types). If the registry does not show the new version after the publish workflow returned `success`, treat that as **CI failed** — diagnose the publish run and re-trigger. **Do not invent intermediate states.** The Tessl registry has no moderation queue, no moderation hold, and no moderation rejection step; agents have repeatedly hallucinated these as plausible-sounding failure modes when the real answer is always "CI failed, go look at the run." Registry verification is the explicit end-to-end confirmation that the publish workflow actually succeeded, not a check against any phantom moderation state

## Protected Branches

- Don't push directly to `main` or `master`
- All changes go through pull requests
