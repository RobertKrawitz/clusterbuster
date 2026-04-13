#!/bin/bash

# Verification: run fio, uperf, memory, HammerDB (pg/mariadb) as pod and VM.
# Requires: clusterbuster-workloads and clusterbuster-vm images.
# Usage: ./examples/verify-workloads-pod-vm.sh [--artifactdir=DIR]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CB="$(dirname "$SCRIPT_DIR")/clusterbuster"
EXTRA_ARGS=("$@")

skip_hammerdb=0
if command -v oc >/dev/null 2>&1; then
    ca=$(oc get nodes -l 'node-role.kubernetes.io/worker=' \
         -o jsonpath='{.items[0].status.nodeInfo.architecture}' 2>/dev/null || true)
    [[ -z "$ca" ]] && ca=$(oc get nodes \
         -o jsonpath='{.items[0].status.nodeInfo.architecture}' 2>/dev/null || true)
    if [[ "$ca" = aarch64 || "$ca" = arm64 ]]; then
        skip_hammerdb=1
    fi
fi

run_workload() {
    local mode="$1"
    shift
    local runtime_arg=""
    if [[ "$mode" == "vm" ]]; then
        runtime_arg="--runtime-class=vm"
    fi
    "$CB" --cleanup --precleanup --exit-at-end \
        $runtime_arg "$@" "${EXTRA_ARGS[@]}"
}

for mode in pod vm; do
    # fio
    run_workload "$mode" \
        --workload=fio --workloadruntime=45 --replicas=1 \
        --fio-patterns=read --fio-iodepths=1 --fio-filesize=1Gi \
        --fio-ioengines=libaio

    # uperf
    run_workload "$mode" \
        --workload=uperf --workloadruntime=30 --replicas=1 \
        --uperf-msg-sizes=8192 --uperf-nthr=1

    # memory
    run_workload "$mode" \
        --workload=memory --workloadruntime=30 --replicas=1 \
        --memory-size=10485760

    if [[ "$skip_hammerdb" -eq 0 ]]; then
        # HammerDB pg
        run_workload "$mode" \
            --workload=hammerdb --workloadruntime=60 --replicas=1 \
            --hammerdb-driver=pg --hammerdb-rampup=1

        # HammerDB mariadb
        run_workload "$mode" \
            --workload=hammerdb --workloadruntime=60 --replicas=1 \
            --hammerdb-driver=mariadb --hammerdb-rampup=1
    fi
done

echo "All verification workloads completed successfully."
