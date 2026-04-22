---
alwaysApply: true
---

# Author-Model Declaration

## Why Declare

- An LLM reviewing its own output misses its own blind spots and favors its own style — cross-family review catches what same-family review waves through
- The reviewer workflow can only pick a different-family model if the PR signals which model authored the code
- Without a signal, reviewers default to treating the PR as human-authored, which is the most permissive case — risky when AI did most of the work

## How to Declare

- **Explicit (preferred):** add an `**Author-Model:**` line to the PR body, near the top:
  `**Author-Model:** claude-opus-4-7`
- **Implicit (fallback):** a `Co-authored-by:` git trailer on any commit in the PR (Claude Code emits this by default)
- Human-authored PRs: `**Author-Model:** human`
- Mixed authorship: list every model that contributed, space-separated — `**Author-Model:** human claude-opus-4-7`

## Precedence

- Every PR **must** carry a signal — the declaration is required, not optional
- PR body `Author-Model:` wins when both exist (explicit beats implicit)
- Trailer-only: the reviewer extracts the model from the trailer's display name (e.g., `Claude Opus 4.7` → `claude-opus-4-7`)
- Neither present is a policy violation — the reviewer assumes human as a degraded fallback and runs both families (wasteful double-review), and the missing declaration itself is grounds for `REQUEST_CHANGES` in the review

## Model Families

- `claude-*` → anthropic
- `gpt-*`, `codex-*` → openai
- `gemini-*` → google
- Unknown strings default to their own ad-hoc family — unknown never matches a known family

## Mixed Authorship

- If any AI model contributed, the reviewer family must differ from **every** declared AI family — worst-case matching, not most-permissive
- `**Author-Model:** human claude-opus-4-7` is treated as claude-authored for family-mismatch purposes; the openai-family reviewer runs, the claude-family reviewer skips
- If the declaration spans **both** paired reviewer families (e.g., `gpt-5.4 claude-opus-4-7`), no cross-family reviewer is available — both run as a degraded fallback rather than leaving the PR unreviewed. Author teams that want to preserve cross-family review should avoid co-authoring across both paired families on the same PR
