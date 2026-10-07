#!/usr/bin/env bash
# Compose one round's briefs from the packaged templates.
#
# Substitution is a pure function of (template, values), so it belongs in a
# script rather than in an agent's hands (`rules/script-delegation.md`): a
# placeholder the agent forgets to fill reaches a worker as the literal
# `{{WORKTREE}}`, and a worker with a cleared context has no way to notice.
#
# Contract:
#   argv  : <templates-dir> <values-json-file> <output-dir>
#   values: packaged templates also take top-level task (existing owner task id)
#           and optional state (non-default ledger path). Exact base values are
#           derived automatically, never supplied as SHA approval assertions.
#           {"shared": {"KEY": "value", ...},
#            "roles":  {"<role>": {"KEY": "value", ...}, ...}}
#           `shared` fills COMMON.md and every brief; a role's own values win
#           on a collision. Roles map to `brief-<role>.md` in the templates dir;
#           a seat `<role>#<slice>` takes its role's template.
#           advisor/investigator/architect fall back to brief-specialist.md.
#           SPECIALIST_CONTEXT defaults to empty only where the template uses it.
#   stdout: one JSON object —
#           {"common":"<path>","briefs":{"<role>":"<path>", ...}}
#   stderr: diagnostics only.
#   exit  : 0 every file written with its template placeholders filled literally,
#           1 precondition unmet (usage, missing dir/file/template, no jq,
#             no python3),
#           2 validation failed — an unfilled placeholder, a supplied key no
#             template uses, a value that is not text, an invalid/reused REPORT
#             path, a REPORT longer than FOREMAN_REPORT_PATH_MAX_COLS, or a reviewer/tester
#             REVIEW_PACKAGE that is not an absolute readable non-empty file,
#             or an unreadable POLICY_INDEX / RELEASE_SKILL / TEAM_OPERATION
#             artifact, an absent TEAM_OPERATION key, or a common template
#             with no {{TEAM_OPERATION}} placeholder.
#             Nothing is written on a
#             validation failure,
#           3 a tool this depends on failed (template reading/value extraction,
#             the placeholder scan, or the
#             renderable-text check in foreman/renderable.py). The answer is
#             unknown, which is never reported as "no placeholders".
#           A REPORT, POLICY_INDEX, RELEASE_SKILL, TEAM_OPERATION or REVIEW_PACKAGE
#           path, or a
#           SLICE_PATHS glob, that foreman/renderable.py refuses is exit 2.
#   env   : FOREMAN_REPORT_PATH_MAX_COLS overrides the REPORT length limit
#           (tests, a fleet whose narrowest pane is wider); a non-integer or
#           zero value is a precondition failure (exit 1).
#
# Validation runs in both directions on purpose. An unfilled placeholder is a
# brief that lies to a worker; a supplied key nothing uses is a value the foreman
# believes it sent and did not.
set -euo pipefail

# A placeholder is upper-case, digits, and underscores between double braces.
PLACEHOLDER_RE='\{\{[A-Z0-9_]+\}\}'
# Longest REPORT value a brief may carry. The worker's final message ends with
# `REPORT: <path>`, and the wait confirms the complete literal on one visible
# row. A TUI wraps that line at its own content width and a wrap cannot be told
# from a newline, so the path must fit one row of the pane it is sent to. This
# cap is a coarse composition-time bound only: no brief knows its pane, so
# `foreman apply` measures the live pane width and refuses a marker that pane
# would wrap (`marker_columns` in foreman/report_delivery.py).
FOREMAN_REPORT_PATH_MAX_COLS="${FOREMAN_REPORT_PATH_MAX_COLS:-100}"

warn() { printf 'compose-briefs: %s\n' "$1" >&2; }

