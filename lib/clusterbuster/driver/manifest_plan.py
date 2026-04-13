# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
"""Shared manifest-plan builder.

Builds the complete list of Kubernetes manifest dicts for a
clusterbuster run.  Used by both the dry-run printer (``cli.py``) and
the live orchestrator (``orchestrator.py``), eliminating the duplicate
manifest-building code paths that previously diverged.
"""

from __future__ import annotations

import base64
import time
from typing import Any

from .config import ClusterbusterConfig
from .manifests import (
    ManifestBuilder,
    SYSTEM_CONFIGMAP_MOUNT_DIR,
    USER_CONFIGMAP_MOUNT_DIR,
)


def _is_client_server_workload(wl: Any) -> bool:
    """True if the workload uses the server/client arglist split.

    True when the workload defines server_arglist/client_arglist but does
    not override the base arglist (or has no arglist at all).
    """
    if not (hasattr(wl, "server_arglist") and hasattr(wl, "client_arglist")):
        return False
    cls_arglist = getattr(type(wl), "arglist", None)
    if cls_arglist is None:
        return True
    from .workload_registry import WorkloadBase
    base_arglist = getattr(cls_arglist, "__func__", cls_arglist)
    return base_arglist is WorkloadBase.arglist


def _namespace_policy(cfg: ClusterbusterConfig, wl: Any) -> str:
    """Return the PodSecurity policy for workload namespaces."""
    if cfg.deployment_type == "vm":
        return "privileged"
    if cfg.create_pods_privileged:
        return "privileged"
    if wl.requires_drop_cache():
        return "privileged"
    if wl.sysctls(cfg):
        return "privileged"
    wl_policy = wl.namespace_policy()
    if wl_policy:
        return wl_policy
    return "restricted"


