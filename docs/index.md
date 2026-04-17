# Coding Policy — Documentation

## Overview

`jbaruch/coding-policy` is a Tessl tile that installs a set of always-on coding standards into any repository. It's designed for AI agents working across Baruch's projects, but the rules are general enough for any team that values clean, predictable code.

## Rules

All rules have `alwaysApply: true` — they're active in every conversation, no manual activation needed.

| Rule | File | Summary |
|------|------|---------|
| Commit Conventions | `rules/commit-conventions.md` | Imperative mood, ~50 char subject, body = "why", one logical change per commit, PR hygiene |
| Testing Standards | `rules/testing-standards.md` | Outcome-based assertions, deterministic tests, no binary fixtures, test independence |
| Error Handling | `rules/error-handling.md` | Specific exceptions, actionable messages, graceful fallback, structured logging |
| Dependency Management | `rules/dependency-management.md` | Stdlib-first, pinned versions, lock files, separate test/dev groups |
| File Hygiene | `rules/file-hygiene.md` | Proper .gitignore, no generated files committed, standalone scripts, exit codes |
| CI Safety | `rules/ci-safety.md` | Never modify CI without asking, never skip tests, branch naming conventions |
| No Secrets | `rules/no-secrets.md` | No credentials in code, env vars or secrets manager, pre-commit scanning |
| Code Formatting | `rules/code-formatting.md` | Use project's formatter, don't mix formatting with functional changes |

## Skills

| Skill | Trigger | Description |
|-------|---------|-------------|
| `release` | `/release` | Structured workflow: create PR, request Copilot review, address feedback, merge + cleanup |

## Philosophy

- **Language-agnostic.** Rules apply to Python, TypeScript, Go, Rust, Java — any language. No framework-specific assumptions.
- **One concern per rule.** Each file covers one topic. Easy to read, easy to reference, easy to override if a project needs an exception.
- **Opinionated but practical.** These rules reflect real patterns found across 17+ repositories. They're not theoretical — they solve problems that actually come up when agents write code.
- **Always on.** Every rule is `alwaysApply: true`. The whole point is that agents follow these conventions without being reminded.
