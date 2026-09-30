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
- A lock file here pins dependencies that this repo's build, test, or CI resolves
- A tool's record of what it installed on one machine is per-machine installer state, not a lock file (e.g., `skills-lock.json` from the `skills` installer, listing skills installed into this machine's agent directories)
- Per-machine installer state may be gitignored
- A file that records resolved dependencies and that a build, test, or CI step in this repo reads is a lock file, whatever it is called

## Freshness

- Every pinned dependency needs a stated renewal mechanism
- Automate it where a scanner supports the ecosystem: a committed Dependabot or Renovate config
- Where no scanner tracks the pin — a version baked into a script or action step, a pinned model version behind a bounded classification — document the renewal cadence beside the pin
- A version bump is its own focused change, never bundled with feature work
- Formatter and linter bumps especially (see `rules/code-formatting.md` Separation of Concerns)
- Match a stale pin locally to ship the current task; bump it in a separate change

## Runtime-Managed Manifest Carve-Out

- Narrow exception for runtime-managed manifests
- Applies when a tool produces the resolved-version state at runtime and gitignores it, in either shape:
  - the tool rewrites the manifest in place
  - the manifest holds a stable floating specifier and the tool resolves it into a separate gitignored resolved state
- Preconditions are binding: read `skills/onboard-repo/references/dependency-carve-outs.md` Runtime-Managed Manifest Carve-Out before relying on it
- Every other manifest in the repo still pins

## Adversarial-Freshness Dependency Carve-Out

- Narrow exception for a dependency whose value is tracking an adversary, not a version
- Applies when the upstream ships countermeasures against an actively-adapting opponent (browser-fingerprint evasion, malware signatures, threat feeds, blocklists) AND the consumed surface is data or rendered output, not a versioned API contract
- Preconditions are binding: read `skills/onboard-repo/references/dependency-carve-outs.md` Adversarial-Freshness Dependency Carve-Out before relying on it
- Every other dependency in the repo still pins with a stated renewal mechanism

## First-Party Co-Shipped Dependency Carve-Out

- Narrow exception for a dependency the same owner writes, reviews, and deploys in lock-step with its consumer
- Applies when the dependency has no release train of its own: the consumer rebuilds against the dependency's default branch on every deploy, and no third artifact selects a version between them
- Preconditions are binding: read `skills/onboard-repo/references/dependency-carve-outs.md` First-Party Co-Shipped Dependency Carve-Out before relying on it
- Every other dependency in the repo still pins with a stated renewal mechanism

## OS-Package Runtime Carve-Out

- Narrow exception for a package installed from the base image's OS package manager inside a container image (`apt-get install`, `apk add`, `dnf install`)
- Applies when the distro archive serves only the current version of a package, so a literal version pin stops resolving at the next security update
- Preconditions are binding: read `skills/onboard-repo/references/dependency-carve-outs.md` OS-Package Runtime Carve-Out before relying on it
- Every other dependency in the repo still pins with a stated renewal mechanism

## Same-Repo Reusable-Workflow Action Carve-Out

- Narrow exception for a reusable workflow (`on: workflow_call`) referencing a composite action in its OWN repository
- Applies when one repo hosts both the reusable workflow and the action it invokes, and external repos call that workflow — a `./` local path resolves against the caller's checkout (which lacks the action), forcing a full `owner/repo/.../action@ref` self-reference
- Preconditions are binding: read `skills/onboard-repo/references/dependency-carve-outs.md` Same-Repo Reusable-Workflow Action Carve-Out before relying on it
- Every external action reference still pins with a scanner-tracked renewal per Freshness

## No Vendoring

- Don't copy library source code into the repo
- Use the language's package manager to install dependencies
- Tessl plugins count as dependencies — never vendor them. Install via `tessl install` at runtime; don't commit plugin content (e.g., `.tessl/plugins/<workspace>/<plugin>/...`) into the consumer repo
- Install Tessl plugins to a non-workspace path for CI agents
- Governs this repository's own dependencies and the content this repository commits — never a product's documented runtime behavior toward a user's project
- Product code that copies third-party content into a user's project by documented design (e.g., a migration bridge, an offline mirror, a fallback the product installs and later removes) is out of scope
- Reviewing that copying is a correctness question under the other rules, not a No Vendoring violation

## Dependency Groups

- Separate test/dev dependencies from production dependencies
- Use the project's convention for grouping (e.g., `[test]` extras, `devDependencies`, build tags)

## CI Compatibility

- Every dependency must be installable in CI
- If something exists as a package, install it properly
