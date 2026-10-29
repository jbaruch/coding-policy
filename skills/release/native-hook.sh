#!/usr/bin/env bash
# Run a generic policy hook from a Tessl skill or an ACR native hook copy.
# argv: session-start|stop-handoff|session-end. stdin/stdout and status belong to the owner.
# Exit 2 on invalid event, 1 with a stderr diagnostic on installation failure.
set -euo pipefail

main() {
  local event="${1:-}" owner src here skill hooks host interpreter=bash
  case "$event" in
    session-start) owner=session-start.sh ;;
    stop-handoff) owner=stop-handoff-hygiene.sh ;;
    session-end) owner=session-end-cleanup.py; interpreter=python3 ;;
    *) echo 'usage: native-hook.sh session-start|stop-handoff|session-end' >&2; return 2 ;;
  esac
  case "${BASH_SOURCE[0]}" in
    */*) src="${BASH_SOURCE[0]%/*}" ;;
    *) src=. ;;
  esac
  if ! here="$(CDPATH='' cd -- "$src" && pwd -P && printf x)"; then
    echo 'coding-policy hook: restore read/search access to the installed skill' >&2
    return 1
  fi
  here="${here%x}"
  here="${here%$'\n'}"
  case "${here##*/}" in
    release|acr__jbaruch__coding-policy__release) skill="$here" ;;
    *)
      # ACR copies hook assets unchanged; select the sibling native skill.
      hooks="${here%/*}"
      host="${hooks%/*}"
      if [[ "${hooks##*/}" != hooks ]]; then
        echo 'coding-policy hook: restore the managed hook under its native host' >&2
        return 1
      fi
      case "${host##*/}" in
        .codex|.claude|.cursor) skill="$host/skills/acr__jbaruch__coding-policy__release" ;;
        *) echo 'coding-policy hook: restore the supported native host layout' >&2; return 1 ;;
      esac ;;
  esac
  if [[ ! -f "$skill/hooks/$owner" || ! -r "$skill/hooks/$owner" ]]; then
    echo 'coding-policy hook: repair the complete release skill before retrying' >&2
    return 1
  fi
  if ! command -v "$interpreter" >/dev/null 2>&1; then
    echo "coding-policy hook: install ${interpreter} and make it available on PATH before retrying" >&2
    return 1
  fi
  "$interpreter" "$skill/hooks/$owner"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
