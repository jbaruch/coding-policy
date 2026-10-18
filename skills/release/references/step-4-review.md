## Step 4 — Policy Review Fires Automatically

Opening the PR, or pushing further commits to an existing PR, automatically triggers the policy reviewer, which reviews the diff against the in-tree `rules/*.md` and posts a verdict. The machinery depends on the repo:

- **coding-policy's own PRs** — the in-repo `review-codex.yml` workflow (Codex CLI on a ChatGPT subscription), posting as `github-actions[bot]`.
- **Consumer repos** — the central `coding-policy-fleet-reviewer[bot]` GitHub App; the in-repo `review-trigger.yml` dispatches it on each `pull_request` event, with a scheduled marker-gated poll as backstop (coding-policy#202).

In both, the PR event (`opened` / `synchronize` / `reopened`) is the primary trigger. A plain `git push` to a non-PR branch does NOT fire a review; the consumer path's scheduled poll is only a backstop for a dispatch that never fired. Fork PRs are skipped in both — no secret or token access to a fork head. Adopt a fork PR via `adopt-fork-pr` (works in any repo where the policy review is fork-guarded). See the trigger / authorship / dismissal mechanics for both at:

```text
skills/release/references/REVIEW_DETAILS.md
```

**Also request Copilot.** Copilot is a deliberate second reviewer with a different lens — the policy reviewer enforces `rules/*.md` compliance, Copilot reads for correctness, bugs, security, and test gaps. The policy reviewer gates the merge only on **blocking** findings; advisory-only reviews post `COMMENT` and never gate, and Copilot is always advisory (read it, never gate on it) — see `rules/review-severity.md`:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/release/request-copilot-review.sh" <owner> <repo> <pr-number>
```

Proceed immediately to Step 5.
