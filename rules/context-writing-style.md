---
alwaysApply: false
applyTo: "rules/**/*.md, skills/**/*.md, README.md, CHANGELOG.md — when writing prose in plugin context artifacts"
description: Prose discipline for rules, skills, and READMEs — what to cut, what to keep, structural format
---

# Context Writing Style

## Scope

- Applies to auto-loaded artifacts: rules declared in `.tessl-plugin/plugin.json`, skills on invocation, READMEs on plugin fetch
- CHANGELOG entries load only on demand, not always-on
- CHANGELOG entries are the archive — they carry the motivation and incident references stripped from rules, and follow looser discipline
- Line-count and section-count budgets target single-concept rules; rules that cover one coherent policy area with several sub-aspects (e.g., `plugin-evals` covering coverage/lift/persistence/naming/hygiene; `context-artifacts` covering structure/review/sync/audit; `skill-authoring` covering frontmatter/preamble/steps/calls) may run larger
- Lists and quoted literals naming the forbidden terms themselves (e.g., this rule's own bullets enumerating the connectives and intensifiers) are not violations — the directive is the list, not prose use of the listed words

## What to Cut

- Logical connectives are why-content detectors: therefore, however, because, since, thus, consequently, moreover, although, despite, as a result, in order to
- When you write one of these, strip the entire clause it introduces and move the explanation to CHANGELOG
- Em-dash, colon, and semicolon are not loopholes — using them to attach the same justifying clause is the same violation
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
- Carve-out preconditions in full (preconditions are the rule for edge cases)

## Reader-Side — Consult the CHANGELOGs

- The relevant tile's `CHANGELOG.md` is the archive for motivation, incidents, and worked examples cut from rule bodies
- Read each tile's archive when a rule spans tiles
- Do not stop after one tile's CHANGELOG
- Pull a CHANGELOG when judging an edge case
- Pull a CHANGELOG when debugging an unexpected directive
- Pull a CHANGELOG when auditing whether a rule still describes current reality
- Never infer that missing rationale in a rule body means the rule is incomplete

## Structure

- Atomic bullets — one directive per bullet
- Carve-outs: lead with "Narrow exception for X.", then numbered preconditions, then a one-line reset stating every other case follows the rule
- Never comma-splice preconditions with "; AND ...; AND ..."
- 3–6 H2 sections per rule, ~25–40 lines total
- At most one parenthetical clause per sentence — break out a second clause into its own bullet. Function-call notation (`Skill()`, `print()`, etc.), label tags inside lists, and other literal-syntax parens do not count as parenthetical clauses
- Active voice, present tense; drop articles where the referent is unambiguous
