# Request: Document Rule Frontmatter Conventions on docs.tessl.io

> **For:** The doc-authoring agent working on `docs.tessl.io`.
> **From:** A plugin author who hit the gap while building `jbaruch/coding-policy`.
> **Status:** Change request with rationale per page. Treat the page-by-page sections below as the work items. Write each addition in the voice and style of the surrounding page; the briefs name what to cover and why, not the literal prose to drop in.

## Why This Request Exists

Tessl rule files are markdown with YAML frontmatter. The CLI preserves that frontmatter byte-identical at install time (no per-agent translation, no field filtering), and the agent's model reads it as part of the rule content — including any scope declared via `alwaysApply` and `applyTo:`. This is verified behavior, both empirically (`diff` between published source and installed `.tessl/plugins/<workspace>/<plugin>/rules/<rule>.md`) and via a Tessl-engineering test where a rule scoped to TypeScript fired on a TS fizzbuzz and skipped a Python one.

None of this is documented on `docs.tessl.io` today. Authors land on `reference/configuration.md` or `create/developing-tiles-locally.md`, see the directory layout and the `rules` list, but find nothing about:

- What frontmatter rule files may carry.
- That `alwaysApply` and `applyTo:` live in the rule file frontmatter (the manifest lists rule paths only).
- How to express scoped activation (`applyTo:` and aliases).
- What Tessl writes per agent at install time.

