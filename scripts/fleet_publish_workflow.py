"""Fixed central workflow phases, with private same-attempt replay protection.

Owner of RUNNER_TEMP/fleet-publish/.phase.json, schema1: identity binds the
central run/attempt/workflow and consumer source; stage is preparing/prepared/
claiming/claimed/executing/finished. Snapshot hashes bind all owned phase output.
Started/unknown phases HOLD; only completed, unchanged phases can be reused.
The durable executing marker precedes execute(), including its publish helper.
A finished failed result is reusable evidence, never permission to retry effects.
The .phase.json artifact uses the Stateful Artifacts gate-store exception:
unsupported schema versions HOLD, never becoming "no usable prior state".
Schema 1 is the only accepted version; no older version is accepted for migration.
Preserve unsupported state and immutable evidence, then update this owner with an
explicit owner-only migration before resuming the same native attempt. No reset
or caller-selected work directory is supported.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fleet_publish import (
    GitHub,
    PublishRefused,
    claim,
    decode,
    execute,
    prepare,
    require,
)
from fleet_publish_record import (
    CENTRAL_REPOSITORY,
    CENTRAL_WORKFLOW,
    valid_repository,
    valid_run_id,
    valid_sha,
)

STATE = ".phase.json"
LOCK = ".phase.lock"
MAX_STATE = 1024 * 1024
MAX_BYTES = 32 * 1024 * 1024
MAX_FILES = 8192
FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
REPAIR = "Preserve the phase state and immutable evidence; inspect the same native attempt without replaying publication."


def stamp(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def private(info: os.stat_result, *, directory: bool = False) -> None:
    require(
        (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        and info.st_uid == os.geteuid()
        and (directory or info.st_nlink == 1)
        and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600),
        REPAIR,
    )


def context(environment: dict[str, str]) -> dict[str, Any]:
    require(
        environment.get("GITHUB_REPOSITORY") == CENTRAL_REPOSITORY
        and environment.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and environment.get("GITHUB_REF") == "refs/heads/main"
        and environment.get("GITHUB_WORKFLOW_REF")
        == CENTRAL_REPOSITORY + "/" + CENTRAL_WORKFLOW + "@refs/heads/main"
        and environment.get("GITHUB_RUN_ATTEMPT") == "1"
        and valid_run_id(environment.get("GITHUB_RUN_ID"))
        and valid_sha(environment.get("GITHUB_SHA")),
        REPAIR,
    )
    return {
        key: environment[key]
        for key in (
            "GITHUB_REPOSITORY",
            "GITHUB_EVENT_NAME",
            "GITHUB_REF",
            "GITHUB_WORKFLOW_REF",
            "GITHUB_RUN_ATTEMPT",
            "GITHUB_RUN_ID",
            "GITHUB_SHA",
        )
    }


class PhaseState:
    def __init__(self, runner: str):
        self.fd = self.lock = -1
        self.expected: bytes | None = None
        self.root = Path(runner) / "fleet-publish"
        require(
            Path(runner).is_absolute() and Path(runner).resolve() == Path(runner),
            REPAIR,
        )
        info = Path(runner).lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid(), REPAIR)
        try:
            self.root.mkdir(mode=0o700, exist_ok=True)
            self.fd = os.open(self.root, DIRECTORY)
            private(os.fstat(self.fd), directory=True)
            self.lock = os.open(
                LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=self.fd
            )
            private(os.fstat(self.lock))
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.expected = self.read()
        except (OSError, PublishRefused):
            self.close()
            raise

    def close(self) -> None:
        try:
            if self.lock != -1:
                os.close(self.lock)
                self.lock = -1
        finally:
            if self.fd != -1:
                os.close(self.fd)
                self.fd = -1

    def read(self) -> bytes | None:
        try:
            fd = os.open(STATE, FILE, dir_fd=self.fd)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(fd)
            private(info)
            require(info.st_size <= MAX_STATE, REPAIR)
            with os.fdopen(os.dup(fd), "rb") as stream:
                value = stream.read(MAX_STATE + 1)
            require(len(value) == info.st_size, REPAIR)
            return value
        finally:
            os.close(fd)

    def save(self, state: dict[str, Any]) -> None:
        raw = (
            json.dumps(
                state, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
            + b"\n"
        )
        require(len(raw) <= MAX_STATE and self.read() == self.expected, REPAIR)
        temporary = ".phase-tmp-" + uuid.uuid4().hex
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=self.fd,
        )
        try:
            with os.fdopen(os.dup(fd), "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(fd)
        require(self.read() == self.expected, REPAIR)
        os.replace(temporary, STATE, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        os.fsync(self.fd)
        self.expected = raw

    def snapshot(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        total = 0

        def walk(fd: int, prefix: str) -> None:
            nonlocal total
            for name in sorted(os.listdir(fd)):
                if not prefix and name in (STATE, LOCK):
                    continue
                require(name not in (".", "..") and "/" not in name, REPAIR)
                relative = prefix + name
                before = os.stat(name, dir_fd=fd, follow_symlinks=False)
                require(
                    before.st_uid == os.geteuid() and not before.st_mode & 0o022, REPAIR
                )
                require(len(result) < MAX_FILES and len(relative) <= 4096, REPAIR)
                if stat.S_ISDIR(before.st_mode):
                    child = os.open(name, DIRECTORY, dir_fd=fd)
                    try:
                        require(stamp(os.fstat(child)) == stamp(before), REPAIR)
                        result[relative + "/"] = {"mode": stat.S_IMODE(before.st_mode)}
                        walk(child, relative + "/")
                    finally:
                        os.close(child)
                else:
                    require(
                        stat.S_ISREG(before.st_mode) and before.st_nlink == 1, REPAIR
                    )
                    total += before.st_size
                    require(total <= MAX_BYTES, REPAIR)
                    child = os.open(name, FILE, dir_fd=fd)
                    try:
                        require(stamp(os.fstat(child)) == stamp(before), REPAIR)
                        digest = hashlib.sha256()
                        with os.fdopen(os.dup(child), "rb") as stream:
                            for chunk in iter(lambda: stream.read(65536), b""):
                                digest.update(chunk)
                        require(stamp(os.fstat(child)) == stamp(before), REPAIR)
                        result[relative] = {
                            "mode": stat.S_IMODE(before.st_mode),
                            "size": before.st_size,
                            "sha256": digest.hexdigest(),
                        }
                    finally:
                        os.close(child)
                require(
                    stamp(os.stat(name, dir_fd=fd, follow_symlinks=False))
                    == stamp(before),
                    REPAIR,
                )

        walk(self.fd, "")
        return result

    def load(self, identity: dict[str, Any]) -> dict[str, Any] | None:
        if self.expected is None:
            return None
        value = decode(self.expected)
        require(
            json.dumps(
                value, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
            + b"\n"
            == self.expected,
            REPAIR,
        )
        require(
            isinstance(value, dict)
            and set(value)
            == {"schema_version", "identity", "stage", "snapshot", "selected", "result"}
            and type(value.get("schema_version")) is int
            and value["schema_version"] == 1
            and value.get("identity") == identity
            and value.get("stage")
            in ("preparing", "prepared", "claiming", "claimed", "executing", "finished")
            and isinstance(value.get("snapshot"), dict)
            and type(value.get("selected")) is bool
            and (
                type(value["result"]) is bool
                if value.get("stage") == "finished"
                else value.get("result") is None
            ),
            REPAIR,
        )
        require(value["stage"] not in ("preparing", "claiming", "executing"), REPAIR)
        require(value["snapshot"] == self.snapshot(), REPAIR)
        return value


def selected_output(selected: bool, environment: dict[str, str]) -> None:
    output = Path(environment["GITHUB_OUTPUT"])
    fd = os.open(output, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.geteuid()
            and info.st_nlink == 1,
            REPAIR,
        )
        os.write(fd, ("selected=" + str(selected).lower() + "\n").encode())
    finally:
        os.close(fd)


def main() -> int:
    require(
        sys.argv[1:] in (["prepare"], ["claim"], ["execute"]),
        "Use one fixed workflow phase.",
    )
    environment = dict(os.environ)
    identity = context(environment)
    phase = sys.argv[1]
    owner = PhaseState(environment["RUNNER_TEMP"])
    try:
        if owner.expected is not None:
            stored = decode(owner.expected)
            require(
                isinstance(stored, dict) and isinstance(stored.get("identity"), dict),
                REPAIR,
            )
            bound = stored["identity"]
            for key, value in identity.items():
                require(bound.get(key) == value, REPAIR)
            for key in ("FLEET_CONSUMER_REPOSITORY", "FLEET_SOURCE_SHA"):
                require(
                    key not in environment or environment[key] == bound.get(key), REPAIR
                )
            identity = bound
        else:
            require(phase == "prepare", REPAIR)
            identity.update(
                {
                    key: environment[key]
                    for key in ("FLEET_CONSUMER_REPOSITORY", "FLEET_SOURCE_SHA")
                }
            )
        require(
            set(identity)
            == set(context(environment))
            | {"FLEET_CONSUMER_REPOSITORY", "FLEET_SOURCE_SHA"}
            and valid_repository(identity.get("FLEET_CONSUMER_REPOSITORY"))
            and identity["FLEET_CONSUMER_REPOSITORY"] != CENTRAL_REPOSITORY
            and valid_sha(identity.get("FLEET_SOURCE_SHA")),
            REPAIR,
        )
        state = owner.load(identity)
        if phase == "prepare":
            if state is None:
                require(owner.snapshot() == {}, REPAIR)
                state = {
                    "schema_version": 1,
                    "identity": identity,
                    "stage": "preparing",
                    "snapshot": {},
                    "selected": None,
                    "result": None,
                }
                owner.save(state)
                environment["FLEET_PUBLISH_DATE"] = datetime.now(UTC).date().isoformat()
                prepare(
                    GitHub(),
                    identity["FLEET_CONSUMER_REPOSITORY"],
                    identity["FLEET_SOURCE_SHA"],
                    owner.root,
                    environment,
                )
                selection = decode((owner.root / "selection.json").read_bytes())
                require(
                    isinstance(selection, dict)
                    and selection.get("mode")
                    in ("already_confirmed", "publish", "moderation_only"),
                    REPAIR,
                )
                state.update(
                    stage="prepared",
                    snapshot=owner.snapshot(),
                    selected=selection["mode"] != "already_confirmed",
                )
                owner.save(state)
            selected_output(state["selected"], environment)
        elif phase == "claim":
            if state is None or state["selected"] is not True:
                raise PublishRefused(REPAIR)
            if state["stage"] == "prepared":
                state.update(stage="claiming")
                owner.save(state)
                claim(GitHub(), owner.root)
                state.update(stage="claimed", snapshot=owner.snapshot())
                owner.save(state)
            else:
                require(state["stage"] in ("claimed", "finished"), REPAIR)
        else:
            if state is None or state["selected"] is not True:
                raise PublishRefused(REPAIR)
            if state["stage"] == "claimed":
                state.update(stage="executing")
                owner.save(state)  # Durable before any execute()/publish helper.
                result = execute(GitHub(), owner.root)
                require(type(result) is bool, REPAIR)
                state.update(stage="finished", snapshot=owner.snapshot(), result=result)
                owner.save(state)
            else:
                require(state["stage"] == "finished", REPAIR)
            return 0 if state["result"] is True else 1
        return 0
    finally:
        owner.close()


def entrypoint() -> int:
    try:
        return main()
    except (PublishRefused, OSError, ValueError, KeyError, TypeError, UnicodeError):
        # outer-boundary-process-contract: the workflow treats nonzero as HOLD;
        # fixed guidance preserves uncertain state without exposing raw inputs.
        print(
            "Central publisher failed closed; preserve phase state and immutable intent/receipt. Do not replay an uncertain publication.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(entrypoint())
