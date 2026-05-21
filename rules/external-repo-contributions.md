---
alwaysApply: true
---

# External Repo Contributions

## Default Deny

- Never open issues, pull requests, comments, reactions, or discussions in repositories the operator does not own unless the operator has explicitly granted permission for that specific target
- "Own" means the operator is the namespace owner of the repo (user account or org they administer) — not collaborator, not contributor, not employee of the org
- Covered actions: filing issues, sending PRs, posting comments on existing issues / PRs / discussions, opening discussions, applying reactions

## What Counts as Permission

- Operator explicitly names the target repo and the action in the current conversation (e.g., "open an issue at `<owner>/<repo>` about <topic>", "send a PR upstream to `<owner>/<repo>` fixing <X>")
- A standing instruction in the conversation, the project's CLAUDE.md, or memory naming the target repo and the authorized action types
- Asking the operator and getting an affirmative answer that names the target repo, the action type, and the content preview

## Common Patterns That Do NOT Qualify

- Permission to fix bug X in the current repo is NOT permission to file the same bug upstream — every external target requires its own consent
- A local fix that would also help upstream does NOT permit upstream contribution
- An external project's bug being the root cause of the current task does NOT permit filing it there
- A "this should be reported" instinct does NOT count
- The operator asking "is this a known issue upstream?" is a research question, not permission to file
- Permission for one external repo does NOT extend to others

## Asking Before Acting

- When tempted to contribute externally, surface the proposed action to the operator before any API call: name the target repo, the action type (issue / PR / comment / reaction), and a preview of the content
- Wait for an affirmative response; silence or ambiguity is not consent
- Permission granted for one specific action does NOT extend to follow-up actions on the same target (a granted issue-filing is not also permission to reply on the resulting thread without a second confirmation)
