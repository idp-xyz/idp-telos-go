# The tiers of the platform acceptance, shared by deploy/compose/e2e.sh and tests/kind/full.sh, which `source` it (tests/ACCEPTANCE.md).
#   PATHS="A P2 P3"   run only these paths (commas work too; unset or empty: all of them); the ids are those of tests/paths.yaml, and an unknown one
#                     stops the script with 2. `want X` says whether X is selected; a path that is not prints  SKIPPED: PATH X (not selected)
#                     (not NOT_RUN: that one says a path could not run in this environment). Setup always runs. The ordered list for a change:
#                     PATHS="$(python3 -I tools/acceptance.py select --changed idp-cmi)"
#   EMBED=double|real the embedding service: double (default) is deploy/images/embedding-double, deterministic and instant; real is idp-embedding
#                     (text-embeddings-inference with Qwen3-Embedding-0.6B). The steps that need semantic similarity print
#                     NOT_RUN: PATH X needs real embeddings (EMBED=double)  with the double.
#   TIMINGS=FILE      each path's start and end as JSON (default $L/timings.json); acceptance_report prints seconds against budgets and WARNs, never fails
# Needs from the caller: L (the scratch directory, set before sourcing), and optionally ACC_MODE=compose|kind (default compose) and acc_path_hook ID
# (called when a selected path starts: e2e.sh refreshes the members' tokens there, which expire after ten minutes).
_ACC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
_acc() { python3 -I "$_ACC_ROOT/tools/acceptance.py" "$@"; }
PATHS="${PATHS:-}"; PATHS="${PATHS//,/ }"
_acc validate --paths "$PATHS" || exit 2
EMBED="${EMBED:-double}"
case "$EMBED" in double|real) ;; *) echo "BLOCKED: EMBED=$EMBED: double or real" >&2; exit 2;; esac
ACC_MODE="${ACC_MODE:-compose}"
TIMINGS="${TIMINGS:-${L:-$_ACC_ROOT/.local}/timings.json}"
export PATHS EMBED TIMINGS
_ACC_OPEN="" _ACC_T0="" _ACC_RUN0="$(date +%s.%N)"
rm -f "$TIMINGS"; mkdir -p "$(dirname "$TIMINGS")"

want() {   # want ID: is the path selected? (a word match: J does not select J0)
  local p
  p="${PATHS// /}"
  if [[ -z "$p" ]]; then return 0; fi
  [[ " $PATHS " == *" $1 "* ]]
}
path_skip() { echo "SKIPPED: PATH $1 (not selected)"; }
path_needs_real() {   # path_needs_real PASS-ID: a step that needs semantic similarity; true (and silent) when EMBED=real
  if [[ "$EMBED" == real ]]; then return 0; fi
  echo "NOT_RUN: PATH $1 needs real embeddings (EMBED=double)"
  return 1
}
path_start() {
  _ACC_OPEN="$1"; _ACC_T0="$(date +%s.%N)"
  if declare -F acc_path_hook >/dev/null; then acc_path_hook "$1"; fi
}
path_end() {
  if [[ -n "$_ACC_OPEN" ]]; then
    _acc record --timings "$TIMINGS" --id "$_ACC_OPEN" --start "$_ACC_T0" --end "$(date +%s.%N)" || true
    _ACC_OPEN=""
  fi
}
acceptance_report() {   # the table; a path that was open (the script failed inside it) is recorded as incomplete. Never changes the exit status.
  if [[ -n "$_ACC_OPEN" ]]; then
    _acc record --timings "$TIMINGS" --id "$_ACC_OPEN" --start "$_ACC_T0" --end "$(date +%s.%N)" --status incomplete || true
    _ACC_OPEN=""
  fi
  _acc record --timings "$TIMINGS" --id _run --start "$_ACC_RUN0" --end "$(date +%s.%N)" || true
  echo "== acceptance timings ($ACC_MODE, EMBED=$EMBED, PATHS=${PATHS:-all}; $TIMINGS)"
  _acc report --timings "$TIMINGS" --mode "$ACC_MODE" --embed "$EMBED" || true
}
