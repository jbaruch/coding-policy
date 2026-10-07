#!/usr/bin/env bash
# Guard the SKILL.md conventions a consumer agent depends on.
#
# Invocation conventions, checked against EVERY skill in SKILLS below:
# 1. Every script invocation carries an explicit `bash` or `python3` interpreter. tessl packaging
#    normalizes plugin files to 0644, so a bare path is a permission-denied on
#    every consumer, and `chmod +x` in this repo does not survive publish.
#    Deterministic because the failure is invisible here: the scripts run fine
#    from a clone and break only once installed.
# 2. The `bash ` convention is present, so a rewrite that drops every
#    invocation cannot pass check 1 vacuously.
# 3. Every fenced bash block that names a plugin script (`$CP`, `skills/`, or
#    `.tessl/plugins`) opens with the resolver, then one quoted `$CP` script
#    invocation and its arguments. The plugin root differs between a
#    project-local install, a global install and a coding-policy clone, so each
#    block carries its own resolver — an agent's shell state does not survive
#    between tool calls. A repo-relative `bash skills/...` fails this check: it
#    resolves only inside a coding-policy clone (#574).
# 3b. No inline code span invokes a plugin script with arguments, so an
#    instruction cannot dodge check 3 by leaving its fence.
#
# Mode-gate conventions, checked against MODE_GATE_SKILL only:
# 4. The first step gates on HERDR_ENV before any script, and the gate turns a
#    standalone agent away by reading rather than by running a script.
# 5. Inside a Herdr round, round work reaches Step 2. Only the bounded factual
#    lookup and already-in-context residual branches finish here. Checks 4a/4b pass with the old
#    direct-execution hatch restored, so they do not cover the routing the
#    foreman actually acts on: a foreman that answers a bounded question or writes a
#    deliverable itself never dispatches the round (#470).
#
# `set -e` is dropped so every check runs and the suite reports an aggregate;
# each check captures its own status (rules/error-handling.md
# aggregate-reporting carve-out).
#
# Run: bash skills/herdr-foreman/tests/test_skill_invocations.sh
set -uo pipefail

# Herdr skills whose SKILL.md invokes a plugin script. herdr-standup shipped
# bare invocations while this suite resolved its target through its own
# directory, so it only ever read herdr-foreman's SKILL.md. release ships to
# every consumer and invoked its scripts by clone-relative path (#574);
# adopt-fork-pr runs in any repo, this one included, and invoked its script
# by a mount path a clone does not have. onboard-repo and migrate-to-plugin
# invoked theirs by the project-local mount alone, which a global-only
# install does not have (#599).
SKILLS=(herdr-foreman herdr-standup release adopt-fork-pr onboard-repo migrate-to-plugin)

# Reference files whose command blocks the bootstrap carve-out also covers.
REFERENCES=(herdr-foreman/references/round-setup.md herdr-foreman/references/judge-round.md
            release/PUBLICATION.md)

# herdr-foreman alone carries the standalone/Herdr mode gate. herdr-standup
# turns a non-Herdr agent away through roster.sh's exit 1, not by reading.
MODE_GATE_SKILL=herdr-foreman

# The one resolver the bootstrap carve-out permits: project-local install,
# then global install, then the current directory, taken only when the
# clone's origin remote is one of six enumerated github.com
# jbaruch/coding-policy URLs, matched whole with no wildcard. Committed content
# cannot set .git/config, so a consumer shipping its own skills/<script> never
# passes as the plugin; it gets an install instruction and a non-zero exit.
# shellcheck disable=SC2016 # Match the documented shell source literally.
BOOTSTRAP='CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac'

die() { echo "fatal: $*" >&2; exit 2; }
warn_cleanup() { echo "warn: could not remove $1" >&2; }
INSTALL_FIXTURE=""
cleanup() {
  if [[ -n "$INSTALL_FIXTURE" ]] && ! rm -rf "$INSTALL_FIXTURE"; then
    warn_cleanup "$INSTALL_FIXTURE"
  else
    INSTALL_FIXTURE=""
  fi
  return 0
}

