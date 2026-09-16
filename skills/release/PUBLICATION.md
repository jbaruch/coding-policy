# Release-Skill Publication Reference

Step 7 names the publication gates in order and leaves the mechanics here: how each channel is recognized, the command for each gate, and the exit-code contract of each helper. Pulled out of SKILL.md to keep the ordered execution plan scannable.

The confirmation a release owes is keyed on the publication, never on the package. A package that publishes through more than one channel owes the duty once per publication, each confirmed against the channel that carried it (`rules/ci-safety.md` Always Watch CI).

## Recognizing the channels

Read the repo's publish workflow and its manifest:

- **Tessl** — a Tessl plugin manifest (`.tessl-plugin/plugin.json`, legacy `tile.json`), or a publish path calling `tessl plugin publish` or `tesslio/patch-version-publish`
- **GitHub tag/asset** — a publish workflow triggered on a tag push (`on: push: tags:`) that creates a release carrying the package's assets, an ACR package published by `acr publish`
- **Both** — every gate marked Tessl AND every gate marked tag/asset, each read from its own channel. A green GitHub release confirms nothing about a pending Tessl moderation, and a cleared Tessl publish confirms nothing about an absent GitHub asset

## Before the merge — Tessl registry baseline

A publication on another channel skips this gate.

```bash
PRE=$(skills/release/capture-registry-baseline.sh <workspace> <plugin> | jq -r .version)
```

It emits one JSON object and exits non-zero on a parse miss or an empty registry value, so an unparseable baseline fails loudly instead of flowing into `verify-publish-landed.sh` as an empty `PRE` (which would pass conjunct 2 vacuously). The parse hardening and numeric-only output contract are the script's — see `skills/release/capture-registry-baseline.sh` header, not restated here (`rules/script-as-black-box.md`).

## After the merge — GitHub tag/asset: push the tag

The tag publication's workflow fires on the tag, never on the merge, so push it from the fast-forwarded `main` before resolving anything. The version follows SKILL.md Step 3.

```bash
git tag <tag> && git push origin <tag>
```

## After the merge — every publication: resolve and watch its run

Bind the resolution to the workflow, the exact commit, the `push` event and the ref that fired it, never to "latest on main". The lookup polls until the run is listed, past the publish workflow's enqueue latency.

Each channel keeps its own run id in its own variable. A mixed publication runs both blocks and holds both ids at once; each confirmation below reads the id for its own channel.

```bash
# Tessl — the publish workflow fires on the merge commit.
merge_sha=$(gh pr view <N> --json mergeCommit --jq '.mergeCommit.oid')
tessl_run_id=$(skills/release/resolve-publish-run.sh <owner> <repo> "$merge_sha" "<tessl-publish-workflow>" | jq -r '.database_id')
gh run watch "$tessl_run_id"

# GitHub tag/asset — the publish workflow fires on the pushed tag, whose
# run carries the tag name as its `headBranch`. Pass the tag as the fifth
# argument and the commit the tag points at as the third.
tag_sha=$(git rev-list -n 1 "<tag>")
tag_run_id=$(skills/release/resolve-publish-run.sh <owner> <repo> "$tag_sha" "<tag-publish-workflow>" "<tag>" | jq -r '.database_id')
gh run watch "$tag_run_id"
```

Omit `--exit-status` from the watch. Read the run conclusion through each channel's confirmation helper — `verify-publish-landed.sh` for Tessl, `verify-github-release.sh` for a tag publication.

`gh pr view` returns the specific merge commit for this PR, unaffected by parallel merges. The four facts the resolver binds, its enqueue-latency retry, and its refusal to pick between two runs matching all four are the script's decision contract — see `skills/release/resolve-publish-run.sh` header, not restated here (`rules/script-as-black-box.md`). Output is `{"database_id": N}` per `rules/script-delegation.md` — extract with `jq -r '.database_id'`. The watch is a timing precondition for the confirmations below, not the gate.

## After the merge — Tessl: conjuncts 1 and 2

Capture the emitted `current` version for the moderation gate that follows.

