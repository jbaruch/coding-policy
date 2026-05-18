---
alwaysApply: true
---

# Author-Model Declaration

## How to Declare

- Explicit (preferred): add an `**Author-Model:**` line near the top of the PR body — `**Author-Model:** claude-opus-4-7`
- Implicit (fallback): a `Co-authored-by:` git trailer on any commit in the PR (Claude Code emits this by default)
- Human-authored PRs: `**Author-Model:** human`
- Mixed authorship: list every model that contributed, space-separated — `**Author-Model:** human claude-opus-4-7`

## Precedence

- Every PR must carry a signal
- PR body `Author-Model:` wins when both exist (explicit beats implicit)
- Trailer-only: known display names normalize to canonical IDs (e.g., `Claude Opus 4.7` → `claude-opus-4-7`); unknown display names are accepted as ad-hoc model IDs
- Neither present blocks the PR — the reviewer exits with `REQUEST_CHANGES` before reading the diff

## Model Families

- `claude-*` → anthropic
- `gpt-*`, `codex-*` → openai
- `gemini-*` → google
- Unknown strings default to their own ad-hoc family

## Mixed Authorship

- If any AI model contributed, the reviewer family must differ from every declared AI family (worst-case matching)
- `**Author-Model:** human claude-opus-4-7` is claude-authored for family-mismatch purposes — the openai-family reviewer runs, the claude-family reviewer skips
- Declaration spans both paired reviewer families (e.g., `gpt-5.4 claude-opus-4-7`): both run as degraded fallback. Avoid co-authoring across both paired families to preserve cross-family review
- Declaration spans neither paired family (e.g., `gemini-2.5`, custom model, `human`-only): both paired reviewers run — both are cross-family relative to the author; the duplicate review is accepted noise
