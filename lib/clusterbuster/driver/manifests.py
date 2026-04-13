# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""Kubernetes manifest generation: Python dicts -> YAML."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import yaml

from .cluster import ClusterError, ClusterInterface
from .config import ClusterbusterConfig, kv_get

_LOG = logging.getLogger(__name__)

SYSTEM_CONFIGMAP_MOUNT_DIR = "/var/lib/clusterbuster"
USER_CONFIGMAP_MOUNT_DIR = "/etc/clusterbuster"


class ManifestBuilder:
    """Builds Kubernetes manifest dicts from a validated config."""

    def __init__(self, config: ClusterbusterConfig):
        self._cfg = config

    # -- Naming helper (matches bash mkpodname) --------------------------------

    def mkpodname(self, *parts: str) -> str:
        """Join *parts* with ``-``, prepend ``pod_prefix``, replace ``_`` with ``-``.

        Matches bash::

            function mkpodname() {
                local podname
                podname="${pod_prefix}$(IFS=-; echo "$*")"
                echo "${podname//_/-}"
            }
        """
        raw = self._cfg.pod_prefix + "-".join(str(p) for p in parts if str(p))
        return raw.replace("_", "-")

    # -- Labels and metadata -------------------------------------------------

    def standard_labels(
        self,
        objtype: str,
        *,
        workload: str = "",
        namespace: str = "",
        instance: str = "",
        replica: str = "",
        suffix: str = "",
        class_name: str = "",
        logger: bool = False,
        worker_label: bool = True,
    ) -> dict[str, str]:
        """Labels matching bash ``standard_labels_yaml``.

        Produces all 12+ labels that the bash version emits, including
        cleanup labels, per-type selectors, monitor labels, and the
        ``clusterbusterbase`` cross-basename labels.
        """
        cfg = self._cfg
        bn = cfg.basename
        labels: dict[str, str] = {
            f"{bn}-xuuid": cfg.xuuid,
            f"{bn}-uuid": cfg.uuid,
            f"{bn}-id": cfg.uuid,
            bn: "true",
            f"{bn}base": "true",
        }
        if objtype:
            labels[f"{bn}-{objtype}"] = cfg.uuid
            labels[f"{bn}-x-{objtype}"] = cfg.xuuid
        if logger:
            labels[f"{bn}-logger"] = "true"
        labels[f"{bn}-objtype"] = objtype

        if objtype in ("worker", "sync"):
            labels[f"{bn}-monitor"] = cfg.xuuid

        if bn != "clusterbuster":
            labels["clusterbusterbase"] = "true"
            labels["clusterbuster-objtype"] = objtype

        if worker_label:
            labels[f"{bn}-workload"] = "true"
            if bn != "clusterbuster":
                labels["clusterbuster-workload"] = "true"

        if workload:
            labels[f"{bn}-{workload}"] = cfg.uuid

            def _join(*parts: str) -> str:
                return "-".join(p for p in parts if p)

            name_parts = _join(namespace, workload, instance)
            app_parts = _join(namespace, workload)
            inst_parts = name_parts
            if suffix:
                app_parts = _join(app_parts, suffix)
                name_parts = _join(name_parts, suffix)
            labels["name"] = name_parts
            labels["app"] = app_parts
            labels["k8s-app"] = app_parts
            labels["instance"] = inst_parts
            rep_parts = _join(inst_parts, replica)
            if suffix:
                rep_parts = _join(rep_parts, suffix)
            labels["replica"] = rep_parts
            labels[workload] = "true"

        obj_class = class_name or objtype
        for lbl in cfg.pod_labels:
            scope, rest, _ = self._parse_scoped_label(lbl, obj_class)
            if scope is None:
                continue
            if "=" in rest:
                k, _, v = rest.partition("=")
                if k:
                    labels[k] = v
            elif rest:
                labels[rest] = "true"

        return labels

    def _parse_scoped_label(
        self, label: str, class_name: str
    ) -> tuple[str | None, str, str]:
        """Parse ``:class1,class2:key=value`` scoped labels.

        Returns ``(scope_match_or_None, key=value_part, full_label)``.
        If the label has no scope prefix, it applies to all classes.
        """
        if label.startswith(":"):
            parts = label.split(":", 2)
            if len(parts) >= 3:
                scope = parts[1]
                rest = parts[2]
                classes = [c.strip() for c in scope.split(",")]
                if class_name in classes or not scope:
                    return (scope, rest, label)
                return (None, rest, label)
        return ("", label, label)

    def annotations(self, class_name: str = "") -> dict[str, str]:
        """Pod annotations, filtered by class scope.

        Bash stores annotations in ``key: value`` (YAML colon-space) format
        and emits them as raw YAML.  We split on ``": "`` as the primary
        separator, falling back to ``"= "`` / ``"="`` for compatibility.
        """
        result: dict[str, str] = {}
        for ann in self._cfg.pod_annotations:
            scope, rest, _ = self._parse_scoped_label(ann, class_name)
            if scope is None:
                continue
            clean = rest if ann.startswith(":") else ann
            if ": " in clean:
                k, _, v = clean.partition(": ")
                result[k.strip()] = v
            elif "=" in clean:
                k, _, v = clean.partition("=")
                result[k.strip()] = v

        net_iface = self._cfg.net_interfaces.get(
            class_name, self._cfg.net_interfaces.get("default", "")
        )
        if net_iface:
            result["k8s.v1.cni.cncf.io/networks"] = net_iface

        if self._cfg.virtiofsd_args and self._cfg.runtime_class.startswith("kata"):
            import json
            result["io.katacontainers.config.hypervisor.virtio_fs_extra_args"] = (
                json.dumps(self._cfg.virtiofsd_args, separators=(",", ":"))
            )

        return result

    # -- Affinity ------------------------------------------------------------

    def affinity(
        self,
        mode: int,
        *,
        label_key: str = "",
        label_value: str = "",
        topology: str = "kubernetes.io/hostname",
    ) -> dict[str, Any] | None:
        """Pod affinity/anti-affinity spec. Returns None if mode==0."""
        if mode == 0:
            return None
        cfg = self._cfg
        key = label_key or f"{cfg.basename}-uuid"
        value = label_value or cfg.uuid

        term = {
            "labelSelector": {
                "matchExpressions": [
                    {"key": key, "operator": "In", "values": [value]}
                ]
            },
            "topologyKey": topology,
        }

        if mode == 1:
            return {
                "podAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": [term]
                }
            }
        return {
            "podAntiAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": [term]
            }
        }

    # -- Tolerations ---------------------------------------------------------

    def tolerations(self) -> list[dict[str, str]] | None:
        if not self._cfg.tolerations:
            return None
        result: list[dict[str, str]] = []
        for tol in self._cfg.tolerations:
            parts = tol.split(":")
            entry: dict[str, str] = {}
            if len(parts) >= 1 and parts[0]:
                entry["key"] = parts[0]
            if len(parts) >= 2 and parts[1]:
                entry["operator"] = parts[1]
            else:
                entry["operator"] = "Exists"
            if len(parts) >= 3 and parts[2]:
                entry["effect"] = parts[2]
            result.append(entry)
        return result

    # -- Resources -----------------------------------------------------------

    def resources(
        self,
        requests: list[str] | None = None,
        limits: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Container resource requests/limits from ``name=value`` tokens."""
        req = requests if requests is not None else self._cfg.resource_requests
        lim = limits if limits is not None else self._cfg.resource_limits
        if not req and not lim:
            return None
        result: dict[str, dict[str, str]] = {}
        if req:
            result["requests"] = self._parse_resource_tokens(req)
        if lim:
            result["limits"] = self._parse_resource_tokens(lim)
        return result

    @staticmethod
    def _parse_resource_tokens(tokens: list[str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for tok in tokens:
            if "=" in tok:
                k, _, v = tok.partition("=")
                result[k] = v
        return result

    # -- Security context ----------------------------------------------------

    def security_context(
        self,
        *,
        privileged: bool | None = None,
        sysctls: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        priv = privileged if privileged is not None else self._cfg.create_pods_privileged
        if priv:
            sc: dict[str, Any] = {
                "privileged": True,
                "allowPrivilegeEscalation": True,
                "runAsUser": 0,
            }
        else:
            sc = {
                "allowPrivilegeEscalation": False,
                "runAsNonRoot": True,
                "capabilities": {"drop": ["ALL"]},
                "seccompProfile": {"type": "RuntimeDefault"},
            }
        if sysctls:
            sc["sysctls"] = [
                {"name": k, "value": v} for k, v in sorted(sysctls.items())
            ]
        return sc

    # -- Liveness probe ------------------------------------------------------

    def liveness_probe(self) -> dict[str, Any] | None:
        cfg = self._cfg
        if cfg.liveness_probe_frequency <= 0:
            return None
        return {
            "exec": {"command": ["/bin/sleep", str(cfg.liveness_probe_sleep_time)]},
            "periodSeconds": cfg.liveness_probe_frequency,
            "initialDelaySeconds": 10,
        }

    # -- Volume name expansion ------------------------------------------------

    @staticmethod
    def expand_volume_name(
        name: str,
        *,
        namespace: str = "",
        instance: str = "",
        replica: str = "",
    ) -> str:
        """Expand ``%N``, ``%i``, ``%r`` in volume/PVC names (matching bash)."""
        name = name.replace("%N", namespace)
        name = name.replace("%i", instance)
        name = name.replace("%r", replica)
        return name

    # -- Volume mounts -------------------------------------------------------

    def volume_mounts(
        self,
        *,
        include_secrets: bool = True,
        include_configmaps: bool = True,
        include_workdir: bool = True,
        include_extra: bool = True,
        namespace: str = "",
        instance: str = "",
        replica: str = "",
    ) -> list[dict[str, Any]]:
        cfg = self._cfg
        mounts: list[dict[str, Any]] = []

        if include_secrets and cfg.secrets > 0 and namespace and instance:
            for k in range(cfg.secrets):
                sec_name = f"secret-{namespace}-{instance}-{k}"
                mounts.append({
                    "name": sec_name,
                    "mountPath": f"/etc/{sec_name}",
                    "readOnly": True,
                })

        if include_configmaps:
            mounts.append({
                "name": "user-configmap",
                "mountPath": USER_CONFIGMAP_MOUNT_DIR,
                "readOnly": True,
            })

        mounts.append({
            "name": "system-configmap",
            "mountPath": SYSTEM_CONFIGMAP_MOUNT_DIR,
            "readOnly": True,
        })

        if include_workdir:
            mounts.append({
                "name": "workdir",
                "mountPath": cfg.common_workdir,
            })

        if include_extra:
            emptydir_id = 0
            for vol_spec in cfg.volumes:
                parts = vol_spec.split(":")
                name = parts[0]
                vtype = parts[1].lower() if len(parts) > 1 else "emptydir"
                if not name and vtype in ("emptydir", "emptydisk"):
                    name = f"cbemptydir{emptydir_id}"
                    emptydir_id += 1
                else:
                    name = self.expand_volume_name(
                        name, namespace=namespace,
                        instance=instance, replica=replica,
                    )
                mount_path = parts[2] if len(parts) > 2 else f"/mnt/{name}"
                mounts.append({"name": name, "mountPath": mount_path})

        return mounts

    # -- Volumes -------------------------------------------------------------

    def volumes(
        self,
        *,
        namespace: str = "",
        instance: str = "",
        replica: str = "",
        include_secrets: bool = True,
        include_configmaps: bool = True,
        include_workdir: bool = True,
        include_extra: bool = True,
    ) -> list[dict[str, Any]]:
        cfg = self._cfg
        vols: list[dict[str, Any]] = []

        if include_secrets and cfg.secrets > 0 and namespace and instance:
            for k in range(cfg.secrets):
                sec_name = f"secret-{namespace}-{instance}-{k}"
                vols.append({
                    "name": sec_name,
                    "secret": {"secretName": sec_name},
                })

        if include_configmaps:
            vols.append({
                "name": "user-configmap",
                "configMap": {"name": f"{cfg.basename}-user-configmap"},
            })

        vols.append({
            "name": "system-configmap",
            "configMap": {"name": f"{cfg.basename}-system-configmap"},
        })

        if include_workdir:
            vols.append({"name": "workdir", "emptyDir": {}})

        if include_extra:
            emptydir_id = 0
            for vol_spec in cfg.volumes:
                parts = vol_spec.split(":")
                name = parts[0]
                vtype = parts[1].lower() if len(parts) > 1 else "emptydir"
                kv_args = parts[3:] if len(parts) > 3 else []

                if vtype in ("emptydir", "emptydisk"):
                    if not name:
                        name = f"cbemptydir{emptydir_id}"
                        emptydir_id += 1
                    vols.append({"name": name, "emptyDir": {}})
                elif vtype in ("pvc", "persistentvolumeclaim"):
                    claim = kv_get(kv_args, "claimName") or name
                    claim = self.expand_volume_name(
                        claim, namespace=namespace,
                        instance=instance, replica=replica,
                    )
                    pvc: dict[str, Any] = {"claimName": claim}
                    ro = kv_get(kv_args, "readOnly")
                    if ro and ro.lower() in ("1", "true", "yes"):
                        pvc["readOnly"] = True
                    vol_entry: dict[str, Any] = {
                        "name": name,
                        "persistentVolumeClaim": pvc,
                    }
                    vols.append(vol_entry)

        return vols

    # -- Container spec ------------------------------------------------------

    def container(
        self,
        name: str,
        *,
        image: str = "",
        command: list[str] | None = None,
        env: dict[str, str] | None = None,
        ports: list[dict[str, Any]] | None = None,
        volume_mounts: list[dict[str, Any]] | None = None,
        security_context: dict[str, Any] | None = None,
        class_name: str = "",
    ) -> dict[str, Any]:
        cfg = self._cfg
        c: dict[str, Any] = {
            "name": name,
            "image": image or cfg.container_image,
        }
        if cfg.image_pull_policy:
            c["imagePullPolicy"] = cfg.image_pull_policy
        if command:
            c["command"] = command
        if ports:
            c["ports"] = ports
        if env:
            c["env"] = [{"name": k, "value": str(v)} for k, v in env.items()]

        res = self.resources()
        if res:
            c["resources"] = res

        sc = security_context if security_context is not None else self.security_context()
        c["securityContext"] = sc

        if volume_mounts is not None:
            c["volumeMounts"] = volume_mounts
        else:
            c["volumeMounts"] = self.volume_mounts()

        probe = self.liveness_probe()
        if probe:
            c["livenessProbe"] = probe

        return c

    # -- Pod spec ------------------------------------------------------------

    def pod_spec(
        self,
        containers: list[dict[str, Any]],
        *,
        volumes: list[dict[str, Any]] | None = None,
        affinity_mode: int | None = None,
        class_name: str = "",
        pin_node: str = "",
        tolerations_list: list[dict[str, str]] | None = None,
        sysctls: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        cfg = self._cfg
        spec: dict[str, Any] = {
            "terminationGracePeriodSeconds": 1,
            "containers": containers,
        }
        if sysctls:
            spec["securityContext"] = {
                "sysctls": [
                    {"name": k, "value": str(v)}
                    for k, v in sorted(sysctls.items())
                ],
            }

        if cfg.scheduler:
            spec["schedulerName"] = cfg.scheduler

        tols = tolerations_list if tolerations_list is not None else self.tolerations()
        if tols:
            spec["tolerations"] = tols

        if cfg.runtime_class:
            rclass = cfg.runtime_classes.get(class_name, cfg.runtime_class)
            if rclass and rclass != "vm":
                spec["runtimeClassName"] = rclass

        aff_mode = affinity_mode if affinity_mode is not None else cfg.affinity
        node = pin_node or cfg.pin_nodes.get(class_name, cfg.pin_nodes.get("default", ""))

        if node:
            spec["nodeSelector"] = {"kubernetes.io/hostname": node}
        elif cfg.node_selector:
            spec["nodeSelector"] = {cfg.node_selector: ""}

        aff = self.affinity(aff_mode)
        if aff:
            if "affinity" not in spec:
                spec["affinity"] = {}
            spec["affinity"].update(aff)

        vols = volumes if volumes is not None else self.volumes()
        if vols:
            spec["volumes"] = vols

        return spec

    # -- Top-level manifest builders -----------------------------------------

    def namespace(self, name: str, *, policy: str = "restricted") -> dict[str, Any]:
        labels = self.standard_labels("namespace")
        labels["pod-security.kubernetes.io/enforce"] = policy
        labels["pod-security.kubernetes.io/audit"] = policy
        labels["pod-security.kubernetes.io/warn"] = policy
        return {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": name, "labels": labels},
        }

    def secret(
        self, name: str, namespace: str, data: dict[str, str]
    ) -> dict[str, Any]:
        labels = self.standard_labels("secret")
        return {
            "apiVersion": "v1",
            "kind": "Secret",
            "type": "Opaque",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": labels,
            },
            "data": data,
        }

    def service(
        self,
        name: str,
        namespace: str,
        ports: list[dict[str, Any]],
        *,
        selector: dict[str, str] | None = None,
        headless: bool | None = None,
    ) -> dict[str, Any]:
        cfg = self._cfg
        labels = self.standard_labels("service")
        svc: dict[str, Any] = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": labels,
            },
            "spec": {
                "ports": ports,
            },
        }
        if selector:
            svc["spec"]["selector"] = selector

        use_headless = headless if headless is not None else cfg.headless_services
        if use_headless:
            svc["spec"]["clusterIP"] = "None"

        return svc

    def external_service(
        self,
        name: str,
        namespace: str,
        external_name: str,
        ports: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        labels = self.standard_labels("service")
        spec: dict[str, Any] = {
            "type": "ExternalName",
            "externalName": external_name,
        }
        if ports:
            spec["ports"] = ports
        return {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": labels,
            },
            "spec": spec,
        }

    def configmap(
        self, name: str, namespace: str, data: dict[str, str],
    ) -> dict[str, Any]:
        """Generic ConfigMap manifest."""
        labels = self.standard_labels("configmap")
        return {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": labels,
            },
            "data": data,
        }

    _SHARED_POD_FILES = [
        "cb_util.py",
        "clusterbuster_pod_client.py",
        "sync.py",
        "drop_cache.py",
        "do-sync",
        "drop-cache",
    ]

    def system_configmap(self, namespace: str) -> dict[str, Any]:
        """System configmap containing shared infrastructure and workload scripts.

        Always includes the shared pod infrastructure files, then adds
        workload-specific files from ``wl.list_configmaps()``.
        """
        import os
        cfg = self._cfg
        data: dict[str, str] = {}

        try:
            from .workload_registry import get_workload
            wl = get_workload(cfg.resolved_workload)
            wl_files = wl.list_configmaps()
        except (ValueError, KeyError, ImportError):
            wl_files = []

        files_to_include = list(self._SHARED_POD_FILES)
        for f in wl_files:
            if f not in files_to_include:
                files_to_include.append(f)

        lib_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))),
            "pod_files",
        )
        for fname in files_to_include:
            path = os.path.join(lib_dir, fname)
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    data[fname] = f.read()
            else:
                _LOG.debug("Configmap file not found: %s", path)

        return self.configmap(
            f"{cfg.basename}-system-configmap", namespace, data,
        )

    def user_configmap(self, namespace: str) -> dict[str, Any]:
        """User configmap containing user-supplied and workload-generated files."""
        import os
        cfg = self._cfg
        data: dict[str, str] = {}

        for path in cfg.configmap_files:
            if not os.path.isfile(path):
                raise FileNotFoundError(
                    f"Can't find configmap file {path}"
                )
            fname = os.path.basename(path)
            with open(path) as f:
                data[fname] = f.read()

        try:
            from .workload_registry import get_workload
            wl = get_workload(cfg.resolved_workload)
            if hasattr(wl, "list_user_configmaps"):
                for path in wl.list_user_configmaps():
                    if os.path.isfile(path):
                        fname = os.path.basename(path)
                        with open(path) as f:
                            data[fname] = f.read()
        except (ValueError, KeyError, ImportError):
            pass

        return self.configmap(
            f"{cfg.basename}-user-configmap", namespace, data,
        )

    def pod(
        self,
        name: str,
        namespace: str,
        containers: list[dict[str, Any]],
        *,
        volumes: list[dict[str, Any]] | None = None,
        class_name: str = "",
        label_namespace: str = "",
        label_instance: str = "",
        label_replica: str = "",
        label_workload: str = "",
        sysctls: dict[str, str] | None = None,
        client_affinity_label: str = "",
    ) -> dict[str, Any]:
        cfg = self._cfg
        labels = self.standard_labels(
            "worker",
            workload=label_workload or cfg.requested_workload,
            namespace=label_namespace or namespace,
            instance=label_instance,
            replica=label_replica,
            class_name=class_name,
            logger=True,
        )
        ann = self.annotations(class_name)
        meta: dict[str, Any] = {
            "name": self.mkpodname(name),
            "namespace": namespace,
            "labels": labels,
        }
        if ann:
            meta["annotations"] = ann

        spec = self.pod_spec(containers, volumes=volumes, class_name=class_name,
                             sysctls=sysctls)
        spec["restartPolicy"] = "Never"

        if client_affinity_label and cfg.affinity:
            aff = self.affinity(
                cfg.affinity,
                label_key="instance",
                label_value=client_affinity_label,
            )
            if aff:
                spec["affinity"] = aff

        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": meta,
            "spec": spec,
        }

    def deployment(
        self,
        name: str,
        namespace: str,
        replicas: int,
        containers: list[dict[str, Any]],
        *,
        volumes: list[dict[str, Any]] | None = None,
        class_name: str = "",
        label_namespace: str = "",
        label_instance: str = "",
        label_replica: str = "",
        label_workload: str = "",
        client_affinity_label: str = "",
    ) -> dict[str, Any]:
        cfg = self._cfg
        labels = self.standard_labels(
            "worker",
            workload=label_workload or cfg.requested_workload,
            namespace=label_namespace or namespace,
            instance=label_instance,
            replica=label_replica,
            class_name=class_name,
            logger=True,
        )
        ann = self.annotations(class_name)

        template_meta: dict[str, Any] = {"labels": labels}
        if ann:
            template_meta["annotations"] = ann

        inst_label = labels.get("instance", "")

        pod_s = self.pod_spec(
            containers, volumes=volumes, class_name=class_name
        )
        if client_affinity_label and cfg.affinity:
            aff = self.affinity(
                cfg.affinity,
                label_key="instance",
                label_value=client_affinity_label,
            )
            if aff:
                pod_s["affinity"] = aff

        return {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": self.mkpodname(name),
                "namespace": namespace,
                "labels": labels,
            },
            "spec": {
                "replicas": replicas,
                "selector": {"matchLabels": {"instance": inst_label}},
                "template": {
                    "metadata": template_meta,
                    "spec": pod_s,
                },
            },
        }

    def replicaset(
        self,
        name: str,
        namespace: str,
        replicas: int,
        containers: list[dict[str, Any]],
        *,
        volumes: list[dict[str, Any]] | None = None,
        class_name: str = "",
        label_namespace: str = "",
        label_instance: str = "",
        label_replica: str = "",
        label_workload: str = "",
        client_affinity_label: str = "",
    ) -> dict[str, Any]:
        manifest = self.deployment(
            name, namespace, replicas, containers,
            volumes=volumes, class_name=class_name,
            label_namespace=label_namespace,
            label_instance=label_instance,
            label_replica=label_replica,
            label_workload=label_workload,
            client_affinity_label=client_affinity_label,
        )
        manifest["kind"] = "ReplicaSet"
        return manifest

    def drop_cache_pod(
        self,
        name: str,
        namespace: str,
        *,
        sync_host: str = "",
        worker_replica_label: str = "",
        basetime: float = 0.0,
    ) -> dict[str, Any]:
        """Build a privileged drop-cache pod.

        Matches bash ``create_drop_cache_deployment`` /
        ``create_container_drop_cache``.  One pod per deployment per
        replica for workloads that ``requires_drop_cache()``.
        """
        cfg = self._cfg
        crtime = time.time() if basetime else 0.0
        command = [
            "python3",
            f"{SYSTEM_CONFIGMAP_MOUNT_DIR}/drop_cache.py",
            f"--cb-sync-nonce={cfg.sync_nonce}",
            f"--cb-namespace={namespace}",
            "--cb-container=c0",
            f"--cb-basetime={basetime}",
            f"--cb-baseoffset={cfg.baseoffset}",
            f"--cb-crtime={crtime}",
            "--cb-exit-at-end=0",
            f"--cb-sync-host={sync_host}",
            f"--cb-sync-port={cfg.sync_port}",
            f"--cb-sync-ns-port={cfg.sync_ns_port}",
            f"--cb-sync-watchdog-port={cfg.sync_watchdog_port}",
            f"--cb-sync-watchdog-timeout={cfg.sync_watchdog_timeout}",
            "--cb-drop-cache-host=none",
            f"--cb-drop-cache-port={cfg.drop_cache_port}",
        ]
        env = {
            "VERBOSE": str(int(cfg.verbose)),
            "SYSTEM_PODFILE_DIR": SYSTEM_CONFIGMAP_MOUNT_DIR,
            "USER_PODFILE_DIR": USER_CONFIGMAP_MOUNT_DIR,
            "PYTHONPATH": SYSTEM_CONFIGMAP_MOUNT_DIR,
        }
        vm = [
            {
                "name": "system-configmap",
                "mountPath": SYSTEM_CONFIGMAP_MOUNT_DIR,
                "readOnly": True,
            },
            {
                "name": "proc-sys-vm",
                "mountPath": "/proc-sys-vm",
            },
        ]
        container: dict[str, Any] = {
            "name": f"{namespace}-dc",
            "image": cfg.container_image,
            "command": command,
            "ports": [{"containerPort": cfg.drop_cache_port}],
            "env": [{"name": k, "value": str(v)} for k, v in env.items()],
            "volumeMounts": vm,
            "securityContext": {
                "privileged": True,
                "runAsUser": 0,
            },
        }
        if cfg.image_pull_policy:
            container["imagePullPolicy"] = cfg.image_pull_policy

        labels = self.standard_labels(
            "drop-cache",
            workload="drop-cache",
            namespace=namespace,
            suffix="dc",
            class_name="dc",
            worker_label=False,
        )

        volumes = [
            {
                "name": "system-configmap",
                "configMap": {"name": f"{cfg.basename}-system-configmap"},
            },
            {
                "name": "proc-sys-vm",
                "hostPath": {"path": "/proc/sys/vm"},
            },
        ]

        spec: dict[str, Any] = {
            "terminationGracePeriodSeconds": 1,
            "containers": [container],
            "volumes": volumes,
            "restartPolicy": "Never",
        }

        if worker_replica_label:
            spec["affinity"] = {
                "podAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": [{
                        "labelSelector": {
                            "matchExpressions": [{
                                "key": "replica",
                                "operator": "In",
                                "values": [worker_replica_label],
                            }],
                        },
                        "topologyKey": "kubernetes.io/hostname",
                    }],
                },
            }

        pin_node = cfg.pin_nodes.get("default", "")
        if pin_node:
            spec["nodeSelector"] = {"kubernetes.io/hostname": pin_node}
        elif cfg.node_selector:
            spec["nodeSelector"] = {cfg.node_selector: ""}

        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": self.mkpodname(name),
                "namespace": namespace,
                "labels": labels,
            },
            "spec": spec,
        }

    def sync_pod(
        self,
        namespace: str,
        *,
        expected_clients: int,
        initial_expected_clients: int,
    ) -> dict[str, Any]:
        """Build the sync pod manifest (a bare Pod, restartPolicy: Never).

        Matches bash ``create_sync_deployment`` / ``create_container_sync``.
        """
        from .sync import (
            SYNC_FLAG_FILE,
            SYNC_ERROR_FILE,
            CONTROLLER_TIMESTAMP_FILE,
        )
        cfg = self._cfg
        sync_image = (
            cfg.sync_pod_image_override
            or cfg.clusterbuster_base_image
        )
        command = [
            "python3",
            f"{SYSTEM_CONFIGMAP_MOUNT_DIR}/sync.py",
            f"--cb-sync-nonce={cfg.sync_nonce}",
            f"--cb-sync-file={SYNC_FLAG_FILE}",
            f"--cb-error-file={SYNC_ERROR_FILE}",
            f"--cb-controller-timestamp-file={CONTROLLER_TIMESTAMP_FILE}",
            f"--cb-predelay={cfg.predelay}",
            f"--cb-postdelay={cfg.postdelay}",
            f"--cb-step-interval={cfg.workload_step_interval}",
            f"--cb-listen-port={cfg.sync_port}",
            f"--cb-ns-port={cfg.sync_ns_port}",
            f"--cb-watchdog-port={cfg.sync_watchdog_port}",
            f"--cb-watchdog-timeout={cfg.sync_watchdog_timeout}",
            f"--cb-expected-clients={expected_clients}",
            f"--cb-initial-expected-clients={initial_expected_clients}",
        ]
        env = {
            "VERBOSE": str(int(cfg.verbose)),
            "SYSTEM_PODFILE_DIR": SYSTEM_CONFIGMAP_MOUNT_DIR,
            "USER_PODFILE_DIR": USER_CONFIGMAP_MOUNT_DIR,
            "PYTHONPATH": SYSTEM_CONFIGMAP_MOUNT_DIR,
        }
        vm = [
            {
                "name": "system-configmap",
                "mountPath": SYSTEM_CONFIGMAP_MOUNT_DIR,
                "readOnly": True,
            },
        ]
        container: dict[str, Any] = {
            "name": namespace,
            "image": sync_image,
            "command": command,
            "ports": [{"containerPort": cfg.container_port}],
            "env": [{"name": k, "value": str(v)} for k, v in env.items()],
            "volumeMounts": vm,
            "resources": {
                "requests": {"memory": "512Mi", "cpu": "2"},
            },
            "securityContext": self.security_context(),
        }
        if cfg.image_pull_policy:
            container["imagePullPolicy"] = cfg.image_pull_policy

        labels = self.standard_labels(
            "sync",
            workload="sync",
            namespace=namespace,
            class_name="sync",
            worker_label=False,
        )
        labels[f"{cfg.basename}-sync-sync"] = "true"

        tols = list(self.tolerations() or [])
        tols.append({
            "key": "node-role.kubernetes.io/infra",
            "operator": "Equal",
            "effect": "NoSchedule",
        })

        volumes = [
            {
                "name": "system-configmap",
                "configMap": {"name": f"{cfg.basename}-system-configmap"},
            },
        ]

        pod_name = self.mkpodname(f"{cfg.basename}-sync")

        spec: dict[str, Any] = {
            "terminationGracePeriodSeconds": 1,
            "containers": [container],
            "volumes": volumes,
            "restartPolicy": "Never",
        }
        if tols:
            spec["tolerations"] = tols

        pin_node = cfg.pin_nodes.get("sync", cfg.pin_nodes.get("default", ""))
        if pin_node:
            spec["nodeSelector"] = {"kubernetes.io/hostname": pin_node}
        elif cfg.node_selector:
            spec["nodeSelector"] = {cfg.node_selector: ""}

        if cfg.sync_affinity:
            aff = self.affinity(
                cfg.sync_affinity,
                label_key=f"{cfg.basename}-worker",
                label_value="true",
            )
            if aff:
                spec["affinity"] = aff

        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name,
                "namespace": namespace,
                "labels": labels,
            },
            "spec": spec,
        }


class ObjectBatcher:
    """Accumulates YAML manifests and flushes in configurable batch sizes.

    Equivalent to bash ``create_object`` / ``really_create_objects`` /
    ``accumulateddata`` pattern.

    When *artifact_dir* is set, each manifest is saved to
    ``<artifact_dir>/<Kind>/<ns>:<name>`` matching bash behavior.
    """

    def __init__(
        self,
        cluster: ClusterInterface,
        *,
        batch_size: int = 1,
        sleep_between: float = 0,
        artifact_dir: str = "",
        use_apply: bool = False,
    ):
        self._cluster = cluster
        self._batch_size = max(batch_size, 1)
        self._sleep_between = sleep_between
        self._artifact_dir = artifact_dir
        self._use_apply = use_apply
        self._buffer: list[dict[str, Any]] = []
        self._count = 0

    @staticmethod
    def _stamp_crtime(manifest: dict[str, Any]) -> None:
        """Update ``--cb-crtime=`` in container args to the current time.

        Manifests are built during the planning phase, so their crtime
        values reflect plan-build time rather than the wall-clock moment
        when the object is actually submitted to the cluster.  This
        method patches the args just before submission, matching bash
        where ``$(ts)`` is evaluated as each object enters the batch.
        """
        now = time.time()
        prefix = "--cb-crtime="
        kind = manifest.get("kind", "")

        if kind == "VirtualMachine":
            ObjectBatcher._stamp_crtime_vm(manifest, now)
            return

        pod_spec = manifest.get("spec", {})
        if kind in ("Deployment", "ReplicaSet"):
            pod_spec = pod_spec.get("template", {}).get("spec", {})
        for container in pod_spec.get("containers", []):
            cmd = container.get("command")
            if not cmd:
                continue
            for i, arg in enumerate(cmd):
                if arg.startswith(prefix):
                    cmd[i] = f"{prefix}{now}"

    @staticmethod
    def _stamp_crtime_vm(manifest: dict[str, Any], now: float) -> None:
        """Patch ``--cb-crtime=`` inside VM cloud-init userData."""
        import re

        try:
            volumes = (manifest["spec"]["template"]["spec"]
                       ["volumes"])
        except (KeyError, TypeError):
            return
        for vol in volumes:
            ci = vol.get("cloudInitNoCloud") or vol.get("cloudInitConfigDrive")
            if not ci or "userData" not in ci:
                continue
            ci["userData"] = re.sub(
                r"--cb-crtime=[0-9.]+",
                f"--cb-crtime={now}",
                ci["userData"],
            )

    def add(self, manifest: dict[str, Any], *, force_flush: bool = False) -> None:
        """Add a manifest to the batch buffer."""
        self._stamp_crtime(manifest)
        self._buffer.append(manifest)
        self._save_manifest_artifact(manifest)
        self._count += 1

        if force_flush or self._count >= self._batch_size:
            self.flush()

    def _save_manifest_artifact(self, manifest: dict[str, Any]) -> None:
        """Save a manifest YAML to the artifact directory."""
        if not self._artifact_dir:
            return
        kind = manifest.get("kind", "Unknown")
        metadata = manifest.get("metadata", {})
        name = metadata.get("name", "unknown")
        ns = metadata.get("namespace", "")
        fname = f"{ns}:{name}" if ns else name
        subdir = os.path.join(self._artifact_dir, kind)
        os.makedirs(subdir, exist_ok=True)
        try:
            with open(os.path.join(subdir, fname), "w") as f:
                f.write("---\n")
                yaml.dump(manifest, f, default_flow_style=False)
        except OSError:
            pass

    def flush(self) -> None:
        """Flush accumulated manifests to the cluster.

        Raises :class:`ClusterError` if creation fails (matching bash
        ``__KUBEFAIL__`` abort semantics).  When *use_apply* is set,
        uses ``oc apply`` instead of ``oc create`` so that pre-existing
        objects are updated rather than rejected.
        """
        if not self._buffer:
            return

        docs = [yaml.dump(m, default_flow_style=False) for m in self._buffer]
        yaml_str = "---\n" + "\n---\n".join(docs)

        if self._use_apply:
            result = self._cluster.apply(yaml_str)
        else:
            result = self._cluster.create(yaml_str)
        if result.returncode != 0:
            verb = "apply" if self._use_apply else "create"
            raise ClusterError(
                f"Failed to {verb} objects: {result.stderr.strip()}",
                kubefail=True,
            )

        self._buffer.clear()
        self._count = 0

        if self._sleep_between > 0:
            time.sleep(self._sleep_between)

    def __enter__(self) -> ObjectBatcher:
        return self

    def __exit__(self, *args: Any) -> None:
        self.flush()
