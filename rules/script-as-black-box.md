---
alwaysApply: false
applyTo: "skills/**/SKILL.md, skills/**/*.md — when authoring skills or reference files that invoke scripts"
description: Skills reference the script's contract (inputs, outputs, exit codes, emitted shapes), not its internal logic — thresholds, predicates, formulas, and allowlists live in the script
---

# Script as Black Box

## The Principle

- Skill prose names the script's contract — required inputs, output shape, exit codes, side effects, comment-text shapes the script emits verbatim
- Skill prose does not restate the script's internal logic — thresholds, predicates, formulas, allowlists, source lists, filter rules
- The script is the source of truth; skill prose points at it (`see scripts/<name>` — named constants at the top of the file, docstring, or named frozenset)
- Composes with `rules/script-delegation.md` (extraction) and `rules/skill-authoring.md` (Script References)

## What Skills Do Not Restate

- Numeric thresholds, multipliers, percentages, time windows, minimum-history requirements
- Boolean predicates and condition expressions (`fires >= 1 AND gated_likely == 0`)
- Enumerated allowlists, blocklists, source lists, per-source / per-tier classification rules
- Formulas and computed values (proposed-cap arithmetic, regression-drop calculation)
- Rationale paragraphs explaining why a constant is set at its current value — that belongs in the script's docstring or the CHANGELOG, not the skill

## What Skills Do Carry

- Required inputs and output shape — agent needs this to invoke the script and parse its result
- Exit codes and error conditions — agent needs this to branch on failure
- Suppression conditions that affect skill flow (e.g., `prior snapshot absent ⇒ no fire`) — they shape the agent's decision tree even though the script enforces them
- Verbatim-posted text shapes the script emits — surfaced as templates the agent recognizes, not as logic the agent reconstructs

## How to Reference

- Name the file and an anchor inside it — top-of-file constants, named frozenset, docstring, function name
- Example: `see scripts/compute-drift.py — CADENCE_CAP_SKILLS frozenset and named constants at the top of the file`
- One reference per concept — do not fan the same rule across multiple skill files (`SKILL.md`, `references/*.md`); pick the file closest to where the agent reads it and reference from the others
