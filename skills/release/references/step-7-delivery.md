## Step 7 — Merge + Cleanup

Only proceed when:
- Step 5's watcher returned `.watch.result` as `ready` — its exit-0 readiness conjunction (mergeable, CI `success`/`none`, both bots posted, no Copilot review still owed, the policy reviewer not `CHANGES_REQUESTED`); the field predicate is the watcher's, not restated here (`rules/script-as-black-box.md` — see `skills/release/watch-pr-reviews.sh` header). `ready` already requires each bot's `state` to have left `none`, so a reviewer that never ran cannot satisfy the gate vacuously, AND
- Every `review_bodies[].body` in the returned snapshot has been read in full, from every author, page and review state, and any blocking finding has been addressed per Step 6, AND
- Every `inline_comment_bodies[].body` in Step 5's snapshot has been read in full and any blocking finding has been addressed per Step 6. Ordinary advisory acknowledgments and thread resolution are outside the merge predicate, AND
- A ruled finding's reply cites its ruling, per Step 6.

A `COMMENTED` review never gates the merge on its state alone — but its body must be read before merge, zero inline comments included. Every inline comment must also be read. Advisory findings (the reviewer's `## Advisory findings` section, and every Copilot comment) do not block the merge or require thread closure; use Step 6's standalone or team acknowledgment form during the existing review work. Only a blocking finding gates.

Once these conditions hold, merge automatically per `rules/ship-on-green.md` — the green gates are the approval, stakes raise care not permission, and the only blocks are its three objective exits (Red / No undo / Murky). Do not pause to ask a human whether to merge.

**Clear superseded review gates first.** This applies to coding-policy's OWN releases, where the policy reviewer posts as `github-actions[bot]`, which cannot `APPROVE` (GitHub returns HTTP 422), so a clean re-review lands as a `COMMENT` that does NOT supersede the bot's earlier `CHANGES_REQUESTED` — the stale request keeps `merge_state.status` at `BLOCKED`. On consumer repos the reviewer is the central fleet App `coding-policy-fleet-reviewer[bot]` (coding-policy#202), which CAN `APPROVE` and supersedes its own earlier `CHANGES_REQUESTED` directly — no dismissal needed there. Dismiss every superseded `github-actions[bot]` review before merging:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/release/dismiss-stale-reviews.sh" <owner> <repo> <pr-number>
```

Run it once Step 5's poll shows every bot's latest verdict clean, a `RULED` policy state included. It emits a JSON summary of what it dismissed and what it left active, exits non-zero on API failure, and is idempotent on re-run. Which reviews it dismisses and which it leaves is the script's decision contract — see `skills/release/dismiss-stale-reviews.sh` header, not restated here (`rules/script-as-black-box.md`).

**Name this repo's publication channels before merging.** The confirmation a release owes is keyed on the publication, never on the package — a package that publishes through more than one channel owes the duty once per publication, each confirmed against the channel that carried it. The release contract's Publication Confirmation section, in the reference block below, is a required read before this step's gates. Read the repo's publish workflow and its manifest, and name every channel it publishes on. How each channel is recognized, the command for every gate below, each helper's exit-code contract, and a walkthrough of the Tessl-only, tag/asset-only and mixed cases:

```text
skills/release/references/PUBLICATION.md
skills/release/references/release-contract.md
```

**Tessl publication:** before merging, run `registry-baseline.sh` and record the version it prints as `<pre>`. A publication on another channel skips this gate.

Pick the right cleanup path based on where you ran the skill from.

**(A) From the base checkout (no additional worktree):**

```bash
# Merge
gh pr merge <N> --merge --delete-branch

# Update local
git checkout main && git pull --ff-only

# Clean up local branch
git branch -d <branch>

# Prune stale remote refs
git remote prune origin
```

**(B) From an additional worktree** (per `rules/agent-worktree-isolation.md`):

```bash
# Merge (running from inside the worktree is fine)
gh pr merge <N> --merge --delete-branch

# Return to the base checkout, fast-forward main
cd <path-to-base-checkout>
git checkout main && git pull --ff-only

# Tear down the worktree — directory + `.git/worktrees/` metadata
git worktree remove <path-to-worktree>

# Now safe: branch is no longer checked out anywhere and is fully merged
git branch -d <branch>

# Prune stale remote refs
git remote prune origin
```

Order in (B) is mandatory: `git branch -d` refuses to delete a branch that is checked out in any worktree, so the `git worktree remove` step must come before `git branch -d`. Reversing the order produces a "checked out at `<path>`" error and leaves a stranded branch.

After merge — per the release contract's Publication Confirmation, run each gate its channels owe, in order:

- Verify the merge landed on main (`git pull --ff-only` succeeds; `git log -1 --oneline` shows the merge commit)
- Confirm every closing issue closed:

  ```bash
  CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
  python3 "$CP/skills/release/check-closing-issues.py" <owner> <repo> <pr-number> --merged
  ```

- Exit 1 (`still_open`) names each closing issue still open at the script's poll budget
- Every issue comment below is an action on that issue's repository, governed by `rules/external-repo-contributions.md`:
  - In a repository the operator owns, close each still-open issue with a comment naming the PR
  - Read each partial issue's repository and number from `partial_references`
  - In a repository the operator owns, comment on each partial issue with what shipped and what remains
  - Anywhere else, report the still-open or partly resolved issue to the operator instead of commenting
- **GitHub tag/asset publication:** push the release tag from the fast-forwarded `main` before resolving anything. Its publish workflow fires on the tag, never on the merge. The version follows Step 3
- **Every publication, whatever channel carries it:** resolve that publication's own run with `resolve-publish-run.sh`
- Bind that resolution to the workflow, the exact commit, the `push` event and the ref that fired it, never to "latest on main"
- Watch the resolved run to a terminal state
- Require its `conclusion` to be `success`
- Each channel keeps its own run id; a mixed publication holds both at once, and each confirmation below reads the id for its own channel
- **Tessl publication:** confirm conjuncts 1 and 2 with `confirm-tessl-landed.sh`, passing `<pre>` and the resolved run id. Which conjuncts it reads is the script's decision contract — see `PUBLICATION.md`
- Gate on that helper's exit code
- Keep the version it prints for the moderation gate
- A non-zero exit stops the release there, with no fall-through to moderation
- Do not compare against a specific expected version
- **Tessl publication:** confirm conjunct 3 with `verify-moderation-cleared.sh` on that `current` version. A freshly published version can be install-blocked until its moderation state reaches `pass`. Never report the release confirmed until this clears
- **GitHub tag/asset publication:** confirm its own conjuncts with `verify-github-release.sh`, passing the tag and its resolved run id. Which conjuncts it reads is the script's decision contract — see `PUBLICATION.md`
- Run none of the three Tessl helpers for a tag/asset publication
- Report the outcome: merged PR URL, the version published, and each publication's own confirmation — registry advance plus moderation clear for a Tessl publication, the published release and its retrievable assets for a tag publication, both for a package on both channels

When this step is wrapped in a reusable script (e.g., `merge-and-cleanup.sh` that other devs run unattended), see `skills/release/references/SCRIPTING.md` for the gates the script must enforce.

Finish here — the skill is complete.
