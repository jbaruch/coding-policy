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
- For plugin/tile/package releases, the duty extends past merge — confirm the resolved run's conclusion, the registry advance, and the moderation clear; no single signal is authoritative
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

## Protected Branches

- Don't push directly to `main` or `master` (except under the Content-Only Direct-Push Carve-Out below)
- All changes go through pull requests (same exception applies)

## Content-Only Direct-Push Carve-Out

- Narrow exception for content-only edits within an explicit, narrowly scoped path-glob set
- Applies when the edited paths are prose / data artifacts a human audience reads directly — not code, not context artifacts an agent loads (rules, skills, scripts, manifests, workflow files, configuration)
- The push may go directly to `main` or `master` without a PR review cycle
- Preconditions (each consuming repo, all required):
  1. Repo documents an authority-of-record rule in its own tile naming the carve-out — the exact path globs, why those paths qualify as content not code/context, and what policy review the direct-push does NOT carry
  2. Carve-out scopes to one or more named path globs — never a broad wildcard like `**/*.md`. Globs that would match `rules/**`, `skills/**`, workflow files (`*.yml` or `*.yaml`), `.tessl-plugin/plugin.json`, `package.json`, or any executable/loaded artifact (regardless of extension) are mis-scoped
  3. Push-time enforcement rejects the push when any changed path lies outside the carve-out globs (allowlist semantics), satisfied by form A or form B. Post-push CI checks satisfy neither
     - Form A — server-side gate: a GitHub push ruleset with path restriction, a pre-receive hook, or an equivalent server-side gate blocks the ref update
     - Form B — client-side content-only diff gate: permitted only where the platform cannot express server-side allowlist enforcement (e.g., github.com personal repos)
       - The publishing tool runs the gate as a deterministic script (per `rules/script-delegation.md`), not agent judgment
       - The gate computes changed paths with `git diff --name-only` against the push target ref and direct-pushes only when every changed path matches a declared content glob
       - Any out-of-glob path forces an automatic branch + PR fallback — never an operator-say-so override
       - The authority-of-record rule (precondition 1) names the gate script
- Every other branch / path in the repo still goes through pull requests