```bash
# Gate on the exit code — only a clean conjunction (rc 0) may proceed to
# the moderation step. A non-zero rc (publish did not land, or a tool
# error) stops the release here; do not fall through to moderation.
landed=$(skills/release/verify-publish-landed.sh <workspace> <plugin> "$PRE" "$tessl_run_id") \
  || { echo "Publish not confirmed — $(jq -r '.reason // "see stderr"' <<<"$landed")" >&2; exit 1; }
CURRENT=$(jq -r '.current' <<<"$landed")
```

Output is exit-code-dependent: rc 0/1 emits the JSON envelope `{"ok": bool, "reason": "...", "run_conclusion": "...", "pre": "...", "current": "..."}` on stdout (parse it for the finding); rc 2 emits the stderr diagnostic (tool-state errors: run still in flight, gh/tessl unreachable). Exception: the missing-jq guard at rc 2 emits a minimal JSON envelope on stdout (the script can't use jq to format JSON when jq itself is absent) so wrappers that always parse stdout still see a parseable failure. Do not compare against a specific expected version. See `rules/ci-safety.md` for full release-contract semantics and failed-publish recovery.

## After the merge — Tessl: conjunct 3, moderation

A freshly published version can be install-blocked until its moderation state reaches `pass`; poll with exponential backoff (the script owns the backoff constants and the cleared/blocked decision).

```bash
skills/release/verify-moderation-cleared.sh <workspace> <plugin> "$CURRENT"
```

Exit 0 = moderation cleared. Exit 1 = blocked or still-pending at budget exhaustion — an unconfirmed release; surface it and do not report success. Exit 2 = tool-state error (tessl unreachable, jq missing). Never report the release confirmed until this clears. See `rules/ci-safety.md` for the full three-conjunct contract. Every Tessl publication keeps this whole contract, mixed distribution included.

## After the merge — GitHub tag/asset: its own two conjuncts

The resolved run's `conclusion` is `success`, AND the release the run was supposed to create exists at that exact tag, is published, and carries retrievable assets. Run none of the three Tessl helpers above for it.

```bash
# Gate on the exit code, the same way the Tessl path gates on
# verify-publish-landed.sh. Pass THIS channel's run id: its conclusion
# is this check's first conjunct, and a mixed publication must not
# confirm the tag release against the Tessl run.
skills/release/verify-github-release.sh <owner> <repo> "<tag>" "$tag_run_id"
```

Exit 0 = both conjuncts hold. Exit 1 = a definitive no (the run concluded something other than `success`, or the release is absent, draft, empty, or carries an asset still uploading) — an unconfirmed release; surface it and do not report success. Exit 2 = indeterminate (run still in flight, gh absent or unreachable); an indeterminate answer is never a landing. Which conjuncts it reads is the script's decision contract — see `skills/release/verify-github-release.sh` header, not restated here (`rules/script-as-black-box.md`).

## Walkthroughs

**Tessl-only** (this repo). Name the channel from `.tessl-plugin/plugin.json`. Capture `PRE` before merging. Merge, fast-forward `main`, verify the merge commit. Resolve the publish run by merge SHA, watch it. Run `verify-publish-landed.sh` with `$PRE` and the run id; gate on rc 0 and keep `CURRENT`. Run `verify-moderation-cleared.sh` on `CURRENT`. Report the PR URL, `CURRENT`, the registry advance and the moderation clear. Push no tag and run `verify-github-release.sh` for nothing.

**GitHub tag/asset-only** (an ACR package). Name the channel from the publish workflow's `on: push: tags:`. Skip the registry baseline. Write the version into the package manifest in the PR per Step 3. Merge, fast-forward `main`, verify the merge commit, then `git tag <tag> && git push origin <tag>`. Resolve the run by the tag's commit with the tag as the fifth argument, watch it. Run `verify-github-release.sh` with the tag and that run id. Report the PR URL, the tag, the published release and its retrievable assets. Run none of the Tessl helpers.

**Both.** Set one version explicitly in every channel's manifest in the PR and confirm the Tessl publisher preserves it without auto-bumping (Step 3). Capture `PRE` before merging. Merge, fast-forward `main`, push the tag. Resolve TWO runs — `tessl_run_id` from the merge SHA, `tag_run_id` from the tag — and watch both. Run `verify-publish-landed.sh` and `verify-moderation-cleared.sh` against the Tessl channel, and `verify-github-release.sh` against `tag_run_id`. Report both confirmations. Neither channel's result stands in for the other's.
