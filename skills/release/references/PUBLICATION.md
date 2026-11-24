# Release-Skill Publication Reference

Step 7 names the publication gates in order and leaves the mechanics here: how each channel is recognized, the command for each gate, and the exit-code contract of each helper. Pulled out of SKILL.md to keep the ordered execution plan scannable.

Each command block resolves `KS` with the same installed-plugin bootstrap. Each block runs in a fresh shell, so a value one block prints reaches the next as a placeholder you fill in (`<pre>`, `<tessl-run-id>`, `<current>`), never as a shell variable.

The confirmation a release owes is keyed on the publication, never on the package. A package that publishes through more than one channel owes the duty once per publication, each confirmed against the channel that carried it (`skills/release/references/release-contract.md` Publication Confirmation).

## Recognizing the channels

Read the repo's publish workflow and its manifest:

- **Tessl** — a Tessl plugin manifest (`.tessl-plugin/plugin.json`, legacy `tile.json`), or a publish path calling `tessl plugin publish` or `tesslio/patch-version-publish`
- **GitHub tag/asset** — a workflow that creates a published release and assets at an immutable tag, including `acr publish`; its trigger may be a tag push or a merge push
- **Both** — every gate marked Tessl AND every gate marked tag/asset, each read from its own channel. A green GitHub release confirms nothing about a pending Tessl moderation, and a cleared Tessl publish confirms nothing about an absent GitHub asset

## Before the merge — Tessl registry baseline

A publication on another channel skips this gate.

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/release/; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/release/ ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$KS/registry-baseline.sh" <workspace> <plugin>
```

Exit 0 prints the version or the verified `unpublished` sentinel; record it as `<pre>`. Exit 3 requires `tessl login`, then re-running the baseline capture. Any other exit also stops the release before merge. Empty or guessed baselines are invalid. The output and refusal contract belongs to `skills/release/registry-baseline.sh`.

## After the merge — GitHub tag/asset: push the tag

Push the tag below only when the inspected workflow is tag-triggered. A merge-triggered dual publisher creates its own tag from the reviewed source commit; skip this command and resolve the merge-triggered run instead. Never move a tag or create a competing tag. The version follows SKILL.md Step 3.

```bash
git tag <tag> && git push origin <tag>
```

## After the merge — every publication: resolve and watch its run

Readback of an existing central fleet publication follows
`skills/release/references/fleet-publication.md` in full for selection, watching
and confirmation. Keep the captured baseline and actual consumer merge SHA.
The commands below retain the ordinary consumer-push and tag publication modes.

For push-triggered publication, bind resolution to the workflow, the exact commit, the `push` event and the ref that fired it, never to "latest on main". This repository's deliberate manifest-only release follows the walkthrough below. The lookup polls until the run is listed, past the publish workflow's enqueue latency.

Each channel uses its actual publisher run. Separate workflows have separate ids. A merge-triggered workflow containing both publication jobs uses one id for both independent confirmation helpers; it still owes both sets of artifact evidence.

**Tessl** — the publish workflow fires on the merge commit. Read that commit and record it as `<merge-sha>`:

```bash
gh pr view <N> --json mergeCommit --jq '.mergeCommit.oid'
```

Resolve its run:

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/release/; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/release/ ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$KS/resolve-publish-run.sh" <owner> <repo> <merge-sha> "<tessl-publish-workflow>"
```

Record the emitted `database_id` as `<tessl-run-id>` and watch it:

```bash
gh run watch <tessl-run-id>
```

**Tag-triggered GitHub tag/asset** — its run carries the pushed tag as `headBranch`. Read the commit the tag points at and record it as `<tag-sha>`:

```bash
git rev-list -n 1 "<tag>"
```

Resolve its run, passing the tag as the fifth argument and its commit as the third:

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/release/; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/release/ ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$KS/resolve-publish-run.sh" <owner> <repo> <tag-sha> "<tag-publish-workflow>" "<tag>"
```

Record the emitted `database_id` as `<tag-run-id>` and watch it:

```bash
gh run watch <tag-run-id>
```

**Merge-triggered dual publisher** — resolve the workflow with the reviewed merge SHA, `push` event and default-branch ref, using the same command as the Tessl merge-triggered case. It creates `v<explicit-manifest-version>` itself. Record that id for both channels, watch it once, and independently verify the Tessl registry/moderation and the exact GitHub tag/assets. The ACR owner receives the triggering source SHA and refuses source outside the fetched default history. A Tessl-only run never substitutes for an ACR publisher run.

Omit `--exit-status` from the push-triggered publisher watch. Read the run conclusion through each channel's confirmation helper — `confirm-tessl-landed.sh` for Tessl, `verify-github-release.sh` for a tag publication.

`gh pr view` returns the specific merge commit for this PR, unaffected by parallel merges. Exit 0 emits `{"database_id": N}` on stdout per `rules/script-delegation.md`. A non-zero exit emits no id and a stderr diagnostic; the run is unresolved, and no watch or confirmation may proceed on a guess or an empty id. The four facts the resolver binds, its enqueue-latency retry, its refusal to pick between two runs matching all four, and which condition lands in which rc are the script's decision contract — see `skills/release/resolve-publish-run.sh` header, not restated here (`rules/script-as-black-box.md`). The watch is a timing precondition for the confirmations below, not the gate.

## After the merge — Tessl: conjuncts 1 and 2

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/release/; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/release/ ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$KS/confirm-tessl-landed.sh" <workspace> <plugin> <pre> <tessl-run-id>
```

