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

## Runtime-Managed Manifest Carve-Out

- Narrow exception for runtime-managed manifests
- Applies when a tool rewrites a manifest in-place at runtime AND the resolved-version state is gitignored
- The manifest may use a floating-but-explicit specifier (e.g., `"version": "latest"`) and skip the lock file
- Preconditions (each covered manifest, all required):
  1. The project documents an authority-of-record rule in its own tile naming the carve-out and listing every covered manifest
  2. A deploy-time check fails the deployment if any disallowed specifier appears (literal pin, range, tag, or anything other than the permitted floating specifier)
  3. Each covered manifest is named explicitly in the authority-of-record rule
- Multiple covered manifests permitted iff each independently meets all three preconditions
- Every other manifest in the repo still pins

## No Vendoring

- Don't copy library source code into the repo
- Use the language's package manager to install dependencies
- Tessl tiles count as dependencies — never vendor them. Install via `tessl install` at runtime; don't commit tile content (e.g., `.tessl/tiles/<workspace>/<tile>/...`) into the consumer repo
- Install Tessl tiles to a non-workspace path for CI agents

## Dependency Groups

- Separate test/dev dependencies from production dependencies
- Use the project's convention for grouping (e.g., `[test]` extras, `devDependencies`, build tags)

## CI Compatibility

- Every dependency must be installable in CI
- If something exists as a package, install it properly
