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