Without that, authors default to `alwaysApply: true` on every rule (because it's the only example present) and lose the activation-narrowing capability the model is already prepared to honor. The change request below distributes the missing content across the right existing pages, plus updates a glossary entry that's currently misleading.

## Sources of Truth to Verify Against

Before writing, please confirm each claim against:

1. **Live install behavior.** `tessl install <some-plugin>` and inspect what's written: `.tessl/plugins/<workspace>/<plugin>/rules/<rule>.md` for source preservation, `.tessl/RULES.md` for the `@import` index, and `.cursor/rules/tessl__rule__*.mdc` (when `--agent cursor`) for the Cursor wrapper structure.
2. **Engineering's TypeScript-scoping test.** Replicate or reference it as the canonical "the model honors frontmatter scope" example.
3. **The plugin.json manifest schema.** The manifest's `rules` field lists rule file paths (or a `rules/` directory); it carries no per-rule config. Scope (`alwaysApply`, `applyTo:`) lives only in the rule file frontmatter. The legacy `tile.json` form had a `steering` map with a per-entry `alwaysApply`; the plugin form drops it, so the rule file frontmatter is the single source of scope.

If any of the above doesn't match what you observe, push back before writing — the gap-filling needs to land on accurate behavior, not on a stale model.

---

## Change A — `reference/configuration.md`

**Current gap:** The page documents `.tessl-plugin/plugin.json` and shows a `rules` list, but neither the `alwaysApply` field nor rule-file frontmatter scoping is described. Authors see how to register a rule path but find nothing on how a rule narrows its own activation.

**What to change:**

1. Note in the `rules` field description that each listed rule file may carry frontmatter that controls its activation, and point at the new section below.
2. Add a new H2 section, `Rule file frontmatter`, immediately after the `rules` description. This section covers:
   - Tessl preserves rule file frontmatter byte-identical at install — no per-agent translation, no field filtering.
   - The agent's model reads frontmatter as natural-language instruction and applies the rule according to its declared scope.
   - The two patterns: **always-on rules** (`alwaysApply: true`, no `applyTo:`) and **conditional rules** (`alwaysApply: false` plus `applyTo:` with the required em-dash glob+prose pattern — `"<globs> — <natural-language clause>"`).
   - That scope lives only in the rule file frontmatter — `plugin.json` lists rule paths and carries no per-rule config.
   - That `applyTo:` accepts field-name aliases (`globs:`, `paths:`) with the same em-dash glob+prose value, and that `description:` is a rule summary, never a substitute for `applyTo:` as a scoping mechanism.
3. Give one example each of an always-on rule and a conditional rule. Pick examples that are universally meaningful — e.g., commit conventions for the always-on case, manifest hygiene scoped to `**/package.json,**/pyproject.toml,**/go.mod,**/Cargo.toml` for the conditional case.

**Why this page:** It's the canonical reference for `plugin.json` structure. The `rules` list is already here; rule file frontmatter is its natural companion.

## Change B — `create/developing-tiles-locally.md`

**Current gap:** The page's structure section shows a directory tree with `rules/standards.md` and labels the directory "Mandatory guidance - always loaded". Nothing tells authors that rule files can carry frontmatter, that the frontmatter controls activation, or where to learn the convention.

**What to change:** Add a brief "Rule file shape" subsection immediately after the directory tree. Cover:

- Rule files are markdown with an optional YAML frontmatter block.
- The frontmatter is preserved at install and read by the agent's model for scoping.
- Link to the new `Rule file frontmatter` section on `reference/configuration.md` for the full convention.
- Include one minimal example showing a scoped rule (so the reader sees what frontmatter looks like in practice).

Keep this short — this page is for the development workflow, not the reference. The example belongs here; the deep convention belongs on the configuration page.

**Why this page:** Authors creating their first plugin read this page. A pointer here saves them from discovering the convention only after they've shipped a plugin with everything defaulted to always-on.

## Change C — `reference/custom-agent-setup.md`

**Current gap:** The page mentions that Cursor, Claude, Gemini, Copilot, and Codex are auto-detected, and refers to MCP setup for others. It does not document which files Tessl actually writes for each agent. Authors debugging "why isn't my rule reaching this agent" have nowhere to look.

**What to change:** Add a new H2 section, `What tessl install writes per agent`, between "Configuring your agent" and "Validating configuration". Two tables:

1. **Always written (every install):** the path → purpose map for `.tessl/plugins/<workspace>/<plugin>/...`, `.tessl/RULES.md`, `tessl.json`, `.mcp.json`, `AGENTS.md`.
2. **Per-agent additions:** for each supported `--agent` value (`claude-code`, `cursor`, `gemini`, `codex`, `copilot`, `copilot-vscode`), the additional files written and the path by which rules reach the agent.

Note that the source rule file in `.tessl/plugins/.../rules/<rule>.md` is byte-identical to the published source, regardless of agent.

**Why this page:** This is the agent setup reference. Per-agent install output is the missing operational detail authors need when something doesn't work as expected.

## Change D — `reference/glossary.md`

**Current gap:** The **Rules** entry reads "Mandatory steering for the agent to always follow, always pushed to the agent's context (eager push)." That framing is misleading: it conflates loading (which is always-on) with activation (which is what `alwaysApply` and `applyTo:` actually control). Authors who read this entry conclude "every rule always applies" and never reach for the scope mechanism. There are also no entries for **Frontmatter** or **alwaysApply**.

**What to change:**

1. Rewrite the **Rules** entry. New framing: rules are steering for the agent; the rule's frontmatter declares whether the rule is always applied (`alwaysApply: true`) or conditional on `applyTo:` matching the current file or task. Cross-reference the new **Frontmatter** entry.
2. Add **Frontmatter**: the optional YAML metadata block at the top of a rule file; preserved verbatim by Tessl at install; read by the agent's model as part of the rule content; the only place rule scope is declared.
3. Add **alwaysApply**: the boolean field in a rule file's frontmatter; `true` for always-on rules, `false` for conditional rules (which then declare scope via `applyTo:`). It is not a manifest field.

**Why this page:** The glossary is the first stop for definitional clarity. The current Rules entry is the strongest single source of the "every rule always fires" misconception.

## Change E — `introduction-to-tessl/how-tessl-works.md`

**Current gap:** The "Types of context" table shows rules as "Always (eager push)". Same issue as the glossary: the framing conflates loading with activation.

**What to change:** Add a sentence directly after the table clarifying that eager push describes loading (every rule is loaded into the agent's context at install time) and that the rule's frontmatter controls whether it applies to a given task. Link to the new `Rule file frontmatter` section on `reference/configuration.md`.

**Why this page:** It's where the eager-vs-lazy framing originates. Fixing it at the source prevents the same misconception from propagating into every downstream page that references this model.

---

## Style and Voice Notes

- Match each existing page's tone. `reference/configuration.md` is dense and example-heavy; `create/developing-tiles-locally.md` is workflow-oriented; the glossary is one-paragraph definitions.
- Use real examples drawn from common plugin shapes (commit conventions, manifest hygiene, test file conventions). Avoid contrived `foo`/`bar` examples — the convention reads better with concrete file paths.
- Don't introduce new terminology. "Always-on rule" and "conditional rule" are the working pair; don't invent synonyms.
- Keep cross-references consistent: the configuration page hosts the canonical convention; every other addition links back to it.

## Out of Scope for This Request

- The Tessl MCP server's runtime rule-serving semantics (how `tessl mcp start` decides what to surface on which call). If relevant to authors, that's a separate addition to `reference/mcp-tools.md`.
- Any change to the install pipeline itself. This request is purely documentation — describing existing behavior accurately.
- Any new lint rules or warnings. If `tessl plugin lint` should validate rule-file frontmatter scope, that's a separate request to the CLI team.
