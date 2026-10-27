#!/usr/bin/env python3
"""Small workflow adapter; paths and clock come only from the central runner."""

from datetime import datetime, timezone
import os
from pathlib import Path
import sys

from fleet_publish import (
    GitHub,
    PublishRefused,
    claim,
    decode,
    execute,
    prepare,
    require,
)


def main() -> int:
    require(
        sys.argv[1:] in (["prepare"], ["claim"], ["execute"]),
        "Use one fixed workflow phase.",
    )
    work = Path(os.environ["RUNNER_TEMP"]) / "fleet-publish"
    if sys.argv[1] == "prepare":
        work.mkdir(mode=0o700)
        environment = dict(os.environ)
        environment["FLEET_PUBLISH_DATE"] = (
            datetime.now(timezone.utc).date().isoformat()
        )
        prepare(
            GitHub(),
            environment["FLEET_CONSUMER_REPOSITORY"],
            environment["FLEET_SOURCE_SHA"],
            work,
            environment,
        )
        selected = (
            decode((work / "selection.json").read_bytes())["mode"]
            != "already_confirmed"
        )
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("selected=" + str(selected).lower() + "\n")
    elif sys.argv[1] == "claim":
        claim(GitHub(), work)
    else:
        return 0 if execute(GitHub(), work) else 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PublishRefused, OSError, ValueError, KeyError, TypeError, UnicodeError):
        # outer-boundary-process-contract: missing/uncertain evidence is failure;
        # preserve durable intent, emit a fixed diagnostic rather than raw input.
        print(
            "Central publisher failed closed; inspect immutable intent/receipt. Do not replay an uncertain publication.",
            file=sys.stderr,
        )
        raise SystemExit(1)
