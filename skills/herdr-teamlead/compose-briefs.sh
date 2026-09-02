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
#   values: {"shared": {"KEY": "value", ...},
#            "roles":  {"<role>": {"KEY": "value", ...}, ...}}
#           `shared` fills COMMON.md and every brief; a role's own values win
#           on a collision. Roles map to `brief-<role>.md` in the templates dir.
#   stdout: one JSON object —
#           {"common":"<path>","briefs":{"<role>":"<path>", ...}}
#   stderr: diagnostics only.
#   exit  : 0 every file written with no placeholder left,
#           1 precondition unmet (usage, missing dir/file/template, no jq),
#           2 validation failed — an unfilled placeholder, a supplied key no
#             template uses, or a value that is not text. Nothing is written on
#             a validation failure,
#           3 a tool this depends on failed (the placeholder scan itself). The
#             answer is unknown, which is never reported as "no placeholders".
#   env   : none.
#
# Validation runs in both directions on purpose. An unfilled placeholder is a
# brief that lies to a worker; a supplied key nothing uses is a value the lead
# believes it sent and did not.
set -euo pipefail

# A placeholder is upper-case, digits, and underscores between double braces.
PLACEHOLDER_RE='\{\{[A-Z0-9_]+\}\}'

warn() { printf 'compose-briefs: %s\n' "$1" >&2; }

# Echo every distinct placeholder name in <file>, one per line.
placeholders_in() { # <file>
  grep -oE "$PLACEHOLDER_RE" "$1" | sed -e 's/^{{//' -e 's/}}$//' | sort -u
}

# Echo the placeholders still standing in <text>, space separated and sorted.
#
# Returns 0 when the scan RAN — empty output then means none are left — and 2
# when the scan itself failed. `grep` exits 1 on no-match and 2 on a real error
# (an unreadable input, a bad pattern), and collapsing those two into "nothing
# found" is what would let an unrendered brief pass validation
# (rules/error-handling.md Shell Error Handling).
leftover_placeholders() { # <text>
  local found rc=0 sorted
  found="$(printf '%s' "$1" | grep -oE "$PLACEHOLDER_RE")" || rc=$?
  if (( rc == 1 )); then
    printf ''
    return 0
  fi
  if (( rc != 0 )); then
    warn "the placeholder scan failed (grep exit ${rc}) — the rendered text could not be checked; re-run, and check that grep is a working GNU/BSD grep"
    return 2
  fi
  rc=0
  sorted="$(printf '%s' "$found" | sort -u | tr '\n' ' ')" || rc=$?
  if (( rc != 0 )); then
    warn "the placeholder scan failed while sorting its matches (exit ${rc}) — the rendered text could not be checked; re-run"
    return 2
  fi
  printf '%s' "$sorted"
  return 0
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

# Echo <template> with every KEY=VALUE pair in the given JSON object applied.
substitute() { # <template-file> <values-json>
  local content key value
  content="$(cat "$1")"
  while IFS= read -r key; do
    [[ -n "$key" ]] || continue
    value="$(printf '%s' "$2" | jq -r --arg k "$key" '.[$k]')"
    content="${content//\{\{$key\}\}/$value}"
  done < <(printf '%s' "$2" | jq -r 'keys[]')
  printf '%s\n' "$content"
}

main() {
  if (( $# != 3 )); then
    warn "usage: compose-briefs.sh <templates-dir> <values-json-file> <output-dir>"
    return 1
  fi
  local templates="$1" values_file="$2" outdir="$3"

  if ! command -v jq >/dev/null 2>&1; then
    warn "jq not found on PATH — install it (\`brew install jq\`) to compose briefs"
    return 1
  fi
  if [[ ! -d "$templates" ]]; then
    warn "templates dir not found: ${templates} — point at skills/herdr-teamlead/templates"
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

  local shared
  shared="$(printf '%s' "$values" | jq -c '.shared // {}')"

  # Every source file must exist before anything is written.
  local common_tpl="${templates}/COMMON.md" role
  if [[ ! -r "$common_tpl" ]]; then
    warn "template not found: ${common_tpl}"
    return 1
  fi
  while IFS= read -r role; do
    if [[ ! -r "${templates}/brief-${role}.md" ]]; then
      warn "template not found: ${templates}/brief-${role}.md — roles are named by their template"
      return 1
    fi
  done <<< "$roles"

  if ! mkdir -p "$outdir"; then
    warn "cannot create the output dir ${outdir} — check permissions"
    return 1
  fi

  # Compose into memory first: a validation failure must leave no half-written
  # round behind (`rules/file-hygiene.md` Idempotency).
  local -a out_paths=() out_bodies=()
  local merged rendered leftovers supplied known unused key
  local common_body scan_rc=0
  validate_values "$shared" "the shared values" || return 2
  common_body="$(substitute "$common_tpl" "$shared")"
  leftovers="$(leftover_placeholders "$common_body")" || scan_rc=$?
  if (( scan_rc != 0 )); then return 3; fi
  if [[ -n "${leftovers// /}" ]]; then
    warn "COMMON.md still holds unfilled placeholders: ${leftovers}— add them to .shared"
    return 2
  fi
  out_paths+=("${outdir}/COMMON.md")
  out_bodies+=("$common_body")

  while IFS= read -r role; do
    merged="$(jq -c -n --argjson a "$shared" --argjson b "$(printf '%s' "$values" | jq -c --arg r "$role" '.roles[$r]')" '$a * $b')"
    validate_values "$merged" "the values for role '${role}'" || return 2
    rendered="$(substitute "${templates}/brief-${role}.md" "$merged")"
    scan_rc=0
    leftovers="$(leftover_placeholders "$rendered")" || scan_rc=$?
    if (( scan_rc != 0 )); then return 3; fi
    if [[ -n "${leftovers// /}" ]]; then
      warn "brief-${role}.md still holds unfilled placeholders: ${leftovers}— add them to .roles.${role} or .shared"
      return 2
    fi
    # A supplied key no template uses is a value the lead believes it sent.
    # The known set is collected ONCE into a string and membership-tested with
    # a glob: piping into `grep -q` under `set -o pipefail` reports failure
    # whenever grep exits early on a match and SIGPIPEs the producer, which
    # reads as "not found" for every key that IS found.
    supplied="$(printf '%s' "$merged" | jq -r 'keys[]')"
    known="$(placeholders_in "${templates}/brief-${role}.md"; placeholders_in "$common_tpl")"
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
    out_paths+=("${outdir}/brief-${role}.md")
    out_bodies+=("$rendered")
  done <<< "$roles"

  local i
  for i in "${!out_paths[@]}"; do
    if ! printf '%s\n' "${out_bodies[$i]}" > "${out_paths[$i]}"; then
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
