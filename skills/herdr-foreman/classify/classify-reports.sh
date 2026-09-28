#!/usr/bin/env bash
# Annotate a round's delivered reports in one call, so the foreman gates them together.
#
# The foreman reads every report body in full before gating
# (rules/agent-team-operation.md Reports), and that does not change. What
# changes is the turn structure: one call annotates every report, then the foreman
# reads them all with their verdicts in hand and gates them in one turn, instead
# of spending a full-context turn per report (#482).
#
# A label never replaces the read and never approves. A failed one never blocks
# gating: the report lands in `unannotated` with the reason, and the foreman
# reads it exactly as it always has. The only effect a label can have is the
# gate `foreman report-gate-record` derives from it, which adds friction and
# never removes it (foreman/report_gates.py).
#
# Usage: classify-reports.sh [--agent jev|codex|claude|grok] <report>...
#
# Output contract (rules/script-delegation.md -- structured stdout):
#   stdout: one JSON object --
#     {"schema_version": 2, "agent": "<kind>" | "default",
#      "labels": [<classify-report.sh label>, ...],
#      "unannotated": [{"report": "<path>", "reason": "<diagnostic>"}, ...]}
#   `agent` is the adapter requested, "default" for Jev; each label names the
#   adapter that answered. A report Jev could not label is in `unannotated`
#   with the reason, and is read in full.
#   stderr: per-report progress and every unannotated report.
#
# Exit 0 whenever the arguments were valid, including when some or every
# annotation failed -- those are reported, not fatal. Exit 2 on a usage error.
#
# Each report is one model call against the chosen vendor's quota.

set -euo pipefail

# Command substitution strips every trailing newline, so the script directory
# never passes through one bare: parameter expansion derives it (#487), and a
# sentinel carries `pwd` across the strip (#466).
case "${BASH_SOURCE[0]}" in
  */*) HERE_SRC="${BASH_SOURCE[0]%/*}" ;;
  *) HERE_SRC=. ;;
esac
if ! HERE="$(cd -- "${HERE_SRC:-/}" && pwd && printf x)"; then
  echo "classify-reports: cannot enter the script directory ${HERE_SRC:-/} — restore read and search access to the plugin directory, or reinstall the plugin, then re-run" >&2
  exit 2
fi
HERE="${HERE%x}"
HERE="${HERE%$'\n'}"

SCRATCH=""
cleanup() { if [ -n "$SCRATCH" ]; then rm -rf "$SCRATCH"; fi; return 0; }
trap cleanup EXIT

die() { echo "classify-reports: $*" >&2; exit 2; }

main() {
  local agent="" reports=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --agent) agent="${2-}"; shift 2 || die "--agent needs jev, codex, claude or grok" ;;
      -h|--help) sed -n '2,31p' "${BASH_SOURCE[0]}"; exit 0 ;;
      --*) die "unknown option '$1'" ;;
      *) reports+=("$1"); shift ;;
    esac
  done
  [ "${#reports[@]}" -gt 0 ] || die "usage: classify-reports.sh [--agent jev|codex|claude|grok] <report>..."

  local work
  work="$(mktemp -d "${TMPDIR:-/tmp}/classify-reports.XXXXXX")" || die "cannot create a temporary directory"
  SCRATCH="$work"

  local index=0 report
  for report in "${reports[@]}"; do
    index=$((index + 1))
    echo "  [${index}/${#reports[@]}] ${report}" >&2
    if bash "${HERE}/classify-report.sh" "$report" ${agent:+--agent "$agent"} \
         --out "${work}/label-${index}.json" >/dev/null 2>"${work}/err-${index}"; then
      cat "${work}/err-${index}" >&2
    else
      printf '%s' "$report" > "${work}/failed-${index}"
      # Best-effort: the batch continues, and the failure is still visible.
      echo "classify-reports: ${report} was not annotated; read it in full. The classifier said:" >&2
      cat "${work}/err-${index}" >&2
    fi
  done

  python3 - "$work" "${#reports[@]}" "${agent:-default}" <<'PY'
import json, pathlib, sys
work, total, agent = pathlib.Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
labels, unannotated = [], []
for index in range(1, total + 1):
    label = work / "label-{}.json".format(index)
    failed = work / "failed-{}".format(index)
    if label.is_file():
        labels.append(json.loads(label.read_text(encoding="utf-8")))
    elif failed.is_file():
        lines = [line for line in (work / "err-{}".format(index)).read_text(encoding="utf-8",
                 errors="replace").splitlines() if line.strip()]
        unannotated.append({"report": failed.read_text(encoding="utf-8"),
                            "reason": lines[-1] if lines else "no diagnostic"})
print(json.dumps({"schema_version": 2, "agent": agent,
                  "labels": labels, "unannotated": unannotated}, sort_keys=True))
PY
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