SKILL_DIR="$(CDPATH='' cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Whether one JSON value (a string or an array of strings) stays intact on a
# rendered line. The character rule is `foreman/renderable.py`, the one check
# the report marker, the GATES block and every brief path share (#578).
# Returns 0 renderable, 1 not, 3 when the check itself could not run. An exit
# code alone is no verdict: python3 also exits 1 on an uncaught exception such
# as a failed import, so a verdict counts only when stdout carries the
# module's JSON object agreeing with the exit code.
renderable_json() { # <json-value> [--code-span]
  local rc=0 out verdict
  out="$(printf '%s' "$1" | PYTHONPATH="${SKILL_DIR}${PYTHONPATH:+:${PYTHONPATH}}" \
    python3 -m foreman.renderable ${2:+"$2"})" || rc=$?
  case "$rc" in
    0) verdict=true ;;
    1) verdict=false ;;
    *) verdict="" ;;
  esac
  if [[ -n "$verdict" ]] \
     && printf '%s' "$out" | jq -e --argjson v "$verdict" \
          'type == "object" and .renderable == $v' >/dev/null; then
    return "$rc"
  fi
  warn "the renderable-text check failed (exit ${rc}, no verdict on stdout) — the value could not be checked; confirm python3 runs and ${SKILL_DIR}/foreman/renderable.py is installed"
  return 3
}

#: The responsibilities a SEAT may fill, mirroring `tiers.SEATABLE_ROLES`.
SEATABLE_ROLES="reviewer tester"

# The slice boundary a seat's brief carries, or empty for a plain role. Derived
# from the seat, so a round cannot dispatch several full-surface verdicts by
# forgetting to write the boundary by hand (skills/herdr-foreman/references/team-operation.md
# Review Before PR).
slice_scope() { # <role-or-seat> <slice-paths-json> <digest>
  # Named paths, not just a slice name: a boundary a worker cannot resolve is
  # not a boundary, and the composer receives no partition document.
  local listed
  case "$1" in
    *"#"*)
      listed="$(printf '%s' "$2" | jq -r 'map("`" + . + "`") | join(", ")')" || return 3
      printf 'Your slice this round is **%s**, and it owns %s. That slice is your whole surface: a full pass covers all of it and nothing beyond it. An observation outside your slice goes in a separate section of your report and forms no part of your verdict. (Partition %s.)' \
        "${1#*#}" "$listed" "$3"
      ;;
    *) printf '' ;;
  esac
}

template_for_role() { # <templates> <role>
  # A seat (`reviewer#api`) takes its ROLE's template: the slice is brief
  # content, never a separate template to author (#434).
  local role="${2%%#*}"
  local path="${1}/brief-${role}.md"
  if [[ ! -r "$path" ]]; then
    case "$role" in
      advisor|investigator|architect) path="${1}/brief-specialist.md" ;;
    esac
  fi
  printf '%s\n' "$path"
}

# Echo every distinct placeholder name in <file>, one per line.
placeholders_in() { # <file>
  local found rc=0
  found="$(grep -oE "$PLACEHOLDER_RE" "$1")" || rc=$?
  if (( rc == 1 )); then return 0; fi
  if (( rc != 0 )); then
    warn "cannot scan placeholders in ${1} — check file permissions and the grep installation"
    return 2
  fi
  if ! printf '%s\n' "$found" | sed -e 's/^{{//' -e 's/}}$//' | sort -u; then
    warn "cannot normalize placeholders in ${1} — check the sed and sort installation"
    return 2
  fi
}

# Refuse a value that is not text before it reaches a brief.
#
# `jq -r` prints a JSON null as the four characters `null`, which substitutes
# cleanly and leaves NO placeholder behind — the brief then reads as fully
# rendered while telling a worker its worktree is at `null`. Objects and arrays
# arrive as JSON fragments the same way.
validate_values() { # <values-json> <label>
  local offenders
  offenders="$(printf '%s' "$1" | jq -r '
    to_entries
    | map(select((.value | type) as $t | $t != "string" and $t != "number"))
    | map("\(.key) (\(.value | type))")
    | join(", ")')" || {
    warn "could not inspect the values for ${2} — check that they are a JSON object"
    return 2
  }
  if [[ -n "$offenders" ]]; then
    warn "${2} carries values that are not text: ${offenders} — give each one a string (a JSON null renders as the literal 'null' in a brief)"
    return 2
  fi
  return 0
}

