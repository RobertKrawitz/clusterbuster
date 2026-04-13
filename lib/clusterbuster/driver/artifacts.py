# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""Parallel artifact collection: pod logs, describe, VM artifacts."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cluster import ClusterInterface
    from .config import ClusterbusterConfig

_LOG = logging.getLogger(__name__)


def retrieve_artifacts(
    cluster: ClusterInterface,
    config: ClusterbusterConfig,
    *,
    force: bool = False,
    run_started: bool = True,
    run_failed: bool = False,
    sync_namespace: str = "",
    already_retrieved: bool = False,
) -> bool:
    """Collect pod logs, describe output, and VM artifacts.

    Returns True if artifacts were collected.
    """
    if not config.artifactdir:
        return False
    if already_retrieved:
        return False
    if not run_started and not force:
        return False

    _ensure_artifact_dirs(config.artifactdir, vm=(config.deployment_type == "vm"))

    pods_json = cluster.get_json(
        "pod", "-A",
        f"-l{config.basename}-id={config.uuid}",
    )
    items = pods_json.get("items", [])
    if not items:
        _LOG.info("No pods found for artifact collection")
        return True

    tasks: list[tuple[str, str, str, str]] = []
    for pod in items:
        metadata = pod.get("metadata", {})
        ns = metadata.get("namespace", "")
        name = metadata.get("name", "")
        phase = pod.get("status", {}).get("phase", "")

        if phase == "Succeeded" and not config.retrieve_successful_logs and not force and not run_failed:
            continue

        containers = pod.get("spec", {}).get("containers", [])
        for container in containers:
            cname = container.get("name", "")
            tasks.append((ns, name, cname, phase))

    _LOG.info("Collecting artifacts for %d container(s)", len(tasks))

    with ThreadPoolExecutor(max_workers=config.parallel_log_retrieval) as pool:
        futures = []
        seen_pods: set[str] = set()
        for ns, pod_name, container, phase in tasks:
            pod_key = f"{ns}/{pod_name}"
            if pod_key not in seen_pods:
                seen_pods.add(pod_key)
                futures.append(pool.submit(
                    _describe_entity, cluster, config.artifactdir, ns, pod_name,
                ))
            futures.append(pool.submit(
                _log_container, cluster, config.artifactdir, ns, pod_name, container,
            ))

        for f in as_completed(futures):
            exc = f.exception()
            if exc is not None:
                _LOG.debug("Artifact collection task failed: %s", exc)

    if config.deployment_type == "vm":
        _retrieve_vm_artifacts(cluster, config, sync_namespace)

    return True


def _ensure_artifact_dirs(artifactdir: str, *, vm: bool = False) -> None:
    """Create artifact subdirectories if needed."""
    for subdir in ("Describe", "Logs"):
        os.makedirs(os.path.join(artifactdir, subdir), exist_ok=True)
    if vm:
        for subdir in ("VMLogs", "VMXML"):
            os.makedirs(os.path.join(artifactdir, subdir), exist_ok=True)


def _describe_entity(
    cluster: ClusterInterface,
    artifactdir: str,
    namespace: str,
    name: str,
    entitytype: str = "pod",
) -> None:
    """Write oc describe output for an entity."""
    output = cluster.describe(entitytype, "-n", namespace, name)
    if output:
        path = os.path.join(artifactdir, "Describe", f"{namespace}:{name}")
        try:
            with open(path, "w") as f:
                f.write(output)
        except OSError as exc:
            _LOG.debug("Failed to write describe for %s/%s: %s", namespace, name, exc)


def _log_container(
    cluster: ClusterInterface,
    artifactdir: str,
    namespace: str,
    pod_name: str,
    container: str,
) -> None:
    """Write oc logs output for a container."""
    output = cluster.logs("-n", namespace, pod_name, "-c", container)
    if output:
        path = os.path.join(artifactdir, "Logs", f"{namespace}:{pod_name}:{container}")
        try:
            with open(path, "w") as f:
                f.write(output)
        except OSError as exc:
            _LOG.debug("Failed to write logs for %s/%s/%s: %s", namespace, pod_name, container, exc)


def _retrieve_vm_artifacts(
    cluster: ClusterInterface,
    config: ClusterbusterConfig,
    sync_namespace: str,
) -> None:
    """Collect VM-specific artifacts (describe vm/vmi, libvirt XML, cloud-init logs)."""
    for resource_type in ("vm", "vmi"):
        data = cluster.get_json(
            resource_type, "-A",
            f"-l{config.basename}-id={config.uuid}",
        )
        for item in data.get("items", []):
            metadata = item.get("metadata", {})
            ns = metadata.get("namespace", "")
            name = metadata.get("name", "")
            if ns == sync_namespace and not config.sync_in_first_namespace:
                continue
            _describe_entity(cluster, config.artifactdir, ns, name, entitytype=resource_type)

    _retrieve_vm_xml(cluster, config, sync_namespace)
    _retrieve_cloud_init_logs(cluster, config, sync_namespace)


