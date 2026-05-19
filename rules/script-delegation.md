---
alwaysApply: false
applyTo: "skills/**, scripts/**, skills/**/*.sh, skills/**/*.py, skills/**/SKILL.md — when authoring deterministic scripts that skills invoke"
description: Deterministic operations → script, reasoning → LLM, the regex trap, script structure conventions, precheck gating
---

# Script Delegation

## The Core Principle

- Everything deterministic → script. Everything requiring reasoning → skill/LLM
- If the logic can be expressed as a pure function with known inputs and outputs, it's a script
- If it requires judgment, synthesis, or context-dependent decisions, it stays in the skill

## What Belongs in a Script

- Database queries, math operations, file parsing
- JSON normalization, fixed-logic API polling, data transformation
- Any operation where the same input always produces the same output

## What Stays in the LLM

- Synthesis across multiple sources, language generation
- Branching decisions that require situational context
- Anything where the "right answer" depends on understanding intent

## The Regex Trap

- Resist the over-eager urge to declare things deterministic on a regex hunch
- If the input has too many edge cases for a reasonable regex, it's reasoning — not scripting
- Parsing natural language dates, extracting meaning from unstructured text, classifying ambiguous input — these are **not** scripting tasks
- A script should only handle patterns that are fully enumerable

## Scripts Are Real Files

- Scripts are executable files that live in the tile (e.g., `scripts/request-review.sh`) — not code blocks in SKILL.md for the agent to copy-paste
- The skill references the script and runs it; the script does the work
- Code blocks in SKILL.md are for showing the agent what command to run, not for embedding logic the agent should reproduce character-by-character

## Script Requirements

Scripts follow the baseline in `rules/file-hygiene.md` (exit codes, stderr, idempotency) plus these Tessl-specific requirements:

- **JSON-producing**: output structured data, not prose
- **Self-error-handling**: exit non-zero on failure, write a diagnostic message to stderr
- **Single-purpose**: one script does one thing — compose scripts, don't build monoliths

## Precheck Gating

- For scheduled or recurring tasks where most runs are no-ops, have the script produce a last-line JSON payload such as `{"wake_agent": false, "data": {}}`; `wake_agent` is a boolean and `data` is an object
- The scheduler runs the script first and only wakes the agent when `wake_agent` is `true`
- `data` carries the inputs the agent will need if it does wake; a single precheck run gates activation *and* supplies the payload
