#!/usr/bin/env python3
"""Validate dispatch data before it can select an App-token repository."""

import os
import sys

from fleet_publish import PublishRefused, require
from fleet_publish_record import CENTRAL_REPOSITORY, valid_repository, valid_sha


def validate(repository: str, sha: str) -> str:
    require(
        valid_repository(repository)
        and repository != CENTRAL_REPOSITORY
        and valid_sha(sha),
        "Select an exact same-owner consumer repository and native source SHA.",
    )
    return repository.split("/", 1)[1]


if __name__ == "__main__":
    try:
        name = validate(
            os.environ.get("FLEET_CONSUMER_REPOSITORY", ""),
            os.environ.get("FLEET_SOURCE_SHA", ""),
        )
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("repository_name=" + name + "\n")
    except (PublishRefused, OSError, KeyError):
        print(
            "Select an exact enrolled jbaruch repository and 40-hex source SHA.",
            file=sys.stderr,
        )
        raise SystemExit(1)
