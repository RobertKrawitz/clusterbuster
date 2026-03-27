#!/bin/bash
# Smoke (10 full example runs) → test_ci → func_ci.
# Log: CB_PIPELINE_LOG=/tmp/cb-pipeline.log (default)
# Exit file: CB_PIPELINE_EXIT_FILE (default /tmp/cb-pipeline.exit) — final line is exit code.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1
exec 9>/tmp/cb-full-pipeline.lock
flock -n 9 || {
  echo "Another process holds /tmp/cb-full-pipeline.lock — only one full pipeline at a time."
  exit 99
}
LOG="${CB_PIPELINE_LOG:-/tmp/cb-pipeline.log}"
EXITF="${CB_PIPELINE_EXIT_FILE:-/tmp/cb-pipeline.exit}"
rm -f "$EXITF"
if [[ -t 1 ]]; then
  exec > >(tee -a "$LOG") 2>&1
else
  exec >>"$LOG" 2>&1
fi
echo "=== $(date -Is) START pipeline (smoke → test_ci → func_ci) log=$LOG exitfile=$EXITF ==="
chmod +x ./clusterbuster ./run-perf-ci-suite 2>/dev/null || true
ec=0

echo "=== $(date -Is) PHASE 1: ordered smoke (10 runs) ==="
# Bash reads the script incrementally; rsync/git updating examples/run-ordered-smoke-tests.sh
# mid-run desyncs line boundaries (bogus "command not found"). Run from a stable temp copy in examples/.
SMOKE_TMP=$(mktemp "${PWD}/examples/.cb-ordered-smoke.XXXXXX.sh")
cp -p examples/run-ordered-smoke-tests.sh "$SMOKE_TMP"
trap 'rm -f "$SMOKE_TMP"' EXIT
bash "$SMOKE_TMP" || ec=$?

if (( ec == 0 )) ; then
  echo "=== $(date -Is) PHASE 2: test_ci profile ==="
  ./run-perf-ci-suite --profile=test_ci || ec=$?
fi
if (( ec == 0 )) ; then
  echo "=== $(date -Is) PHASE 3: func_ci profile ==="
  ./run-perf-ci-suite --profile=func_ci || ec=$?
fi

if (( ec == 0 )) ; then
  echo "=== $(date -Is) ALL_PIPELINE_PASSED ==="
fi
echo "=== $(date -Is) PIPELINE_FINAL_EXIT=$ec ==="
echo "$ec" >"$EXITF"
exit "$ec"
