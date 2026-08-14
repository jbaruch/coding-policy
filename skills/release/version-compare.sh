#!/usr/bin/env bash
# Shared version-comparison helper. Sourced by verify-publish-landed.sh and
# confirm-publish-landed.sh — defines version_gt() only, with no side effects,
# no `set` changes, and no main, so sourcing it under `set -euo pipefail` is
# safe and running it directly is a harmless no-op.
#
# version_gt <a> <b>: exit 0 iff a > b, comparing numeric MAJOR.MINOR.PATCH.
# Deliberately numeric-only — no prerelease/build precedence — because its
# inputs come from capture-registry-baseline.sh, which emits numeric-only
# versions (a prerelease value would reach (( )) and error rather than order).

version_gt() {
  [[ "$1" != "$2" ]] || return 1
  local a1 a2 a3 b1 b2 b3
  IFS='.' read -r a1 a2 a3 <<< "$1"
  IFS='.' read -r b1 b2 b3 <<< "$2"
  a1=${a1:-0}; a2=${a2:-0}; a3=${a3:-0}
  b1=${b1:-0}; b2=${b2:-0}; b3=${b3:-0}
  (( a1 > b1 )) && return 0
  (( a1 < b1 )) && return 1
  (( a2 > b2 )) && return 0
  (( a2 < b2 )) && return 1
  (( a3 > b3 )) && return 0
  return 1
}
