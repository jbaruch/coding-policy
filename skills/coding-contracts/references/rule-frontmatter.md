# Rule Frontmatter

## Passthrough

- Tessl preserves rule file frontmatter byte-identical from publish to install — no per-agent translation, no field filtering
- Every rule a host includes reaches the consuming agent's context with its bytes and frontmatter unchanged
- A host never rewrites, trims, merges or translates an included rule
- Omitting a whole rule is the only filtering a host performs

## Context Assembly Tiers

- A runtime host assembling an agent's context may omit a conditional rule
- A conditional rule declares `alwaysApply: false` or a `requires:` list in its frontmatter
- A rule declaring neither is always included
- A host that filters decides each conditional rule at the cheapest tier able to decide it:
  1. **Host script** — a deterministic predicate over the rule's frontmatter and run facts the host holds: agent tier, session kind, skills or capabilities present, paths the run may touch
  2. **Bounded classification** — a question within approved bounds per `rules/script-delegation.md` Bounded Classification
  3. **Reasoning model** — the rule is included and the agent's model applies its declared scope
- Each tier returns include, omit or undecided
- An undecided rule passes to the next tier
- Tier 3 decides only rules the cheaper tiers left undecided
- A host that does not filter includes every rule, leaving every decision to tier 3

## Host Script Tier

- A `requires:` list naming no skill or capability present in the session omits the rule
- A host may declare an agent tier or session kind outside every conditional rule's scope
- For a run of a declared tier or kind, the script omits every `alwaysApply: false` rule
- The glob half of `applyTo:` is script-readable
- The natural-language half is not script-readable
- A glob non-match alone leaves the rule undecided
- Missing, malformed or unrecognized frontmatter keeps the rule included
- The script owns its predicates per `rules/script-as-black-box.md`

## Bounded Classification Tier

- The question asks whether the rule applies to this run, from the rule's frontmatter and the run facts the host holds
- Omission on a tier-2 answer requires the standing approval `rules/script-delegation.md` Bounded Classification names
- "Not enough evidence", an unavailable classifier and an answer outside the list each keep the rule included
- An included rule's frontmatter reaches tier 3 unchanged

## Reasoning Model Tier

- The agent's model reads the frontmatter and applies the rule according to its declared scope
- Verified: a published rule scoped to TypeScript fires on TS files and skips Python files

## Always-On Rules

- For rules that apply regardless of file or task context (commits, secrets, code formatting, error handling): set `alwaysApply: true` in the rule file frontmatter
- Omit `applyTo:` — the rule has no narrowed scope
- `description:` is optional for always-on rules; add it when the README rules-table row needs a complement, otherwise the rule body's H1 + sections speak for themselves

## Conditional Rules

- For rules whose prescriptions only fire in specific files or contexts: set `alwaysApply: false` in the rule file frontmatter and add `applyTo:`
- `applyTo:` syntax is `"<glob list> — <natural-language clause>"` — both halves required. Example: `applyTo: "skills/**/SKILL.md — when authoring or modifying skills"`
- The em dash separates the file-level scope (globs) from the action-level scope (prose); a reader at tier 2 or 3 reads both halves and narrows on whichever signal is clearer
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
