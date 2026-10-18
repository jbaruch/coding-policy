# Release-Skill Publication Reference

Step 7 names the publication gates in order and leaves the mechanics here: how each channel is recognized, the command for each gate, and the exit-code contract of each helper. Pulled out of SKILL.md to keep the ordered execution plan scannable.

Each command block resolves `KS` the same way SKILL.md does. Each block runs in a fresh shell, so a value one block prints reaches the next as a placeholder you fill in (`<pre>`, `<tessl-run-id>`, `<current>`), never as a shell variable.

The confirmation a release owes is keyed on the publication, never on the package. A package that publishes through more than one channel owes the duty once per publication, each confirmed against the channel that carried it (`skills/release/references/release-contract.md` Publication Confirmation).

## Recognizing the channels

Read the repo's publish workflow and its manifest:

- **Tessl** — a Tessl plugin manifest (`.tessl-plugin/plugin.json`, legacy `tile.json`), or a publish path calling `tessl plugin publish` or `tesslio/patch-version-publish`
- **GitHub tag/asset** — a workflow that creates a published release and assets at an immutable tag, including `acr publish`; its trigger may be a tag push or a merge push
- **Both** — every gate marked Tessl AND every gate marked tag/asset, each read from its own channel. A green GitHub release confirms nothing about a pending Tessl moderation, and a cleared Tessl publish confirms nothing about an absent GitHub asset

## Before the merge — Tessl registry baseline

A publication on another channel skips this gate.

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/release; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/release ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$KS/registry-baseline.sh" <workspace> <plugin>
```

Exit 0 prints the version or the verified `unpublished` sentinel; record it as `<pre>`. Any other exit stops the release before merge. Empty or guessed baselines are invalid. The output and refusal contract belongs to `skills/release/registry-baseline.sh`.

## After the merge — GitHub tag/asset: push the tag

Push the tag below only when the inspected workflow is tag-triggered. A merge-triggered dual publisher creates its own tag from the reviewed source commit; skip this command and resolve the merge-triggered run instead. Never move a tag or create a competing tag. The version follows SKILL.md Step 3.

```bash
git tag <tag> && git push origin <tag>
```

## After the merge — every publication: resolve and watch its run

Bind the resolution to the workflow, the exact commit, the `push` event and the ref that fired it, never to "latest on main". The lookup polls until the run is listed, past the publish workflow's enqueue latency.

Each channel uses its actual publisher run. Separate workflows have separate ids. A merge-triggered workflow containing both publication jobs uses one id for both independent confirmation helpers; it still owes both sets of artifact evidence.

**Tessl** — the publish workflow fires on the merge commit. Read that commit and record it as `<merge-sha>`:

```bash
gh pr view <N> --json mergeCommit --jq '.mergeCommit.oid'
```

Resolve its run:

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/release; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/release ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
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
KS=.tessl/plugins/jbaruch/coding-policy/skills/release; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/release ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$KS/resolve-publish-run.sh" <owner> <repo> <tag-sha> "<tag-publish-workflow>" "<tag>"
```

Record the emitted `database_id` as `<tag-run-id>` and watch it:

```bash
gh run watch <tag-run-id>
```

**Merge-triggered dual publisher** — resolve the workflow with the reviewed merge SHA, `push` event and default-branch ref, using the same command as the Tessl merge-triggered case. It creates `v<explicit-manifest-version>` itself. Record that id for both channels, watch it once, and independently verify the Tessl registry/moderation and the exact GitHub tag/assets. The ACR owner receives the triggering source SHA and refuses source outside the fetched default history. A Tessl-only run never substitutes for an ACR publisher run.

Omit `--exit-status` from the watch. Read the run conclusion through each channel's confirmation helper — `confirm-tessl-landed.sh` for Tessl, `verify-github-release.sh` for a tag publication.

`gh pr view` returns the specific merge commit for this PR, unaffected by parallel merges. Exit 0 emits `{"database_id": N}` on stdout per `rules/script-delegation.md`. A non-zero exit emits no id and a stderr diagnostic; the run is unresolved, and no watch or confirmation may proceed on a guess or an empty id. The four facts the resolver binds, its enqueue-latency retry, its refusal to pick between two runs matching all four, and which condition lands in which rc are the script's decision contract — see `skills/release/resolve-publish-run.sh` header, not restated here (`rules/script-as-black-box.md`). The watch is a timing precondition for the confirmations below, not the gate.

## After the merge — Tessl: conjuncts 1 and 2

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/release; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/release ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$KS/confirm-tessl-landed.sh" <workspace> <plugin> <pre> <tessl-run-id>
```

Exit 0 prints the landed version; record it as `<current>` for the moderation gate that follows. Exit 1 is a definitive "did not land"; exit 2 is "cannot tell yet" — the run is not terminal, or a tool is unreachable. Neither proceeds to moderation, and the two are kept apart because their recoveries differ: a caller that collapses them reports an unreachable `gh` as a failed release. The wrapper owns that dispatch so no caller retypes it; its contract and the underlying envelope are the scripts' — see `skills/release/confirm-tessl-landed.sh` and `skills/release/verify-publish-landed.sh` headers, not restated here (`rules/script-as-black-box.md`). Do not compare against a specific expected version. See `rules/ci-safety.md` for full release-contract semantics and failed-publish recovery.

## After the merge — Tessl: conjunct 3, moderation

A freshly published version can be install-blocked until its moderation state reaches `pass`; poll with exponential backoff (the script owns the backoff constants and the cleared/blocked decision).

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/release; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/release ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$KS/verify-moderation-cleared.sh" <workspace> <plugin> <current>
```

