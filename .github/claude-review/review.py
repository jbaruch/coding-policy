#!/usr/bin/env python3
"""Post a subscription-backed, context-isolated Claude code review on one PR.

Usage: review.py OWNER REPO PR BASE
Requires GH_TOKEN and CLAUDE_CODE_OAUTH_TOKEN at runtime. The model receives
only its subscription token, a replacement system prompt and a throwaway source
checkout. No policy installation, GitHub token, settings, history or execution
tools enter its session. Findings are blocking or advisory; the verdict is bound
to the exact analyzed commit. A failed/invalid/stale review fails visibly without posting.
"""

import base64
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile


HERE = Path(__file__).resolve().parent
# Model IDs are version pins. Recheck alongside the Claude CLI Renovate update
# and the weekly capability refresh (rules/dependency-management.md Freshness).
MODEL = "claude-sonnet-5-5"


class ReviewError(Exception):
    """An actionable failure safe to print without credentials or model output."""


class GitHubValidationError(ReviewError):
    """HTTP 422, including GitHub's refusal to approve one's own pull request."""


def run(args: list[str], *, cwd: Path | None = None,
        env: dict[str, str] | None = None, data: str | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, env=env, input=data, text=True,
                            capture_output=True, check=False)
    if result.returncode:
        if args[0] == "gh" and "(HTTP 422)" in result.stderr:
            raise GitHubValidationError("GitHub rejected the review payload (HTTP 422)")
        # Child output can contain credentials or prompt-injected source text.
        raise ReviewError(f"{args[0]} failed (exit {result.returncode}); "
                          "check CI authentication, permissions and CLI availability")
    return result.stdout


def api(endpoint: str, payload: dict | None = None) -> dict:
    args = ["gh", "api", endpoint]
    if payload is not None:
        args += ["--method", "POST", "--input", "-"]
    try:
        raw = run(args, data=json.dumps(payload) if payload else None)
    except GitHubValidationError:
        if not payload or payload.get("event") != "APPROVE":
            raise
        # github-actions[bot] cannot approve its own repository's PRs. A clean
        # COMMENT carries the same lane marker; blocking reviews never fall back.
        return api(endpoint, {**payload, "event": "COMMENT"})
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ReviewError("GitHub API returned a non-object response; retry the review")
    return value


def validate_review(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"summary", "findings"}:
        raise ReviewError("Claude returned an invalid review object; retry the review")
    if not isinstance(value["summary"], str) or not value["summary"].strip():
        raise ReviewError("Claude returned an empty summary; retry the review")
    if not isinstance(value["findings"], list):
        raise ReviewError("Claude returned invalid findings; retry the review")
    for finding in value["findings"]:
        if not isinstance(finding, dict) or set(finding) != {"path", "line", "severity", "message"}:
            raise ReviewError("Claude returned a malformed finding; retry the review")
        path, line = finding["path"], finding["line"]
        if (not isinstance(path, str) or not path or "\\" in path
                or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts
                or type(line) is not int or line < 1
                or finding["severity"] not in ("blocking", "advisory")
                or not isinstance(finding["message"], str) or not finding["message"].strip()):
            raise ReviewError("Claude returned an invalid finding location or text; retry the review")
    return value


