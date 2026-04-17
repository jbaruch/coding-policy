# PR Merge and Branch Cleanup Automation

## Problem/Feature Description

A platform team manages a monorepo where several developers merge PRs throughout the day. After a merge, developers frequently forget to sync their local main branch, leave stale local branches around, and don't verify whether the post-merge publish pipeline actually fired. This leads to subtle issues: developers push new work on top of outdated local main, accidentally re-create deleted branches, and only notice the publish pipeline failed hours later when a release is expected.

The team wants a `merge-and-cleanup.sh` bash script that a developer can run once a PR is ready to land. The script should handle the merge itself, clean up the local and remote state, and confirm the release is on track — giving the developer a clear final status rather than leaving them to check manually.

## Output Specification

Produce a `merge-and-cleanup.sh` bash script that:
- Accepts the PR number, repository owner, and repository name as inputs (arguments or environment variables)
- Checks that the PR is ready to merge before proceeding
- Merges the PR
- Brings the local main branch up to date
- Removes the local feature branch
- Cleans up stale remote-tracking references
- Verifies the merge landed on main
- Confirms the publish/release CI workflow was triggered
- Prints a final summary including the merged PR URL

Also produce a `MERGE_CHECKLIST.md` that describes the manual steps a developer would follow if not using the script, including what to check before merging and what to verify after.

The script does not need to run successfully (no GitHub credentials available) — focus on correctness of the approach and commands used.