validate_review_package() { # <merged-values-json> <role-or-seat>
  # A seat (`reviewer#api`) owes its ROLE's review-package checks: the slice
  # narrows what it reviews, never what its brief must carry (#434).
  local role="${2%%#*}"
  case "$role" in reviewer|tester) ;; *) return 0 ;; esac
  local package ref key
  for key in REVIEW_BASE REVIEW_HEAD; do
    ref="$(printf '%s' "$1" | jq -r --arg k "$key" '.[$k] // ""')" || return 2
    if [[ ! "$ref" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]]; then
      warn "${key} for role '${2}' must be a full lowercase commit SHA — resolve the recorded range with git rev-parse before composing"
      return 2
    fi
  done
  local package_json check_rc=0
  package_json="$(printf '%s' "$1" | jq -c '.REVIEW_PACKAGE')" || return 2
  renderable_json "$package_json" --code-span || check_rc=$?
  if (( check_rc == 3 )); then return 3; fi
  if (( check_rc != 0 )); then
    warn "REVIEW_PACKAGE for role '${2}' must be a file path without control, format or line-separator characters or backticks (the brief renders it in a code span) — run review-package.sh for the recorded range and pass its output path"
    return 2
  fi
  package="$(printf '%s' "$1" | jq -r '.REVIEW_PACKAGE // ""')" || return 2
  if [[ "$package" != /* \
        || ! -f "$package" || ! -r "$package" || ! -s "$package" ]]; then
    warn "REVIEW_PACKAGE for role '${2}' must name an absolute readable non-empty file — run review-package.sh for the recorded range and pass its output path"
    return 2
  fi
}

# Echo <template> with every KEY=VALUE pair in the given JSON object applied.
substitute() { # <template-file> <values-json>
  local content key value keys token prefix rendered=""
  if ! content="$(cat "$1" && printf x)"; then
    warn "cannot read template $1 — restore its read access before composing"
    return 3
  fi
  content="${content%x}"
  if ! keys="$(printf '%s' "$2" | jq -r 'keys[]')"; then
    warn "cannot enumerate template values — repair the JSON values and jq installation before composing"
    return 3
  fi
  # Consume only the original template. Inserted text is never scanned as
  # another placeholder, including when it names a supplied or unknown key.
  while [[ "$content" =~ $PLACEHOLDER_RE ]]; do
    token="${BASH_REMATCH[0]}"
    key="${token#\{\{}"
    key="${key%\}\}}"
    if [[ $'\n'"$keys"$'\n' != *$'\n'"$key"$'\n'* ]]; then
      warn "${1##*/} still holds unfilled placeholders: $token — add it to the template's shared or role values"
      return 2
    fi
    prefix="${content%%"$token"*}"
    content="${content#*"$token"}"
    # Sentinels preserve EOF across every command-substitution boundary.
    if ! value="$(printf '%s' "$2" | jq -rj --arg k "$key" '.[$k]' && printf x)"; then
      warn "cannot read template value $key — repair the JSON values and jq installation before composing"
      return 3
    fi
    value="${value%x}"
    rendered+="$prefix$value"
  done
  printf '%s' "$rendered$content"
}

