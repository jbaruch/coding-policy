name: Trigger fleet policy review

# PR-time trigger for the maintainer's own jbaruch/coding-policy review setup —
# same-owner infrastructure holding their shared coding rules. On every same-repo
# PR event this asks coding-policy to run a single-PR review immediately, so the
# verdict is available before merge (the coding-policy cron poll is only a
# backstop). Fork PRs are skipped — they cannot read the token. Dependabot PRs are
# skipped too — the dependabot actor gets no secrets, so the request could never
# authenticate (the cron poll backstop still reviews them).
#
# Requires one repo secret (set it at
# https://github.com/<owner>/<repo>/settings/secrets/actions for this repo):
#   FLEET_DISPATCH_TOKEN — a fine-grained token the maintainer scopes to ONLY their
#     own jbaruch/coding-policy with `Actions: write` and nothing else (least
#     privilege). It can ask that repo to run a review and nothing more; the review
#     credential itself stays only in coding-policy.

on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions: {}

concurrency:
  group: trigger-fleet-review-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  trigger:
    # Fork PRs cannot read secrets — skip them (adopt via the adopt-fork-pr skill).
    # Dependabot's actor also gets no secrets, so its request can't authenticate —
    # skip it too (the coding-policy cron poll reviews dependabot PRs instead).
    if: >-
      github.event.pull_request.head.repo.full_name == github.repository &&
      github.actor != 'dependabot[bot]'
    runs-on: ubuntu-latest
    steps:
      - name: Dispatch the single-PR review to coding-policy
        env:
          GH_TOKEN: ${{ secrets.FLEET_DISPATCH_TOKEN }}
          REPO: ${{ github.event.repository.name }}
          PR: ${{ github.event.pull_request.number }}
          BASE: ${{ github.event.pull_request.base.ref }}
        run: |
          set -euo pipefail
          if [ -z "${GH_TOKEN:-}" ]; then
            echo "error: FLEET_DISPATCH_TOKEN secret is empty — set a fine-grained token scoped to Actions: write on jbaruch/coding-policy only; see this workflow's header" >&2
            exit 1
          fi
          gh workflow run fleet-review.yml \
            --repo jbaruch/coding-policy \
            -f repo="$REPO" -f pr="$PR" -f base="$BASE"
