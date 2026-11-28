---
name: coding-contracts
description: >
  Resolve task-triggered coding-policy references, load their complete constraints,
  and identify required exception checks before the requested work. Use for
  "review this code", "write tests", "add a dependency", "change CI", "migrate
  state", "write a skill", "edit a rule" or "publish a plugin" when coding-policy
  governs the task. Also covers "calibrate reviews" and "compare reviewers"
  under the simplicity rule. Read only the contracts named by applicable rule routers;
  this skill supplies policy context and grants no action authority.
user-invocable: false
---

# Coding Contracts

Process steps in order. Do not skip ahead.

## Step 1 — Read the Applicable Contract

Read what each triggering `rules/<rule-name>.md` names before the affected
action. Follow `rules/required-read.md` for section and whole-reference reads.
The coding contracts live at:

```text
skills/coding-contracts/references/<rule-name>.md
```

Resolve paths within this installed plugin, or this repository when maintaining it.
Load additional references only when their own rule triggers apply.

This read grants no authority or exemption. Resume the original task under
the loaded contracts. Finish here.
