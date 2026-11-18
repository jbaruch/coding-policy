# Dependency Management Carve-Outs

The full preconditions and authority-of-record detail of each `rules/dependency-management.md` carve-out, moved out of the always-loaded rule (#642). They bind as rule content: the rule keeps each carve-out's trigger line and requires reading its section here before relying on it. Each section carries only the moved detail; the trigger, applies-when and reset lines stay in the rule.

## Runtime-Managed Manifest Carve-Out

- The manifest may use a floating-but-explicit specifier (e.g., `"version": "latest"`) and skip the lock file
- Preconditions (each covered manifest, all required):
  1. Project policy or dependency documentation names the carve-out and every covered manifest

### Authority of Record — consumer `tessl.json`

- Covered manifest: the fleet's consumer `tessl.json`
- tessl writes its resolved state into the gitignored `.tessl/`
- `jbaruch/*`-owned dependencies use the `latest` specifier
- Deterministic check: the plugin-shipped `skills/release/hooks/check-tessl-latest.sh` `SessionStart` hook, which flags any `jbaruch/*` dependency not at `latest`
- `skills/onboard-repo` sets `latest` and gitignores `.tessl/` at onboarding
- Third-party dependencies (`tessl-labs/*`, `tessl/npm-*`) pin normally and stay out of scope

### Authority of Record — consumer `agents.yaml`

- Covered manifest: the fleet's consumer ACR `agents.yaml`
- ACR writes its resolved state into `.agents/registry.lock`, gitignored with the rest of `.agents/`
- `github:jbaruch/*` dependencies use `requested: latest`
- Deterministic check: the plugin-shipped `skills/release/hooks/check-acr-latest.sh` `SessionStart` hook
- The hook flags any `github:jbaruch/*` dependency not at `latest`
- The hook refuses to update while `.agents/registry.lock` is tracked
- `skills/onboard-repo` gitignores `.agents/` at onboarding
- Dependencies from other owners pin normally and stay out of scope

## Adversarial-Freshness Dependency Carve-Out

- A pin degrades the capability rather than stabilizing it: the pin's renewal cadence competes with the adversary's release cadence, and staleness surfaces as silent capability loss, never as a build break
- The covered reference MAY use a floating specifier (e.g., a `:latest` container tag)
- The exemption reaches that reference alone — never the project's lock file, and never a sibling dependency in the same manifest
- Preconditions (each covered reference, all required):
  1. The project documents every covered reference, the adversary being tracked, and why a pin degrades rather than stabilizes
- "The upstream releases often" does NOT qualify. See Freshness
- "Pinning is inconvenient" does NOT qualify
- A dependency whose consumed surface is a versioned API does NOT qualify in an adversarial domain

## First-Party Co-Shipped Dependency Carve-Out

- The covered reference MUST carry no specifier at all — a bare `owner/repo` install. A branch ref, a tag, and a commit SHA are all specifiers and all stay forbidden under this carve-out
- The exemption reaches that reference alone — never the project's lock file, and never a sibling dependency in the same manifest
- Preconditions (each covered reference, all required):
  1. Project documentation names every covered reference, who owns both sides, and what stands in for the version-bump review
  2. The dependency's default branch is CI-gated: CI runs its test suite on every merge to the default branch
  3. Consumer rebuilds refetch the reference on every deploy
- "We wrote it" alone does NOT qualify — a dependency with its own release train still pins
- "The bump PRs are noise" does NOT qualify. See Freshness
- A consumed surface the owner does not control end-to-end does NOT qualify

## OS-Package Runtime Carve-Out

- The consumed surface is a command-line invocation or a distro-managed ABI, not a semver-governed source API the project compiles or imports against
- The covered install MAY omit the version specifier
- The exemption reaches the named packages alone — never a language package manager in the same image (`pip`, `npm`, `gem`), which pins normally
- Preconditions (each covered image, all required):
  1. Project documentation names every covered image, the exact package set, and the rebuild cadence that stands in for a pin
  2. The image's base is pinned to a specific tag or digest and scanner-tracked
  3. The image is rebuilt on a stated recurring cadence
- "Pinning apt is annoying" does NOT qualify — the archive-retention failure mode is the test, and a distro that serves historical versions does not meet it
- A language-ecosystem dependency does NOT qualify, whatever installs it

## Same-Repo Reusable-Workflow Action Carve-Out

- The self-reference MAY track the hosting repo's default branch (`@main`) instead of a pin
- Preconditions (all required):
  1. The referenced action lives in the same repository as the reusable workflow
  2. Workflow and action move together on the default branch
  3. The external caller's WORKFLOW ref follows the Pinning contract in `skills/coding-contracts/references/dependency-management.md`
