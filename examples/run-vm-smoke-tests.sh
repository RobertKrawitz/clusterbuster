#!/bin/bash
# Ordered VM smoke: memory, uperf, fio, hammerdb (pg + mariadb) — same workloads as
# run-ordered-smoke-tests.sh VM phase.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

echo "========== VM smoke: wait until no other clusterbuster --DoIt runs =========="
if [[ "${CB_SKIP_DOIT_WAIT:-0}" == "1" ]]; then
  echo "CB_SKIP_DOIT_WAIT=1: skipping DoIt wait"
else
  for _w in $(seq 1 180); do
    if ! pgrep -f 'clusterbuster.*--DoIt' >/dev/null 2>&1; then
      break
    fi
    echo "  wait $_w: another clusterbuster still running"
    sleep 20
  done
  if pgrep -f 'clusterbuster.*--DoIt' >/dev/null 2>&1; then
    echo "FATAL: other --DoIt still running after 1h"
    pgrep -af 'clusterbuster.*--DoIt' 2>/dev/null || true
    exit 1
  fi
fi
echo "========== Remove leftover clusterbuster namespaces =========="
if command -v oc >/dev/null 2>&1; then
  oc get ns -oname 2>/dev/null | grep -E 'namespace/clusterbuster' | xargs -r oc delete --ignore-not-found=true --wait=false 2>/dev/null || true
  for _t in $(seq 1 90); do
    rem=$(oc get ns --no-headers 2>/dev/null | grep -cE '^clusterbuster' || true)
    rem=$((10#${rem:-0}))
    [[ "$rem" -eq 0 ]] && break
    sleep 2
  done
fi
sleep 5

if command -v oc >/dev/null 2>&1; then
  ca=$(oc get nodes -l 'node-role.kubernetes.io/worker=' -o jsonpath='{.items[0].status.nodeInfo.architecture}' 2>/dev/null || true)
  [[ -z "$ca" ]] && ca=$(oc get nodes -o jsonpath='{.items[0].status.nodeInfo.architecture}' 2>/dev/null || true)
  if [[ "$ca" = aarch64 || "$ca" = arm64 ]] && [[ "${CB_ALLOW_VM_AARCH64:-0}" != "1" ]]; then
    echo "Skipping VM smoke tests on aarch64 cluster (KubeVirt/UEFI). Set CB_ALLOW_VM_AARCH64=1 to force."
    exit 0
  fi
fi

run() {
  echo "========== $* =========="
  "$@" || { echo "FAILED: $*"; exit 1; }
  echo "OK: $*"
}
run ./clusterbuster -f examples/memory.yaml --deployment-type=vm
run ./clusterbuster -f examples/uperf.yaml --deployment-type=vm
run ./clusterbuster -f examples/fio.yaml --deployment-type=vm
run ./clusterbuster -f examples/hammerdb.yaml --hammerdb-driver=pg --deployment-type=vm
run ./clusterbuster -f examples/hammerdb.yaml --hammerdb-driver=mariadb --deployment-type=vm
echo "All VM smoke tests passed."
