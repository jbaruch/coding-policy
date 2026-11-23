## Step 3 — Reason About Versioning

Inspect the publisher before editing versions. In this repository, PRs leave both `.tessl-plugin/plugin.json` and `agent-plugin.yaml` unchanged. `publish.yml` uses `smart-publish` in `auto-bump` mode; its commit-back records the published version in both manifests with the CHANGELOG stamp. ACR reads that commit. The commit omits CI-skip markers; `publish.yml` filters bookkeeping-only pushes. A deliberate release changing only filtered paths uses `workflow_dispatch` on `main` per `PUBLICATION.md`. Do not create a competing tag. A minor or major release requires a separately authorized publisher change.

For consumer repositories, decide the bump per semver, then apply the inspected channels' requirements:

- **Tessl** — patch is the default and is auto-bumped by the publish workflow's `smart-publish` step; update the manifest version only for minor or major
- **GitHub tag/asset** — write every bump, patch included, into the package manifest in this PR (`agent-plugin.yaml` for an ACR package). For a tag-triggered publisher, cut Step 7's release tag at that version. For a merge-triggered publisher, let that workflow create the tag; never create a competing tag
- **Both** — set one version explicitly in every channel's manifest in this PR. Follow the same trigger-specific tag ownership above. Verify that the actual Tessl publishing workflow preserves the version without auto-bumping: use `publish-mode: as-is` with `.github/workflows/publish-plugin.yml`, or `mode: as-is` when calling `.github/actions/smart-publish` directly. For another publisher, inspect its supported mechanism before proceeding. Keep the required skill review, registry confirmation and moderation checks enabled