Exit 0 prints the landed version; record it as `<current>` for the moderation gate that follows. Exit 1 is a definitive "did not land"; exit 2 is "cannot tell yet" — the run is not terminal, or a tool is unreachable. Neither proceeds to moderation, and the two are kept apart because their recoveries differ: a caller that collapses them reports an unreachable `gh` as a failed release. The wrapper owns that dispatch so no caller retypes it; its contract and the underlying envelope are the scripts' — see `skills/release/confirm-tessl-landed.sh` and `skills/release/verify-publish-landed.sh` headers, not restated here (`rules/script-as-black-box.md`). Do not compare against a specific expected version. See `skills/release/references/release-contract.md` for full release-contract semantics and failed-publish recovery.

## After the merge — Tessl: conjunct 3, moderation

A freshly published version can be install-blocked until its moderation state reaches `pass`; poll with exponential backoff (the script owns the backoff constants and the cleared/blocked decision).

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/release/; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/release/ ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$KS/verify-moderation-cleared.sh" <workspace> <plugin> <current>
```

Exit 0 = moderation cleared. Exit 1 = blocked or still-pending at budget exhaustion — an unconfirmed release; surface it and do not report success. Exit 2 = a usage or tool-state error, never a moderation verdict. Which condition lands in which rc is the script's decision contract — see `skills/release/verify-moderation-cleared.sh` header, not restated here (`rules/script-as-black-box.md`). Never report the release confirmed until this clears. See `skills/release/references/release-contract.md` for the full three-conjunct contract. Every Tessl publication keeps this whole contract, mixed distribution included.

## After the merge — GitHub tag/asset: its own two conjuncts

The resolved run's `conclusion` is `success`, AND the release the run was supposed to create exists at that exact tag, is published, and carries retrievable assets. Run none of the three Tessl helpers above for it.

Gate on the exit code, the same way the Tessl path gates on `confirm-tessl-landed.sh`. Pass the run that actually includes this channel's publication job. A dual workflow uses its shared run id; a separate Tessl-only run cannot confirm GitHub assets.

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/release/; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/release/ ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$KS/verify-github-release.sh" <owner> <repo> "<tag>" <tag-run-id>
```

Exit 0 = both conjuncts hold. Exit 1 = a definitive no — an unconfirmed release; surface it and do not report success. Exit 2 = indeterminate or a usage error; an indeterminate answer is never a landing. Which conjuncts it reads, and which conditions land in which exit code, are the script's decision contract — see `skills/release/verify-github-release.sh` header, not restated here (`rules/script-as-black-box.md`).

## Walkthroughs

**Tessl-only.** Name the channel from `.tessl-plugin/plugin.json`. Record `<pre>` before merging. Merge, fast-forward `main`, verify the merge commit. Resolve the publish run by merge SHA, watch it. Run `confirm-tessl-landed.sh` with `<pre>` and the run id; gate on its exit code and keep the version it prints as `<current>`. Run `verify-moderation-cleared.sh` on `<current>`. Report the PR URL, `<current>`, the registry advance and the moderation clear. Push no tag and run `verify-github-release.sh` for nothing.

**GitHub tag/asset-only** (an ACR package). Name the channel from the publish workflow's `on: push: tags:`. Skip the registry baseline. Write the version into the package manifest in the PR per Step 3. Merge, fast-forward `main`, verify the merge commit, then `git tag <tag> && git push origin <tag>`. Resolve the run by the tag's commit with the tag as the fifth argument, watch it. Run `verify-github-release.sh` with the tag and that run id. Report the PR URL, the tag, the published release and its retrievable assets. Run none of the Tessl helpers.

**Both, separate workflows.** Set one version explicitly in every channel's manifest in the PR and confirm the Tessl publisher preserves it without auto-bumping (Step 3). Record `<pre>` before merging. Merge, fast-forward `main`, push the tag. Resolve TWO runs — `<tessl-run-id>` from the merge SHA, `<tag-run-id>` from the tag — and watch both. Run `confirm-tessl-landed.sh` and `verify-moderation-cleared.sh` against the Tessl channel, and `verify-github-release.sh` against `<tag-run-id>`. Report both confirmations. Neither channel's result stands in for the other's.

