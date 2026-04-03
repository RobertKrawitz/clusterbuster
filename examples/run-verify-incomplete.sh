#!/bin/bash

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
# Rerun only autonomous-verify steps that often fail or were skipped (HammerDB VMs).
# Full suite: examples/run-autonomous-verify.sh
set -euo pipefail
cd "$(dirname "$0")/.."
LOG="${LOG:-/tmp/cb-verify-incomplete.log}"
if [[ -t 1 ]]; then
  exec > >(tee -a "$LOG") 2>&1
else
  exec >>"$LOG" 2>&1
fi
echo "=== $(date -Is) retry incomplete (hammerdb VM pg + mariadb) ==="

VM="--precleanup --cleanup=1 --replicas=1 --namespaces=1 --report=none --deployment-type=vm"

run() {
  echo "---------- $(date -Is) $* ----------"
  "$@" || { echo "FAILED exit $? : $*"; exit 1; }
  echo "OK: $*"
}

run ./clusterbuster -f hammerdb --hammerdb-driver=pg $VM --workload-runtime=90 --hammerdb-rampup=1
run ./clusterbuster -f hammerdb --hammerdb-driver=mariadb $VM --workload-runtime=90 --hammerdb-rampup=1

echo "=== $(date -Is) INCOMPLETE RETRY PASSED ==="
