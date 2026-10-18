#!/usr/bin/env bash
# Run a generic policy hook from a Tessl skill or an ACR native hook copy.
# argv: session-start|stop-handoff. stdin/stdout and status belong to the owner.
# Exit 2 on invalid event, 0 with an actionable diagnostic on installation failure.
set -euo pipefail

main() {
  local event="${1:-}" owner src here skill
  case "$event" in
    session-start) owner=session-start.sh ;;
    stop-handoff) owner=stop-handoff-hygiene.sh ;;
    *) echo 'usage: native-hook.sh session-start|stop-handoff' >&2; return 2 ;;
  esac
  case "${BASH_SOURCE[0]}" in
    */*) src="${BASH_SOURCE[0]%/*}" ;;
    *) src=. ;;
  esac
  if ! here="$(CDPATH='' cd -- "$src" && pwd -P && printf x)"; then
    echo 'coding-policy hook: restore read/search access to the installed skill' >&2
    return 0
  fi
  here="${here%x}"
  here="${here%$'\n'}"
  case "${here##*/}" in
    release|acr__jbaruch__coding-policy__release) skill="$here" ;;
    *)
      # ACR rebases the owned skill literal; anchor it at the hook's project.
      local runtime=.tessl/plugins/jbaruch/coding-policy/skills/release
      skill="${here}/../../../${runtime}" ;;
  esac
  if [[ ! -f "$skill/hooks/$owner" || ! -r "$skill/hooks/$owner" ]]; then
    echo 'coding-policy hook: repair the complete release skill before retrying' >&2
    return 0
  fi
  bash "$skill/hooks/$owner"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
