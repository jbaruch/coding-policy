## Step 4 — Reviews Fire Automatically

Opening, reopening or pushing to a same-repository PR starts both independent
reviewers. Codex checks policy compliance. Claude reviews code quality and bugs
with a replacement system prompt and no loaded policy, agent instructions,
plugins, skills, hooks or memory. Both authenticate through subscriptions.

- In coding-policy, review-codex.yml and review-claude.yml post as github-actions[bot].
- In consumers, review-trigger.yml dispatches fleet-review.yml; its policy and
  quality jobs post as coding-policy-fleet-reviewer[bot]. The marker-gated
  scheduled poll is a backstop, deduplicated separately for each lane.

Claude quality reviews carry a trusted leading marker so a quality comment
cannot replace a policy verdict from the same bot. The shared classifier lives
in skills/release/review-lanes.sh. Both reviewers classify findings as blocking or advisory under
rules/review-severity.md. Blocking findings from either hold the merge; both
review bodies must arrive and be read.

Fork heads receive no credentials. Adopt them through the adopt-fork-pr skill.
A push without an open PR does not trigger these reviewers. If a lane fails to
arrive, inspect its workflow and authentication, repair the cause and rerun the
failed job; do not request Copilot or invent a replacement polling loop.

Proceed immediately to Step 5.