PASS=0
FAIL=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# Echo the first matching line number, or "" when there is no match. Exits the
# suite on a grep tool error: an unreadable SKILL.md must never read as a
# clean file. grep exits 1 on no-match and 2+ on error, and collapsing the two
# is what `|| true` would do.
first_match_line() { # <pattern> <file>
  local out rc=0
  out="$(grep -n -- "$1" "$2")" || rc=$?
  case "$rc" in
    0) printf '%s' "$out" | head -1 | cut -d: -f1 ;;
    1) printf '' ;;
    *) die "grep failed on $2 (exit ${rc}) while matching ${1}" ;;
  esac
}

check_invocations() { # <skill-name> <skill-file>
  local name="$1" skill="$2"

  # 1. No bare invocation of a plugin script, in either shape — the literal
  # mount path, or a resolved `$CP` with no interpreter in front of it.
  local bare rc=0
  bare="$(grep -nE '^[[:space:]]*("?[$]CP/[^[:space:]]+\.(sh|py)|((bash|python3)[[:space:]]+)?"?([$]HOME/)?\.tessl/plugins/[^[:space:]]+\.(sh|py))' "$skill")" || rc=$?
  case "$rc" in
    1) pass ;;
    0)
      fail "${name}: bare script invocation(s) — tessl ships plugin files 0644, so these are permission-denied on every consumer:"
      printf '%s\n' "$bare" >&2
      ;;
    *) die "grep failed scanning ${skill} for bare invocations (exit ${rc})" ;;
  esac

  # 2. The `bash ` convention is actually present, so a future rewrite that
  # drops every invocation cannot pass check 1 vacuously.
  local invocations
  rc=0
  invocations="$(grep -cE '^[[:space:]]*bash "[$]CP/[^[:space:]]+\.sh"' "$skill")" || rc=$?
  case "$rc" in
    0) if (( invocations > 0 )); then pass; else fail "${name}: no bash-prefixed invocations found"; fi ;;
    1) fail "${name}: no bash-prefixed invocations found — the convention regressed" ;;
    *) die "grep failed counting invocations in ${skill} (exit ${rc})" ;;
  esac

  # 3. Every fenced block that names a plugin script defines `CP` first. A
  # block that inherits the resolver from an earlier block is broken on
  # arrival: the agent runs each block as its own tool call, in a fresh shell.
  local unresolved
  rc=0
  # The bootstrap exception permits this exact directory choice only. Check
  # every such block, including its interpreter and continuation shape.
  # A block nested in a list item is indented; its fence's indentation is
  # stripped from every line so the literal comparison still holds. A block
  # naming no plugin script (git, gh) is an ordinary command, outside the
  # carve-out, and is skipped.
  unresolved="$(awk -v bootstrap="$BOOTSTRAP" '
    function check(   i, line, row) {
      if (!(blk ~ /\$CP|skills\/|\.tessl\/plugins/)) return
      row = 0
      for (i = 1; i <= n; i++) {
        line = lines[i]
        if (line ~ /^[[:space:]]*$/) continue
        row++
        if (row == 1 && line != bootstrap) print nums[i] ": unsupported bootstrap: " line
        if (row == 2 && line !~ /^(bash|python3) "\$CP\/skills\/[^[:space:]]+\.(sh|py)"([[:space:]]|$)/)
          print nums[i] ": expected quoted co-shipped script invocation: " line
        if (row > 2 && line !~ /^[[:space:]]+(-|<|\[|"\$CP\/)/)
          print nums[i] ": expected script arguments only: " line
        if (row > 1 && (index(line, "$(") || index(line, "`") || index(line, ";") || index(line, "&&") || index(line, "||")))
          print nums[i] ": inline evaluation is forbidden: " line
      }
      if (row < 2) print start ": incomplete bootstrap block"
    }
    !inblock && /^[[:space:]]*```bash/ {
      inblock = 1; n = 0; blk = ""; start = FNR
      ind = $0; sub(/```.*/, "", ind); ind = length(ind)
      next
    }
    inblock && /^[[:space:]]*```/ { check(); inblock = 0; next }
    inblock {
      line = $0
      if (substr(line, 1, ind) ~ /^[[:space:]]*$/) line = substr(line, ind + 1)
      lines[++n] = line; nums[n] = FNR; blk = blk "\n" line
    }
  ' "$skill")" || rc=$?
  if (( rc != 0 )); then
    die "awk failed scanning ${skill} for unresolved \$CP uses (exit ${rc})"
  fi
  if [[ -z "$unresolved" ]]; then
    pass
  else
    fail "${name}: plugin script named in a block that does not open with the resolver — each block is its own shell:"
    printf '%s\n' "$unresolved" >&2
  fi

  # 3b. No inline code span runs a plugin script: an interpreter in front of a
  # `skills/` path, or a `skills/` script followed by arguments.
  local inline
  rc=0
  # shellcheck disable=SC2016 # Backticks are Markdown code spans matched literally.
  inline="$(grep -nE '`(bash|python3) [^`]*skills/|`[^` ]*skills/[^` ]*\.(sh|py) [^`]+`' "$skill")" || rc=$?
  case "$rc" in
    1) pass ;;
    0)
      fail "${name}: inline code span invokes a plugin script — move it into a resolved command block:"
      printf '%s\n' "$inline" >&2
      ;;
    *) die "grep failed scanning ${skill} for inline invocations (exit ${rc})" ;;
  esac
}

