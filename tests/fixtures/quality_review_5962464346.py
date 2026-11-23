"""Build the misattributed native review from coding-policy PR #867."""

import json


review_id = 5962464346
analyzed_sha = "07ff68f99b0a1acbb5b7af20532eb255b06d34f8"
if __name__ == "__main__":
    print(json.dumps({
        "id": review_id,
        "html_url": f"https://github.com/jbaruch/coding-policy/pull/867#pullrequestreview-{review_id}",
        "user": {"login": "jbaruch-claude-reviewer[bot]"},
        "commit_id": "d8b9a1e0ea8706cf7ed95f04ca08092ae3d839ca",
        "state": "APPROVED",
        "submitted_at": "2026-11-18T18:14:12Z",
        "body": ("<!-- coding-policy:claude-quality-review:v1 -->\n## Claude code review\n\n"
                 f"Reviewed commit: {analyzed_sha}. Code quality and bugs only.\n"),
    }))