def build_manifest_plan(
    cfg: ClusterbusterConfig,
    namespaces: list[str],
    *,
    basetime: float = 0.0,
    first_deployment: int = -1,
) -> list[dict[str, Any]]:
    """Build every manifest dict that a clusterbuster run would create.

    Parameters
    ----------
    cfg : ClusterbusterConfig
        Fully built configuration.
    namespaces : list[str]
        Workload namespace names (e.g. ``["clusterbuster-0"]``).
    basetime : float
        Base timestamp for worker arglists.  ``0.0`` for dry-run,
        ``time.time()`` for live runs.
    first_deployment : int
        First deployment index.  Defaults to ``cfg.first_deployment``.

    Returns
    -------
    list[dict[str, Any]]
        Ordered list of manifest dicts, in lifecycle order:
        namespaces -> configmaps -> secrets -> sync objects -> deployments.
    """
    from .workload_registry import get_workload

    if first_deployment < 0:
        first_deployment = cfg.first_deployment

    mb = ManifestBuilder(cfg)
    wl = get_workload(cfg.resolved_workload)
    is_cs = _is_client_server_workload(wl)

    vm_builder = None
    if cfg.deployment_type == "vm":
        from .vm import VmManifestBuilder
        vm_builder = VmManifestBuilder(cfg, mb)

    if cfg.sync_in_first_namespace:
        sync_ns = namespaces[0] if namespaces else f"{cfg.basename}-0"
    else:
        sync_ns = f"{cfg.basename}-sync"

    sync_svc_name = f"{cfg.basename}-sync-0"
    sync_host = f"{sync_svc_name}.{sync_ns}.svc.cluster.local"
    policy = _namespace_policy(cfg, wl)

    manifests: list[dict[str, Any]] = []

    # -- 1. Namespaces -------------------------------------------------------
    for ns in namespaces:
        manifests.append(mb.namespace(ns, policy=policy))

    if cfg.sync_start and sync_ns not in namespaces:
        manifests.append(mb.namespace(sync_ns, policy=policy))

    # -- 2. ConfigMaps (system + user per workload namespace) ----------------
    for ns in namespaces:
        manifests.append(mb.system_configmap(ns))
        manifests.append(mb.user_configmap(ns))

    # -- 3. Secrets ----------------------------------------------------------
    if cfg.secrets > 0:
        for ns in namespaces:
            for dep in range(first_deployment,
                             cfg.deps_per_namespace + first_deployment):
                for k in range(cfg.secrets):
                    name = f"secret-{ns}-{dep}-{k}"
                    secret_data = {
                        "key1": base64.b64encode(
                            f"{ns}X{dep}Y{k}Z1\n".encode()
                        ).decode(),
                        "key2": base64.b64encode(
                            f"{ns}X{dep}Y{k}Z2\n".encode()
                        ).decode(),
                    }
                    manifests.append(mb.secret(name, ns, secret_data))

    # -- 4. Sync objects ------------------------------------------------------
    if cfg.sync_start:
        if sync_ns not in namespaces:
            manifests.append(mb.system_configmap(sync_ns))

        svc_ports: list[dict[str, Any]] = []
        for port in (cfg.sync_port, cfg.sync_ns_port):
            for proto in ("TCP", "UDP"):
                svc_ports.append({
                    "name": f"svc-{sync_svc_name}-{port}-{proto.lower()}",
                    "protocol": proto,
                    "port": port,
                    "targetPort": port,
                })
        manifests.append(mb.service(
            sync_svc_name, sync_ns,
            ports=svc_ports,
            selector={f"{cfg.basename}-sync-sync": "true"},
        ))

        num_ns = len(namespaces)
        deps = cfg.deps_per_namespace
        if is_cs:
            client_conns = cfg.containers_per_pod * cfg.replicas * deps
            server_conns = deps
            expected = client_conns * num_ns
            initial = (client_conns + server_conns) * num_ns
        else:
            expected = (cfg.containers_per_pod * cfg.replicas
                        * cfg.processes_per_pod * deps * num_ns)
            extra_dc = 1 if wl.requires_drop_cache() else 0
            initial = ((cfg.containers_per_pod + extra_dc)
                       * cfg.replicas * deps * num_ns)
        manifests.append(mb.sync_pod(
            sync_ns,
            expected_clients=expected,
            initial_expected_clients=initial,
        ))

        ext_ports = [
            {"port": p, "name": f"svc-{sync_svc_name}-{p}"}
            for p in (cfg.sync_port, cfg.sync_ns_port)
        ]
        for ns in namespaces:
            if ns == sync_ns:
                continue
            manifests.append(mb.external_service(
                sync_svc_name, ns, sync_host, ports=ext_ports,
            ))

    # -- 5. Deployments / ReplicaSets / Pods / VMs ---------------------------
    needs_dc = wl.requires_drop_cache()
    env = {
        "VERBOSE": str(int(cfg.verbose)),
        "SYSTEM_PODFILE_DIR": SYSTEM_CONFIGMAP_MOUNT_DIR,
        "USER_PODFILE_DIR": USER_CONFIGMAP_MOUNT_DIR,
        "PYTHONPATH": SYSTEM_CONFIGMAP_MOUNT_DIR,
    }
    if hasattr(wl, "generate_environment"):
        wl_env = wl.generate_environment()
        if wl_env:
            env.update(wl_env)

    for ns in namespaces:
        for dep in range(cfg.deps_per_namespace):
            dep_idx = dep + first_deployment
            dep_name = f"{ns}-{cfg.resolved_workload}-{dep_idx}"

            if needs_dc:
                for replica in range(1, cfg.replicas + 1):
                    dc_name = f"{dep_name}-{replica}-dc"
                    worker_replica = f"{dep_name}-{replica}"
                    manifests.append(mb.drop_cache_pod(
                        dc_name, ns, sync_host=sync_host,
                        worker_replica_label=worker_replica,
                        basetime=basetime,
                    ))

            if is_cs:
                server_if = cfg.net_interfaces.get("server", "")
                ports = wl.listen_ports(cfg) if not server_if else []
                if ports:
                    svc_name = (
                        f"{ns}-{cfg.resolved_workload}-server-{dep_idx}-1"
                    )
                    wl_svc_ports: list[dict[str, Any]] = []
                    for p in ports:
                        wl_svc_ports.append({
                            "name": f"svc-{svc_name}-{p}-tcp",
                            "protocol": "TCP", "port": p, "targetPort": p,
                        })
                        wl_svc_ports.append({
                            "name": f"svc-{svc_name}-{p}-udp",
                            "protocol": "UDP", "port": p, "targetPort": p,
                        })
                    manifests.append(mb.service(
                        svc_name, ns, wl_svc_ports,
                        selector={"replica": svc_name},
                    ))

            if cfg.deployment_type == "vm" and vm_builder:
                _build_vm_manifests(
                    manifests, cfg, mb, vm_builder, wl, is_cs,
                    ns, dep_idx, dep_name, env, sync_host,
                    basetime,
                )
                continue

            wl_sysctls = wl.sysctls(cfg) or None
            if is_cs:
                manifests.extend(
                    _build_client_server_deployments(
                        cfg, mb, wl, ns, dep_idx, dep_name,
                        env=env, sync_host=sync_host,
                        basetime=basetime, sysctls=wl_sysctls,
                    )
                )
            else:
                containers = _build_containers(
                    cfg, mb, wl.arglist, ns, dep_idx,
                    env=env, sync_host=sync_host,
                    basetime=basetime, sysctls=wl_sysctls,
                )
                manifests.extend(
                    _build_single_role_deployment(
                        cfg, mb, dep_name, ns, containers,
                        dep_idx=dep_idx,
                        arglist_fn=wl.arglist, env=env,
                        sync_host=sync_host, basetime=basetime,
                        sysctls=wl_sysctls,
                    )
                )

    return manifests


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_vm_manifests(
    manifests: list[dict[str, Any]],
    cfg: ClusterbusterConfig,
    mb: ManifestBuilder,
    vm_builder: Any,
    wl: Any,
    is_cs: bool,
    ns: str,
    dep_idx: int,
    dep_name: str,
    env: dict[str, str],
    sync_host: str,
    basetime: float,
) -> None:
    """Build VirtualMachine manifests for one deployment."""
    from .workload_registry import ArglistContext

    if is_cs:
        vm_roles: list[tuple[str, Any, int]] = [
            ("server", wl.server_arglist, 1),
            ("client", wl.client_arglist, cfg.replicas),
        ]
    else:
        vm_roles = [("", wl.arglist, cfg.replicas)]

    for vm_role, vm_arglist_fn, role_replicas in vm_roles:
        if vm_role:
            vm_base = f"{ns}-{cfg.resolved_workload}-{vm_role}-{dep_idx}"
        else:
            vm_base = dep_name
        lbl_wl = (f"{cfg.resolved_workload}-{vm_role}"
                  if vm_role else "")
        for replica in range(1, role_replicas + 1):
            vm_name = f"{vm_base}-{replica}"
            arg_ctx = ArglistContext(
                mountdir=SYSTEM_CONFIGMAP_MOUNT_DIR + "/",
                namespace=ns,
                instance=dep_idx,
                secret_count=cfg.secrets,
                replicas=cfg.replicas,
                containers_per_pod=cfg.containers_per_pod,
                container_index=0,
                config=cfg,
                sync_host=sync_host,
                basetime=basetime,
                crtime=time.time() if basetime else 0.0,
                drop_cache_host="",
            )
            vm_cmd = vm_arglist_fn(arg_ctx)
            manifests.append(vm_builder.virtual_machine(
                vm_name, ns,
                workload_args=vm_cmd,
                workload_env=env,
                workload_packages=wl.vm_required_packages(),
                workload_setup_commands=wl.vm_setup_commands(),
                workload_sysctls=wl.sysctls(cfg),
                class_name=vm_role,
                label_namespace=ns,
                label_instance=str(dep_idx),
                label_replica=str(replica),
                label_workload=lbl_wl,
            ))


