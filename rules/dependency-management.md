---
alwaysApply: true
---

# Dependency Management

## Stdlib First

- Prefer the standard library over external dependencies
- Only add a dependency when it provides significant value over a stdlib solution

## Declaration

- All dependencies declared in the project's manifest file (e.g., `pyproject.toml`, `package.json`, `go.mod`, `Cargo.toml`)
- No undeclared dependencies — if your code imports it, the manifest lists it

## Pinning

- Pin versions or use a lock file to ensure reproducible builds
- Lock files are committed to the repo

## Freshness

- Every pinned dependency needs a stated renewal mechanism
- Automate it where a scanner supports the ecosystem: a committed Dependabot or Renovate config
- Where no scanner tracks the pin — a version baked into a script or action step — document the renewal cadence beside the pin
- A version bump is its own focused change, never bundled with feature work
- Formatter and linter bumps especially (see `rules/code-formatting.md` Separation of Concerns)
- Match a stale pin locally to ship the current task; bump it in a separate change

## Runtime-Managed Manifest Carve-Out

- Narrow exception for runtime-managed manifests
- Applies when a tool rewrites a manifest in-place at runtime AND the resolved-version state is gitignored
- The manifest may use a floating-but-explicit specifier (e.g., `"version": "latest"`) and skip the lock file
- Preconditions (each covered manifest, all required):
  1. The project documents an authority-of-record rule in its own plugin naming the carve-out and listing every covered manifest
  2. A deploy-time check fails the deployment if any disallowed specifier appears (literal pin, range, tag, or anything other than the permitted floating specifier)
  3. Each covered manifest is named explicitly in the authority-of-record rule
- Multiple covered manifests permitted iff each independently meets all three preconditions
- Every other manifest in the repo still pins

## Adversarial-Freshness Dependency Carve-Out

- Narrow exception for a dependency whose value is tracking an adversary, not a version
- Applies when the upstream ships countermeasures against an actively-adapting opponent (browser-fingerprint evasion, malware signatures, threat feeds, blocklists) AND the consumed surface is data or rendered output, not a versioned API contract
- A pin degrades the capability rather than stabilizing it: the pin's renewal cadence competes with the adversary's release cadence, and staleness surfaces as silent capability loss, never as a build break
- The covered reference MAY use a floating specifier (e.g., a `:latest` container tag)
- The exemption reaches that reference alone — never the project's lock file, and never a sibling dependency in the same manifest
- Preconditions (each covered reference, all required):
  1. The project documents an authority-of-record rule in its own plugin naming every covered reference, the adversary being tracked, and why a pin degrades rather than stabilizes
  2. A deploy-time check fails the deployment when the committed reference is anything other than the permitted floating form, and fails when it can no longer locate the reference (a moved or renamed target fails loudly, never passes vacuously)
  3. The check runs as a deterministic script per `rules/script-delegation.md`, not agent judgment
  4. A per-install override lets an operator pin for reproducibility, documented in the authority-of-record rule and explicitly outside the deploy check's scope — environment configuration is not a committed dependency
- "The upstream releases often" does NOT qualify. See Freshness
- "Pinning is inconvenient" does NOT qualify
- A dependency whose consumed surface is a versioned API does NOT qualify in an adversarial domain
- Every other dependency in the repo still pins with a stated renewal mechanism

## First-Party Co-Shipped Dependency Carve-Out

