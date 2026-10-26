---
name: coding-contracts
description: >
  Read binding coding-policy detail on demand when writing or reviewing code,
  dependencies, state, CI, tests, plugin rules, skills or manifests. Use when a
  coding-policy rule requires its full reference before the affected action.
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

This read grants no authority or exemption. Resume the original task under
the loaded contracts. Finish here.