def _retrieve_vm_xml(
    cluster: ClusterInterface,
    config: ClusterbusterConfig,
    sync_namespace: str,
) -> None:
    """Retrieve libvirt XML from virt-launcher pods.

    Matches bash: finds the virt-launcher pod for each VM via the
    ``vm.kubevirt.io/name`` label, then cats the QEMU XML file from
    the filesystem.  Writes to ``VMXML/{ns}_{vm}.xml``.
    """
    vms_json = cluster.get_json(
        "vm", "-A",
        f"-l{config.basename}-id={config.uuid}",
    )
    for vm in vms_json.get("items", []):
        metadata = vm.get("metadata", {})
        ns = metadata.get("namespace", "")
        vm_name = metadata.get("name", "")
        if ns == sync_namespace and not config.sync_in_first_namespace:
            continue

        pod_result = cluster.run(
            "get", "pod", "-n", ns,
            f"-lvm.kubevirt.io/name={vm_name}",
            "-ojsonpath={.items[0].metadata.name}",
            dry_run_skip=False, filter_output=False,
        )
        podname = pod_result.stdout.strip() if pod_result.returncode == 0 else ""
        if not podname:
            _LOG.debug("No virt-launcher pod found for VM %s/%s", ns, vm_name)
            continue

        xml_path = f"/var/run/libvirt/qemu/run/{ns}_{vm_name}.xml"
        result = cluster.run(
            "exec", "-n", ns, podname,
            "--", "/bin/sh", "-c", f"cat '{xml_path}'",
            dry_run_skip=False, filter_output=False,
        )
        if result.returncode == 0 and result.stdout:
            vmxml_dir = os.path.join(config.artifactdir, "VMXML")
            os.makedirs(vmxml_dir, exist_ok=True)
            dest = os.path.join(vmxml_dir, f"{ns}_{vm_name}.xml")
            try:
                with open(dest, "w") as f:
                    f.write(result.stdout)
            except OSError:
                pass
        else:
            _LOG.warning("Unable to retrieve qemu XML file for %s:%s", ns, vm_name)


def _retrieve_cloud_init_logs(
    cluster: ClusterInterface,
    config: ClusterbusterConfig,
    sync_namespace: str,
) -> None:
    """Retrieve cloud-init logs from VMs via virtctl scp.

    Matches bash: requires both virtctl and ``vm_ssh_keyfile``;
    uses ``root@`` for the SCP source; iterates ``vm`` resources.
    """
    import shutil
    virtctl = shutil.which("virtctl")
    if not virtctl:
        _LOG.debug("virtctl not found, skipping cloud-init log retrieval")
        return
    if not config.vm_ssh_keyfile:
        _LOG.debug("No vm_ssh_keyfile, skipping cloud-init log retrieval")
        return

    data = cluster.get_json(
        "vm", "-A",
        f"-l{config.basename}-id={config.uuid}",
    )
    for item in data.get("items", []):
        metadata = item.get("metadata", {})
        ns = metadata.get("namespace", "")
        name = metadata.get("name", "")
        if ns == sync_namespace and not config.sync_in_first_namespace:
            continue

        vmlog_dir = os.path.join(config.artifactdir, "VMLogs")
        os.makedirs(vmlog_dir, exist_ok=True)
        tmp_dest = os.path.join(vmlog_dir, f"{ns}.{name}")
        final_dest = os.path.join(vmlog_dir, f"{ns}:{name}")
        cmd = [
            virtctl, "scp",
            "-i", config.vm_ssh_keyfile,
            "-t", "-oBatchMode=yes",
            "-t", "-oStrictHostKeyChecking=no",
            "-t", "-oGlobalKnownHostsFile=/dev/null",
            "-t", "-oUserKnownHostsFile=/dev/null",
            "-t", "-oLogLevel ERROR",
            f"root@{name}.{ns}:/var/log/cloud-init-output.log",
            tmp_dest,
        ]

        import subprocess as sp
        result = sp.run(cmd, capture_output=True, text=True, check=False,
                        stdin=sp.DEVNULL)
        if result.returncode == 0 and os.path.isfile(tmp_dest):
            try:
                os.rename(tmp_dest, final_dest)
            except OSError:
                pass
