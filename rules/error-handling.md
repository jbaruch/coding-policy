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
  1. The catch line, the suppressor line, or a comment directly above either carries the literal grep token `outer-boundary-process-contract`
  2. Where a linter requires a catch-all suppressor, it sits attached to the catch by whichever placement the formatter permits, never separated from the catch by any other line:
     - Catch line, where the formatter keeps a trailing comment there — Python/Ruff `# noqa: BLE001` on the `except Exception:` line
     - Immediately-preceding line, where the formatter relocates a same-line trailing comment off the catch's opening brace — TypeScript/ESLint under Prettier `// eslint-disable-next-line` directly above the `catch`
  3. A comment directly above the catch — or directly above the suppressor, when precondition 2 places the suppressor immediately above the catch — names three things:
     - caller's silent-failure shape
     - what catch emits
     - why propagation breaks contract
  4. Handler at outermost process boundary — never inner function
- Every other catch in the file still uses specific exception types

## Shell Error Handling

- Every shell script opens with `set -euo pipefail`
- Never suppress a failure — no `|| true`, no `|| :`, no `2>/dev/null` standing in for a handler
- A command that can legitimately fail gets an explicit `if` or `case` on its exit code, never blanket suppression
- Distinguish an expected non-result from a tool failure — `grep` exits 1 on no-match and 2 on error, and `|| true` collapses both, so an unreadable file or a bad regex reads as "nothing found"
- Silencing a tool's diagnostic while explicitly handling its failure is not suppression: `cmd 2>/dev/null || { echo "<actionable message>" >&2; exit 1; }` replaces a worse message with a better one
- Fail visibly does not require exit non-zero — best-effort work that legitimately continues past a failure emits a warning to stderr, never nothing
- An `EXIT` trap's final command status becomes the script's exit status — end cleanup handlers with `return 0` so cleanup never rewrites the outcome
- Test harnesses that count failures use `set -uo pipefail` — a harness that dies on its first red assertion cannot report the rest

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