def _build_client_server_deployments(
    cfg: ClusterbusterConfig,
    mb: ManifestBuilder,
    wl: Any,
    namespace: str,
    dep_idx: int,
    dep_name: str,
    *,
    env: dict[str, str],
    sync_host: str,
    basetime: float,
    sysctls: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Build both server and client deployments for client/server workloads."""
    manifests: list[dict[str, Any]] = []
    server_name = f"{namespace}-{cfg.resolved_workload}-server-{dep_idx}"
    for role, arglist_fn in (("server", wl.server_arglist),
                             ("client", wl.client_arglist)):
        role_name = f"{namespace}-{cfg.resolved_workload}-{role}-{dep_idx}"
        lbl_wl = f"{cfg.resolved_workload}-{role}"
        containers = _build_containers(
            cfg, mb, arglist_fn, namespace, dep_idx,
            env=env, sync_host=sync_host,
            basetime=basetime, sysctls=sysctls,
        )
        client_affinity_label = server_name if role == "client" else ""
        manifests.extend(
            _build_single_role_deployment(
                cfg, mb, role_name, namespace, containers,
                class_name=role, dep_idx=dep_idx,
                label_workload=lbl_wl,
                force_pod=(role == "server"),
                client_affinity_label=client_affinity_label,
                arglist_fn=arglist_fn, env=env,
                sync_host=sync_host, basetime=basetime,
                sysctls=sysctls,
            )
        )
    return manifests


def _build_containers(
    cfg: ClusterbusterConfig,
    mb: ManifestBuilder,
    arglist_fn: Any,
    namespace: str,
    dep_idx: int,
    *,
    env: dict[str, str],
    sync_host: str,
    basetime: float,
    sysctls: dict[str, str] | None = None,
    replica: str = "",
) -> list[dict[str, Any]]:
    """Build container specs from workload arglist."""
    from .workload_registry import ArglistContext

    containers = []
    sc = mb.security_context(sysctls=sysctls) if sysctls else None
    for ci in range(cfg.containers_per_pod):
        arg_ctx = ArglistContext(
            mountdir=SYSTEM_CONFIGMAP_MOUNT_DIR + "/",
            namespace=namespace,
            instance=dep_idx,
            secret_count=cfg.secrets,
            replicas=cfg.replicas,
            containers_per_pod=cfg.containers_per_pod,
            container_index=ci,
            config=cfg,
            sync_host=sync_host,
            basetime=basetime,
            crtime=time.time() if basetime else 0.0,
            drop_cache_host="",
        )
        cmd = arglist_fn(arg_ctx)
        container = mb.container(
            f"c{ci}", command=cmd or None, env=env,
            volume_mounts=mb.volume_mounts(
                namespace=namespace, instance=str(dep_idx),
                replica=replica,
            ),
            security_context=sc,
        )
        containers.append(container)
    return containers


def _build_single_role_deployment(
    cfg: ClusterbusterConfig,
    mb: ManifestBuilder,
    dep_name: str,
    ns: str,
    containers: list[dict[str, Any]],
    *,
    class_name: str = "",
    dep_idx: int = 0,
    label_workload: str = "",
    force_pod: bool = False,
    client_affinity_label: str = "",
    arglist_fn: Any = None,
    env: dict[str, str] | None = None,
    sync_host: str = "",
    basetime: float = 0.0,
    sysctls: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Build deployment/replicaset/pod manifests.

    Bash creates one object per replica (each ReplicaSet/Deployment has
    ``replicas: 1``), with per-replica names and labels.  We replicate
    that model so that drop-cache pod affinity can target each individual
    worker replica by its unique ``replica`` label.
    """
    inst = str(dep_idx)
    dtype = "pod" if force_pod else cfg.deployment_type
    manifests: list[dict[str, Any]] = []
    replicas = 1 if force_pod else cfg.replicas
    for replica in range(1, replicas + 1):
        rep = str(replica)
        obj_name = f"{dep_name}-{replica}"
        if arglist_fn is not None and env is not None:
            rep_containers = _build_containers(
                cfg, mb, arglist_fn, ns, dep_idx,
                env=env, sync_host=sync_host,
                basetime=basetime, sysctls=sysctls,
                replica=rep,
            )
        else:
            rep_containers = containers
        vols = mb.volumes(namespace=ns, instance=inst, replica=rep)
        if dtype == "replicaset":
            manifests.append(mb.replicaset(
                obj_name, ns, 1, rep_containers,
                volumes=vols, class_name=class_name,
                label_namespace=ns, label_instance=inst,
                label_replica=rep,
                label_workload=label_workload,
                client_affinity_label=client_affinity_label,
            ))
        elif dtype == "deployment":
            manifests.append(mb.deployment(
                obj_name, ns, 1, rep_containers,
                volumes=vols, class_name=class_name,
                label_namespace=ns, label_instance=inst,
                label_replica=rep,
                label_workload=label_workload,
                client_affinity_label=client_affinity_label,
            ))
        else:
            manifests.append(mb.pod(
                obj_name, ns, rep_containers,
                volumes=vols, class_name=class_name,
                label_namespace=ns, label_instance=inst,
                label_replica=rep,
                label_workload=label_workload,
                client_affinity_label=client_affinity_label,
            ))
    return manifests
