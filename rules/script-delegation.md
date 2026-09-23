---
alwaysApply: false
applyTo: "skills/**, scripts/**, skills/**/*.sh, skills/**/*.py, skills/**/SKILL.md — when choosing between a script, a bounded classifier and the model, or authoring the scripts skills invoke"
description: Deterministic operations → script, a fixed answer set read by meaning → bounded classification, everything else → LLM, the regex trap, script structure conventions, precheck gating
---

# Script Delegation

## The Core Principle

- Everything deterministic → script. A fixed answer set read by meaning → bounded classification. Everything else requiring reasoning → skill/LLM
- If the logic can be expressed as a pure function with known inputs and outputs, it's a script
- If the answer is one of a fixed enumerated set, picking it requires reading meaning, and the question's input carries everything the answer depends on, it's a bounded classification
- Any other question requiring judgment, synthesis, or context-dependent decisions stays in the skill

## What Belongs in a Script

- Database queries, math operations, file parsing
- JSON normalization, fixed-logic API polling, data transformation
- Any operation where the same input always produces the same output

## What Stays in the LLM

- Synthesis across multiple sources, language generation
- Branching decisions that require situational context the question's input does not carry
- Open-ended answers that depend on understanding intent

## Bounded Classification

- The answer is one of a fixed list, and picking the right one takes reading meaning rather than computing a value
- The question's input carries everything the answer depends on
- One of the allowed answers is "not enough evidence to tell"
- When it answers that, the question goes to the reasoning round instead
- An unavailable classifier or an answer outside the list takes the same path, never a retry into another answer
- The label never triggers an action that cannot be undone (see `rules/ship-on-green.md`)

## The Regex Trap

- Resist the over-eager urge to declare things deterministic on a regex hunch
- If the input has too many edge cases for a reasonable regex, it is not scripting
- Parsing natural language dates, extracting meaning from unstructured text, classifying ambiguous input — these are **not** scripting tasks
- Classifying into a fixed answer set is a bounded classification
- Every other case in this section is reasoning
- A script should only handle patterns that are fully enumerable

## Scripts Are Real Files

- Scripts are executable files that live in the plugin (e.g., `scripts/request-review.sh`) — not code blocks in SKILL.md for the agent to copy-paste
- The skill references the script and runs it; the script does the work
- Code blocks in SKILL.md are for showing the agent what command to run, not for embedding logic the agent should reproduce character-by-character

- Narrow exception for Herdr's installed-plugin bootstrap.
- Applies only to command blocks in `skills/herdr-teamlead/SKILL.md`, `skills/herdr-standup/SKILL.md`, `skills/herdr-teamlead/references/round-setup.md`, and `skills/herdr-teamlead/references/judge-round.md`.
- Preconditions (all required):
  1. The block initializes `CP` to the literal `.tessl/plugins/jbaruch/coding-policy`; its only inline branch tests that directory and falls back to the same path under `$HOME`
  2. The block invokes only co-shipped scripts through quoted `$CP` paths with an explicit interpreter; each independent call repeats the bootstrap
  3. Bootstrap performs no writes, network access, permission changes, sourcing, or evaluation of repository-controlled code
  4. All work after root selection stays in the invoked script; no inline business logic, loops, or additional selection heuristics
  5. `skills/herdr-teamlead/tests/test_skill_invocations.sh` checks every covered block and executes fixtures for local precedence, global fallback, missing installs, spaces, and mode-0644 scripts
- Every other command block follows Scripts Are Real Files unchanged.

## Script Requirements

Scripts follow the baseline in `rules/file-hygiene.md` (exit codes, stderr, idempotency) plus these requirements:

- **JSON-producing**: output structured data, not prose
- **Self-error-handling**: exit non-zero on failure, write a diagnostic message to stderr
- **Single-purpose**: one script does one thing — compose scripts, don't build monoliths

- Narrow exception for `skills/herdr-teamlead/review-package.sh` artifact-path stdout.
- Preconditions (all required):
  1. Success emits only the absolute path of the completed review package and a newline
  2. Failure emits no path, exits non-zero, and writes an actionable diagnostic to stderr
  3. The package contains the resolved commit range, commit list, diff stat, and patch
- Every other skill script retains the JSON-producing requirement

- Narrow exception for `skills/release/registry-baseline.sh` and `skills/release/confirm-tessl-landed.sh` version stdout.
- Applies when a release-gate wrapper's whole output is the version string its caller passes as an argument to the next gate command
- Preconditions (all required):
  1. Success emits only that version and a newline
  2. Failure emits no version, exits non-zero, and writes an actionable diagnostic to stderr
  3. Each distinct verdict of the helper it wraps reaches the caller as a distinct exit code
- Every other release script retains the JSON-producing requirement

## Precheck Gating

- For scheduled or recurring tasks where most runs are no-ops, have the script produce a last-line JSON payload such as `{"wake_agent": false, "data": {}}`; `wake_agent` is a boolean and `data` is an object
- The scheduler runs the script first and only wakes the agent when `wake_agent` is `true`
- `data` carries the inputs the agent will need if it does wake; a single precheck run gates activation *and* supplies the payload
