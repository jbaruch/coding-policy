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
- A label may add a reversible gate
- A reversible gate is a recorded obligation that a recorded clear removes
- A label never removes a gate, approves, accepts, or skips a check
- A gate's level is decided by a script from the label's recorded probabilities, never by the label's own claim

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

- Narrow exception for the installed-plugin bootstrap.
- Applies only to command blocks in `skills/release/SKILL.md`, `skills/release/references/*.md`, `skills/adopt-fork-pr/SKILL.md`, `skills/onboard-repo/SKILL.md`, and `skills/migrate-to-plugin/SKILL.md`.
- Preconditions (all required):
  1. The block initializes `KS` to `.tessl/plugins/jbaruch/coding-policy/skills/<name>/`, with `<name>` the co-shipped skill invoked by that block
  2. The inline branches are exactly three: a directory test on `KS` falling back to the same path under `$HOME`, a second directory test, and one `case` on `git config --get remote.origin.url`
  3. The `case` selects `skills/<same-name>/` only on a whole-string match against an enumerated set: `git@github.com:jbaruch/coding-policy`, `https://github.com/jbaruch/coding-policy` and `ssh://git@github.com/jbaruch/coding-policy`, each with or without `.git`
  4. No accepted pattern carries a wildcard; every other origin, or none, exits non-zero with an instruction to run `tessl install jbaruch/coding-policy`
  5. The block invokes only co-shipped scripts through quoted `$KS` paths with an explicit interpreter; each independent call repeats the bootstrap
  6. Bootstrap performs no writes, network access, permission changes, sourcing, or evaluation of repository-controlled code
  7. All work after root selection stays in the invoked script; no inline business logic, loops, or additional selection heuristics
  8. `scripts/tests/test_skill_bootstrap.py` checks every covered block and executes fixtures for local precedence, global fallback, clone fallback, impostor refusal, missing installs, spaces, and mode-0644 scripts
  9. ACR consumers use the renderer's owned native skill directories with the same trailing slash and lookup branches
- Every other command block follows Scripts Are Real Files unchanged.

## Script Requirements

Scripts follow the baseline in `rules/file-hygiene.md` (exit codes, stderr, idempotency) plus these requirements:

- **JSON-producing**: output structured data, not prose
- **Self-error-handling**: exit non-zero on failure, write a diagnostic message to stderr
- **Single-purpose**: one script does one thing — compose scripts, don't build monoliths

- Narrow exception for another plugin's scoped skill lookup or stdout contract.
- Applies to an installed plugin or a plugin authored in its own verified repository.
- Preconditions are binding: read `skills/release/references/script-output-contracts.md` Scoped Plugin Contracts before relying on it.
- Every other command block follows Scripts Are Real Files and Script Requirements unchanged.

- Narrow exception for `skills/release/registry-baseline.sh` and `skills/release/confirm-tessl-landed.sh` version or verified baseline stdout.
- Applies when the wrapper's whole output becomes the next gate's version or baseline argument
- Preconditions are binding: read `skills/release/references/script-output-contracts.md` Version Stdout before relying on it.
- Every other release script retains the JSON-producing requirement

- Narrow exception for `skills/release/lib/bounded-run.sh` pass-through stdout.
- Applies when a caller runs another script under a wall-clock budget
- Preconditions are binding: read `skills/release/references/script-output-contracts.md` Bounded Runner Stdout before relying on it.
- Every other skill script retains the JSON-producing requirement

## Precheck Gating

- For scheduled or recurring tasks where most runs are no-ops, have the script produce a last-line JSON payload such as `{"wake_agent": false, "data": {}}`; `wake_agent` is a boolean and `data` is an object
- The scheduler runs the script first and only wakes the agent when `wake_agent` is `true`
- `data` carries the inputs the agent will need if it does wake; a single precheck run gates activation *and* supplies the payload