main() {
  if (( $# != 3 )); then
    warn "usage: compose-briefs.sh <templates-dir> <values-json-file> <output-dir>"
    return 1
  fi
  local templates="$1" values_file="$2" outdir="$3"

  case "$FOREMAN_REPORT_PATH_MAX_COLS" in
    ''|*[!0-9]*)
      warn "FOREMAN_REPORT_PATH_MAX_COLS must be a positive integer, got '${FOREMAN_REPORT_PATH_MAX_COLS}' — unset it to use the script's default"
      return 1
      ;;
  esac
  if (( 10#$FOREMAN_REPORT_PATH_MAX_COLS < 1 )); then
    warn "FOREMAN_REPORT_PATH_MAX_COLS must be a positive integer, got '${FOREMAN_REPORT_PATH_MAX_COLS}' — unset it to use the script's default"
    return 1
  fi
  # Normalize to decimal once, so a validated `08` is not reparsed as octal.
  FOREMAN_REPORT_PATH_MAX_COLS=$(( 10#$FOREMAN_REPORT_PATH_MAX_COLS ))

  if ! command -v jq >/dev/null 2>&1; then
    warn "jq not found on PATH — install it (\`brew install jq\`) to compose briefs"
    return 1
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    warn "python3 not found on PATH — install Python 3.11+ (\`brew install python@3.11\`) to compose briefs"
    return 1
  fi
  if [[ ! -d "$templates" ]]; then
    warn "templates dir not found: ${templates} — point at skills/herdr-foreman/templates"
    return 1
  fi
  if [[ ! -r "$values_file" ]]; then
    warn "values file not readable: ${values_file}"
    return 1
  fi

  local values rc=0
  values="$(jq -e '.' < "$values_file" 2>/dev/null)" || rc=$?
  if (( rc != 0 )); then
    warn "values file ${values_file} is not valid JSON — fix it and re-run"
    return 1
  fi
  local roles
  roles="$(printf '%s' "$values" | jq -r '.roles | keys[]' 2>/dev/null)" || {
    warn "values file ${values_file} has no .roles object — see the contract at the top of this script"
    return 1
  }
  if [[ -z "$roles" ]]; then
    warn "values file ${values_file} names no roles — nothing to compose"
    return 1
  fi
  # In jq, before the keys become a newline-delimited list: a key carrying a
  # newline is split into two pseudo-roles by the `while read` below, so a
  # later shell test never sees the offending key at all.
  local bad_key
  bad_key="$(printf '%s' "$values" | jq -r '[.roles | keys[] | select(length == 0 or test("[/=,\u0000-\u001f\u007f]"))] | first // empty')" || return 2
  if [[ -n "$bad_key" ]]; then
    warn "values file ${values_file} has a role key that cannot name the brief it writes — a key carrying a path separator, '=', ',' or a control character does not read back through the output path and the CLI keys"
    return 2
  fi

  # Packaged provenance comes from the already-registered task and Git-owned
  # worktree receipts. Custom templates remain a pure rendering interface.
  local provenance_keys=""
  if [[ -r "${templates}/COMMON.md" ]]; then
    provenance_keys="$(placeholders_in "${templates}/COMMON.md")" || return 3
    if [[ $'\n'"$provenance_keys"$'\n' == *$'\nBASE_REVISION\n'* ]]; then
      values="$(printf '%s' "$values" | PYTHONPATH="${SKILL_DIR}${PYTHONPATH:+:${PYTHONPATH}}" python3 -m foreman.provision compose)" || return 2
    fi
  fi

  local shared
  shared="$(printf '%s' "$values" | jq -c '.shared // {}')"

  # Every source file must exist before anything is written.
  local common_tpl="${templates}/COMMON.md" role role_tpl seatable_base
  if [[ ! -r "$common_tpl" ]]; then
    warn "template not found: ${common_tpl}"
    return 1
  fi
  while IFS= read -r role; do
    # The key names the brief this run WRITES (`brief-<role>.md`), so it is
    # checked before it reaches a path. `template_for_role` resolves a seat to
    # its role, which would otherwise let `reviewer#/../../outside` take the
    # reviewer template and redirect the output outside `outdir` (#434). The
    # CLI's own `require_seatable` is not in the picture when this script runs
    # directly.
    # A denylist, not an allowlist: the planner accepts any custom role name,
    # so rejecting more than what could reach a path would break the plan →
    # compose round-trip for a round the planner happily emits.
    # Exactly what cannot name `brief-<role>.md` inside the output directory or
    # read back through `--brief ROLE=PATH` and `--roles a,b`. The `brief-`
    # prefix makes a leading dot or dash harmless, and `..` without a separator
    # names an ordinary file, so neither is rejected: the planner emits custom
    # roles, and everything it emits has to compose.
    # The jq pass above rejects the whole set before the keys are split into
    # lines; this arm catches what a line-oriented read could still hand us.
    if [[ -z "$role" || "$role" == *"/"* || "$role" == *"="* || "$role" == *","* ]]; then
      warn "role key '${role}' cannot name the brief it writes — a key carrying a path separator, '=', ',' or a control character does not read back through the output path and the CLI keys"
      return 2
    fi
    # Only a responsibility whose verification a slice terminates is seated;
    # `plan`, `apply` and recovery all refuse the rest, so composing a brief
    # for one would write a round nothing downstream accepts (#434).
    if [[ "$role" == *"#"* ]] && ! [[ "${role#*#}" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
      warn "seat '${role}' names the slice '${role#*#}', which cannot address it: name a slice with letters, digits, underscores, dots or hyphens, starting with a letter or digit"
      return 2
    fi
    if [[ "$role" == *"#"* ]]; then
      # An exact arm, never substring membership: `reviewer tester#api` matches
      # inside " reviewer tester " and would pass as a seat.
      case " ${SEATABLE_ROLES} " in
        *" ${role%%#*} "*)
          case "${role%%#*}" in
            *[[:space:]]*) seatable_base="" ;;
            *) seatable_base="${role%%#*}" ;;
          esac ;;
        *) seatable_base="" ;;
      esac
      if [[ -z "$seatable_base" ]]; then
        warn "role key '${role}' seats '${role%%#*}', and only ${SEATABLE_ROLES// /, } are seated — every other responsibility holds a per-task counter one worker owns"
        return 2
      fi
    fi
    role_tpl="$(template_for_role "$templates" "$role")"
    if [[ ! -r "$role_tpl" ]]; then
      warn "template not found: ${role_tpl} — supply the packaged role template"
      return 1
    fi
  done <<< "$roles"

  # Compose into memory first: a validation failure must leave no half-written
  # round behind, and no output directory either (`rules/file-hygiene.md`
  # Idempotency); the directory is created only once every check has passed.
  local -a out_paths=() out_bodies=() report_paths=()
  local merged rendered supplied known common_known unused key report rendered_scope slice_digest
  local common_body check_rc=0
  validate_values "$shared" "the shared values" || return 2
  # Resolver-produced policy paths are explicit brief inputs. Custom templates
  # need not carry POLICY_INDEX or RELEASE_SKILL; any supplied artifact must
  # remain readable at compose. TEAM_OPERATION is different: every worker brief
  # names the team-round contract as a required read (rules/agent-team-operation.md
  # Team Round Contract), so the key is required and COMMON.md must render it,
  # whichever templates the foreman passes.
  local policy_key policy_path policy_present
  if ! printf '%s' "$shared" | jq -e 'has("TEAM_OPERATION")' >/dev/null; then
    warn "TEAM_OPERATION is required in .shared — every worker brief names the team-round contract; run resolve-policy-paths.sh and copy its TEAM_OPERATION path"
    return 2
  fi
  common_known="$(placeholders_in "$common_tpl")" || return 3
  if [[ $'\n'"${common_known}"$'\n' != *$'\nTEAM_OPERATION\n'* ]]; then
    warn "$(basename "$common_tpl") carries no {{TEAM_OPERATION}} placeholder — every worker brief must render the team-round contract path; add it to the common template"
    return 2
  fi
  for policy_key in POLICY_INDEX RELEASE_SKILL TEAM_OPERATION; do
    policy_present="$(printf '%s' "$shared" | jq -r --arg k "$policy_key" 'has($k)')" || return 2
    if [[ "$policy_present" == true ]]; then
      check_rc=0
      renderable_json "$(printf '%s' "$shared" | jq -c --arg k "$policy_key" '.[$k]')" --code-span || check_rc=$?
      if (( check_rc == 3 )); then return 3; fi
      if (( check_rc != 0 )); then
        warn "${policy_key} must be a file path without control, format or line-separator characters or backticks (the brief renders it in a code span) — use resolve-policy-paths.sh output"
        return 2
      fi
      policy_path="$(printf '%s' "$shared" | jq -r --arg k "$policy_key" '.[$k]')" || return 2
      if [[ "$policy_path" != /* || ! -f "$policy_path" || ! -r "$policy_path" || ! -s "$policy_path" ]]; then
        warn "${policy_key} must name an absolute readable non-empty file — run resolve-policy-paths.sh and supply its output before composing"
        return 2
      fi
    fi
  done
  common_body="$(substitute "$common_tpl" "$shared" && printf x)" || return $?
  common_body="${common_body%x}"
  out_paths+=("${outdir}/COMMON.md")
  out_bodies+=("$common_body")

  while IFS= read -r role; do
    role_tpl="$(template_for_role "$templates" "$role")"
    local policy_override
    policy_override="$(printf '%s' "$values" | jq -r --arg r "$role" '.roles[$r] | has("POLICY_INDEX") or has("RELEASE_SKILL") or has("TEAM_OPERATION")')" || return 2
    if [[ "$policy_override" == true ]]; then
      warn "policy artifact paths for role '${role}' belong only in .shared — remove per-role POLICY_INDEX, RELEASE_SKILL and TEAM_OPERATION keys"
      return 2
    fi
    merged="$(jq -c -n --argjson a "$shared" --argjson b "$(printf '%s' "$values" | jq -c --arg r "$role" '.roles[$r]')" '$a * $b')"
    # SLICE_PATHS is a list, not a placeholder value: it is read here and
    # dropped before the text check, which every rendered value must pass.
    merged="$(printf '%s' "$merged" | jq -c 'del(.SLICE_PATHS, .SLICE_DIGEST)')" || return 2
    validate_values "$merged" "the values for role '${role}'" || return 2
    known="$(placeholders_in "$role_tpl")" || return 3
    if [[ $'\n'"${known}"$'\n' == *$'\nSPECIALIST_CONTEXT\n'* ]]; then
      merged="$(printf '%s' "$merged" | jq -c '{SPECIALIST_CONTEXT:""} * .')" || return 2
    fi
    # `.shared` too: a key merged from there is overwritten below, so leaving it
    # unchecked would accept a supplied boundary by silently discarding it.
    if printf '%s' "$values" | jq -e --arg r "$role" '(.shared // {} | has("SLICE_SCOPE")) or (.roles[$r] | has("SLICE_SCOPE"))' >/dev/null; then
      warn "SLICE_SCOPE for role '${role}' is composed from the seat and its paths, not supplied — remove the key from .shared and .roles"
      return 2
    fi
    if printf '%s' "$values" | jq -e '.shared // {} | has("SLICE_PATHS")' >/dev/null; then
      warn "SLICE_PATHS belongs to one seat, never to .shared — every slice owns different paths"
      return 2
    fi
    if printf '%s' "$values" | jq -e '.shared // {} | has("SLICE_DIGEST")' >/dev/null; then
      warn "SLICE_DIGEST belongs to one seat, never to .shared — apply re-derives each seat's digest from the plan"
      return 2
    fi
    local slice_paths="[]"
    slice_digest=""
    if [[ "$role" == *"#"* ]]; then
      # The globs are rendered verbatim into the worker's brief, so a backtick
      # or a control character could close the code span and append
      # instructions of its own. A path glob needs neither.
      check_rc=0
      if printf '%s' "$values" | jq -e --arg r "$role" '.roles[$r].SLICE_PATHS | type == "array" and length > 0 and all(type == "string" and (. | gsub("\\s";"") | length) > 0)' >/dev/null; then
        renderable_json "$(printf '%s' "$values" | jq -c --arg r "$role" '.roles[$r].SLICE_PATHS')" --code-span || check_rc=$?
        if (( check_rc == 3 )); then return 3; fi
      else
        check_rc=1
      fi
      if (( check_rc != 0 )); then
        warn "seat '${role}' needs SLICE_PATHS: the non-empty list of path globs its slice owns, copied from the partition validate-partition accepted, each a string without backticks or control characters. A slice name alone leaves the worker no boundary to respect"
        return 2
      fi
      slice_paths="$(printf '%s' "$values" | jq -c --arg r "$role" '.roles[$r].SLICE_PATHS')" || return 2
      # Transported, never derived here: the digest is computed in one place
      # (`partition.seat_digest`), and `apply` re-derives it from the plan and
      # compares. A second implementation in shell would drift from the first.
      if ! printf '%s' "$values" | jq -e --arg r "$role" '.roles[$r].SLICE_DIGEST | type == "string" and test("^[0-9a-f]{12}$")' >/dev/null; then
        warn "seat '${role}' needs SLICE_DIGEST: its twelve-character entry from the plan's seat_digests. It travels into the brief, and apply re-derives it from the plan, so a boundary edited after validation is refused at dispatch"
        return 2
      fi
      slice_digest="$(printf '%s' "$values" | jq -r --arg r "$role" '.roles[$r].SLICE_DIGEST')" || return 2
    elif printf '%s' "$values" | jq -e --arg r "$role" '.roles[$r] | has("SLICE_PATHS") or has("SLICE_DIGEST")' >/dev/null; then # unseated
      warn "SLICE_PATHS for role '${role}' names a slice it does not own — an unseated role reviews the whole change"
      return 2
    fi
    if [[ $'\n'"${known}"$'\n' == *$'\nSLICE_SCOPE\n'* ]]; then
      # Assigned and checked on its own line: nested in the outer jq's --arg,
      # a failing slice_scope is discarded and the brief composes with an empty
      # boundary -- the one outcome the placeholder exists to prevent.
      rendered_scope="$(slice_scope "$role" "$slice_paths" "$slice_digest")" || return 3
      merged="$(printf '%s' "$merged" | jq -c --arg s "$rendered_scope" '. + {SLICE_SCOPE:$s}')" || return 2
    elif [[ "$role" == *"#"* ]]; then
      # A custom template without the placeholder would compose a seated brief
      # carrying no boundary, and its worker would return a full-surface
      # verdict over a partitioned change.
      warn "the template for seat '${role}' carries no {{SLICE_SCOPE}} placeholder — a seated brief must render its slice boundary; add it to $(basename "$role_tpl")"
      return 2
    fi
    case "$role" in
      advisor|investigator|architect)
        if ! printf '%s' "$merged" | jq -e --arg role "$role" '.RESPONSIBILITY == $role' >/dev/null; then
          warn "RESPONSIBILITY must match consultation role '${role}' — preserve the assigned responsibility in its brief"
          return 2
        fi
        ;;
    esac
    # Validate in JSON before command substitution can strip trailing newlines
    # or discard a NUL byte from the path. A U+2028 or C1 control splits the
    # worker's `REPORT: <path>` marker across rows as surely as a newline.
    check_rc=0
    renderable_json "$(printf '%s' "$merged" | jq -c '.REPORT')" --code-span || check_rc=$?
    if (( check_rc == 3 )); then return 3; fi
    if (( check_rc != 0 )); then
      warn "REPORT for role '${role}' must be a string without control, format or line-separator characters or backticks (the brief renders it in a code span) — choose a fresh absolute file path on one line"
      return 2
    fi
    report="$(printf '%s' "$merged" | jq -r '.REPORT // ""')"
    if [[ "$report" != /* || "$report" == */ ]]; then
      warn "REPORT for role '${role}' must be an absolute file path on one line — choose a fresh path for this attempt"
      return 2
    fi
    if [[ -e "$report" || -L "$report" ]]; then
      warn "REPORT for role '${role}' already exists at ${report} — preserve it and choose a fresh path for this attempt"
      return 2
    fi
    local prior_report
    for prior_report in ${report_paths[@]+"${report_paths[@]}"}; do
      if [[ "$report" == "$prior_report" ]]; then
        warn "REPORT for role '${role}' duplicates another role's destination — give each assignment a distinct report path"
        return 2
      fi
    done
    report_paths+=("$report")
    if (( ${#report} > FOREMAN_REPORT_PATH_MAX_COLS )); then
      warn "REPORT for role '${role}' is ${#report} characters; the limit is ${FOREMAN_REPORT_PATH_MAX_COLS}, a coarse bound on the worker's \`REPORT: <path>\` line (\`foreman apply\` checks the live pane width before dispatch) — use a shorter reports directory (e.g. one under \$HOME/.local/state) and re-run"
      return 2
    fi
    rendered="$(substitute "$role_tpl" "$merged" && printf x)" || return $?
    rendered="${rendered%x}"
    # A supplied key no template uses is a value the foreman believes it sent.
    # The known set is collected ONCE into a string and membership-tested with
    # a glob: piping into `grep -q` under `set -o pipefail` reports failure
    # whenever grep exits early on a match and SIGPIPEs the producer, which
    # reads as "not found" for every key that IS found.
    supplied="$(printf '%s' "$merged" | jq -r 'keys[]')"
    common_known="$(placeholders_in "$common_tpl")" || return 3
    known+=$'\n'"$common_known"
    unused=""
    while IFS= read -r key; do
      [[ -n "$key" ]] || continue
      if [[ $'\n'"${known}"$'\n' != *$'\n'"${key}"$'\n'* ]]; then
        unused+="${key} "
      fi
    done <<< "$supplied"
    if [[ -n "$unused" ]]; then
      warn "values for role '${role}' carry keys no template uses: ${unused}— remove them or fix the name"
      return 2
    fi
    check_rc=0
    validate_review_package "$merged" "$role" || check_rc=$?
    if (( check_rc != 0 )); then return "$check_rc"; fi
    out_paths+=("${outdir}/brief-${role}.md")
    out_bodies+=("$rendered")
  done <<< "$roles"

  local output_path
  for report in "${report_paths[@]}"; do
    for output_path in "${out_paths[@]}"; do
      if [[ "$report" == "$output_path" ]]; then
        warn "REPORT overlaps a generated brief at ${report} — choose a separate report destination"
        return 2
      fi
    done
  done

  if ! mkdir -p "$outdir"; then
    warn "cannot create the output dir ${outdir} — check permissions"
    return 1
  fi
  local i
  for i in "${!out_paths[@]}"; do
    if ! printf '%s' "${out_bodies[$i]}" > "${out_paths[$i]}"; then
      warn "cannot write ${out_paths[$i]} — check permissions on ${outdir}"
      return 1
    fi
  done

  local briefs_json="{}"
  while IFS= read -r role; do
    briefs_json="$(printf '%s' "$briefs_json" | jq -c --arg r "$role" --arg p "${outdir}/brief-${role}.md" '. + {($r): $p}')"
  done <<< "$roles"
  jq -n --arg common "${outdir}/COMMON.md" --argjson briefs "$briefs_json" \
    '{common: $common, briefs: $briefs}'
  return 0
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
