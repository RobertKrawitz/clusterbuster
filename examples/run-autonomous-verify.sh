#!/bin/bash
# Autonomous verification (same workloads as ordered smoke, bounded size).
set -euo pipefail
cd "$(dirname "$0")/.."
LOG=/tmp/cb-autonomous-verify.log
exec > >(tee -a "$LOG") 2>&1
echo "=== $(date -Is) start ==="

POD="--precleanup --cleanup=1 --replicas=1 --namespaces=1 --report=none"
VM="--precleanup --cleanup=1 --replicas=1 --namespaces=1 --report=none --deployment-type=vm"

run() {
  echo "---------- $(date -Is) $* ----------"
  "$@" || { echo "FAILED exit $? : $*"; exit 1; }
  echo "OK: $*"
}

run ./clusterbuster -f memory $POD --processes=1 --workload-runtime=20
run ./clusterbuster -f uperf $POD --workload-runtime=25
run ./clusterbuster -f fio $POD --workload-runtime=25 --fio-patterns=read --fio-filesize=1Gi
run ./clusterbuster -f hammerdb --hammerdb-driver=pg $POD --workload-runtime=60 --hammerdb-rampup=1
run ./clusterbuster -f hammerdb --hammerdb-driver=mariadb $POD --workload-runtime=60 --hammerdb-rampup=1
run ./clusterbuster -f memory $VM --processes=1 --workload-runtime=25
run ./clusterbuster -f uperf $VM --workload-runtime=30
run ./clusterbuster -f fio $VM --workload-runtime=30 --fio-patterns=read --fio-filesize=1Gi
run ./clusterbuster -f hammerdb --hammerdb-driver=pg $VM --workload-runtime=90 --hammerdb-rampup=1
run ./clusterbuster -f hammerdb --hammerdb-driver=mariadb $VM --workload-runtime=90 --hammerdb-rampup=1

echo "=== $(date -Is) ALL 10 PASSED ==="
