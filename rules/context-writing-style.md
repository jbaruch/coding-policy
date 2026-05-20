---
alwaysApply: false
applyTo: "rules/**/*.md, skills/**/*.md, README.md, CHANGELOG.md — when writing prose in tile context artifacts"
description: Prose discipline for rules, skills, and READMEs — what to cut, what to keep, structural format
---

# Context Writing Style

## Scope

- Applies to auto-loaded artifacts: rules from `tile.json` steering, skills on invocation, READMEs on tile fetch
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

- When you need the motivation, incident, or worked example behind a rule's directive — the rule itself won't carry it; the relevant tile's `CHANGELOG.md` is the archive
- A rule that touches multiple tiles has motivation spread across multiple CHANGELOGs; read each tile's archive, don't stop at one
- Read on demand only: CHANGELOGs are not auto-loaded; pull them up when judging an edge case, debugging an unexpected directive, or auditing whether a rule still describes current reality
- The rule body's silence on rationale is by design — not an omission. Don't infer the rule is incomplete; consult the archive instead

## Structure

- Atomic bullets — one directive per bullet
- Carve-outs: lead with "Narrow exception for X.", then numbered preconditions, then a one-line reset stating every other case follows the rule
- Never comma-splice preconditions with "; AND ...; AND ..."
- 3–6 H2 sections per rule, ~25–40 lines total
- At most one parenthetical clause per sentence — break out a second clause into its own bullet. Function-call notation (`Skill()`, `print()`, etc.), label tags inside lists, and other literal-syntax parens do not count as parenthetical clauses
- Active voice, present tense; drop articles where the referent is unambiguous
