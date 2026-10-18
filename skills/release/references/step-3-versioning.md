## Step 3 — Reason About Versioning

Decide the bump per semver, then apply it the way the channels named in Step 7 require:

- **Tessl** — patch is the default and is auto-bumped by the publish workflow's `smart-publish` step; update the manifest version only for minor or major
- **GitHub tag/asset** — nothing auto-bumps here. Write every bump, patch included, into the package manifest in this PR (`agent-plugin.yaml` for an ACR package), and cut Step 7's release tag at that same version
- **Both** — set one version explicitly in every channel's manifest in this PR and tag that version. Verify that the actual Tessl publishing workflow preserves it without auto-bumping: use `publish-mode: as-is` with `.github/workflows/publish-plugin.yml`, or `mode: as-is` when calling `.github/actions/smart-publish` directly. For another publisher, inspect its supported mechanism before proceeding. Keep the required skill review, registry confirmation and moderation checks enabled

