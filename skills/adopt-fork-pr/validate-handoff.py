"""Validate one UTF-8 JSON object on stdin before jq reads its values."""

import json
import sys


def reject_constant(value: str) -> None:
    raise ValueError("non-JSON number")


def main() -> int:
    try:
        raw = sys.stdin.buffer.read()
        # jq can normalize malformed input; reject it before comparing values.
        # Keep every key/value pair, including values shadowed by a duplicate
        # key. Tuples represent objects and their pairs, lists represent arrays.
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=tuple,
                              parse_constant=reject_constant, parse_int=str, parse_float=str)
        if not isinstance(document, tuple):
            return 1
        pending: list[object] = [document]
        while pending:
            value = pending.pop()
            if isinstance(value, (tuple, list)):
                pending.extend(value)
            elif isinstance(value, str):
                if "\0" in value:
                    return 1
                value.encode("utf-8")  # An unpaired surrogate cannot be encoded.
    except (ValueError, RecursionError):
        return 1
    sys.stdout.buffer.write(raw)
    return 0


if __name__ == "__main__":
    status = main()
    if status:
        print("Expected one valid UTF-8 JSON object with no NUL characters.", file=sys.stderr)
    sys.exit(status)
