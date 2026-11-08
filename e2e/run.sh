#!/usr/bin/env bash
# Unattended end-to-end check for the Ask Ledger plugin.
#
# Runs as a normal user (no root) in a fenced test machine. Everything it
# writes goes under $OUT; nothing outside $WORK and $OUT is touched.
#
# Inputs (environment):
#   HERMES_TAR   required. `git archive` tar of hermes-agent at the pinned commit
#                (network to GitHub is not assumed).
#   HERMES_SHA   required. The full 40-hex commit the tar must come from; checked against
#                the commit id git archive embeds in the tar (git get-tar-commit-id).
#                Hermes cannot read its own version from an archive (it reports
#                "unknown", which satisfies every requires_hermes floor), so this hash
#                is the record of which release was tested.
#   HERMES_REQS  required past the unit tests. The tag's locked dependency set, exported
#                with hashes (one `name==version --hash=sha256:…` per package). pip can't
#                resolve Hermes's own pins (uv's lock can), so this set is installed
#                as-is, hash-checked, wheels only, then Hermes itself with --no-deps.
#   OUT          output directory (default ./e2e-out). Logs + copies of the ledger and page.
#   WORK         scratch directory (default: mktemp -d).
#   E2E_CONFIG   optional path to a Hermes config.yaml with model/provider settings and
#                no secrets. The model key itself comes from the environment the
#                operator provides (for example the provider's *_API_KEY variable);
#                this script never prints or writes it.
#   E2E_FENCED=1 required for anything past the unit tests: says this is the fenced
#                test machine, where installing third-party code is allowed.
#   E2E_PYTHON   interpreter for Hermes's venv (default python3). Hermes 0.22.6+/0.23.0
#                gate their core dependencies on Python 3.14, so this must be a 3.14.
#   SKIP_DEMO=1  run unit tests, doctor and validate only (no model calls).
#
# Exit code: 0 when every step passed, else the number of failed steps.

set -u -o pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
HERMES_SHA="${HERMES_SHA:-}"
OUT="$(mkdir -p "${OUT:-$PWD/e2e-out}" && cd "${OUT:-$PWD/e2e-out}" && pwd)"
WORK="${WORK:-$(mktemp -d)}"
FAILED=0
LOG="$OUT/run.log"
: > "$LOG"

say()  { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$LOG"; }
step() {  # step <name> <command...>: run, log to $OUT/<name>.log, record pass/fail
  local name="$1"; shift
  say "== $name"
  if "$@" > "$OUT/$name.log" 2>&1; then say "   PASS $name"; else say "   FAIL $name (see $name.log)"; FAILED=$((FAILED+1)); fi
}

[ -n "${HERMES_TAR:-}" ] && [ -f "$HERMES_TAR" ] || { say "HERMES_TAR must name a git archive tar of hermes-agent"; exit 99; }
[[ "$HERMES_SHA" =~ ^[0-9a-f]{40}$ ]] || { say "HERMES_SHA must be the full 40-hex pinned commit"; exit 99; }
TAR_SHA="$(git get-tar-commit-id < "$HERMES_TAR" 2>/dev/null || true)"
[ "$TAR_SHA" = "$HERMES_SHA" ] || { say "tar commit '$TAR_SHA' is not the pinned $HERMES_SHA; refusing"; exit 98; }
HERMES_SRC="$WORK/hermes-src"
mkdir -p "$HERMES_SRC" && tar -x -f "$HERMES_TAR" -C "$HERMES_SRC" || { say "cannot unpack $HERMES_TAR"; exit 97; }
printf '%s\n' "$HERMES_SHA" > "$OUT/hermes-commit.txt"
say "repo=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo 'from archive') hermes_sha=$HERMES_SHA work=$WORK"

# 1. Unit tests: stdlib only, no Hermes needed.
step unit-tests python3 -I -m unittest discover -s "$REPO/tests" -t "$REPO" -v

# Everything below installs third-party code. Only on the fenced test machine.
if [ "${E2E_FENCED:-0}" != "1" ]; then
  say "E2E_FENCED=1 not set: stopping before any install (unit tests only)"; say "failed steps: $FAILED"; exit "$FAILED"
fi
grep -q '^name = "hermes-agent"' "$HERMES_SRC/pyproject.toml" 2>/dev/null \
  || { say "tar is not a hermes-agent source tree; refusing to install it"; exit 96; }
[ -f "${HERMES_REQS:-}" ] || { say "HERMES_REQS must name the tag's hashed requirements export"; exit 94; }
sha256sum "$HERMES_REQS" > "$OUT/hermes-reqs.sha256" 2>/dev/null || true

# 2. Isolated Hermes: own venv, own HERMES_HOME, never the operator's.
export HERMES_HOME="$WORK/hermes-home"
mkdir -p "$HERMES_HOME/plugins"
PY="${E2E_PYTHON:-python3}"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 14) else 1)' 2>/dev/null \
  || { say "$PY is not Python 3.14+; Hermes needs 3.14 (set E2E_PYTHON)"; exit 95; }