- Narrow exception for a dependency the same owner writes, reviews, and deploys in lock-step with its consumer
- Applies when the dependency has no release train of its own: the consumer rebuilds against the dependency's default branch on every deploy, and no third artifact selects a version between them
- The covered reference MUST carry no specifier at all — a bare `owner/repo` install. A branch ref, a tag, and a commit SHA are all specifiers and all stay forbidden under this carve-out
- The exemption reaches that reference alone — never the project's lock file, and never a sibling dependency in the same manifest
- Preconditions (each covered reference, all required):
  1. The project documents an authority-of-record rule in its own plugin naming every covered reference, who owns both sides, and what stands in for the version-bump review
  2. The dependency's own default branch is CI-gated: its test suite runs on every merge
  3. The consumer's build refetches on every rebuild — any build-cache layer that would freeze the floating reference carries an explicit upstream-change trigger, bound to the covered reference
  4. A deploy-time check fails the deployment when the committed reference carries any specifier, when the refetch trigger is absent or not bound to that reference, and when it can no longer locate the reference (a moved or renamed target fails loudly, never passes vacuously)
  5. The check runs as a deterministic script per `rules/script-delegation.md`, not agent judgment
- "We wrote it" alone does NOT qualify — a dependency with its own release train, or with consumers outside the owner, still pins
- "The bump PRs are noise" does NOT qualify. See Freshness
- A consumed surface the owner does not control end-to-end does NOT qualify
- Every other dependency in the repo still pins with a stated renewal mechanism

## OS-Package Runtime Carve-Out

- Narrow exception for a package installed from the base image's OS package manager inside a container image (`apt-get install`, `apk add`, `dnf install`)
- Applies when the distro archive serves only the current version of a package, so a literal version pin stops resolving at the next security update
- The consumed surface is a command-line invocation or a distro-managed ABI, not a semver-governed source API the project compiles or imports against
- The covered install MAY omit the version specifier
- The exemption reaches the named packages alone — never a language package manager in the same image (`pip`, `npm`, `gem`), which pins normally
- Preconditions (each covered image, all required):
  1. The project documents an authority-of-record rule in its own plugin naming every covered image, the exact package set, and the rebuild cadence that stands in for a pin
  2. The image's base is pinned to a specific tag or digest and scanner-tracked
  3. The image is rebuilt on a stated recurring cadence
  4. A deploy-time check fails the deployment when a covered image's base is unpinned, and when a package the image EXPLICITLY installs (an operand of its package-manager install command) falls outside the recorded set. Packages already present in the base image, and transitive dependencies the package manager resolves, are out of scope
  5. The check runs as a deterministic script per `rules/script-delegation.md`, not agent judgment
- "Pinning apt is annoying" does NOT qualify — the archive-retention failure mode is the test, and a distro that serves historical versions does not meet it
- A language-ecosystem dependency does NOT qualify, whatever installs it
- Every other dependency in the repo still pins with a stated renewal mechanism

## Same-Repo Reusable-Workflow Action Carve-Out

- Narrow exception for a reusable workflow (`on: workflow_call`) referencing a composite action in its OWN repository
- Applies when one repo hosts both the reusable workflow and the action it invokes, and external repos call that workflow — a `./` local path resolves against the caller's checkout (which lacks the action), forcing a full `owner/repo/.../action@ref` self-reference
- The self-reference MAY track the hosting repo's default branch (`@main`) instead of a pin
- Preconditions (all required):
  1. The referenced action lives in the same repository as the reusable workflow
  2. No dependency scanner updates the reference — Dependabot and Renovate skip same-repo self-references, so a pin has no renewal path and a SHA pin of one's own repo is circular
  3. Workflow and action move together on the default branch; the external caller pins the WORKFLOW ref (`@<sha>`) for reproducible caller logic
- Every external action reference still pins with a scanner-tracked renewal per Freshness

## No Vendoring

- Don't copy library source code into the repo
- Use the language's package manager to install dependencies
- Tessl plugins count as dependencies — never vendor them. Install via `tessl install` at runtime; don't commit plugin content (e.g., `.tessl/plugins/<workspace>/<plugin>/...`) into the consumer repo
- Install Tessl plugins to a non-workspace path for CI agents

## Dependency Groups

- Separate test/dev dependencies from production dependencies
- Use the project's convention for grouping (e.g., `[test]` extras, `devDependencies`, build tags)

## CI Compatibility

- Every dependency must be installable in CI
- If something exists as a package, install it properly
