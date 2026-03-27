#!/bin/bash
# Ordered smoke: memory, uperf, fio, hammerdb (pg + mariadb) — pods then VMs.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

echo "========== Wait until no other clusterbuster --DoIt runs =========="
# Set CB_SKIP_DOIT_WAIT=1 only on a cluster dedicated to this run (concurrent runs corrupt namespaces).
if [[ "${CB_SKIP_DOIT_WAIT:-0}" == "1" ]]; then
  echo "CB_SKIP_DOIT_WAIT=1: skipping wait for other --DoIt processes"
else
  for _w in $(seq 1 180); do
    if ! pgrep -f 'clusterbuster.*--DoIt' >/dev/null 2>&1; then
      break
    fi
    echo "  wait $_w: another clusterbuster still running"
    sleep 20
  done
  if pgrep -f 'clusterbuster.*--DoIt' >/dev/null 2>&1; then
    echo "FATAL: after 1h wait, other clusterbuster --DoIt still running (stop stale jobs or wait):"
    pgrep -af 'clusterbuster.*--DoIt' 2>/dev/null || true
    echo "Re-run after clearing those PIDs, or CB_SKIP_DOIT_WAIT=1 if cluster is exclusive."
    exit 1
  fi
fi
echo "========== Remove leftover clusterbuster namespaces =========="
if command -v oc >/dev/null 2>&1; then
  oc get ns -oname 2>/dev/null | grep -E 'namespace/clusterbuster' | xargs -r oc delete --ignore-not-found=true --wait=false 2>/dev/null || true
  for _t in $(seq 1 90); do
    # grep -c prints 0 with exit 1 when no match; do not use || echo 0 (yields "0\n0").
    rem=$(oc get ns --no-headers 2>/dev/null | grep -cE '^clusterbuster' || true)
    rem=$((10#${rem:-0}))
    [[ "$rem" -eq 0 ]] && break
    sleep 2
  done
fi
sleep 5

# VM workloads are disabled on aarch64 clusters (KubeVirt/UEFI); see clusterbuster CB_ALLOW_VM_AARCH64.
skip_vm=0
if command -v oc >/dev/null 2>&1; then
  ca=$(oc get nodes -l 'node-role.kubernetes.io/worker=' -o jsonpath='{.items[0].status.nodeInfo.architecture}' 2>/dev/null || true)
  [[ -z "$ca" ]] && ca=$(oc get nodes -o jsonpath='{.items[0].status.nodeInfo.architecture}' 2>/dev/null || true)
  if [[ "$ca" = aarch64 || "$ca" = arm64 ]] && [[ "${CB_ALLOW_VM_AARCH64:-0}" != "1" ]]; then
    skip_vm=1
  fi
fi

run() {
  echo "========== $* =========="
  "$@" || { echo "FAILED: $*"; exit 1; }
  echo "OK: $*"
}
run ./clusterbuster -f memory
run ./clusterbuster -f uperf
run ./clusterbuster -f fio
run ./clusterbuster -f hammerdb --hammerdb-driver=pg
run ./clusterbuster -f hammerdb --hammerdb-driver=mariadb
if [[ "$skip_vm" -eq 0 ]]; then
  run ./clusterbuster -f memory --deployment-type=vm
  run ./clusterbuster -f uperf --deployment-type=vm
  run ./clusterbuster -f fio --deployment-type=vm
  run ./clusterbuster -f hammerdb --hammerdb-driver=pg --deployment-type=vm
  run ./clusterbuster -f hammerdb --hammerdb-driver=mariadb --deployment-type=vm
  echo "All 10 tests passed (5 pod + 5 VM)."
else
  echo "========== Skipping VM smoke tests (aarch64 cluster; set CB_ALLOW_VM_AARCH64=1 to force) =========="
  echo "All 5 pod tests passed."
fi
