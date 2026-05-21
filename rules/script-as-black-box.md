---
alwaysApply: false
applyTo: "skills/**/SKILL.md, skills/**/*.md — when authoring skills or reference files that invoke scripts"
description: Skills reference the script's contract (inputs, outputs, exit codes, emitted shapes), not its internal logic — thresholds, predicates, formulas, and allowlists live in the script
---

# Script as Black Box

## The Principle

- Skill prose names the script's contract — required inputs, output shape, exit codes, side effects, comment-text shapes the script emits verbatim
- Skill prose does not restate the script's internal logic — thresholds, predicates, formulas, allowlists, source lists, filter rules
- The script is the source of truth; skill prose points at it (`see <script-path> — named constants at the top of the file, docstring, or named frozenset`)
- Script paths in skill prose follow `rules/skill-authoring.md` Script References — repo-relative `skills/<name>/<file>.<ext>` in this tile, tile-mount paths when running inside a consumer
- Composes with `rules/script-delegation.md` for when to extract a script in the first place

## What Skills Do Not Restate

- Numeric thresholds, multipliers, percentages, time windows, minimum-history requirements
- Boolean predicates and condition expressions (`fires >= 1 AND gated_likely == 0`)
- Enumerated allowlists, blocklists, source lists, per-source / per-tier classification rules
- Formulas and computed values (proposed-cap arithmetic, regression-drop calculation)
- Rationale paragraphs explaining why a constant is set at its current value — that belongs in the script's docstring or the CHANGELOG, not the skill

## What Skills Do Carry

- Required inputs and output shape
- Exit codes and error conditions
- Suppression conditions that affect skill flow (e.g., `prior snapshot absent ⇒ no fire`)
- Verbatim-posted text shapes the script emits, surfaced as templates

## How to Reference

- Name the file and an anchor inside it — top-of-file constants, named frozenset, docstring, function name
- Example: `see skills/release/resolve-publish-run.sh — argument contract and filter logic in the top-of-file docstring`
- One reference per concept across all of a skill's files
- Do not fan the same reference across `SKILL.md` and `references/*.md`
- Pick the file closest to where the agent reads the contract and reference from the others
