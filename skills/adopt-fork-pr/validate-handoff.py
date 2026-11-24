"""Pass through the original JSON object bytes from gh or a Base64 handoff."""

import base64
import json
import sys


def reject_constant(value: str) -> None:
    raise ValueError("non-JSON number")


def main() -> int:
    try:
        if len(sys.argv) == 1:
            raw = sys.stdin.buffer.read()
        elif len(sys.argv) == 2:
            token = sys.argv[1].encode("ascii")
            raw = base64.b64decode(token, validate=True)
            if base64.b64encode(raw) != token:
                return 1
        else:
            return 1

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
        print("Expected a UTF-8 JSON object or its canonical Base64 handoff. "
              "Regenerate the handoff and retry.", file=sys.stderr)
    sys.exit(status)
