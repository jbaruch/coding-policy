# Dependency Management Carve-Outs

The full preconditions and authority-of-record detail of each `rules/dependency-management.md` carve-out, moved out of the always-loaded rule (#642). They bind as rule content: the rule keeps each carve-out's trigger line and requires reading its section here before relying on it. Text is unchanged from the rule.

## Runtime-Managed Manifest Carve-Out

- Narrow exception for runtime-managed manifests
- Applies when a tool produces the resolved-version state at runtime and gitignores it, in either shape:
  - the tool rewrites the manifest in place
  - the manifest holds a stable floating specifier and the tool resolves it into a separate gitignored resolved state
- The manifest may use a floating-but-explicit specifier (e.g., `"version": "latest"`) and skip the lock file
- Preconditions (each covered manifest, all required):
  1. An authority-of-record rule names the carve-out and lists every covered manifest, in the project's own plugin or in a shared plugin the project installs (whose rules load as the project's policy)
  2. A deterministic check surfaces any disallowed specifier (literal pin, range, tag, or anything other than the permitted floating specifier), in either form:
     - a deploy-time gate that fails the deployment
     - a plugin-shipped `SessionStart` hook that reads the manifest and flags a disallowed specifier each session
  3. Each covered manifest is named explicitly in the authority-of-record rule
- Multiple covered manifests permitted iff each independently meets all three preconditions
- Every other manifest in the repo still pins

### Authority of Record — consumer `tessl.json`

- Covered manifest: the fleet's consumer `tessl.json`
- tessl writes its resolved state into the gitignored `.tessl/`
- `jbaruch/*`-owned dependencies use the `latest` specifier
- Deterministic check: the plugin-shipped `hooks/check-tessl-latest.sh` `SessionStart` hook, which flags any `jbaruch/*` dependency not at `latest`
- `skills/onboard-repo` sets `latest` and gitignores `.tessl/` at onboarding
- Third-party dependencies (`tessl-labs/*`, `tessl/npm-*`) pin normally and stay out of scope

### Authority of Record — consumer `agents.yaml`

- Covered manifest: the fleet's consumer ACR `agents.yaml`
- ACR writes its resolved state into `.agents/registry.lock`, gitignored with the rest of `.agents/`
- `github:jbaruch/*` dependencies use `requested: latest`
- Deterministic check: the plugin-shipped `hooks/check-acr-latest.sh` `SessionStart` hook
- The hook flags any `github:jbaruch/*` dependency not at `latest`
- The hook refuses to update while `.agents/registry.lock` is tracked
- `skills/onboard-repo` gitignores `.agents/` at onboarding
- Dependencies from other owners pin normally and stay out of scope

## Adversarial-Freshness Dependency Carve-Out

- Narrow exception for a dependency whose value is tracking an adversary, not a version
- Applies when the upstream ships countermeasures against an actively-adapting opponent (browser-fingerprint evasion, malware signatures, threat feeds, blocklists) AND the consumed surface is data or rendered output, not a versioned API contract
- A pin degrades the capability rather than stabilizing it: the pin's renewal cadence competes with the adversary's release cadence, and staleness surfaces as silent capability loss, never as a build break
- The covered reference MAY use a floating specifier (e.g., a `:latest` container tag)
- The exemption reaches that reference alone — never the project's lock file, and never a sibling dependency in the same manifest
- Preconditions (each covered reference, all required):
  1. The project documents an authority-of-record rule in its own plugin naming every covered reference, the adversary being tracked, and why a pin degrades rather than stabilizes
  2. A deploy-time check fails the deployment when the committed reference is anything other than the permitted floating form, and fails when it can no longer locate the reference (a moved or renamed target fails loudly, never passes vacuously)
  3. The check runs as a deterministic script per `rules/script-delegation.md`, never agent judgment and never a bounded classification
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
  5. The check runs as a deterministic script per `rules/script-delegation.md`, never agent judgment and never a bounded classification
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
  5. The check runs as a deterministic script per `rules/script-delegation.md`, never agent judgment and never a bounded classification
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
