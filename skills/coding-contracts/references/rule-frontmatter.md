# Rule Frontmatter

## Passthrough

- Tessl preserves rule file frontmatter byte-identical from publish to install — no per-agent translation, no field filtering
- Every rule a host loads reaches the consuming agent's context with its bytes and frontmatter unchanged
- A host loads whole rule files as published

## Rule Loading Tiers

- A runtime host assembling an agent's context loads the conditional rules that match the run
- A conditional rule declares `alwaysApply: false` or a `requires:` list in its frontmatter
- A rule declaring neither is always loaded
- A host that selects conditional rules answers whether each one matches the run at the cheapest tier able to answer:
  1. **Host script** — a deterministic predicate over the rule's frontmatter and run facts the host holds: agent tier, session kind, skills or capabilities present, paths the run touches
  2. **Bounded classification** — a question within approved bounds per `rules/script-delegation.md` Bounded Classification
  3. **Reasoning model** — the host loads the rule and the agent's model applies its declared scope
- Tiers 1 and 2 each answer match, no match or undecided
- The host loads a matching rule
- A tier-1 or tier-2 no match leaves the rule unloaded
- An undecided rule goes to the next tier
- At tier 3 the rule stays loaded and the agent's model applies its declared scope
- A host that does not select loads every rule, and the model applies scope for all of them

## Host Script Tier

- A rule whose `requires:` list names no skill or capability present in the session does not match
- A host may declare an agent tier or session kind that no conditional rule's scope covers
- In a run of a declared tier or kind, no `alwaysApply: false` rule matches
- A run touching no path the `applyTo:` globs match does not match the rule
- The natural-language half is not script-readable
- A glob match alone leaves the rule undecided
- Missing, malformed or unrecognized frontmatter loads the rule
- The script owns its predicates per `rules/script-as-black-box.md`

## Bounded Classification Tier

- The question asks whether the rule matches this run, from the rule's frontmatter and the run facts the host holds
- A tier-2 no-match answer takes effect only within the standing approval `rules/script-delegation.md` Bounded Classification names
- "Not enough evidence", an unavailable classifier and an answer outside the list each load the rule
- A loaded rule's frontmatter reaches tier 3 unchanged

## Reasoning Model Tier

- The agent's model reads the frontmatter and applies the rule according to its declared scope
- Verified: a published rule scoped to TypeScript fires on TS files and skips Python files

## Always-On Rules

- For rules that apply regardless of file or task context (commits, secrets, code formatting, error handling): set `alwaysApply: true` in the rule file frontmatter
- Omit `applyTo:` — the rule has no narrowed scope
- A `requires:` list still gates an always-on rule on the named skills or capabilities
- Within that gate, `alwaysApply: true` applies regardless of file or task context
- `description:` is optional for always-on rules; add it when the README rules-table row needs a complement, otherwise the rule body's H1 + sections speak for themselves

## Conditional Rules

- For rules whose prescriptions only fire in specific files or contexts: set `alwaysApply: false` in the rule file frontmatter and add `applyTo:`
- `applyTo:` syntax is `"<glob list> — <natural-language clause>"` — both halves required. Example: `applyTo: "skills/**/SKILL.md — when authoring or modifying skills"`
- The em dash separates the file-level scope (globs) from the action-level scope (prose)
- The globs cover every file the rule governs; the prose narrows them by action and never widens past them
- A rule governing an action in any file uses the `**` glob with the action in the prose
- `globs:` and `paths:` are accepted **field-name aliases** for `applyTo:` — the value still follows the em-dash glob+prose form; pick one field name per file
- `description:` is a rule summary, not a scoping mechanism — never put scope prose only in `description:` as a substitute for `applyTo:` on a conditional rule

## When to Path-Scope

- Path-scope when **every** prescription in the rule is bound to a specific file set or context (plugin-authoring rules whose content only fires inside `rules/`, `skills/` are good candidates)
- Stay universal when the rule mixes file-bound and broad guidance — `applyTo:` is exclusionary at every tier, so a too-narrow scope drops the broad bits
- Example — `dependency-management` covers two practices:
  - "stdlib first" fires when writing code
  - "pin versions" fires when editing manifests
- Scoping `dependency-management` to manifests would silently drop the code-level guidance, so the rule stays universal

## Frontmatter Is the Only Scope Surface

- `alwaysApply`, `applyTo:` and `requires:` live only in the rule file frontmatter — `.tessl-plugin/plugin.json` lists rule paths in its `rules` array and carries no per-rule config
- Install-time write map per agent — `docs/tessl-rule-frontmatter.md` is the repo-internal draft of additions for `docs.tessl.io`. The `docs/` directory is `.tesslignore`d and does not ship with the published plugin, so consumers read the integrated content on `docs.tessl.io` once it lands; until then, read the source on GitHub
