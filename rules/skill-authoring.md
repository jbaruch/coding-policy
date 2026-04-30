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
- The first content line after the H1 must declare the skill's execution mode and prevent the agent from parallelizing or freelancing
- For **sequential workflows** (the default — release, deploy, migrate): force in-order execution: *"Process steps in order. Do not skip ahead."*
- For **action routers** (group-management, scheduler-config, anything where the agent picks one of several alternatives by user intent): force single-step execution: *"This skill is an action router — pick the step that matches the user's intent and execute only that step. Do not run other steps; do not parallelize."* Action-router skills must list the available actions in the skill's `description` so the runtime can match intent

## Step Structure

- Use flat numbered headings with descriptive titles: `## Step 1 — Verify Readiness`, `## Step 2 — Create PR`
- The same flat-numbering format applies to both sequential workflows and action routers — in routers, "Step N" labels each alternative action rather than each phase of a workflow
- No decimals, no sub-steps — flat numbering only
- When inserting a step, renumber all subsequent steps
- Each step is one action — if the step's **title** combines two verbs with "and" or "&" (e.g., "Build and Deploy", "Build & Deploy"), split it. The check is a title heuristic, not a body-atomization mandate: a step's body MAY list sub-tasks or supporting bullets that all serve one cohesive action. Splitting on every body bullet produces micro-steps for what is logically one phase
- Action-router preambles are exhaustive on chaining: if any step is meant to chain to another (e.g. "after registering a group, also configure mounts"), say so explicitly in the preamble or at the end of the originating step. The default in routers remains "execute only the chosen step and finish"

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
- In rule prose, documentation, and skill cross-references, use repo-relative paths (`skills/<name>/<file>.sh`) — stable, greppable, runtime-agnostic
- In step bodies the agent executes, use the path that resolves at the **invocation site**: repo-relative when the skill runs from a clone of this repo (e.g., `skills/release/poll-pr-reviews.sh`); the consumer's tile-mount path when the skill runs inside a consumer repo (e.g., `.tessl/tiles/jbaruch/coding-policy/skills/install-reviewer/preflight.sh`, or whichever absolute path the consumer's runtime documents — container mounts like `/home/node/.claude/...` are common for hosted runners). The two shipped skills in this tile each exemplify one case: `release/SKILL.md` runs from a clone (repo-relative), `install-reviewer/SKILL.md` runs inside a consumer (mount path)
- Don't mix conventions inside one SKILL.md — if Step 3 invokes via a mount path, every other step must too
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
