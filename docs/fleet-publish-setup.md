# Enrolling a Repo in the Fleet Publish Pipeline

> **For:** the `jbaruch/coding-policy` fleet maintainer (or the agent acting for them).
> **Status:** repo-internal reference. `docs/` is `.tesslignore`d — this does not ship with the plugin; read it from the repo.

The canonical fleet publish pipeline lives in `.github/workflows/publish-plugin.yml`
(`on: workflow_call`, `jbaruch/coding-policy#206`). It owns the publish steps and the
third-party action versions; every consumer repo carries only a **thin caller** that
supplies triggers plus a `with:` block. This is the paved path — a new plugin repo, or
one still on a bespoke `tesslio/patch-version-publish` workflow, should use it.

`onboard-repo` sets up the *review* side (the fleet policy reviewer). It deliberately does
**not** touch publishing — publish varies by language, artifact, and bump model where
review does not, so enrollment is this manual (documented) step rather than a scaffolder.

## The thin caller

Drop this at `.github/workflows/publish.yml` in the consumer repo:

```yaml
name: Review & Publish Plugin

# Thin caller for the canonical fleet publish pipeline
# (jbaruch/coding-policy#206). Display name preserved for run-name watchers.

on:
  push:
    branches:
      - main
  workflow_dispatch:

permissions:
  id-token: write
  contents: write        # smart-publish pushes the auto-bump commit to the protected branch
  pull-requests: write

jobs:
  publish:
    uses: jbaruch/coding-policy/.github/workflows/publish-plugin.yml@<coding-policy-sha>
    secrets:
      TESSL_TOKEN: ${{ secrets.TESSL_TOKEN }}
    with:
      # every input is optional — the defaults publish a plain plugin with auto-bump
      stamp-changelog: true
```

Pin `@<coding-policy-sha>` to the current `jbaruch/coding-policy` commit
(`git ls-remote https://github.com/jbaruch/coding-policy main`). Dependabot renews the
pin (see below); the `@main`-referenced sibling actions inside the reusable workflow —
`smart-publish`, `skill-review`, `stamp-changelog` — track live `main` regardless of the
pin, so publish-logic fixes reach every caller on merge without a pin bump.

Preserve the workflow `name:` a repo already had — release skills watch runs by display
name. `Review & Publish Plugin` is the fleet default for new repos.

## Inputs

| Input | Default | Use it when |
|---|---|---|
| `publish-mode` | `auto-bump` | `as-is` only for a human-bump model (the manifest version is authored by hand and published verbatim, e.g. `koog-tessl`). Leave default otherwise. |
| `stamp-changelog` | `false` | The repo keeps a `CHANGELOG.md` with un-headed `### ` blocks a PR adds. Set `true` to stamp the `## <version> — <date>` heading before publish. |
| `pre-publish-script` | `''` | The repo has its own gates (typecheck, tests, carve-out checks). Point at one bash script — see below. |
| `python-version` | `''` | The pre-publish gate needs a pinned Python interpreter. Sets up Python before the gate. |
| `skills-dir` | `skills` | The repo's skills live somewhere other than `skills/`. |
| `skill-review-credit-outage` | `fail` | Set `skip` to opt into publishing an unreviewed skill during a tessl out-of-credits (403) outage (context-artifacts Credit-Outage Review Carve-Out). Every other review failure still hard-fails. |

`TESSL_TOKEN` is passed as a secret (never an input). The review threshold is fixed at 85
inside the reusable workflow — it is not a caller input (context-artifacts forbids
lowering it).

## Repo-specific gates (`pre-publish-script`)

The reusable workflow runs one gate script after checkout, before the tessl steps. Put
deterministic gate logic in a committed bash script (`rules/script-delegation.md`) and
point `pre-publish-script` at it:

```yaml
    with:
      pre-publish-script: scripts/pre-publish-checks.sh
      python-version: '3.11'   # only if the script needs a pinned Python
```

The script is self-contained — it runs before `setup-tessl`, so it must not assume tessl.
For a **Python** gate, set `python-version` and the workflow sets the interpreter up for
you. For a **node** gate there is no `node-version` input today: the workflow pins only
Python. A node project either runs its `npm ci && npm run typecheck && npm test` against
the runner's system/`nvm` node inside the script (unpinned), or the reusable workflow
gains a `node-version` input first. Do not silently regress a pinned toolchain — decide
that trade-off explicitly.

## Pin renewal (Dependabot)

Every pinned action needs an automated renewal mechanism (`dependency-management`
Freshness). Add `.github/dependabot.yml` with the `github-actions` ecosystem so the
`publish-plugin.yml@<sha>` pin gets a weekly renewal PR whose CI gate is the acceptance
check:

```yaml
version: 2
updates:
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    commit-message:
      prefix: "chore(ci)"
```

Add a second `updates:` entry for the repo's language ecosystem (`pip`, `npm`, …) when it
has one.

## Migrating an existing `tesslio/patch-version-publish` repo

The bespoke `patch-version-publish` workflows red a run when the org hits out-of-credits
*after* the artifact lands, and strand the manifest bump. Moving to the reusable workflow
replaces that with `smart-publish` + the landed-gate reconciliation. Per repo:

1. Replace `.github/workflows/publish.yml` with the thin caller above. Fold any inline
   gates (typecheck, tests, lint) into a `pre-publish-script`; drop the inline
   `skill-review` / `stamp-changelog` steps (the reusable workflow runs them).
2. Add or extend `.github/dependabot.yml` for the pin.
3. Open the change as its own PR **scoped as a CI change** — the PR title/body being an
   explicit CI-config change is the approval artifact (`rules/ci-safety.md` Hands Off CI
   Config forbids only *unannounced* CI edits).
4. After merge, watch the first publish run to terminal state and confirm the registry
   advanced — the reusable pipeline's own publish is the end-to-end test.

## When NOT to use it

A genuinely bespoke release stays bespoke — e.g. `fifty-tabs-of-fares` publishes a Python
library (wheel + tag + GH release) in the same job as the version write, and runs
`publish-mode: as-is`. Library releases and any repo whose publish is not "lint → review →
tessl publish a plugin/tile" are out of scope for the reusable workflow.