"$PY" -m venv "$WORK/venv"
# shellcheck disable=SC1091
. "$WORK/venv/bin/activate"
# The locked set exactly as exported: hash-checked, wheels only, no resolution.
step pip-locked  python -m pip install --disable-pip-version-check --no-input --require-hashes --only-binary=:all: --no-deps -r "$HERMES_REQS"
# Editable (Hermes's setup refuses to build a wheel or sdist of itself), with no deps of its own.
step pip-install python -m pip install --disable-pip-version-check --no-input --no-deps -e "$HERMES_SRC"
python -m pip freeze --exclude-editable > "$OUT/pip-freeze.txt" 2>&1 || true
command -v hermes >/dev/null || { say "hermes CLI not installed; stopping"; exit $((FAILED+1)); }
hermes --version > "$OUT/hermes-version.txt" 2>&1 || true
[ -n "${E2E_CONFIG:-}" ] && cp "$E2E_CONFIG" "$HERMES_HOME/config.yaml"

# 3. The two catalog checks, against the plugin directory.
step doctor   hermes plugins doctor "$REPO/plugin" --ci
step validate hermes plugins validate "$REPO/plugin" --install-deps

# 4. Install as a user plugin and enable it.
cp -R "$REPO/plugin" "$HERMES_HOME/plugins/ask-ledger"
step enable  hermes plugins enable ask-ledger
# `plugins enable` runs admission, which syncs Hermes's managed environment and may need
# network; when it fails it stays a FAIL above. The loader itself only reads plugins.enabled,
# and this plugin has no dependencies, so the checks below run after this direct write.
# The value must be a YAML list (a bare name is refused); it replaces the list, which is
# fine in this fresh HERMES_HOME.
step enable-config hermes config set plugins.enabled '[ask-ledger]'
step listed  bash -c 'hermes plugins list 2>&1 | tee /dev/stderr | grep -q ask-ledger'

# 4b. Hook contract through Hermes's own dispatch (no model calls, no gateway):
#     gateway message = one ask, wake event = no ask but a reminder, "local" = captured once.
#     Uses a separate HERMES_HOME copy so its invented asks stay out of the demo ledger.
HC_HOME="$WORK/hermes-home-contract"
cp -R "$HERMES_HOME" "$HC_HOME"
HERMES_HOME="$HC_HOME" hermes config set plugins.entries.ask-ledger.settings.remind_after_minutes 0 \
  > "$OUT/contract-config.log" 2>&1
step hook-contract env HERMES_HOME="$HC_HOME" python -I "$HERE/hook_contract.py"

if [ "${SKIP_DEMO:-0}" = "1" ]; then
  say "SKIP_DEMO=1: stopping before model calls"; say "failed steps: $FAILED"; exit "$FAILED"
fi

# 5. Demo (invented messages only). Each `hermes chat` is a fresh process,
#    so every call after the first is a restart as far as the plugin goes.
DATA="$HERMES_HOME/plugin-data/ask-ledger"
ask()  { hermes chat --oneshot -q "$1"; }
step demo-ask-1 ask "Please draft a short packing list for a weekend hiking trip."
step demo-ask-2 ask "What is a good name for a houseplant that is a small cactus?"
step demo-ask-3 ask "Plan a three-course dinner for six that is all vegetarian. Before you plan it, ask me which night it is for."
step ledger-has-3 python3 -I -c '
import json,sys
ev=[json.loads(l) for l in open(sys.argv[1]) if l.strip()]
new=[e for e in ev if e.get("op")=="new"]
print(len(new),"asks"); assert len(new)>=3' "$DATA/asks.jsonl"

# Restart: a new process asks what is still pending; the reminder block comes from disk.
step demo-remind ask "Before anything else: which of my earlier requests are still open or waiting on me? Use the ids."
step remind-uses-ids grep -Eq 'A[0-9]' "$OUT/demo-remind.log"

# Mark one answered through the tool, then show /asks and the page.
step demo-update ask "Mark A2 as answered with the one-line answer 'Spike', using ask_update."
step ledger-answered python3 -I -c '
import json,sys
ev=[json.loads(l) for l in open(sys.argv[1]) if l.strip()]
assert any(e.get("op")=="set" and e.get("id")=="A2" and e.get("state")=="answered" for e in ev)' "$DATA/asks.jsonl"
step demo-slash ask "/asks"
step page-written bash -c "grep -q 'Ask Ledger' '$DATA/asks.html' && ! grep -qi '<script' '$DATA/asks.html'"

cp "$DATA/asks.jsonl" "$DATA/asks.html" "$OUT/" 2>/dev/null || true
say "failed steps: $FAILED"
exit "$FAILED"
