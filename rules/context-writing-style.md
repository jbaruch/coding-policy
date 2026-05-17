---
alwaysApply: true
---

# Context Writing Style

## Scope

- Applies to artifacts that load into agent context: rules (always-on per `tile.json` steering), skills (loaded on invocation), READMEs (loaded on tile fetch)
- CHANGELOG entries are the archive — they carry the motivation and incident references stripped from rules, and follow looser discipline because they load only on demand
- Line-count and section-count budgets target single-concept rules; rules that cover one coherent policy area with several sub-aspects (e.g., `plugin-evals` covering coverage, lift, persistence, naming, hygiene) may run larger

## What to Cut

- Logical connectives used as prose connectors: therefore, however, because, since, thus, consequently, moreover, although, despite, as a result, in order to — bullet order or punctuation carries the relation. Keep when stating a real causal constraint inside a directive (e.g., "the teardown precedes branch delete — `git branch -d` refuses..." stays; "we did X because of Y" goes)
- Intensifiers used as noise modifiers: very, extremely, quite, really, fully, exactly, precisely, narrowly, completely. Keep when carrying a real constraint (`sum to exactly 100`, `fully enumerable`, `narrowly scoped`)
- Reinforcement and hedging restatements of conclusions the preconditions already entail
- Meta-justification prose explaining why the rule exists — belongs in CHANGELOG
- Reference incidents, dated outages, worked examples tracing a case through the rule — belongs in CHANGELOG
- Cross-rule rationale shoulder-taps; a bare reference suffices, the relationship stays out
- CHANGELOG pointers like "see CHANGELOG entry: PR #X" left behind after cutting incidents — rules stand alone; CHANGELOG is archive, not ongoing reference
- Anti-patterns buried in prose — bullet them instead ("X does NOT qualify" is rule content, not commentary)

## What to Keep

- File paths, command names, flag literals, version numbers, dates, function names, env vars, error codes
- Constraint-bearing words: required, optional, mandatory, forbidden, deprecated
- Carve-out preconditions in full — those preconditions are the rule for edge cases

## Structure

- Atomic bullets — one directive per bullet
- Carve-outs: lead with "Narrow exception for X.", then numbered preconditions, then a one-line reset stating every other case follows the rule
- Never comma-splice preconditions with "; AND ...; AND ..."
- 3–6 H2 sections per rule, ~25–40 lines total
- At most one parenthetical per sentence; three or more signals missing structure
- Active voice, present tense; drop articles where the referent is unambiguous
