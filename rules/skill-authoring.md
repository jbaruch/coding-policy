---
alwaysApply: true
---

# Skill Authoring

## SKILL.md Frontmatter

- Required fields: `name`, `description` (include trigger phrases so the agent knows when to activate)
- Optional fields: `allowed-tools`, `disable-model-invocation`, `user-invocable` (set to `false` for background-knowledge skills the runtime loads as context but the user should never invoke directly)
- The `description` field is your discovery surface — write it for the agent, not a human audience

## Title and Preamble

- Start the body with an `# H1` title that names the skill (e.g., `# Release Skill` for `skills/release/SKILL.md`)
- The first content line after the H1 must force sequential execution: process steps in order, do not skip ahead
- This prevents the agent from parallelizing steps that depend on earlier output

## Step Structure

- Use flat numbered headings with descriptive titles: `## Step 1 — Verify Readiness`, `## Step 2 — Create PR`
- No decimals, no sub-steps — flat numbering only
- When inserting a step, renumber all subsequent steps
- Each step is one action — if a step has an "and", split it

## Step Continuity

- The default handoff between steps is "continue immediately" — never "pause and wait for the user"
- When a step hands off to the next, state the continuation explicitly ("Proceed immediately to Step N"); don't rely on implicit reading order
- If a step can legitimately end the skill, say so explicitly ("Finish here"); otherwise the agent will keep going
- Ambiguity at step boundaries is the most common cause of agents stalling mid-skill waiting for a nudge that was never needed

## Keep Skills Compact

- SKILL.md is the execution plan, not a reference manual
- Move detailed reference material (API docs, long examples, lookup tables) to separate files
- Reference them with typed code blocks and full relative paths

## Typed Calls

- Invoke other skills with typed `Skill(skill: "name")` calls, never prose references
- Never call `Skill()` on a rule — rules are auto-loaded, not invoked

## Silence Instructions

- If a step can legitimately produce no output, say so explicitly: "If no issues found, proceed silently"
- Prevents the agent from fabricating output to fill the gap

## Script References

- Deterministic operations must be executable script files, not inline code blocks for the agent to copy-paste — see `rules/script-delegation.md`
- Reference scripts with full paths: `` `scripts/foo.sh` ``
- Include the expected input/output contract in the step description

## tile.json Manifest Reference

Required fields:
- `name` — `<workspace>/<tile-name>` format
- `version` — semver string
- `summary` — one-line description of the tile
- `entrypoint` — path to the tile's README (typically `README.md`)

Optional fields:
- `private` — `true` to prevent publishing to the public registry
- `docs` — path to extended documentation (avoid — keep docs in the entrypoint to prevent duplicate tables that drift)
- `keywords` — array of discovery tags
- `skills` — map of skill names to `{ "path": "skills/<name>/SKILL.md" }`
- `steering` — map of rule names to `{ "rules": "rules/<name>.md", "alwaysApply": true }`
