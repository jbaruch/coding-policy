## Step 4 — Reviews Fire Automatically

Opening, reopening or pushing to a same-repository PR starts both independent
reviewers. Codex checks policy compliance. Claude reviews code quality and bugs
with a replacement system prompt and no loaded policy, agent instructions,
plugins, skills, hooks or memory. Both authenticate through subscriptions.

- In coding-policy, review-codex.yml posts as github-actions[bot]; review-claude.yml posts as jbaruch-claude-reviewer[bot].
- In consumers, review-trigger.yml dispatches fleet-review.yml; its policy and
  quality jobs post as coding-policy-fleet-reviewer[bot] and jbaruch-claude-reviewer[bot], respectively. The marker-gated
  scheduled poll is a backstop, deduplicated separately for each lane.

Policy and quality use distinct GitHub identities. An approval from either
cannot supersede the other's change request in GitHub. The quality login and
body marker are owned by skills/release/review-lanes.sh. Both reviewers classify
findings as blocking or advisory under rules/review-severity.md. Blocking
findings from either hold the merge; both bodies must arrive and be read.

Fork heads receive no credentials. Adopt them with `Skill(skill: "adopt-fork-pr")`.
A push without an open PR does not trigger these reviewers. If a lane fails to
arrive, inspect its workflow and authentication, repair the cause and rerun the
failed job; do not request Copilot or invent a replacement polling loop.

Proceed immediately to Step 5.