def claude_environment(config: Path) -> dict[str, str]:
    # Allowlist instead of removing a growing list of provider/CI credentials.
    allowed = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update(CLAUDE_CONFIG_DIR=str(config),
               CLAUDE_CODE_OAUTH_TOKEN=os.environ["CLAUDE_CODE_OAUTH_TOKEN"],
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
    return env


def claude_command() -> list[str]:
    """One command owner, exercised by the real-CLI isolation probe too."""
    return [
        "claude", "--print", "--safe-mode", "--restricted", "--setting-sources", "",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--disable-slash-commands", "--no-session-persistence",
        "--permission-mode", "dontAsk", "--tools", "Read,Glob,Grep",
        "--system-prompt", (HERE / "system-prompt.md").read_text(),
        "--model", MODEL, "--effort", "high", "--output-format", "json",
        "--json-schema", (HERE / "schema.json").read_text(),
    ]


def review_pr(owner: str, repo: str, pr: str, base: str) -> dict:
    endpoint = f"repos/{owner}/{repo}/pulls/{pr}"
    current = api(endpoint)
    if current["state"] != "open" or current["head"]["repo"]["full_name"] != f"{owner}/{repo}":
        raise ReviewError("review requires an open same-repository PR; adopt fork PRs first")
    if current["base"]["ref"] != base:
        raise ReviewError("PR base changed; dispatch again with its current base")
    head = current["head"]["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ReviewError("GitHub returned an invalid PR head SHA")

    with tempfile.TemporaryDirectory(prefix="claude-quality-") as temp:
        root = Path(temp)
        source, config = root / "source", root / "config"
        config.mkdir(mode=0o700)
        # Transient git configuration: never persist credentials in the clone.
        git_env = os.environ.copy()
        header = base64.b64encode(f"x-access-token:{os.environ['GH_TOKEN']}".encode()).decode()
        git_env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                       GIT_CONFIG_VALUE_0=f"Authorization: Basic {header}")
        run(["git", "clone", "--quiet", "--no-checkout", "--no-tags",
             f"https://github.com/{owner}/{repo}.git", str(source)], env=git_env)
        run(["git", "fetch", "--quiet", "origin", f"refs/pull/{pr}/head"], cwd=source, env=git_env)
        fetched = run(["git", "rev-parse", "FETCH_HEAD"], cwd=source).strip()
        if fetched != head:
            raise ReviewError("PR head changed during fetch; retry against the new head")
        run(["git", "checkout", "--quiet", "--detach", head], cwd=source)
        diff = run(["git", "diff", "--no-ext-diff", "--no-textconv", "--no-color",
                    f"origin/{base}...{head}"], cwd=source)
        prompt = f"Review {owner}/{repo} PR #{pr}, exact head {head}, base {base}.\nDiff:\n{diff}"
        result = run(claude_command(), cwd=source, env=claude_environment(config), data=prompt)
        for secret in (os.environ["CLAUDE_CODE_OAUTH_TOKEN"], os.environ["GH_TOKEN"], header):
            if secret in result:
                raise ReviewError("Claude output contained credential material; refusing to post")
        envelope = json.loads(result)
        if (not isinstance(envelope, dict) or envelope.get("is_error") is not False
                or envelope.get("subtype") != "success"):
            raise ReviewError("Claude did not complete a successful review; retry after checking subscription access")
        review = validate_review(envelope.get("structured_output"))

    latest = api(endpoint)
    if latest["state"] != "open" or latest["head"]["sha"] != head or latest["base"]["ref"] != base:
        raise ReviewError("PR changed during review; refusing to post a stale verdict; retry")
    marker = json.loads(run(["bash", str(HERE.parents[1] / "skills/release/review-lanes.sh")]))["quality_marker"]
    body = f"{marker}\n## Claude code review\n\n{review['summary']}\n\n"
    body += f"Reviewed commit: {head}. Code quality and bugs only.\n"
    blocking = [finding for finding in review["findings"] if finding["severity"] == "blocking"]
    advisory = [finding for finding in review["findings"] if finding["severity"] == "advisory"]
    for severity, findings in (("Blocking", blocking), ("Advisory", advisory)):
        if findings:
            body += f"\n## {severity} findings\n"
            for finding in findings:
                body += f"\n- {finding['path']}:{finding['line']} — {finding['message']}\n"
    event = "REQUEST_CHANGES" if blocking else "COMMENT" if advisory else "APPROVE"
    posted = api(endpoint + "/reviews", {"commit_id": head, "event": event, "body": body})
    states = {"CHANGES_REQUESTED"} if blocking else {"COMMENTED"} if advisory else {"APPROVED", "COMMENTED"}
    if posted.get("state") not in states or posted.get("commit_id") != head:
        raise ReviewError("GitHub did not confirm the posted review at the analyzed head; inspect PR reviews")
    return {"state": "posted", "reviewer": "claude", "head_sha": head,
            "findings": len(review["findings"]), "blocking": len(blocking),
            "advisory": len(advisory), "url": posted.get("html_url")}


def main() -> int:
    if len(sys.argv) != 5 or not all(re.fullmatch(r"[A-Za-z0-9._-]+", s) for s in sys.argv[1:3]) or not sys.argv[3].isdigit():
        print("usage: review.py OWNER REPO PR BASE", file=sys.stderr)
        return 2
    if not os.environ.get("GH_TOKEN") or not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        print("error: set GH_TOKEN and the subscription CLAUDE_CODE_OAUTH_TOKEN secret (claude setup-token)", file=sys.stderr)
        return 2
    try:
        print(json.dumps(review_pr(*sys.argv[1:])))
    except (ReviewError, OSError, ValueError, KeyError, TypeError) as exc:
        # Parsing errors never echo the raw response or credentials.
        message = str(exc) if isinstance(exc, ReviewError) else type(exc).__name__ + ": review failed; check CLI/API response format and runner setup"
        print("error: " + message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
