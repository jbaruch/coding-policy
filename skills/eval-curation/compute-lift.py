#!/usr/bin/env python3
"""compute-lift.py — Per-scenario lift from a `tessl eval view --json` payload.

Per `jbaruch/coding-policy: rules/plugin-evals.md` "Lift, Not Attainment",
lift is the headline metric. Per `rules/script-delegation.md`, deterministic
JSON parsing + arithmetic belongs in a script, not in skill prose for the
agent to execute inline.

Input: `tessl eval view --json <run-id>` payload, read from stdin or a path
argument.

Output (stdout, JSON, per the script-delegation "JSON-producing" requirement):

    {
      "lifts": [
        { "scenario_id": "<uuid>",
          "lift": <float>,
          "with_context_total": <float>,
          "baseline_total": <float> },
        ...
      ],
      "skipped": [
        { "scenario_id": "<uuid>", "reason": "<diagnostic>" },
        ...
      ]
    }

Variant pairing — `with-context` paired against `baseline`; tessl-eval also
emits `usage-spec` (older "with tile loaded") and `without-context` (older
"without tile loaded"), so the script accepts those as aliases. The first
preferred variant present wins per side.

Exit codes: 0 on success (even when scenarios are skipped — skips are data,
not errors). Non-zero only when the payload itself is malformed (not JSON,
or missing the `data.attributes.scenarios` envelope).
"""

import argparse
import json
import sys
from typing import Dict, List, Optional

WITH_CONTEXT_VARIANTS = ["with-context", "usage-spec"]
BASELINE_VARIANTS = ["baseline", "without-context"]


def solution_total(solution: Dict) -> float:
    """Sum `assessmentResults[].score` for a single solution."""
    total = 0.0
    for result in solution.get("assessmentResults") or []:
        score = result.get("score")
        if score is None:
            continue
        try:
            total += float(score)
        except (TypeError, ValueError):
            # Per script-delegation "self-error-handling": fail loud rather
            # than silently producing a wrong total. Caller turns this into
            # a `skipped` entry.
            raise ValueError(
                f"non-numeric score {score!r} in criterion "
                f"{result.get('name')!r}"
            )
    return total


def pick_variant(solutions: List[Dict], preferred: List[str]) -> Optional[Dict]:
    """Return the first solution whose `variant` is in `preferred` (priority
    order), or None if no preferred variant is present.
    """
    by_variant = {
        s.get("variant"): s
        for s in solutions or []
        if isinstance(s, dict)
    }
    for v in preferred:
        if v in by_variant:
            return by_variant[v]
    return None


def compute_lifts(payload: Dict) -> Dict:
    """Walk `payload.data.attributes.scenarios` and produce the
    {lifts, skipped} structure documented at module top.
    """
    try:
        scenarios = payload["data"]["attributes"]["scenarios"]
    except (KeyError, TypeError):
        raise ValueError(
            "payload missing `data.attributes.scenarios` — is this a "
            "`tessl eval view --json` response?"
        )
    if not isinstance(scenarios, list):
        raise ValueError("`data.attributes.scenarios` is not a list")

    lifts: List[Dict] = []
    skipped: List[Dict] = []

    for sc in scenarios:
        if not isinstance(sc, dict):
            continue
        sid = sc.get("id", "<no-id>")
        solutions = sc.get("solutions") or []
        with_context = pick_variant(solutions, WITH_CONTEXT_VARIANTS)
        baseline = pick_variant(solutions, BASELINE_VARIANTS)
        if with_context is None or baseline is None:
            skipped.append({
                "scenario_id": sid,
                "reason": (
                    f"missing variant — with-context present: "
                    f"{with_context is not None}, baseline present: "
                    f"{baseline is not None}"
                ),
            })
            continue
        try:
            wc_total = solution_total(with_context)
            bl_total = solution_total(baseline)
        except ValueError as e:
            skipped.append({"scenario_id": sid, "reason": str(e)})
            continue
        lifts.append({
            "scenario_id": sid,
            "lift": round(wc_total - bl_total, 4),
            "with_context_total": wc_total,
            "baseline_total": bl_total,
        })

    return {"lifts": lifts, "skipped": skipped}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
    )
    parser.add_argument(
        "payload",
        nargs="?",
        default="-",
        help="Path to a tessl-eval-view JSON file, or '-' to read stdin",
    )
    args = parser.parse_args(argv)

    src = sys.stdin if args.payload == "-" else open(args.payload)
    try:
        payload = json.load(src)
    except json.JSONDecodeError as e:
        print(f"error: input is not valid JSON: {e}", file=sys.stderr)
        return 2
    finally:
        if src is not sys.stdin:
            src.close()

    try:
        result = compute_lifts(payload)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
