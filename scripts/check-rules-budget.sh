#!/usr/bin/env bash
# Fail when the always-loaded rule files outgrow their byte budget.
#
# Every file under rules/ loads into every consuming agent session, and
# agents warn once the instruction files they load pass a 150k-character
# total (#642). Other plugins share that total, so coding-policy's own rules
# keep to the budget below. Text that governs only one workflow belongs in
# that skill's references/, which an agent reads only when it elects to.
#
# The budget counts bytes, never characters: a byte count is at least the
# character count, so staying under it in bytes keeps the character total
# under it too.
#
# Usage:  scripts/check-rules-budget.sh [rules-dir]
#   rules-dir  Directory holding the *.md rule files. Defaults to the repo's
#              rules/ (the script's parent's sibling). The argument exists so
#              the test suite can point the check at a fixture tree.
# Env:    RULES_BUDGET_BYTES overrides the budget, for the test suite only.
# stdout: one JSON object —
#           {"total_bytes": N, "budget_bytes": B, "within_budget": true|false,
#            "files": [{"path": "<file>", "bytes": n}, ...]}  (largest first)
# stderr: the actionable diagnostic when over budget or on a setup error.
# Exit:   0 within budget, 1 over budget, 2 setup error (directory missing or
#         unreadable, no rule files, a rule file whose size cannot be read,
#         python3 absent, a non-integer budget override).

set -euo pipefail

# The budget. Raising it is a policy change for its own PR: move text into a
# skill's references/ first.
readonly DEFAULT_RULES_BUDGET_BYTES=95000

main() {
  local rules_dir="${1:-}"
  if [[ -z "$rules_dir" ]]; then
    rules_dir="$(CDPATH='' cd -- "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/rules"
  fi
  local budget="${RULES_BUDGET_BYTES:-$DEFAULT_RULES_BUDGET_BYTES}"
  if [[ ! "$budget" =~ ^[1-9][0-9]*$ ]]; then
    echo "check-rules-budget: RULES_BUDGET_BYTES must be a positive integer, got '${budget}' — unset it to use the script's budget" >&2
    return 2
  fi
  if [[ ! -d "$rules_dir" ]]; then
    echo "check-rules-budget: rules directory not found: ${rules_dir} — pass the directory holding the rule files" >&2
    return 2
  fi
  if ! command -v python3 >/dev/null; then
    echo "check-rules-budget: python3 not found on PATH — install it to run this check" >&2
    return 2
  fi
  python3 - "$rules_dir" "$budget" <<'PY'
import json
import sys
from pathlib import Path

rules_dir, budget = Path(sys.argv[1]), int(sys.argv[2])
try:
    files = sorted(rules_dir.glob("*.md"))
except OSError as err:
    print("check-rules-budget: cannot list rule files under {}: {} — restore read access to the directory and rerun".format(rules_dir, err.strerror or err), file=sys.stderr)
    sys.exit(2)
if not files:
    print("check-rules-budget: no *.md rule files under {} — pass the directory holding the rule files".format(rules_dir), file=sys.stderr)
    sys.exit(2)
sizes = []
for f in files:
    try:
        sizes.append((f.stat().st_size, f.name))
    except OSError as err:
        print("check-rules-budget: cannot read the size of {}: {} — fix or remove the file and rerun".format(f, err.strerror or err), file=sys.stderr)
        sys.exit(2)
sizes.sort(reverse=True)
total = sum(size for size, _ in sizes)
within = total <= budget
print(json.dumps({
    "total_bytes": total,
    "budget_bytes": budget,
    "within_budget": within,
    "files": [{"path": str(rules_dir / name), "bytes": size} for size, name in sizes],
}))
if not within:
    largest = ", ".join("{} ({})".format(name, size) for size, name in sizes[:3])
    print("check-rules-budget: {} rule files total {} bytes, over the {}-byte budget by {}. "
          "Move text that governs one workflow into that skill's references/ and leave a binding "
          "pointer in the rule; carve-outs keep their trigger line and move their preconditions. "
          "Largest: {}.".format(len(sizes), total, budget, total - budget, largest), file=sys.stderr)
    sys.exit(1)
PY
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
