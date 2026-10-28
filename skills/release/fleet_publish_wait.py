#!/usr/bin/env python3
"""Wait for a positively source-bound central selection, never a guessed run.

Usage: fleet_publish_wait.py REPOSITORY SOURCE_SHA [--run-id ID]
Success emits the resolver's JSON selection envelope. Nonzero emits no id and
a redacted diagnostic. Only pending/not_found are retried, at this owner's
interval and budget; ambiguity, expired or unverifiable evidence stops at once.
This read-only wait does not watch completion, dispatch, publish or retry effects.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Callable, Any, NoReturn

from fleet_publish_github import ResolutionGitHubClient as GitHubClient
from fleet_publish_resolver import CentralPublishHold, select

INTERVAL_SECONDS = 5.0
BUDGET_SECONDS = 1200.0


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # Coordinates are validated by their owner; never echo caller strings.
        raise CentralPublishHold("source_binding")


def wait(
    repository: str,
    source_sha: str,
    *,
    run_id: str | None = None,
    lookup: Callable[..., dict[str, Any]] = select,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    deadline = clock() + BUDGET_SECONDS
    while True:
        if clock() >= deadline:
            raise CentralPublishHold("pending")
        try:
            # A new bounded client per snapshot; no cross-snapshot API results
            # or command allowance are carried forward as current authority.
            selected = lookup(
                GitHubClient(deadline=deadline, clock=clock),
                repository,
                source_sha,
                run_id=run_id,
            )
            if clock() >= deadline:
                raise CentralPublishHold("pending")
            return selected
        except CentralPublishHold as error:
            if error.kind not in ("pending", "not_found"):
                raise
            remaining = deadline - clock()
            if remaining <= 0:
                raise
            sleep(min(INTERVAL_SECONDS, remaining))


def main(arguments: list[str]) -> int:
    parser = Parser(description=__doc__)
    parser.add_argument("repository")
    parser.add_argument("source_sha")
    parser.add_argument("--run-id")
    selected = parser.parse_args(arguments)
    print(
        json.dumps(
            wait(selected.repository, selected.source_sha, run_id=selected.run_id)
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (CentralPublishHold, OSError, ValueError, TypeError):
        # outer-boundary-process-contract: an unresolved selection is nonzero
        # with empty stdout; fixed stderr prevents API/credential disclosure.
        print(
            "Central publication run is unresolved; inspect the native source and intent without dispatching or replaying publication.",
            file=sys.stderr,
        )
        raise SystemExit(1)
