# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""Two-stage configuration: mutable builder for option parsing, frozen config for run."""

from __future__ import annotations

import os
import platform
import uuid as _uuid_mod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ClusterbusterConfigBuilder:
    """Mutable configuration accumulator — populated during CLI / job-file parsing.

    Call :meth:`build` after all options are processed to produce a
    validated :class:`ClusterbusterConfig`.
    """

    # -- Identity and naming --------------------------------------------------
    requested_workload: str = ""
    basename: str = ""
    job_name: str = ""
    pod_prefix: str = ""
    arch: str = ""
    uuid: str = ""

    # -- Namespace layout -----------------------------------------------------
    namespaces: int = 1
    deps_per_namespace: int = 1
    remove_namespaces: int = -1  # tri-state: -1=auto, 0=keep, 1=remove
    create_namespaces_only: bool = False
    secrets: int = 0
    scale_ns: bool = False
    scale_deployments: bool = True
    first_deployment: int = 0

    # -- Deployment topology --------------------------------------------------
    replicas: int = 1
    containers_per_pod: int = 1
    processes_per_pod: int = 1
    deployment_type: str = "pod"

    # -- Parallelism and batching ---------------------------------------------
    parallel: int = 1
    parallel_secrets: int = 0
    parallel_configmaps: int = 0
    parallel_namespaces: int = 0
    parallel_deployments: int = 0
    objs_per_call: int = 1
    objs_per_call_secrets: int = 0
    objs_per_call_configmaps: int = 0
    objs_per_call_namespaces: int = 0
    objs_per_call_deployments: int = 0
    sleep_between_secrets: float = 0
    sleep_between_configmaps: float = 0
    sleep_between_namespaces: float = 0
    sleep_between_deployments: float = 0
    sleeptime: float = 0

    # -- Sync and coordination ------------------------------------------------
    sync_start: bool = True
    sync_port: int = 7778
    sync_ns_port: int = 7753
    sync_watchdog_timeout: int = 0
    sync_in_first_namespace: bool = False
    sync_host: str = ""
    sync_nonce: str = ""
    exit_at_end: bool = True

    # -- Timing ---------------------------------------------------------------
    timeout: int = 0
    predelay: int = 0
    postdelay: int = 0
    workload_step_interval: int = 0
    pod_start_timeout: int = -1  # negative = pick default by deployment type
    wait_forever: bool = False

    # -- Workload sizing ------------------------------------------------------
    bytes_transfer: int = 0
    bytes_transfer_max: int = 0
    workload_run_time: int = 0
    workload_run_time_max: int = 0
    target_data_rate: int = 0
    wait_for_secrets: bool = True

    # -- Images and pull policy -----------------------------------------------
    clusterbuster_base_image: str = "quay.io/rkrawitz/clusterbuster-base:latest"
    sync_pod_image_override: str = ""
    container_image: str = ""
    image_pull_policy: str = ""

    # -- Affinity and placement -----------------------------------------------
    affinity: int = 0  # 0=none, 1=affinity, 2=anti
    sync_affinity: int = 0
    pin_nodes: dict[str, str] = field(default_factory=dict)
    runtime_classes: dict[str, str] = field(default_factory=dict)
    net_interfaces: dict[str, str] = field(default_factory=dict)
    runtime_class: str = ""
    scheduler: str = ""
    node_selector: str = "node-role.kubernetes.io/worker"

    # -- Resources, security, pod metadata ------------------------------------
    resource_requests: list[str] = field(default_factory=list)
    resource_limits: list[str] = field(default_factory=list)
    create_pods_privileged: bool = False
    tolerations: list[str] = field(default_factory=list)
    pod_annotations: list[str] = field(default_factory=list)
    pod_labels: list[str] = field(default_factory=list)

    # -- Volumes and workdir --------------------------------------------------
    volumes: list[str] = field(default_factory=list)
    common_workdir: str = "/var/tmp/clusterbuster"
    configmap_files: list[str] = field(default_factory=list)

    # -- Reporting, artifacts, metrics ----------------------------------------
    report_format: str = "summary"
    artifactdir: str = ""
    compress_report: bool = False
    parallel_log_retrieval: int = 50
    retrieve_successful_logs: bool = False
    metrics_file: str = "default"
    metrics_epoch: int = 0
    metrics_interval: int = 30
    metrics_support: int = -1  # -1=unknown, 0=no, 1=yes
    take_prometheus_snapshot: bool = False
    failure_status: str = "Fail"
    baseoffset: int = 0

    # -- Cleanup --------------------------------------------------------------
    precleanup: bool = True
    cleanup: bool = False
    cleanup_always: bool = False
    force_cleanup_timeout: str = ""

    # -- Verbosity and run control --------------------------------------------
    verbose: bool = False
    doit: bool = True
    preserve_tmpdir: bool = False
    report_object_creation: bool = True

    # -- VM (KubeVirt) --------------------------------------------------------
    vm_cores: int = 1
    vm_threads: int = 1
    vm_sockets: int = 1
    vm_memory: str = "2Gi"
    vm_grace_period: int = 30
    vm_image: str = "quay.io/rkrawitz/clusterbuster-vm:latest"
    vm_evict_migrate: bool = True
    vm_run_as_container: bool = False
    vm_user: str = "cluster"
    vm_password: str = "buster"
    vm_ssh_keyfile: str = ""
    vm_run_as_root: bool = False
    vm_start_running: bool = True
    vm_run_strategy: str = ""
    vm_block_multiqueue: int = 0

    # -- Services and probes --------------------------------------------------
    headless_services: bool = True
    liveness_probe_frequency: int = 0
    liveness_probe_sleep_time: int = 0

    # -- Drop cache -----------------------------------------------------------
    drop_node_cache: bool = False
    drop_all_node_cache: bool = False

    # -- Kata / virtiofs ------------------------------------------------------
    virtiofsd_writeback: bool = False
    virtiofsd_direct: bool = True
    virtiofsd_threadpoolsize: int = 0

    # -- Debug / testing ------------------------------------------------------
    injected_errors: dict[str, str] = field(default_factory=dict)
    debug_conditions: dict[str, str] = field(default_factory=dict)

    # -- Accumulated parsing state (not carried to config) --------------------
    unknown_opts: list[str] = field(default_factory=list)
    extra_args: list[str] = field(default_factory=list)
    processed_options: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.basename:
            self.basename = os.environ.get(
                "CLUSTERBUSTER_DEFAULT_BASENAME", "clusterbuster"
            )

    # -- Validation helpers ---------------------------------------------------

    @staticmethod
    def _validate_resources(specs: list[str], kind: str) -> None:
        for spec in specs:
            if "=" not in spec:
                raise ConfigError(
                    f"Invalid resource {kind} {spec!r}: must be in name=value form"
                )

    @staticmethod
    def _validate_volumes(volumes: list[str]) -> list[str]:
        """Validate and deduplicate volume specs.

        Volume format: ``name:type:mountpoint[:key=value...]``.
        Mountpoint is at index 2 (matching bash).  Key=value args
        (e.g. ``size=1Gi``) start at index 3.
        Returns rewritten list with duplicates removed.
        """
        valid_types = {"pvc", "emptydir", "emptydisk"}
        seen_names: set[str] = set()
        seen_mounts: set[str] = set()
        result: list[str] = []
        for vol in volumes:
            parts = vol.split(":")
            if len(parts) < 3:
                raise ConfigError(
                    f"Invalid volume spec {vol!r}: need at least name:type:mountpoint"
                )
            name = parts[0]
            vtype = parts[1].lower()
            mountpoint = parts[2]
            if vtype not in valid_types:
                raise ConfigError(
                    f"Invalid volume type {vtype!r} in {vol!r}: "
                    f"must be one of {', '.join(sorted(valid_types))}"
                )
            if vtype == "emptydisk":
                kv_args = parts[3:]
                size_val = ""
                for kv in kv_args:
                    if "=" in kv:
                        k, _, v = kv.partition("=")
                        if k.lower() == "size":
                            size_val = v
                if not size_val or size_val == "0":
                    raise ConfigError(
                        f"emptydisk volume {name!r} must have a positive size (size=NNN)"
                    )
            if name in seen_names:
                continue
            seen_names.add(name)
            if mountpoint in seen_mounts:
                continue
            seen_mounts.add(mountpoint)
            result.append(vol)
        return result

    # -- build() --------------------------------------------------------------

    def build(
        self,
        *,
        command_line: list[str] | None = None,
        workload_process_options: Any = None,
        workload_finalize_args: Any = None,
    ) -> ClusterbusterConfig:
        """Validate and produce an immutable run config.

        Args:
            command_line: Original argv for report / artifact output.
            workload_process_options: Callback ``(unknown_opts) -> list[str]``
                returning still-unknown opts after workload processing.
                ``None`` means no workload option handling (Phase 3A stub).
            workload_finalize_args: Callback ``(builder) -> None`` for
                ``finalize_expanded_command_line_extra_args``.  Phase 3A stub.
        """
        errors: list[str] = []

        if not self.requested_workload:
            errors.append("Workload must be specified with --workload")

        # Workload-specific option processing (Phase 3B hook)
        remaining_unknown = list(self.unknown_opts)
        if workload_process_options is not None and remaining_unknown:
            remaining_unknown = workload_process_options(remaining_unknown)
        if remaining_unknown:
            for opt in remaining_unknown:
                errors.append(f"Unknown option {opt!r}")

        # Deployment type normalization (with bash aliases)
        dtype = self.deployment_type.lower()
        _dtype_aliases = {"rs": "replicaset", "dep": "deployment", "deploy": "deployment"}
        dtype = _dtype_aliases.get(dtype, dtype)
        if dtype in ("deployment", "replicaset"):
            self.exit_at_end = False
        elif dtype == "vm":
            self.runtime_class = "vm"
        elif dtype != "pod":
            errors.append(
                f"Invalid deployment type {self.deployment_type!r}: "
                "must be pod, vm, deployment, or replicaset"
            )

        if dtype == "vm" and self.containers_per_pod != 1:
            errors.append("containers_per_pod must be 1 for VM deployments")

        # aarch64 VM guard
        actual_arch = self.arch or platform.machine()
        if dtype == "vm" and actual_arch == "aarch64":
            if not os.environ.get("CB_ALLOW_VM_AARCH64"):
                errors.append(
                    "VM deployments on aarch64 are not supported "
                    "(set CB_ALLOW_VM_AARCH64=1 to override)"
                )

        # Resource validation
        self._validate_resources(self.resource_requests, "request")
        self._validate_resources(self.resource_limits, "limit")

        # Volume validation
        try:
            validated_volumes = self._validate_volumes(self.volumes)
        except ConfigError as exc:
            errors.append(str(exc))
            validated_volumes = list(self.volumes)

        # Metrics file resolution and readability
        resolved_metrics = self.metrics_file
        if resolved_metrics in ("default", "1"):
            default_name = "metrics-default.yaml"
            resolved_metrics = os.path.normpath(os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", "..", default_name,
            ))
        elif resolved_metrics in ("", "0", "none"):
            resolved_metrics = ""
        if resolved_metrics:
            if not os.path.isfile(resolved_metrics):
                errors.append(f"Metrics file {resolved_metrics!r} not found")
            elif not os.access(resolved_metrics, os.R_OK):
                errors.append(f"Metrics file {resolved_metrics!r} is not readable")

        # Workload alias resolution
        resolved_workload = self.requested_workload
        try:
            from .workload_registry import resolve_alias
            resolved_workload = resolve_alias(self.requested_workload)
        except (ValueError, ImportError):
            pass

        if errors:
            raise ConfigError("\n".join(errors))

        # Defaults — xuuid is always a fresh execution UUID, never
        # overridden by --uuid (matches bash behaviour).
        run_uuid = self.uuid or str(_uuid_mod.uuid4())
        run_xuuid = str(_uuid_mod.uuid4())
        run_arch = self.arch or platform.machine()
        sync_nonce = self.sync_nonce or str(_uuid_mod.uuid4())

        # Parallelism defaults
        par = self.parallel
        par_secrets = self.parallel_secrets or par
        par_configmaps = self.parallel_configmaps or par
        par_namespaces = self.parallel_namespaces or par
        par_deployments = self.parallel_deployments or par

        opc = self.objs_per_call
        opc_secrets = self.objs_per_call_secrets or opc
        opc_configmaps = self.objs_per_call_configmaps or opc
        opc_namespaces = self.objs_per_call_namespaces or opc
        opc_deployments = self.objs_per_call_deployments or opc

        # Pod start timeout defaults
        if self.pod_start_timeout < 0:
            pod_start_timeout = 180 if dtype == "vm" else 60
        else:
            pod_start_timeout = self.pod_start_timeout

        # Sync watchdog port
        sync_watchdog_port = 7780 if self.sync_watchdog_timeout > 0 else 0

        # Virtiofsd args — each flag is a separate JSON element matching
        # the kata annotation format: '["-o","allow_direct_io",...]'
        virtiofsd_args: list[str] = []
        if self.virtiofsd_direct:
            virtiofsd_args.extend(["-o", "allow_direct_io"])
        if self.virtiofsd_writeback:
            virtiofsd_args.extend(["-o", "writeback"])
        if self.virtiofsd_threadpoolsize > 0:
            virtiofsd_args.append(f"--thread-pool-size={self.virtiofsd_threadpoolsize}")

        # Finalize extra args (Phase 3B hook)
        if workload_finalize_args is not None:
            workload_finalize_args(self)

        container_image = self.container_image or self.clusterbuster_base_image

        return ClusterbusterConfig(
            # Identity
            requested_workload=self.requested_workload,
            resolved_workload=resolved_workload,
            basename=self.basename,
            job_name=self.job_name or self.requested_workload,
            pod_prefix=self.pod_prefix,
            arch=run_arch,
            uuid=run_uuid,
            xuuid=run_xuuid,
            command_line=command_line or [],
            # Namespace
            namespaces=max(self.namespaces, 1) if self.namespaces > 0 else 1,
            deps_per_namespace=self.deps_per_namespace,
            remove_namespaces=self.remove_namespaces,
            create_namespaces_only=self.create_namespaces_only,
            secrets=self.secrets,
            scale_ns=self.scale_ns,
            scale_deployments=self.scale_deployments,
            first_deployment=self.first_deployment,
            use_namespaces=self.namespaces > 0,
            # Deployment
            replicas=self.replicas,
            containers_per_pod=self.containers_per_pod,
            processes_per_pod=self.processes_per_pod,
            deployment_type=dtype,
            # Parallelism
            parallel=par,
            parallel_secrets=par_secrets,
            parallel_configmaps=par_configmaps,
            parallel_namespaces=par_namespaces,
            parallel_deployments=par_deployments,
            objs_per_call=opc,
            objs_per_call_secrets=opc_secrets,
            objs_per_call_configmaps=opc_configmaps,
            objs_per_call_namespaces=opc_namespaces,
            objs_per_call_deployments=opc_deployments,
            sleep_between_secrets=self.sleep_between_secrets,
            sleep_between_configmaps=self.sleep_between_configmaps,
            sleep_between_namespaces=self.sleep_between_namespaces,
            sleep_between_deployments=self.sleep_between_deployments,
            sleeptime=self.sleeptime,
            # Sync
            sync_start=self.sync_start,
            sync_port=self.sync_port,
            sync_ns_port=self.sync_ns_port,
            sync_watchdog_timeout=self.sync_watchdog_timeout,
            sync_watchdog_port=sync_watchdog_port,
            sync_in_first_namespace=self.sync_in_first_namespace,
            sync_host=self.sync_host,
            sync_nonce=sync_nonce,
            exit_at_end=self.exit_at_end,
            # Timing
            timeout=self.timeout,
            predelay=self.predelay,
            postdelay=self.postdelay,
            workload_step_interval=self.workload_step_interval,
            pod_start_timeout=pod_start_timeout,
            wait_forever=self.wait_forever,
            # Workload sizing
            bytes_transfer=self.bytes_transfer,
            bytes_transfer_max=self.bytes_transfer_max,
            workload_run_time=self.workload_run_time,
            workload_run_time_max=self.workload_run_time_max,
            target_data_rate=self.target_data_rate,
            wait_for_secrets=self.wait_for_secrets,
            # Images
            clusterbuster_base_image=self.clusterbuster_base_image,
            sync_pod_image_override=self.sync_pod_image_override,
            container_image=container_image,
            image_pull_policy=self.image_pull_policy,
            # Affinity
            affinity=self.affinity,
            sync_affinity=self.sync_affinity,
            pin_nodes=dict(self.pin_nodes),
            runtime_classes=dict(self.runtime_classes),
            net_interfaces=dict(self.net_interfaces),
            runtime_class=self.runtime_class,
            scheduler=self.scheduler,
            node_selector=self.node_selector,
            # Resources
            resource_requests=list(self.resource_requests),
            resource_limits=list(self.resource_limits),
            create_pods_privileged=self.create_pods_privileged,
            tolerations=list(self.tolerations),
            pod_annotations=list(self.pod_annotations),
            pod_labels=list(self.pod_labels),
            # Volumes
            volumes=validated_volumes,
            common_workdir=self.common_workdir,
            configmap_files=list(self.configmap_files),
            # Reporting
            report_format=self.report_format,
            artifactdir=self.artifactdir,
            compress_report=self.compress_report,
            parallel_log_retrieval=self.parallel_log_retrieval,
            retrieve_successful_logs=self.retrieve_successful_logs,
            metrics_file=resolved_metrics,
            metrics_epoch=self.metrics_epoch,
            metrics_interval=self.metrics_interval,
            metrics_support=self.metrics_support,
            take_prometheus_snapshot=self.take_prometheus_snapshot,
            failure_status=self.failure_status,
            baseoffset=self.baseoffset,
            # Cleanup
            precleanup=self.precleanup,
            cleanup=self.cleanup,
            cleanup_always=self.cleanup_always,
            force_cleanup_timeout=self.force_cleanup_timeout,
            # Run control
            verbose=self.verbose,
            doit=self.doit,
            preserve_tmpdir=self.preserve_tmpdir,
            report_object_creation=self.report_object_creation,
            # VM
            vm_cores=self.vm_cores,
            vm_threads=self.vm_threads,
            vm_sockets=self.vm_sockets,
            vm_memory=self.vm_memory,
            vm_grace_period=self.vm_grace_period,
            vm_image=self.vm_image,
            vm_evict_migrate=self.vm_evict_migrate,
            vm_run_as_container=self.vm_run_as_container,
            vm_user=self.vm_user,
            vm_password=self.vm_password,
            vm_ssh_keyfile=self.vm_ssh_keyfile,
            vm_run_as_root=self.vm_run_as_root,
            vm_start_running=self.vm_start_running,
            vm_run_strategy=self.vm_run_strategy,
            vm_block_multiqueue=self.vm_block_multiqueue,
            # Services
            headless_services=self.headless_services,
            liveness_probe_frequency=self.liveness_probe_frequency,
            liveness_probe_sleep_time=self.liveness_probe_sleep_time,
            # Drop cache
            drop_node_cache=self.drop_node_cache,
            drop_all_node_cache=self.drop_all_node_cache,
            # Kata
            virtiofsd_writeback=self.virtiofsd_writeback,
            virtiofsd_direct=self.virtiofsd_direct,
            virtiofsd_threadpoolsize=self.virtiofsd_threadpoolsize,
            virtiofsd_args=virtiofsd_args,
            # Constants
            container_port=7777,
            drop_cache_port=7779,
            # Debug
            injected_errors=dict(self.injected_errors),
            debug_conditions=dict(self.debug_conditions),
            # Parsing state carried forward for artifact output and report JSON.
            # The design originally excluded processed_options from the frozen
            # config, but it is retained intentionally for diagnostic output.
            extra_args=list(self.extra_args),
            processed_options=list(self.processed_options),
        )


