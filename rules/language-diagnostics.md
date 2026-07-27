---
alwaysApply: true
---

# Language Diagnostics

## Enable the Language Server

- Every project turns on the diagnostics engine appropriate to its language — pyright/pylance for Python, tsserver/tsc for TypeScript, rust-analyzer/clippy for Rust, gopls/`go vet` for Go, and the like
- Enable it in-editor for the human and in-loop for the agent — Claude Code and other coding CLIs expose the language server (the `LSP` tool here), so the agent reads diagnostics on the files it touches as it works
- Clearing findings before handoff is a deterministic run, not a remembered intention — see Gate It Deterministically
- Configure the engine for the project's module layout so references resolve (see Resolve Modules First)
- Use whatever engine the project already has — don't introduce a second one without consensus

## Findings Are Non-Dismissible Without Cause

- A diagnostic is fixed, or suppressed inline with a stated reason — never silently ignored
- The cause rides next to the suppression — a one-line comment naming why the finding is wrong or unreachable, not a bare ignore directive
- No blanket or file-wide silencing — `# type: ignore`, `# pyright: basic`, `// @ts-nocheck`, `//nolint:all` switch the engine off for a whole file and mask the same signal it exists to catch
- Prefer a typed helper that proves the invariant once over scattered ignores

## Gate It Deterministically

- CI runs the engine's headless/CLI form — pyright CLI, `tsc --noEmit`, `clippy`, `go vet` — as a gate at zero findings
- The language server is the editor surface; the gate runs the same engine in batch mode, before tests, alongside format/lint (see `rules/code-formatting.md` CI Integration)
- "Don't ignore the warnings" enforced by an agent's or contributor's memory is not a gate — a deterministic check nobody runs does not exist (see `rules/script-delegation.md`)
- Before handoff, the agent runs the same gate command CI runs and clears every finding, scoped to the changed set only where the gate supports it
- The `LSP` tool is the live editor surface for those findings as the agent works
- The pre-handoff run makes "cleared before handoff" a checkable event
- CI runs the same gate as the backstop, never the first place a finding surfaces
- A `Stop` or pre-handoff hook running the gate mechanizes this where the harness supports it

## Resolve Modules First

- Configure the engine so imports resolve before turning strictness up — unresolved modules stop deep checking, so real bugs hide behind resolution false-positives
- Match the config to the project's module layout — pyright `executionEnvironments` with per-root `extraPaths` for Python path-insert bundles; tsc `baseUrl`/`paths`/project references for TS workspaces
- Resolution is what unlocks the real findings — null/undefined-flow crashes, could-be-null `raise`/`throw`, Optional/nullable misuse

## Adopting on a Dirty Tree

- Turning the gate on for a tree never checked is its own focused change — land the config plus the fixes in a PR separate from feature work
- The tree goes green first; wire the gate into CI only once it reports zero findings
- Sequence large adoptions: a config PR, then fix PRs grouped by finding shape, then the CI-gate PR
