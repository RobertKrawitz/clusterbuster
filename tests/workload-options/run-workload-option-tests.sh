#!/usr/bin/env bash
# Thin wrapper: implementation is Python (lib/clusterbuster/workload_options).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec python3 "$REPO/tests/workload-options/run_workload_option_tests.py" "$@"