**Both, merge-triggered jobs (this repository).** Leave both manifest versions unchanged in the PR. Capture `<pre>` before merge. Resolve and watch `publish.yml` for the reviewed merge SHA and the `main` push. Use that SHA as `<source-sha>` and apply the dual-release confirmations below with the bound run ID.

**Deliberate manifest-only release (this repository).** Capture `<pre>` and record the current HEAD of `main` as `<source-sha>` (`gh api repos/<owner>/<repo>/git/refs/heads/main --jq '.object.sha'`), then run `gh workflow run publish.yml --ref main`. Resolve the dispatch run with the existing resolver's explicit event argument.

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/release/; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/release/ ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$KS/resolve-publish-run.sh" <owner> <repo> <source-sha> publish.yml main workflow_dispatch
```

On exit 0, record `database_id` as `<dispatch-run-id>` and watch it. A non-zero exit leaves the run unresolved.

```bash
gh run watch --repo <owner>/<repo> --exit-status <dispatch-run-id>
```

A successful watch proceeds to the dual-release confirmations with the selected run ID and captured `<source-sha>`. A non-zero watch leaves delivery unconfirmed. Follow partial publication recovery if the publisher fails or observation is interrupted.

### Dual-release confirmations (this repository)

From the publisher's checkout, resolve bookkeeping using the original source SHA and bound publisher run ID. The helper reads the stamp-producing publish job's log and fetches `main` from `jbaruch/coding-policy`, including after a publish or ACR-only retry.

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/release/; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/release/ ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$KS/resolve-bookkeeping-stamp.sh" <source-sha> <bound-run-id>
```

Exit 0 returns JSON with `outcome` and `source_commit`. Record `source_commit` as `<release-sha>` for the ACR tag check. `pushed` identifies the verified bookkeeping commit. `noop` identifies the original source when the publisher explicitly recorded no bookkeeping change. A non-zero exit stops confirmation, including when the selected job archive is unavailable. The header of `skills/release/resolve-bookkeeping-stamp.sh` owns the evidence and exit contract.

For `pushed`, pass `<owner> <repo> <release-sha> tests.yml main` to `resolve-publish-run.sh` as above. Watch the emitted `database_id` with `gh run watch <id> --repo <owner>/<repo> --exit-status`. For `noop`, report that no bookkeeping push occurred and proceed to the channel checks.

Run `confirm-tessl-landed.sh` with the baseline and bound publisher run ID to obtain `<current>`. Confirm moderation with `verify-moderation-cleared.sh` for that version. Require `gh api repos/<owner>/<repo>/git/refs/tags/v<current> --jq '.object.sha'` to match `<release-sha>`. Confirm the published tag and retrievable assets with `verify-github-release.sh` for `v<current>` and the same publisher run ID. Report both channel results and the bookkeeping Tests result when a stamp was pushed. Final confirmation requires both channels and successful Tests for a pushed stamp.

## Partial publication recovery

Resume an interrupted publisher watch on the same bound run ID. Before any publication retry, reconcile an unknown bookkeeping push with `resolve-bookkeeping-stamp.sh` as above. A refusal leaves bookkeeping unconfirmed. Watch Tests for a verified stamp even when the publisher stays red after a lost push reply. Resume a failed or interrupted Tests observation on the same run ID or resolve it again from the verified stamp, without rerunning the publisher. Inspect a failed Tests run's logs and keep delivery unconfirmed until Tests succeeds. A successful recovery watch confirms only Tests.

For a first Tessl publication, `registry-baseline.sh` prints `unpublished` only after an authenticated absent-plugin response or an empty versions array. Preserve that literal as `<pre>`; `confirm-tessl-landed.sh` requires a successful bound run and a real published version. Empty output, authentication failures and unreadable responses remain errors. Never substitute a guessed `0.0.0` baseline.

When Tessl succeeds and the downstream ACR job fails, preserve the original baseline, source SHA and bookkeeping result for this repository. Use `gh run rerun <run-id> --failed` to retry only the failed job; its successful Tessl prerequisite remains completed. Do not dispatch a new full publish or rerun the successful Tessl job against an already-published version. Watch the same run to a terminal result, then recheck each channel's artifacts independently. Existing matching tags are reused; another commit at that version is a refusal, never a force-push. An unknown external effect remains unconfirmed until readback proves it.

If Tessl lands but the bookkeeping push is rejected, the job stays red and ACR does not run. Run `bash skills/release/registry-has-version.sh jbaruch coding-policy <attempted-version>` and require `exists: true` before treating that artifact as published. Restore `STAMP_DEPLOY_KEY` write access using `.env.example` if that caused the rejection; a concurrent main advance instead requires a fresh source checkout. Follow the release contract's follow-up-commit recovery, which starts a fresh publish from the registry's latest version. Never force-push over a concurrent merge or replay the old local bookkeeping commit. Verify the new run's registry advance, moderation and exact ACR tag/assets through the helpers above. The next publish allocates another patch; it does not repair the earlier immutable artifact in place.