check_mode_gate() { # <skill-name> <skill-file>
  local name="$1" skill="$2"

  # Checks 3 and 4 read the BODY only. Matching the whole file would find
  # HERDR_ENV in the frontmatter `description`, so deleting the entire gate
  # section would still pass -- an assertion that survives the removal of the
  # thing it asserts is not a test (rules/testing-standards.md Assertions).
  local body
  body="$(awk 'BEGIN{n=0} /^---[[:space:]]*$/{n++; next} n>=2' "$skill")" \
    || die "could not strip the frontmatter from ${skill}"
  [[ -n "$body" ]] || die "${skill} has no body after its frontmatter"

  local body_file="${TMPDIR:-/tmp}/skill-body.$$.md"
  printf '%s\n' "$body" > "$body_file" || die "could not stage the skill body"

  # 4a. The mode gate is the first step, before roster/script execution.
  local gate_line step1_line step2_line
  gate_line="$(first_match_line 'HERDR_ENV' "$body_file")"
  step1_line="$(first_match_line '^## Step 1 ' "$body_file")"
  step2_line="$(first_match_line '^## Step 2 ' "$body_file")"
  if [[ -z "$gate_line" ]]; then
    fail "${name}: the body states no HERDR_ENV gate — the frontmatter alone does not gate execution"
  elif [[ -z "$step1_line" ]]; then
    fail "${name}: could not locate the Step 1 heading in the body"
  elif [[ -z "$step2_line" ]]; then
    fail "${name}: could not locate the Step 2 heading in the body"
  elif (( step1_line < gate_line && gate_line < step2_line )); then pass
  else fail "${name}: the HERDR_ENV gate (body line ${gate_line}) is outside Step 1"; fi

  # 4b. That gate tells a standalone agent to stop, and says so before the
  # next step rather than anywhere in the file.
  local head flat
  if [[ -n "$step1_line" && -n "$step2_line" ]]; then
    head="$(sed -n "${step1_line},${step2_line}p" "$body_file")" || die "could not read the mode gate"
  else
    head=""
  fi
  flat="$(printf '%s' "$head" | tr '\n' ' ' | tr -s '[:space:]' ' ' \
    | tr '[:upper:]' '[:lower:]')" || die "could not flatten the preamble"
  if [[ "$flat" == *"this skill does not apply"* ]]; then pass
  else fail "${name}: Step 1 does not tell a non-Herdr agent the skill does not apply"; fi

  # 5. Read each Herdr-mode branch of Step 1 by its disposition, which is a
  # closed set of two literal sentences. Inferring routing from prose is the
  # regex trap (rules/script-delegation.md): "must not proceed to Step 2"
  # reads as routing to a pattern and as its opposite to a human. The branch
  # states its disposition verbatim instead, and this check compares literals.
  local disp_proceed="Proceed to Step 2." disp_finish="Finish here."
  # The residual branch's condition IS the contract, so it is pinned whole.
  # A substring would accept "not already in the foreman's context".
  local residual_label="Set, none of the above applies, and the answer is already in the foreman's context"
  local factual_label="Set, with a bounded factual lookup"
  local round_work=("lookup" "file inspection" "research" "bounded question" \
                    "review of existing code" "repository edit" "task deliverable")

  local branches
  branches="$(printf '%s\n' "$head" | awk '
    function flush(   b, label) {
      if (bullet == "") return
      b = bullet
      bullet = ""
      gsub(/[[:space:]]+/, " ", b)
      if (b !~ /^- \*\*Set[,*]/) return
      label = b
      sub(/^- \*\*/, "", label)
      sub(/\*\*.*$/, "", label)
      print label "\t" b
    }
    /^- \*\*/ { flush(); bullet = $0; next }
    /^[[:space:]]+[^[:space:]]/ { if (bullet != "") bullet = bullet " " $0; next }
    { flush() }
    END { flush() }')" || die "could not read the Step 1 branches"

  # A branch ends in one disposition or the other. The literal must open its
  # own sentence, so "Do not Proceed to Step 2." is not routing.
  local label text verdict routing_labels="" terminals=0 factual=0 residual=0 stray=""
  while IFS=$'\t' read -r label text; do
    [[ -n "$label" ]] || continue
    case "$text" in
      *". ${disp_proceed}"|*"— ${disp_proceed}") verdict=proceed ;;
      *". ${disp_finish}"|*"— ${disp_finish}") verdict=finish ;;
      *) verdict=none ;;
    esac
    case "$verdict" in
      proceed) routing_labels+="${label}"$'\n' ;;
      finish)
        terminals=$(( terminals + 1 ))
        [[ "$label" == "$residual_label" || "$label" == "$factual_label" ]] || stray="$label"
        [[ "$label" != "$residual_label" ]] || residual=$(( residual + 1 ))
        [[ "$label" != "$factual_label" ]] || factual=$(( factual + 1 ))
        ;;
      *) stray="$label" ;;
    esac
  done <<< "$branches"

  # 5a. Every branch disposes of its request, and only the two bounded branches end
  # the round. The old hatch was a branch that did neither.
  if [[ -n "$stray" ]]; then
    fail "${name}: Step 1 branch '${stray}' neither ends in '${disp_proceed}' nor is the residual branch ending in '${disp_finish}'"
  elif (( terminals == 2 && factual == 1 && residual == 1 )); then pass
  else fail "${name}: Step 1 has ${terminals} Herdr-mode branches ending in '${disp_finish}'; only the factual and residual branches may"; fi

  if [[ "$flat" == *"bounded factual lookup"* && "$flat" == *"bounded factual lookup. answer within that boundary and cite the source"* \
        && "$flat" == *"run no round preflight, roster measurement, enrollment, report gate or context reset for the lookup"* ]]; then pass
  else fail "${name}: bounded factual lookup must bind its contract, cite facts and skip round-only overhead"; fi

  # 5b. Each kind of round work is named by a branch that routes. Dropping one
  # returns it to the foreman.
  local term missing=()
  for term in "${round_work[@]}"; do
    [[ "$routing_labels" == *"$term"* ]] || missing+=("$term")
  done
  if (( ${#missing[@]} > 0 )); then
    fail "${name}: no Step 1 branch ending in '${disp_proceed}' names: ${missing[*]}"
  else pass; fi

  rm -f "$body_file" || warn_cleanup "$body_file"
}

# Execute a documented block's resolver and invocation against packaged-mode
# fixtures: a project-local install, a global install, a coding-policy clone
# (the current directory), and none of them. A consumer holding its own copy of
# the script, with a foreign origin or no repository at all, must refuse. Substitute a task-owned fixture
# root for the HOME token without changing the process's HOME or touching the
# user's installed plugin. The invocation runs without its documented
# arguments: placeholders like `<owner>` are redirections to a shell.
check_install_shapes() { # <skill-file>
  local fixture resolver original_resolver invocation interp script code output rc shape
  local project local_root global_root self_root body
  fixture="$(mktemp -d)" || die "cannot create install-shape fixture"
  INSTALL_FIXTURE="$fixture"
  resolver="$(awk '/^[[:space:]]*CP=/{sub(/^[[:space:]]+/, ""); print; exit}' "$1")" \
    || die "cannot read documented resolver"
  invocation="$(awk '/^[[:space:]]*(bash|python3) "\$CP\//{sub(/^[[:space:]]+/, ""); print; exit}' "$1")" \
    || die "cannot read documented invocation"
  [[ -n "$resolver" && -n "$invocation" ]] || die "missing executable command block in $1"
  interp="${invocation%% *}"
  # shellcheck disable=SC2016 # $CP is the documented literal the sed pattern matches.
  script="$(printf '%s\n' "$invocation" | sed -E 's/^(bash|python3) "\$CP\/([^"]+)".*/\2/')" \
    || die "cannot read the invoked script path"
  [[ "$script" == skills/* ]] || die "unparseable invocation in $1: $invocation"
  # shellcheck disable=SC2016 # $CP is expanded by the fixture shell, not here.
  invocation="$interp \"\$CP/$script\""
  original_resolver="$resolver"
  resolver="${resolver//\$HOME/\$INVOCATION_FIXTURE_GLOBAL}"
  code="$resolver"$'\n'"$invocation"
  project="$fixture/project with spaces"
  local_root="$project/.tessl/plugins/jbaruch/coding-policy"
  global_root="$fixture/global with spaces/.tessl/plugins/jbaruch/coding-policy"
  self_root="$project"
  git init -q "$project" || die "cannot create clone fixture"
  git -C "$project" remote add origin git@github.com:jbaruch/coding-policy.git \
    || die "cannot set clone fixture origin"
  local accepted
  for accepted in git@github.com:jbaruch/coding-policy https://github.com/jbaruch/coding-policy \
    https://github.com/jbaruch/coding-policy.git ssh://git@github.com/jbaruch/coding-policy \
    ssh://git@github.com/jbaruch/coding-policy.git git@github.com:jbaruch/coding-policy.git; do
    git -C "$project" remote set-url origin "$accepted" || die "cannot set clone fixture origin"
    rc=0
    output="$(cd "$project" && INVOCATION_FIXTURE_GLOBAL="$fixture/absent" bash -c "$resolver"$'\n''printf "%s\n" "$CP"' 2>&1)" || rc=$?
    if (( rc == 0 )) && [[ "$output" == "." ]]; then pass
    else fail "accepted origin $accepted must resolve to the clone: rc=$rc output=$output"; fi
  done
  for shape in local global self; do
    case "$shape" in
      local) body="$local_root/$script" ;;
      global) body="$global_root/$script" ;;
      self) body="$self_root/$script" ;;
    esac
    mkdir -p "$(dirname "$body")" || die "cannot create $shape plugin fixture"
    if [[ "$interp" == python3 ]]; then
      printf 'print("%s")\n' "$shape" > "$body" || die "cannot write $shape fixture"
    else
      printf 'printf "%s\\n"\n' "$shape" > "$body" || die "cannot write $shape fixture"
    fi
    chmod 0644 "$body" || die "cannot set published file modes"
  done
  for shape in local global self missing; do
    rc=0
    output="$(cd "$project" && INVOCATION_FIXTURE_GLOBAL="$fixture/global with spaces" bash -c "$code" 2>&1)" || rc=$?
    if [[ "$shape" == missing ]]; then
      if (( rc != 0 )); then pass; else fail "missing installs must fail visibly"; fi
    elif (( rc == 0 )) && [[ "$output" == "$shape" ]]; then pass
    else fail "$shape install invocation of $script: rc=$rc output=$output"; fi
    case "$shape" in
      local) mv "$local_root" "$fixture/local-unused" || die "cannot stage global-only install" ;;
      global) mv "$global_root" "$fixture/global-unused" || die "cannot stage clone-only root" ;;
      self) mv "$project/skills" "$fixture/self-unused" || die "cannot stage missing install" ;;
    esac
  done
  # A repository-controlled copy of the script is never run as the plugin.
  local impostor origin
  # Foreign repos, look-alike hosts before or after github.com, and a URL
  # that only embeds an accepted one: a leading or trailing wildcard in the
  # match would admit each of these.
  for origin in https://github.com/someone/impostor.git \
    https://evil.example/jbaruch/coding-policy.git \
    https://evilgithub.com/jbaruch/coding-policy \
    ssh://git@notgithub.com/jbaruch/coding-policy.git \
    https://github.com.evil.example/jbaruch/coding-policy.git \
    git@evilgithub.com:jbaruch/coding-policy.git \
    https://github.com/jbaruch/coding-policy.git.evil \
    none; do
    impostor="$fixture/impostor with spaces"
    mkdir -p "$impostor/$(dirname "$script")" || die "cannot create impostor fixture"
    if [[ "$origin" != none ]]; then
      git init -q "$impostor" || die "cannot create impostor repository"
      git -C "$impostor" remote add origin "$origin" || die "cannot set impostor origin"
    fi
    if [[ "$interp" == python3 ]]; then
      printf 'print("impostor")\n' > "$impostor/$script" || die "cannot write impostor fixture"
    else
      printf 'printf "impostor\\n"\n' > "$impostor/$script" || die "cannot write impostor fixture"
    fi
    rc=0
    output="$(cd "$impostor" && INVOCATION_FIXTURE_GLOBAL="$fixture/global with spaces" bash -c "$code" 2>&1)" || rc=$?
    if (( rc != 0 )) && [[ "$output" != *impostor* && "$output" == *"tessl install jbaruch/coding-policy"* ]]; then pass
    else fail "impostor ($origin) must refuse with the install instruction: rc=$rc output=$output"; fi
    rm -rf "$impostor" || die "cannot reset impostor fixture"
  done
  local bad_block
  for bad_block in \
    $'CP=.tessl/plugins/jbaruch/coding-policy\nbash "$CP/skills/herdr-foreman/roster.sh"' \
    $'CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"\nbash "$CP/skills/herdr-foreman/roster.sh"' \
    $'CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || CP=.\nbash "$CP/skills/herdr-foreman/roster.sh"' \
    $'bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-foreman/roster.sh' \
    $'"$HOME/.tessl/plugins/jbaruch/coding-policy/skills/herdr-foreman/roster.sh"' \
    $'bash skills/release/check-leftovers.sh' \
    "$original_resolver"$'\n'"$invocation"$'\n  eval unsafe'; do
    # shellcheck disable=SC2016 # Backticks delimit Markdown, not shell commands.
    printf '```bash\n%s\n```\n' "$bad_block" > "$fixture/invalid.md" || die "cannot write invalid bootstrap fixture"
    if bash -c 'source "$1"; check_invocations invalid "$2"; (( FAIL > 0 ))' \
      bash "${BASH_SOURCE[0]}" "$fixture/invalid.md" > "$fixture/guard.log" 2>&1; then
      pass
    else
      fail "bootstrap guard accepted a nonconforming command block"
    fi
  done
  # An instruction moved out of its fence into an inline span is still caught.
  # shellcheck disable=SC2016 # Backticks delimit Markdown, not shell commands.
  printf -- '- Confirm: `python3 skills/release/check-closing-issues.py <owner> <repo> <pr> --merged`\n' \
    > "$fixture/inline.md" || die "cannot write inline invocation fixture"
  if bash -c 'source "$1"; check_invocations inline "$2"; (( FAIL > 0 ))' \
    bash "${BASH_SOURCE[0]}" "$fixture/inline.md" > "$fixture/guard.log" 2>&1; then
    pass
  else
    fail "inline-span guard accepted a repo-relative invocation"
  fi
  # A stale resolver in a block nested in a list item is still caught (#621).
  # shellcheck disable=SC2016 # Backticks and $CP are Markdown literals, not shell.
  printf -- '- Run it:\n\n  ```bash\n  %s\n  bash "$CP/skills/herdr-foreman/roster.sh"\n  ```\n' \
    'CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"' \
    > "$fixture/indented.md" || die "cannot write indented block fixture"
  if bash -c 'source "$1"; check_invocations indented "$2"; (( FAIL > 0 ))' \
    bash "${BASH_SOURCE[0]}" "$fixture/indented.md" > "$fixture/guard.log" 2>&1; then
    pass
  else
    fail "bootstrap guard accepted a stale resolver in an indented command block"
  fi
  cleanup
  [[ -z "$INSTALL_FIXTURE" ]] || die "install fixture cleanup failed; the exit trap will retry"
}

check_cleanup_retry() {
  local fixture
  fixture="$(mktemp -d)" || die "cannot create cleanup fixture"
  INSTALL_FIXTURE="$fixture"
  # A failed explicit removal leaves the path available for the exit trap.
  if bash -c '
    source "$1"
    INSTALL_FIXTURE="$2"
    cleanup_calls=0
    rm() {
      cleanup_calls=$((cleanup_calls+1))
      if (( cleanup_calls == 1 )); then return 1; fi
      command rm "$@"
    }
    cleanup
    [[ "$INSTALL_FIXTURE" == "$2" && -d "$2" ]] || exit 1
    cleanup
    [[ -z "$INSTALL_FIXTURE" && ! -e "$2" ]]
  ' bash "${BASH_SOURCE[0]}" "$fixture"; then
    pass
  else
    fail "failed cleanup must retain the path and retry removal"
  fi
  cleanup
  [[ -z "$INSTALL_FIXTURE" ]] || die "cleanup retry fixture remains; the exit trap will retry"
}

# Exercise the actual release entrypoints together: standalone has no simulated
# team artifact, and ordinary advisory replies cannot become merge predicates.
check_release_advisory_contract() {
  local skills_root="$1"
  if python3 - "$skills_root" <<'PYCONTRACT'
import sys
from pathlib import Path
root = Path(sys.argv[1])
release = (root / "release/SKILL.md").read_text()
brief = (root / "herdr-foreman/templates/brief-release.md").read_text()
mechanics = (root / "release/SCRIPTING.md").read_text()
rules = root.parent / "rules"
boy_scout = (rules / "boy-scout.md").read_text()
severity = (rules / "review-severity.md").read_text()
advisory_directive = next(line for line in boy_scout.splitlines() if line.startswith("- **Advisory**"))
risk_directive = next(line for line in boy_scout.splitlines() if line.startswith("- **Unrelated blocking risk**"))
assert "Herdr round" in risk_directive and "current conversation standalone" in risk_directive
references = {
    "herdr-foreman/SKILL.md": ["skills/herdr-foreman/templates/brief-release.md"],
    "herdr-foreman/references/dispatch-recovery.md": ["skills/herdr-foreman/foreman/composer.py", "skills/herdr-foreman/foreman/assign.py"],
    "herdr-foreman/references/judge-round.md": ["skills/herdr-foreman/templates/brief-judge.md", "skills/herdr-foreman/templates/brief-judge-diagnosis.md"],
    "herdr-foreman/references/round-setup.md": ["skills/herdr-foreman/references/specialists.md"],
    "herdr-foreman/state-schema.md": ["skills/herdr-foreman/provision-worktree.sh", "skills/herdr-foreman/foreman/provision.py"],
}
for source, paths in references.items():
    content = (root / source).read_text()
    for target in paths:
        assert "`" + target + "`" in content and (root.parent / target).is_file(), (source, target)
assert "reconcile --state <state-path> --dispatch <recorded-dispatch-id>" in (root / "herdr-foreman/references/dispatch-recovery.md").read_text()
assert "rules/review-severity.md" in advisory_directive
assert "task report" not in advisory_directive and "round log" not in advisory_directive
assert "Advisory in a team round → acknowledge in the existing task report or round log" in severity
assert "Advisory standalone → note it directly in the existing review conversation" in severity
assert "Acknowledged — advisory noted" in release
assert "never simulate a team report" in release
assert "outside merge prerequisites" in release
assert "outside the merge predicate" in release and "outside the merge predicate" in mechanics
assert "Every inline comment must also be read" in release
assert "no follow-up issue or reference is required" in brief
assert "ruling obligations below" in brief
for content in (release, mechanics, brief, boy_scout):
    for contradiction in ("Reply on EVERY thread", "every thread has a reply",
                          "no review thread is unresolved", "existing follow-up references"):
        assert contradiction not in content, contradiction
PYCONTRACT
  then pass; else fail "release advisory routes contradict standalone/team merge contracts"; fi
}

# Progress and failures go to stderr; stdout carries one JSON result.
run_suite() {
  local skills_root skill name
  trap cleanup EXIT
  skills_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)" || die "could not resolve the skills dir"

  for name in "${SKILLS[@]}"; do
    skill="${skills_root}/${name}/SKILL.md"
    [[ -r "$skill" ]] || die "SKILL.md not found at ${skill}"
    check_invocations "$name" "$skill"
    check_install_shapes "$skill"
  done

  local ref
  for ref in "${REFERENCES[@]}"; do
    [[ -r "$skills_root/$ref" ]] || die "reference not found at $skills_root/$ref"
    check_invocations "$ref" "$skills_root/$ref"
  done
  check_cleanup_retry
  check_release_advisory_contract "$skills_root"

  skill="${skills_root}/${MODE_GATE_SKILL}/SKILL.md"
  check_mode_gate "$MODE_GATE_SKILL" "$skill"

  echo "results: ${PASS} pass, ${FAIL} fail" >&2
  printf '{"suite":"test_skill_invocations.sh","passed":%d,"failed":%d}\n' "$PASS" "$FAIL"
  (( FAIL == 0 ))
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  run_suite
fi
