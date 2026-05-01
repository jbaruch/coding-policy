---
alwaysApply: true
---

# Error Handling

## Specific Exceptions

- Catch specific exception types, never bare catch-all handlers
- Let unexpected exceptions propagate — they indicate bugs that need fixing, not hiding
- **Narrow exception for outer-boundary process contracts**: when a process boundary's caller treats non-zero exit OR invalid stdout as a silent-failure signal (e.g. agent-runner subprocess prechecks, network-protocol stdout contracts, IPC handlers where the wrapping framework reads malformed output as "skip the task"), letting an unexpected exception propagate from a programming bug silently disables the contract — the exact failure mode the "let unexpected propagate" clause above cannot accept here. The outer-boundary handler may use the language's narrowest "everything except interrupts" form — Python `except Exception:`, never `except BaseException:` (`KeyboardInterrupt` and `SystemExit` must be allowed to propagate past this handler so processes stay killable via Ctrl-C / `sys.exit()`); the analogous form in other languages — **only when** the line (or the comment above it) carries the literal grep token `outer-boundary-process-contract` so one tile-wide grep catches every sanctioned carve-out instance regardless of language — additionally accompanied by the language-native catch-all suppressor where the linter requires one (Python/Ruff: `# noqa: BLE001`); AND a comment immediately above names (a) the caller's silent-failure shape, (b) what the catch emits to satisfy the contract (e.g. stderr traceback + safe-shape JSON on stdout), and (c) why propagation would break the contract; AND the handler sits at the outermost process boundary, never an inner function. Every other catch in the file still uses specific exception types. Reference incident: 2026-04-16 → 2026-04-18 silent outage where an unhandled `TypeError` in a precheck script caused agent-runner to read the non-zero exit as `wake_agent=false` (the precheck JSON contract documented in `rules/script-delegation.md`) and the scheduled task never woke; the carve-out exists so the defensible outer-boundary catch is also defensible to literal-rule reviewers.

## Actionable Messages

- Error messages must tell the user **what to do**, not just what went wrong
- Bad: "File not found"
- Good: "Config file not found at ~/.config/app.toml — run `app init` to create one"

## Graceful Fallback

- When multiple approaches exist, try alternatives before failing
- Example: try the preferred tool, fall back to an alternative, then fail with a clear message listing what was tried

## Structured Logging

- Log at appropriate levels: DEBUG for internals, INFO for progress, WARN for recoverable issues, ERROR for failures
- Include enough context to diagnose without reproducing: input parameters, relevant state, error details
- **Never log secrets**, tokens, passwords, or credentials — not even at DEBUG level
