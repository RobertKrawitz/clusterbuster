#!/usr/bin/env bash

# Copyright 2026 Robert Krawitz/Red Hat
# AI-assisted tooling (Cursor Agent).
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Run the full workload-options live suite up to N times; stop on first failure or after N passes.
# Usage: run-live-repeat.sh [max_iterations] [-- extra args to run-workload-option-tests.sh]
# Example: ./run-live-repeat.sh 10
# Example: ./run-live-repeat.sh 5 -- --workload sysbench --deployment-targets pod
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNNER="$ROOT/tests/workload-options/run-workload-option-tests.sh"
MAX="${1:-10}"
shift || true

if ! [[ "$MAX" =~ ^[0-9]+$ ]] || (( MAX < 1 )); then
	echo "usage: $0 <max_iterations> [-- extra runner args]" >&2
	exit 2
fi

for ((i = 1; i <= MAX; i++)); do
	TS=$(date +%Y%m%d_%H%M%S)
	REPORT_DIR="$ROOT/tests/workload-options/reports/run_${TS}_iter${i}"
	echo "=== iteration $i/$MAX report: $REPORT_DIR ==="
	if "$RUNNER" --mode live --report-dir "$REPORT_DIR" "$@"; then
		echo "=== iteration $i PASS ==="
	else
		ec=$?
		echo "=== iteration $i FAIL (exit $ec) — inspect logs under $REPORT_DIR and artifacts under $REPORT_DIR/artifacts/ ===" >&2
		exit "$ec"
	fi
done

echo "=== all $MAX iterations passed ==="