@dataclass(frozen=True)
class ClusterbusterConfig:
    """Validated, immutable run configuration."""

    # Identity
    requested_workload: str
    resolved_workload: str
    basename: str
    job_name: str
    pod_prefix: str
    arch: str
    uuid: str
    xuuid: str
    command_line: list[str]

    # Namespace
    namespaces: int
    deps_per_namespace: int
    remove_namespaces: int
    create_namespaces_only: bool
    secrets: int
    scale_ns: bool
    scale_deployments: bool
    first_deployment: int
    use_namespaces: bool

    # Deployment
    replicas: int
    containers_per_pod: int
    processes_per_pod: int
    deployment_type: str

    # Parallelism
    parallel: int
    parallel_secrets: int
    parallel_configmaps: int
    parallel_namespaces: int
    parallel_deployments: int
    objs_per_call: int
    objs_per_call_secrets: int
    objs_per_call_configmaps: int
    objs_per_call_namespaces: int
    objs_per_call_deployments: int
    sleep_between_secrets: float
    sleep_between_configmaps: float
    sleep_between_namespaces: float
    sleep_between_deployments: float
    sleeptime: float

    # Sync
    sync_start: bool
    sync_port: int
    sync_ns_port: int
    sync_watchdog_timeout: int
    sync_watchdog_port: int
    sync_in_first_namespace: bool
    sync_host: str
    sync_nonce: str
    exit_at_end: bool

    # Timing
    timeout: int
    predelay: int
    postdelay: int
    workload_step_interval: int
    pod_start_timeout: int
    wait_forever: bool

    # Workload sizing
    bytes_transfer: int
    bytes_transfer_max: int
    workload_run_time: int
    workload_run_time_max: int
    target_data_rate: int
    wait_for_secrets: bool

    # Images
    clusterbuster_base_image: str
    sync_pod_image_override: str
    container_image: str
    image_pull_policy: str

    # Affinity
    affinity: int
    sync_affinity: int
    pin_nodes: dict[str, str]
    runtime_classes: dict[str, str]
    net_interfaces: dict[str, str]
    runtime_class: str
    scheduler: str
    node_selector: str

    # Resources
    resource_requests: list[str]
    resource_limits: list[str]
    create_pods_privileged: bool
    tolerations: list[str]
    pod_annotations: list[str]
    pod_labels: list[str]

    # Volumes
    volumes: list[str]
    common_workdir: str
    configmap_files: list[str]

    # Reporting
    report_format: str
    artifactdir: str
    compress_report: bool
    parallel_log_retrieval: int
    retrieve_successful_logs: bool
    metrics_file: str
    metrics_epoch: int
    metrics_interval: int
    metrics_support: int
    take_prometheus_snapshot: bool
    failure_status: str
    baseoffset: int

    # Cleanup
    precleanup: bool
    cleanup: bool
    cleanup_always: bool
    force_cleanup_timeout: str

    # Run control
    verbose: bool
    doit: bool
    preserve_tmpdir: bool
    report_object_creation: bool

    # VM
    vm_cores: int
    vm_threads: int
    vm_sockets: int
    vm_memory: str
    vm_grace_period: int
    vm_image: str
    vm_evict_migrate: bool
    vm_run_as_container: bool
    vm_user: str
    vm_password: str
    vm_ssh_keyfile: str
    vm_run_as_root: bool
    vm_start_running: bool
    vm_run_strategy: str
    vm_block_multiqueue: int

    # Services
    headless_services: bool
    liveness_probe_frequency: int
    liveness_probe_sleep_time: int

    # Drop cache
    drop_node_cache: bool
    drop_all_node_cache: bool

    # Kata
    virtiofsd_writeback: bool
    virtiofsd_direct: bool
    virtiofsd_threadpoolsize: int
    virtiofsd_args: list[str]

    # Constants
    container_port: int
    drop_cache_port: int

    # Debug
    injected_errors: dict[str, str]
    debug_conditions: dict[str, str]

    # Carried from parsing
    extra_args: list[str]
    processed_options: list[str]


class ConfigError(Exception):
    """Raised when configuration validation fails."""


def kv_get(kv_args: list[str], key: str) -> str:
    """Extract a value from a ``key=value`` args list (case-insensitive key).

    Used by manifests.py and vm.py to parse volume spec key=value trailing
    arguments (e.g. ``size=1Gi``, ``claimName=my-pvc``).
    """
    for kv in kv_args:
        if "=" in kv:
            k, _, v = kv.partition("=")
            if k.lower() == key.lower():
                return v
    return ""