Exit 0 = moderation cleared. Exit 1 = blocked or still-pending at budget exhaustion — an unconfirmed release; surface it and do not report success. Exit 2 = a usage or tool-state error, never a moderation verdict. Which condition lands in which rc is the script's decision contract — see `skills/release/verify-moderation-cleared.sh` header, not restated here (`rules/script-as-black-box.md`). Never report the release confirmed until this clears. See `rules/ci-safety.md` for the full three-conjunct contract. Every Tessl publication keeps this whole contract, mixed distribution included.

## After the merge — GitHub tag/asset: its own two conjuncts

The resolved run's `conclusion` is `success`, AND the release the run was supposed to create exists at that exact tag, is published, and carries retrievable assets. Run none of the three Tessl helpers above for it.

Gate on the exit code, the same way the Tessl path gates on `confirm-tessl-landed.sh`. Pass the run that actually includes this channel's publication job. A dual workflow uses its shared run id; a separate Tessl-only run cannot confirm GitHub assets.

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/release; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/release ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$KS/verify-github-release.sh" <owner> <repo> "<tag>" <tag-run-id>
```

Exit 0 = both conjuncts hold. Exit 1 = a definitive no — an unconfirmed release; surface it and do not report success. Exit 2 = indeterminate or a usage error; an indeterminate answer is never a landing. Which conjuncts it reads, and which conditions land in which exit code, are the script's decision contract — see `skills/release/verify-github-release.sh` header, not restated here (`rules/script-as-black-box.md`).

## Walkthroughs

**Tessl-only.** Name the channel from `.tessl-plugin/plugin.json`. Record `<pre>` before merging. Merge, fast-forward `main`, verify the merge commit. Resolve the publish run by merge SHA, watch it. Run `confirm-tessl-landed.sh` with `<pre>` and the run id; gate on its exit code and keep the version it prints as `<current>`. Run `verify-moderation-cleared.sh` on `<current>`. Report the PR URL, `<current>`, the registry advance and the moderation clear. Push no tag and run `verify-github-release.sh` for nothing.

**GitHub tag/asset-only** (an ACR package). Name the channel from the publish workflow's `on: push: tags:`. Skip the registry baseline. Write the version into the package manifest in the PR per Step 3. Merge, fast-forward `main`, verify the merge commit, then `git tag <tag> && git push origin <tag>`. Resolve the run by the tag's commit with the tag as the fifth argument, watch it. Run `verify-github-release.sh` with the tag and that run id. Report the PR URL, the tag, the published release and its retrievable assets. Run none of the Tessl helpers.

**Both, separate workflows.** Set one version explicitly in every channel's manifest in the PR and confirm the Tessl publisher preserves it without auto-bumping (Step 3). Record `<pre>` before merging. Merge, fast-forward `main`, push the tag. Resolve TWO runs — `<tessl-run-id>` from the merge SHA, `<tag-run-id>` from the tag — and watch both. Run `confirm-tessl-landed.sh` and `verify-moderation-cleared.sh` against the Tessl channel, and `verify-github-release.sh` against `<tag-run-id>`. Report both confirmations. Neither channel's result stands in for the other's.


**Both, merge-triggered jobs (this repository).** Set aligned manifest versions in the PR. Capture `<pre>` before merge. Resolve and watch `publish.yml` by its reviewed merge SHA and `main` push. The Tessl job confirms registry advance; the dependent ACR job creates the immutable version tag and publishes assets from that same reviewed source. Tessl may commit its changelog stamp separately; it changes no shared runtime/version surface. Run `confirm-tessl-landed.sh`, `verify-moderation-cleared.sh` and `verify-github-release.sh` with the actual run and exact version tag. Report both channel results.

## Partial publication recovery

For a first Tessl publication, `registry-baseline.sh` prints `unpublished` only after a verified absent-plugin response or an empty versions array. Preserve that literal as `<pre>`; `confirm-tessl-landed.sh` requires a successful bound run and a real published version. Empty output, authentication failures and unreadable responses remain errors. Never substitute a guessed `0.0.0` baseline.

When Tessl succeeds and the downstream ACR job fails, preserve the original baseline and source commit. Use `gh run rerun <run-id> --failed` to retry only the failed job; its successful Tessl prerequisite remains completed. Do not dispatch a new full publish or rerun the successful Tessl job against an already-published version. Watch the same run to a terminal result, then recheck each channel's artifacts independently. Existing matching tags are reused; another commit at that version is a refusal, never a force-push. An unknown external effect remains unconfirmed until readback proves it.
