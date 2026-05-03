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
- **Narrow exception for runtime-managed manifests**: if a tool the deployment relies on rewrites a manifest in-place at runtime AND the resolved-version state is gitignored, pin-or-lock produces silent drift where git and the running deployment disagree across every restart. In that one case the manifest may use a floating-but-explicit specifier (e.g. `"version": "latest"`) and skip the lock file, **only when** the project documents an authority-of-record rule in its own tile naming the carve-out (filename, scope, why the rewrite-in-place violates pin/lock semantics); AND a deploy-time check fails the deployment if a literal pin reappears in that manifest; AND the carve-out is narrowly scoped to a single named manifest. Every other manifest in the repo still pins. Reference incident: NanoClaw's `tessl-workspace/tessl.json` accumulated a 22-day silent drift on 2026-04-27 because `tessl update` rewrites the manifest in-place; authority-of-record rule is `nanoclaw-host: tessl-version-floating`.

## No Vendoring

- Don't copy library source code into the repo
- Use the language's package manager to install dependencies
- Tessl tiles count as dependencies — never vendor them. Don't commit tile content (e.g., `.tessl/tiles/<workspace>/<tile>/...`) into the consumer repo; install via `tessl install` at runtime. A vendored copy silently drifts from the registry version, and a workspace-local `.tessl/` is also wiped by `actions/checkout`'s default `clean: true` before CI agents read it — both failure modes have surfaced on real consumer installs (see `install-reviewer` 0.2.x changelog entries)

## Dependency Groups

- Separate test/dev dependencies from production dependencies
- Use the project's convention for grouping (e.g., `[test]` extras, `devDependencies`, build tags)

## CI Compatibility

- Every dependency must be installable in CI
- If something exists as a package, install it properly — don't skip tests because a dependency is "hard to install"
