#!/usr/bin/env python3
"""Run a reviewer CLI and report fixed failure categories without raw output.

Usage: reviewer_failure.py run codex -- codex exec ...
Stdin is inherited for the review prompt; stdout/stderr are captured privately.
Exit 0 means the child succeeded. Failures preserve its positive exit status;
launch failure is 127, invalid invocation is 2. No review verdict is posted here.
"""
import json
import re
import subprocess
import sys


def reviewer_failure_hint(stdout: str, stderr: str) -> str:
    """Return fixed diagnostic text only; CLI output may contain protected data."""
    text = (stdout + "\n" + stderr).casefold().replace("’", "'")
    signatures = (
        (("authentication_error", "invalid authentication credentials", "invalid bearer token",
          "oauth token has expired", "token has expired", "401 unauthorized",
          "invalid api key", "not logged in", "please run /login", "api error: 401",
          "refresh_token_reused", "refresh_token_expired", "invalid_grant",
          "refresh token has expired", "refresh token was already used"),
         "authentication rejected; refresh the CI subscription credential"),
        (("you've hit your limit", "you have hit your limit",
          "usage limit reached", "you\'ve hit your usage limit", "usage_limit_reached", "insufficient_quota"),
         "subscription usage limit reached; wait for its reset before retrying"),
        (("rate_limit_error", "rate_limit_exceeded", "api error: 429"),
         "provider rate or usage limit; check usage and respect the provider retry/reset before retrying"),
        (("model_not_found", "invalid model", "does not exist or you do not have access"),
         "model unavailable; check the pinned model and subscription access"),
        (("unknown option", "unknown argument", "unrecognized option"),
         "CLI argument rejected; check the pinned CLI's supported options"),
        (("econnreset", "econnrefused", "etimedout", "enotfound",
          "certificate verify failed", "unable to verify the first certificate"),
         "network or TLS failure; check runner connectivity and certificate trust"),
    )
    for patterns, hint in signatures:
        if any(pattern in text for pattern in patterns):
            return hint
    if re.search(r"(?<!\d)429(?!\d)", text) and ("limit" in text or "usage" in text):
        return "provider rate or usage limit (HTTP 429); check subscription usage and the provider's reset before retrying"
    # Only this fixed vocabulary may cross the log boundary for unknown errors.
    # It reveals no raw word, identifier, source excerpt or credential fragment.
    vocabulary = ("login", "subscription", "limit", "usage", "billing", "balance",
                  "oauth", "permission", "model", "network", "timeout", "expired",
                  "scope", "restricted", "schema", "rate", "401", "403", "429",
                  "400", "invalid_request_error", "module", "node", "config", "internal")
    markers = ",".join(word for word in vocabulary if word in text) or "none"
    return ("unclassified CLI failure; check CI authentication, permissions and CLI availability; "
            f"fixed diagnostic markers: {markers}")



def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) < 4 or args[:3] != ["run", "codex", "--"] or args[3] != "codex":
        print("usage: reviewer_failure.py run codex -- codex exec ...", file=sys.stderr)
        return 2
    try:
        result = subprocess.run(args[3:], text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)
    except OSError:
        print("error: Codex CLI could not start; restore the declared CLI and runner permissions", file=sys.stderr)
        return 127
    if result.returncode:
        hint = reviewer_failure_hint(result.stdout, result.stderr)
        print(f"error: codex failed (exit {result.returncode}); {hint}", file=sys.stderr)
        return result.returncode if result.returncode > 0 else 1
    print(json.dumps({"reviewer": "codex", "cli_completed": True}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
