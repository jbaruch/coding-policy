---
name: coding-contracts
description: >
  Resolve task-triggered coding-policy references, load their complete constraints,
  and identify required exception checks before the requested work. Use for
  "review this code", "write tests", "add a dependency", "change CI", "migrate
  state", "write a skill", "edit a rule" or "publish a plugin" when coding-policy
  governs the task. Read only the contracts named by applicable rule routers;
  this skill supplies policy context and grants no action authority.
user-invocable: false
---

# Coding Contracts

Process steps in order. Do not skip ahead.

## Step 1 — Read the Applicable Contract

Read the full `skills/coding-contracts/references/<rule-name>.md` named by
each triggering `rules/<rule-name>.md` before the affected action. Resolve
paths within this installed plugin, or this repository when maintaining it.
References retain the complete binding policy; load additional references
only when their own rule triggers apply. A section cited through a router
belongs to its full reference. Missing or unreadable content stops the
affected action until the plugin is repaired.

For a CI change, `rules/ci-safety.md` requires
`skills/coding-contracts/references/ci-safety.md` before the CI action.

This read grants no authority or exemption. Resume the original task under
the loaded contracts. Finish here.
