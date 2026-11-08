#!/usr/bin/env bash
# Run e2e/run.sh against each pinned Hermes release that has a tar present.
#
#   TARS_DIR=/path/to/tars [REQS_DIR=/path/to/reqs] OUT=/path/to/out bash e2e/run_matrix.sh
#
# Expects, in TARS_DIR, `git archive` tars named hermes-<tag>.tar, and in REQS_DIR
# (default TARS_DIR) each tag's hashed requirements export, hermes-<tag>.requirements.txt.
# A release whose tar or requirements file is missing is skipped and reported, never
# treated as a pass. v0.23.0 is the primary run.
# Each run gets its own OUT/<tag>/ and its own WORK (venv + HERMES_HOME).
# Other run.sh inputs (E2E_CONFIG, SKIP_DEMO, the model key) pass through unchanged.

set -u -o pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARS_DIR="${TARS_DIR:?set TARS_DIR}"
REQS_DIR="${REQS_DIR:-$TARS_DIR}"   # hermes-<tag>.requirements.txt: hashed export of the tag's lock
OUT_ROOT="$(mkdir -p "${OUT:-$PWD/e2e-out}" && cd "${OUT:-$PWD/e2e-out}" && pwd)"

# tag -> pinned commit (peeled; verified against each tar's embedded commit id)
RELEASES=(
  "v0.23.0 2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19"
  "v0.22.6 f0d94882e89ab8d4098b5bb2f166adfb9ee0a0ab"
)

SUMMARY="$OUT_ROOT/matrix.txt"; : > "$SUMMARY"
WORST=0
for row in "${RELEASES[@]}"; do
  read -r tag sha <<< "$row"
  tar="$TARS_DIR/hermes-$tag.tar"
  if [ ! -f "$tar" ]; then
    echo "$tag $sha SKIPPED (no $tar)" | tee -a "$SUMMARY"; continue
  fi
  reqs="$REQS_DIR/hermes-$tag.requirements.txt"
  if [ ! -f "$reqs" ]; then
    echo "$tag $sha SKIPPED (no $reqs)" | tee -a "$SUMMARY"; continue
  fi
  out="$OUT_ROOT/$tag"; work="$(mktemp -d)"
  OUT="$out" WORK="$work" HERMES_TAR="$tar" HERMES_SHA="$sha" HERMES_REQS="$reqs" bash "$HERE/run.sh"
  rc=$?
  echo "$tag $sha exit=$rc" | tee -a "$SUMMARY"
  [ "$rc" -gt "$WORST" ] && WORST=$rc
done
exit "$WORST"
