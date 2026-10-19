## Step 3 — Reason About Versioning

Decide the bump per semver, then apply it the way the channels named in Step 7 require:

- **Tessl** — patch is the default and is auto-bumped by the publish workflow's `smart-publish` step; update the manifest version only for minor or major
- **GitHub tag/asset** — write every bump, patch included, into the package manifest in this PR (`agent-plugin.yaml` for an ACR package). For a tag-triggered publisher, cut Step 7's release tag at that version. For a merge-triggered publisher, let that workflow create the tag; never create a competing tag
- **Both** — set one version explicitly in every channel's manifest in this PR. Follow the same trigger-specific tag ownership above. Verify that the actual Tessl publishing workflow preserves the version without auto-bumping: use `publish-mode: as-is` with `.github/workflows/publish-plugin.yml`, or `mode: as-is` when calling `.github/actions/smart-publish` directly. For another publisher, inspect its supported mechanism before proceeding. Keep the required skill review, registry confirmation and moderation checks enabled
