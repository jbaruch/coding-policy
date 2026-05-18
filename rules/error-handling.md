---
alwaysApply: true
---

# Error Handling

## Specific Exceptions

- Catch specific exception types, never bare catch-all handlers
- Let unexpected exceptions propagate

## Outer-Boundary Carve-Out

- Narrow exception for outer-boundary process contracts
- Applies when a process boundary's caller reads non-zero exit OR invalid stdout as a silent-failure signal (agent-runner prechecks, network-protocol stdout contracts, IPC handlers)
- A propagating unexpected exception silently disables the contract
- Use the language's narrowest "everything except interrupts" form — Python `except Exception:`, or the analogous form in other languages
- Never `except BaseException:` (or its equivalent that traps interrupts); `KeyboardInterrupt` and `SystemExit` must propagate so processes stay killable
- Preconditions (all required):
  1. Catch line or its preceding comment contains literal grep token `outer-boundary-process-contract`
  2. Where a linter requires a catch-all suppressor, it sits inline with the catch (Python/Ruff: `# noqa: BLE001` on the `except Exception:` line, not above)
  3. Comment above names three things: caller's silent-failure shape; what catch emits; why propagation breaks contract
  4. Handler at outermost process boundary — never inner function
- Every other catch in the file still uses specific exception types

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
