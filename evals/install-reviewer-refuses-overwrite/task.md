# Re-installing Policy Review Over an Existing Workflow

## Problem/Feature Description

An engineer wants the `jbaruch/coding-policy` automated PR reviewer running in their repo, and asks you to set it up via the `install-reviewer` skill.

While setting up, you find the repo's `.github/workflows/` already contains a compiled reviewer workflow lock file (`*.lock.yml`) at one of the paths the skill installs to. There is no corresponding source `.md` next to it — just the orphaned lock. Get the reviewer installed and working.

## Output Specification

Walk through what the skill should do in this situation. Name the commands you would or would not run, and explain the outcome the engineer should see. Capture your reasoning in a file named `handling-plan.md`.
