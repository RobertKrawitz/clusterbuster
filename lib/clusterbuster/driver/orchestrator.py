# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""Top-level run lifecycle, object creation, namespace allocation."""

from __future__ import annotations

import atexit
import json
import logging
import os
import re
import shlex
import shutil
import signal
import sys
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from .cluster import ClusterInterface
from .config import ClusterbusterConfig
from .manifests import ManifestBuilder, ObjectBatcher
from .vm import VmManifestBuilder

_LOG = logging.getLogger(__name__)

_JOIN_TIMEOUT = 30


class _IsoTimestampFormatter(logging.Formatter):
    """Log formatter producing ISO 8601 timestamps with microseconds.

    Matches the bash ``timestamp`` function output format:
    ``2026-03-23T22:31:35.462426 message``
    """

    import datetime as _dt

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        ct = self._dt.datetime.fromtimestamp(record.created)
        return ct.strftime("%Y-%m-%dT%H:%M:%S.%f")


class _StderrLogFilter(logging.Filter):
    """Accept WARNING+ from any logger, plus INFO+ from the driver loggers.

    This mirrors bash behaviour where all oc command output (create, apply,
    delete, label messages) and status messages (metrics wait, run
    complete) are redirected to stderr.

    When *report_object_creation* is False, cluster INFO messages
    (creation output) are suppressed, but status messages from the
    orchestrator and metrics modules are still shown.
    """

    _DRIVER_PREFIX = "clusterbuster.driver."
    _CLUSTER_PREFIX = "clusterbuster.driver.cluster"
    _MONITOR_PREFIX = "clusterbuster.driver.monitoring"

    def __init__(
        self,
        *args: object,
        report_object_creation: bool = True,
        **kwargs: object,
    ):
        super().__init__()
        self._report_creation = report_object_creation

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        if record.levelno < logging.INFO:
            return False
        if not record.name.startswith(self._DRIVER_PREFIX):
            return False
        if record.name.startswith(self._MONITOR_PREFIX):
            return False
        if record.name.startswith(self._CLUSTER_PREFIX):
            return self._report_creation
        return True


class _MonitorLogFilter(logging.Filter):
    """Accept only records from the pod/VM monitoring subsystem."""

    _MONITOR_PREFIX = "clusterbuster.driver.monitoring"

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith(self._MONITOR_PREFIX)


# ---------------------------------------------------------------------------
# RunFailed exception
# ---------------------------------------------------------------------------

class RunFailed(Exception):
    """Fatal orchestration error — triggers cleanup and failure report."""


# ---------------------------------------------------------------------------
# RunContext — mutable state for a single run
# ---------------------------------------------------------------------------

@dataclass
class RunContext:
    """Mutable context object tracking state accumulated during a run."""

    config: ClusterbusterConfig
    cluster: ClusterInterface
    manifest_builder: ManifestBuilder
    vm_builder: VmManifestBuilder | None

    # Namespace state
    namespaces_to_create: list[str] = field(default_factory=list)
    namespaces_in_use: list[str] = field(default_factory=list)
    sync_namespace: str = ""
    sync_pod_args: list[str] = field(default_factory=list)

    # Sync service DNS
    global_sync_service: str = ""

    # Timing
    first_start_timestamp: float = 0.0
    second_start_timestamp: float = 0.0
    prometheus_starting_timestamp: float = 0.0
    first_local_ts: float = 0.0
    remote_ts: float = 0.0
    second_local_ts: float = 0.0
    basetime: float = 0.0

    # Object creation tracking
    objects_created: dict[str, int] = field(default_factory=dict)
    total_objects_created: int = 0

    # Run state flags
    shutdown_event: threading.Event = field(default_factory=threading.Event)
    run_started: threading.Event = field(default_factory=threading.Event)
    run_failed: threading.Event = field(default_factory=threading.Event)
    run_complete: threading.Event = field(default_factory=threading.Event)
    any_thread_done: threading.Event = field(default_factory=threading.Event)
    failure_reason: str = ""
    run_aborted: bool = False

    # Namespace removal override (auto-detection of pre-existing namespaces)
    effective_remove_namespaces: int | None = None

    # Temp directory
    tempdir: str = ""

    # Artifact state
    artifacts_retrieved: bool = False

    # Background threads (protected by _threads_lock)
    threads: list[threading.Thread] = field(default_factory=list)
    _threads_lock: threading.Lock = field(default_factory=threading.Lock)

    # Active subprocesses for interruptible blocking
    _active_subprocesses: list[subprocess.Popen] = field(default_factory=list)
    _subprocess_lock: threading.Lock = field(default_factory=threading.Lock)

    # First-writer-wins lock for failure_reason
    _failure_reason_lock: threading.Lock = field(default_factory=threading.Lock)

    # Worker results from sync
    worker_results: dict[str, Any] | None = None

    # Effective first deployment index (adjusted for scale_deployments);
    # initialized from config.first_deployment, updated by _find_first_deployment
    effective_first_deployment: int = -1

    def __post_init__(self) -> None:
        if self.effective_first_deployment < 0:
            self.effective_first_deployment = self.config.first_deployment


# ---------------------------------------------------------------------------
# Error injection
# ---------------------------------------------------------------------------

def _check_inject_error(ctx: RunContext, point: str) -> None:
    """Raise RunFailed if *point* is listed in injected_errors."""
    if point in ctx.config.injected_errors:
        msg = f"Injected error at {point!r}"
        _LOG.warning(msg)
        raise RunFailed(msg)


# ---------------------------------------------------------------------------
# _set_run_failed — failure propagation with sync pod notification
# ---------------------------------------------------------------------------

def _set_run_failed(ctx: RunContext, reason: str) -> None:
    """Set the run-failed flag and notify the sync pod."""
    from . import sync as sync_mod

    with ctx._failure_reason_lock:
        if ctx.run_failed.is_set():
            _LOG.info("Secondary failure: %s", reason)
            return
        ctx.failure_reason = reason
        ctx.run_failed.set()

    _LOG.error("Run failed: %s", reason)

    if ctx.sync_pod_args:
        ok = sync_mod.notify_sync_pod_error(
            ctx.cluster, ctx.sync_pod_args,
        )
        if not ok:
            _LOG.warning("Cannot write to sync pod error file; marking run aborted")
            ctx.run_aborted = True


# ---------------------------------------------------------------------------
# Thread management
# ---------------------------------------------------------------------------

def _terminate_subprocesses(ctx: RunContext) -> None:
    """Kill all active subprocesses to unblock stuck threads."""
    with ctx._subprocess_lock:
        for proc in ctx._active_subprocesses:
            try:
                proc.kill()
            except OSError:
                pass


def _interruptible_run(
    ctx: RunContext, *args: str, stdin_data: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess that can be killed on shutdown.

    Registers the Popen handle in ``ctx._active_subprocesses`` so
    ``_terminate_subprocesses`` can ``kill()`` it if the run is aborted.
    """
    proc = subprocess.Popen(
        [ctx.cluster.oc_path, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
        text=True,
    )
    with ctx._subprocess_lock:
        ctx._active_subprocesses.append(proc)
    try:
        stdout, stderr = proc.communicate(input=stdin_data)
        return subprocess.CompletedProcess(
            args=proc.args,
            returncode=proc.returncode,
            stdout=stdout or "",
            stderr=stderr or "",
        )
    finally:
        with ctx._subprocess_lock:
            if proc in ctx._active_subprocesses:
                ctx._active_subprocesses.remove(proc)


def _join_threads(ctx: RunContext) -> None:
    """Join all background threads with timeout."""
    _terminate_subprocesses(ctx)
    with ctx._threads_lock:
        threads_snapshot = list(ctx.threads)
    for thread in threads_snapshot:
        thread.join(timeout=_JOIN_TIMEOUT)
        if thread.is_alive():
            _LOG.warning("Thread %s did not exit within %ds; proceeding",
                         thread.name, _JOIN_TIMEOUT)


# ---------------------------------------------------------------------------
# Namespace allocation
# ---------------------------------------------------------------------------

def plan_namespace_names(
    config: ClusterbusterConfig,
    existing: set[str] | None = None,
) -> list[str]:
    """Compute namespace names without cluster contact.

    When *existing* is provided (from ``scale_ns`` cluster query), names
    that collide are skipped.  Without it, names are sequential from 0.
    This function is the single source of truth for namespace naming,
    used by both ``allocate_namespaces`` (live) and ``_get_namespace_plan``
    (dry-run).
    """
    skip = existing or set()
    ns_list: list[str] = []
    idx = 0
    while len(ns_list) < config.namespaces:
        name = f"{config.basename}-{idx}"
        if name not in skip:
            ns_list.append(name)
        idx += 1
    return ns_list


def allocate_namespaces(ctx: RunContext) -> None:
    """Allocate namespace names, determine sync namespace and pod."""
    cfg = ctx.config
    existing: set[str] = set()
    if cfg.scale_ns:
        result = ctx.cluster.run(
            "get", "namespace", f"-l{cfg.basename}", "-oname", "--no-headers",
            dry_run_skip=False, filter_output=False,
        )
        if result.returncode == 0:
            existing = {
                line.removeprefix("namespace/")
                for line in result.stdout.strip().splitlines()
                if line.strip()
            }

    ctx.namespaces_to_create = plan_namespace_names(cfg, existing)

    if cfg.sync_start:
        if cfg.sync_in_first_namespace:
            ctx.sync_namespace = ctx.namespaces_to_create[0]
        else:
            ctx.sync_namespace = f"{cfg.basename}-sync"
        sync_pod_name = f"{cfg.basename}-sync"
        ctx.sync_pod_args = ["-n", ctx.sync_namespace, sync_pod_name]

        ctx.global_sync_service = (
            f"{cfg.basename}-sync-0.{ctx.sync_namespace}"
            f".svc.cluster.local"
        )


# ---------------------------------------------------------------------------
# Object creation with parallelism
# ---------------------------------------------------------------------------

def _create_all_parallel(
    ctx: RunContext,
    objtype: str,
    build_fn: Any,
    *,
    use_apply: bool = False,
) -> None:
    """Create objects with strided parallelism."""
    cfg = ctx.config
    parallel = getattr(cfg, f"parallel_{objtype}")
    objs_per_call = getattr(cfg, f"objs_per_call_{objtype}")
    sleep_between = getattr(cfg, f"sleep_between_{objtype}")

    def _worker(stride_offset: int) -> int:
        batcher = ObjectBatcher(
            ctx.cluster, batch_size=objs_per_call,
            sleep_between=sleep_between,
            use_apply=use_apply,
            artifact_dir=cfg.artifactdir,
        )
        count = 0
        idx = stride_offset
        while idx < len(ctx.namespaces_to_create):
            if ctx.run_failed.is_set():
                batcher.flush()
                return count
            manifests = build_fn(ctx, idx)
            for m in manifests:
                batcher.add(m)
                count += 1
            idx += parallel
        batcher.flush()
        return count

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = [pool.submit(_worker, i) for i in range(parallel)]
        errors = []
        total = 0
        for f in as_completed(futures):
            exc = f.exception()
            if exc is not None:
                errors.append(exc)
            else:
                total += f.result()

        ctx.objects_created[objtype] = total
        ctx.total_objects_created += total

        if errors:
            raise RunFailed(f"Unable to create {objtype}: {errors[0]}")


def _report_object_creation(ctx: RunContext) -> None:
    """Print object creation summary matching bash ``report_object_creation``."""
    width = len(str(ctx.total_objects_created))
    for objtype in sorted(ctx.objects_created):
        count = ctx.objects_created[objtype]
        plural = "s" if count != 1 else ""
        _LOG.info("Created %*d %s%s", width, count, objtype, plural)
    plural = "s" if ctx.total_objects_created != 1 else ""
    _LOG.info("Created %d object%s total", ctx.total_objects_created, plural)


def create_all_objects(ctx: RunContext, objtype: str) -> None:
    """Create objects of the given type across all namespaces."""
    if ctx.run_failed.is_set():
        raise RunFailed(ctx.failure_reason)

    _LOG.info("Creating %s", objtype)
    _OBJECT_CREATORS[objtype](ctx)


def _namespace_policy(cfg: ClusterbusterConfig) -> str:
    """Return the PodSecurity policy for workload namespaces."""
    from .manifest_plan import _namespace_policy as _shared_policy
    from .workload_registry import get_workload
    try:
        wl = get_workload(cfg.resolved_workload)
        return _shared_policy(cfg, wl)
    except (ValueError, KeyError, ImportError):
        pass
    return "restricted"


def _namespace_exists(ctx: RunContext, ns: str) -> bool:
    """Check whether a namespace exists on the cluster."""
    result = ctx.cluster.run(
        "get", "namespace", ns,
        dry_run_skip=False, filter_output=False, log_errors=False,
    )
    return result.returncode == 0


def _create_all_namespaces(ctx: RunContext) -> None:
    """Create all namespaces.

    Matches bash semantics: if a namespace already exists it is reused
    rather than failing with ``AlreadyExists``.  This is important for
    ``--removenamespaces=0`` workflows where existing PVCs are retained.
    PSA labels are always applied via ``oc label --overwrite``.
    """
    cfg = ctx.config
    policy = _namespace_policy(cfg)

    existing: set[str] = set()

    all_check = list(ctx.namespaces_to_create)
    if ctx.sync_namespace and ctx.sync_namespace not in all_check:
        all_check.append(ctx.sync_namespace)

    for ns in all_check:
        if _namespace_exists(ctx, ns):
            existing.add(ns)
            if cfg.remove_namespaces == -1:
                ctx.effective_remove_namespaces = 0
    if ctx.effective_remove_namespaces is None:
        ctx.effective_remove_namespaces = cfg.remove_namespaces

    def _build(ctx: RunContext, ns_idx: int) -> list[dict[str, Any]]:
        ns = ctx.namespaces_to_create[ns_idx]
        if ns in existing:
            return []
        return [ctx.manifest_builder.namespace(ns, policy=policy)]

    _create_all_parallel(ctx, "namespaces", _build)

    if ctx.sync_namespace and ctx.sync_namespace not in ctx.namespaces_to_create:
        if not _namespace_exists(ctx, ctx.sync_namespace):
            manifest = ctx.manifest_builder.namespace(ctx.sync_namespace, policy=policy)
            batcher = ObjectBatcher(ctx.cluster, batch_size=1, artifact_dir=cfg.artifactdir)
            batcher.add(manifest)
            batcher.flush()

    all_ns = list(ctx.namespaces_to_create)
    if ctx.sync_namespace and ctx.sync_namespace not in all_ns:
        all_ns.append(ctx.sync_namespace)

    for ns in all_ns:
        ctx.cluster.run(
            "label", "namespace", "--overwrite", ns,
            f"pod-security.kubernetes.io/enforce={policy}",
            f"pod-security.kubernetes.io/audit={policy}",
            f"pod-security.kubernetes.io/warn={policy}",
            dry_run_skip=True, log_output=True,
        )

    if policy == "privileged":
        for ns in all_ns:
            sa_exists = ctx.cluster.run(
                "get", "serviceaccount", "-n", ns, ns,
                dry_run_skip=True, filter_output=False,
            ).returncode == 0
            if not sa_exists:
                ctx.cluster.run(
                    "create", "serviceaccount", "-n", ns, ns,
                    dry_run_skip=True, log_output=True,
                )
            ctx.cluster.run(
                "label", "serviceaccount", "--overwrite", "-n", ns, ns,
                f"{cfg.basename}=true",
                dry_run_skip=True, log_output=True,
            )
            ctx.cluster.run(
                "adm", "policy", "add-scc-to-user", "-n", ns,
                "privileged", "-z", ns,
                dry_run_skip=True, log_output=True,
            )


def _create_all_configmaps(ctx: RunContext) -> None:
    """Create system and user configmaps."""
    _save_sysfiles(ctx)

    def _build(ctx: RunContext, ns_idx: int) -> list[dict[str, Any]]:
        ns = ctx.namespaces_to_create[ns_idx]
        result = [ctx.manifest_builder.system_configmap(ns)]
        result.append(ctx.manifest_builder.user_configmap(ns))
        return result

    _create_all_parallel(ctx, "configmaps", _build, use_apply=True)


def _save_sysfiles(ctx: RunContext) -> None:
    """Copy system pod files to SYSFILES/ in the artifact directory."""
    cfg = ctx.config
    if not cfg.artifactdir:
        return
    import shutil
    sysdir = os.path.join(cfg.artifactdir, "SYSFILES")
    os.makedirs(sysdir, exist_ok=True)
    lib_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))),
        "pod_files",
    )
    for fname in ManifestBuilder._SHARED_POD_FILES:
        src = os.path.join(lib_dir, fname)
        if os.path.isfile(src):
            shutil.copy2(src, sysdir)


def _create_all_secrets(ctx: RunContext) -> None:
    """Create all secrets."""
    if ctx.config.secrets <= 0:
        return

    import base64

    def _build(ctx: RunContext, ns_idx: int) -> list[dict[str, Any]]:
        ns = ctx.namespaces_to_create[ns_idx]
        cfg = ctx.config
        first_dep = ctx.effective_first_deployment
        manifests = []
        for dep in range(first_dep, cfg.deps_per_namespace + first_dep):
            for k in range(cfg.secrets):
                ns_suffix = ns.split("-")[-1]
                name = f"secret-{cfg.basename}-{ns_suffix}-{dep}-{k}"
                secret_data = {
                    f"secret-{k}": base64.b64encode(
                        f"clusterbuster-secret-data-{k}".encode()
                    ).decode(),
                }
                manifests.append(ctx.manifest_builder.secret(name, ns, secret_data))
        return manifests

    _create_all_parallel(ctx, "secrets", _build, use_apply=True)

    if ctx.config.wait_for_secrets:
        _wait_for_secrets(ctx)


def _wait_for_secrets(ctx: RunContext) -> None:
    """Poll until all expected secrets are visible in the API."""
    expected: set[str] = set()
    cfg = ctx.config
    first_dep = ctx.effective_first_deployment
    for ns in ctx.namespaces_to_create:
        ns_suffix = ns.split("-")[-1]
        for dep in range(first_dep, cfg.deps_per_namespace + first_dep):
            for k in range(cfg.secrets):
                expected.add(f"secret/secret-{cfg.basename}-{ns_suffix}-{dep}-{k}")

    while expected:
        if ctx.run_failed.is_set():
            return
        result = ctx.cluster.run(
            "get", "secret", "-oname", "--no-headers",
            f"-l{cfg.basename}", "-A",
            dry_run_skip=False, filter_output=False,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                name = line.strip()
                expected.discard(name)
        if expected:
            _LOG.info("Waiting for %d secrets", len(expected))
            time.sleep(10)


def _find_first_deployment(ctx: RunContext) -> int:
    """Determine the first available deployment index (for scaling)."""
    cfg = ctx.config
    if not cfg.scale_deployments:
        return cfg.first_deployment

    result = ctx.cluster.run(
        "get", "deployment", "-A",
        f"-l{cfg.basename}",
        "-o", "jsonpath={range .items[*]}{.metadata.name}{'\\n'}{end}",
        dry_run_skip=False, filter_output=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return 0

    max_idx = -1
    for line in result.stdout.strip().splitlines():
        parts = line.strip().rsplit("-", 1)
        if len(parts) == 2:
            try:
                max_idx = max(max_idx, int(parts[1]))
            except ValueError:
                pass
    return max_idx + 1


def _create_sync_services(ctx: RunContext) -> None:
    """Create sync service, sync pod, and ExternalName services.

    Uses build_manifest_plan() to generate the sync manifests, then
    applies them via ObjectBatcher.  Only sync-specific objects are
    created here; worker pods/deployments are handled by
    ``_create_all_deployments`` with proper batching and parallelism.
    """
    _check_inject_error(ctx, "sync")
    cfg = ctx.config
    if not cfg.sync_start:
        return

    from .manifest_plan import build_manifest_plan
    all_manifests = build_manifest_plan(
        cfg, ctx.namespaces_to_create,
        basetime=ctx.basetime,
        first_deployment=ctx.effective_first_deployment,
    )

    batcher = ObjectBatcher(ctx.cluster, batch_size=1, use_apply=True, artifact_dir=cfg.artifactdir)
    sync_ns = ctx.sync_namespace
    sync_pod_name = f"{cfg.basename}-sync"
    for m in all_manifests:
        kind = m.get("kind", "")
        ns = m.get("metadata", {}).get("namespace", "")
        name = m.get("metadata", {}).get("name", "")
        if kind == "ConfigMap" and ns == sync_ns and sync_ns not in ctx.namespaces_to_create:
            batcher.add(m, force_flush=True)
        elif kind == "Service":
            batcher.add(m, force_flush=True)
        elif kind == "Pod" and name == sync_pod_name:
            batcher.add(m, force_flush=True)


def _create_all_deployments(ctx: RunContext) -> None:
    """Create all deployments.

    Uses the shared ``manifest_plan`` module for manifest construction,
    then applies via the parallel batcher.  The ``create_deployment()``
    workload hook is the only orchestrator-specific override.
    """
    from .workload_registry import get_workload

    _check_inject_error(ctx, "create_deployment")

    cfg = ctx.config
    try:
        wl = get_workload(cfg.resolved_workload)
    except (ValueError, KeyError, ImportError) as exc:
        raise RunFailed(
            f"Cannot resolve workload {cfg.resolved_workload!r} during "
            f"deployment creation: {exc}"
        ) from exc

    _create_sync_services(ctx)

    from .manifest_plan import build_manifest_plan
    first_dep = ctx.effective_first_deployment
    infra_kinds = frozenset({
        "Namespace", "ConfigMap", "Secret", "Service",
    })
    sync_pod_name = f"{cfg.basename}-sync"

    def _build(ctx: RunContext, ns_idx: int) -> list[dict[str, Any]]:
        ns = ctx.namespaces_to_create[ns_idx]

        for dep in range(cfg.deps_per_namespace):
            dep_idx = dep + first_dep
            if wl.create_deployment(_make_deployment_ctx(ctx, ns, dep_idx)):
                return []

        all_manifests = build_manifest_plan(
            cfg, [ns],
            basetime=ctx.basetime,
            first_deployment=first_dep,
        )
        return [
            m for m in all_manifests
            if m.get("kind", "") not in infra_kinds
            and m.get("metadata", {}).get("name", "") != sync_pod_name
        ]

    _create_all_parallel(ctx, "deployments", _build, use_apply=True)
    _deployments_post_hook(ctx)


def _is_client_server_workload(wl: Any) -> bool:
    """True if the workload uses the server/client arglist split."""
    from .manifest_plan import _is_client_server_workload as _shared
    return _shared(wl)


def _deployments_post_hook(ctx: RunContext) -> None:
    """Start VMs if needed after deployment creation."""
    cfg = ctx.config
    if cfg.deployment_type != "vm":
        return
    if cfg.vm_run_strategy == "Always" or cfg.vm_start_running:
        return

    try:
        from .vm import VirtctlInterface
        virtctl = VirtctlInterface()
        data = ctx.cluster.get_json(
            "vm", "-A", f"-l{cfg.basename}-xuuid={cfg.xuuid}",
        )
        for item in data.get("items", []):
            meta = item.get("metadata", {})
            ns = meta.get("namespace", "")
            name = meta.get("name", "")
            virtctl.start(ns, name)
    except Exception as exc:
        _LOG.warning("VM post-hook failed: %s", exc)


def _make_deployment_ctx(ctx: RunContext, namespace: str, dep_idx: int) -> Any:
    """Create a DeploymentContext for workload.create_deployment()."""
    from .workload_registry import DeploymentContext
    cfg = ctx.config
    return DeploymentContext(
        namespace=namespace,
        count=dep_idx,
        secret_count=cfg.secrets,
        replicas=cfg.replicas,
        containers_per_pod=cfg.containers_per_pod,
        config=cfg,
        manifest_builder=ctx.manifest_builder,
        cluster=ctx.cluster,
    )


_OBJECT_CREATORS = {
    "namespaces": _create_all_namespaces,
    "configmaps": _create_all_configmaps,
    "secrets": _create_all_secrets,
    "deployments": _create_all_deployments,
}


# ---------------------------------------------------------------------------
# do_logging — monitoring + sync retrieval
# ---------------------------------------------------------------------------

def do_logging(ctx: RunContext) -> int:
    """Start sync log retrieval and pod monitoring. Returns 0 or 1."""
    cfg = ctx.config
    from . import sync as sync_mod
    from . import monitoring as mon_mod

    if cfg.wait_forever:
        while not ctx.run_failed.is_set():
            time.sleep(1)
        return 1

    is_reporting = True
    try:
        from .workload_registry import get_workload
        wl = get_workload(cfg.resolved_workload)
        is_reporting = wl.supports_reporting()
    except (ValueError, KeyError, ImportError):
        pass

    def _on_failure(reason: str) -> None:
        _set_run_failed(ctx, reason)

    def _on_complete() -> None:
        ctx.run_complete.set()

    def _shutdown_check() -> bool:
        return ctx.shutdown_event.is_set() or ctx.run_failed.is_set()

    # Timestamp handshake (only for reporting workloads with sync)
    if is_reporting and cfg.sync_start:
        try:
            ctx.first_local_ts, ctx.remote_ts, ctx.second_local_ts = (
                sync_mod.get_pod_and_local_timestamps(
                    ctx.cluster, ctx.sync_pod_args,
                    pod_start_timeout=cfg.pod_start_timeout,
                    shutdown_check=_shutdown_check,
                )
            )
        except RuntimeError as exc:
            _set_run_failed(ctx, str(exc))
            return 1

    # Start monitor thread (always — handles non-reporting completion too)
    monitor_thread = threading.Thread(
        target=_monitor_thread_fn,
        args=(ctx, mon_mod),
        name="monitor_pods",
        daemon=True,
    )
    with ctx._threads_lock:
        ctx.threads.append(monitor_thread)
    monitor_thread.start()

    active_threads = [monitor_thread]

    # Start log helper thread (only for reporting workloads)
    log_thread = None
    if is_reporting:
        log_thread = threading.Thread(
            target=_log_helper_thread_fn,
            args=(ctx, sync_mod),
            name="log_helper",
            daemon=True,
        )
        with ctx._threads_lock:
            ctx.threads.append(log_thread)
        log_thread.start()
        active_threads.append(log_thread)

    # Wait for any thread to finish or timeout
    if cfg.timeout > 0:
        deadline = time.time() + cfg.timeout
        while not ctx.any_thread_done.is_set():
            remaining = deadline - time.time()
            if remaining <= 0:
                _set_run_failed(ctx, f"Run timed out after {cfg.timeout}s")
                break
            ctx.any_thread_done.wait(timeout=min(remaining, 1.0))

            if not ctx.run_failed.is_set():
                _check_pods_direct(ctx)
    else:
        while not ctx.any_thread_done.is_set():
            ctx.any_thread_done.wait(timeout=1.0)
            if ctx.run_failed.is_set():
                break

    ctx.shutdown_event.set()

    for t in active_threads:
        t.join(timeout=_JOIN_TIMEOUT)

    if ctx.run_failed.is_set():
        return 1
    return 0


def _monitor_thread_fn(ctx: RunContext, mon_mod: Any) -> None:
    """Monitor thread entry point."""
    try:
        _check_inject_error(ctx, "monitor")
        mon_mod.monitor_pods(
            ctx.config,
            ctx.cluster.oc_path,
            on_failure=lambda reason: _set_run_failed(ctx, reason),
            on_complete=lambda: ctx.run_complete.set(),
            shutdown_check=lambda: ctx.shutdown_event.is_set() or ctx.run_complete.is_set(),
        )
    except Exception as exc:
        _LOG.error("Monitor thread exception: %s", exc)
        _set_run_failed(ctx, f"Monitor thread failed: {exc}")
    finally:
        ctx.any_thread_done.set()


def _log_helper_thread_fn(ctx: RunContext, sync_mod: Any) -> None:
    """Log helper thread entry point."""
    try:
        fail_thread = threading.Thread(
            target=_fail_helper_thread_fn,
            args=(ctx, sync_mod),
            name="fail_helper",
            daemon=True,
        )
        with ctx._threads_lock:
            ctx.threads.append(fail_thread)
        fail_thread.start()

        def _exec_fn(*args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return _interruptible_run(ctx, *args, **{
                k: v for k, v in kwargs.items() if k == "stdin_data"
            })

        result = sync_mod.log_helper(
            ctx.cluster,
            ctx.config,
            ctx.sync_pod_args,
            shutdown_check=lambda: ctx.shutdown_event.is_set() or ctx.run_failed.is_set(),
            on_complete=lambda: ctx.run_complete.set(),
            on_failure=lambda reason: _set_run_failed(ctx, reason),
            exec_fn=_exec_fn,
        )
        if result is not None:
            _LOG.info("Run complete, retrieving results")
            try:
                ctx.worker_results = json.loads(result)
            except json.JSONDecodeError:
                _LOG.warning("Invalid JSON from log helper")
    except Exception as exc:
        _LOG.error("Log helper thread exception: %s", exc)
        _set_run_failed(ctx, f"Log helper failed: {exc}")
    finally:
        ctx.any_thread_done.set()


def _fail_helper_thread_fn(ctx: RunContext, sync_mod: Any) -> None:
    """Fail helper thread entry point."""
    try:
        def _exec_fn(*args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return _interruptible_run(ctx, *args, **{
                k: v for k, v in kwargs.items() if k == "stdin_data"
            })

        sync_mod.fail_helper(
            ctx.cluster,
            ctx.sync_pod_args,
            shutdown_check=lambda: ctx.shutdown_event.is_set() or ctx.run_failed.is_set(),
            on_failure=lambda reason: _set_run_failed(ctx, reason),
            exec_fn=_exec_fn,
        )
    except Exception as exc:
        _LOG.debug("Fail helper exception: %s", exc)


_POD_FAILURE_RE = re.compile(r"([^r]Error|Evicted|Crash)")


def _check_pods_direct(ctx: RunContext) -> None:
    """Independent pod failure check (belt-and-suspenders with monitor).

    Uses table output and regex matching the bash pattern:
    ``grep -q -E -e '([^r]Error|Evicted|Crash)'``
    """
    result = ctx.cluster.run(
        "get", "pod", "-A",
        f"-l{ctx.config.basename}-id={ctx.config.uuid}",
        "--no-headers",
        dry_run_skip=False, filter_output=False,
    )
    if result.returncode != 0:
        return
    for line in result.stdout.strip().splitlines():
        if _POD_FAILURE_RE.search(line):
            _set_run_failed(ctx, f"Pod failure detected (direct check): {line.strip()}")
            return


# ---------------------------------------------------------------------------
# Signal handler factory
# ---------------------------------------------------------------------------

def _make_signal_handler(ctx: RunContext) -> Any:
    """Create the signal handler closure for a run.

    Extracted from ``run()`` so the actual handler can be tested directly.
    """
    def _signal_handler(signum: int, frame: Any) -> None:
        if ctx.shutdown_event.is_set():
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
        ctx.shutdown_event.set()
        acquired = ctx._failure_reason_lock.acquire(blocking=False)
        if acquired:
            try:
                if not ctx.run_failed.is_set():
                    ctx.failure_reason = f"Received signal {signal.Signals(signum).name}"
            finally:
                ctx._failure_reason_lock.release()
        ctx.run_failed.set()

    return _signal_handler


# ---------------------------------------------------------------------------
# run() — top-level entry point
# ---------------------------------------------------------------------------

def run(config: ClusterbusterConfig) -> int:
    """Execute a full clusterbuster run. Returns 0 on success, 1 on failure."""
    from . import cleanup as cleanup_mod
    from . import metrics as metrics_mod
    from . import reporting as reporting_mod
    from . import artifacts as artifacts_mod

    cluster = ClusterInterface(
        doit=config.doit,
        verbose=config.verbose,
        debug_conditions=config.debug_conditions,
    )
    mb = ManifestBuilder(config)
    vm_builder = VmManifestBuilder(config, mb) if config.deployment_type == "vm" else None

    ctx = RunContext(
        config=config,
        cluster=cluster,
        manifest_builder=mb,
        vm_builder=vm_builder,
    )

    status = 0
    original_handlers: dict[int, Any] = {}
    _signal_handler = _make_signal_handler(ctx)
    console_handler: logging.StreamHandler | None = None
    stderr_handler: logging.FileHandler | None = None
    monitor_handler: logging.FileHandler | None = None

    try:
        # Install signal handlers
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            original_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, _signal_handler)

        # Temp directory
        ctx.tempdir = tempfile.mkdtemp(prefix="clusterbuster-")
        if not config.preserve_tmpdir:
            atexit.register(lambda d=ctx.tempdir: shutil.rmtree(d, ignore_errors=True))

        # Console logging — show oc output on the terminal like bash does.
        # Must be set up before precleanup so deletion messages are visible.
        root_logger = logging.getLogger()
        if root_logger.level > logging.INFO:
            root_logger.setLevel(logging.INFO)
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(logging.INFO)
        console_handler.addFilter(
            _StderrLogFilter(
                report_object_creation=config.report_object_creation,
            )
        )
        console_handler.setFormatter(
            _IsoTimestampFormatter("%(asctime)s %(message)s")
        )
        root_logger.addHandler(console_handler)

        # Pre-cleanup
        if config.precleanup:
            cleanup_mod.do_cleanup(cluster, config, pre=True)

        # Timestamps
        if config.take_prometheus_snapshot and config.artifactdir:
            ts = metrics_mod.start_prometheus_snapshot(cluster, config)
        else:
            ts = metrics_mod.set_start_timestamps(cluster)
        ctx.first_start_timestamp, ctx.prometheus_starting_timestamp, ctx.second_start_timestamp = ts

        # Artifact directory setup
        if config.artifactdir:
            artdir = config.artifactdir
            artdir = artdir.replace("%s", config.resolved_workload)
            artdir = artdir.replace("%w", config.resolved_workload)
            artdir = artdir.replace("%n", config.job_name)
            os.makedirs(artdir, exist_ok=True)

            cmdline_path = os.path.join(artdir, "commandline")
            with open(cmdline_path, "w") as f:
                f.write(shlex.join(config.command_line) + "\n")

            iso_fmt = _IsoTimestampFormatter("%(asctime)s %(message)s")

            stderr_handler = logging.FileHandler(
                os.path.join(artdir, "stderr.log"), mode="w",
            )
            stderr_handler.setLevel(logging.INFO)
            stderr_handler.addFilter(_StderrLogFilter())
            stderr_handler.setFormatter(iso_fmt)
            root_logger.addHandler(stderr_handler)

            monitor_handler = logging.FileHandler(
                os.path.join(artdir, "monitor.log"), mode="w",
            )
            monitor_handler.setLevel(logging.INFO)
            monitor_handler.addFilter(_MonitorLogFilter())
            monitor_handler.setFormatter(iso_fmt)
            root_logger.addHandler(monitor_handler)

        # Namespace allocation
        allocate_namespaces(ctx)

        # Create objects in order
        create_all_objects(ctx, "namespaces")
        create_all_objects(ctx, "configmaps")

        ctx.effective_first_deployment = _find_first_deployment(ctx)

        create_all_objects(ctx, "secrets")

        ctx.basetime = time.time()

        # Drop host caches (blocking)
        cleanup_mod.drop_host_caches(cluster, config)

        # Create deployments
        create_all_objects(ctx, "deployments")

        ctx.run_started.set()

        # Run monitoring + sync retrieval
        status = do_logging(ctx)

    except RunFailed as exc:
        status = 1
        if not ctx.failure_reason:
            ctx.failure_reason = str(exc)
        _LOG.error("RunFailed: %s", exc)

    except Exception as exc:
        status = 1
        _set_run_failed(ctx, f"Unexpected error: {exc}")
        _LOG.exception("Unexpected exception in run()")

    finally:
        _join_threads(ctx)

        # Prometheus snapshot (optional, separate from metrics extraction)
        if config.take_prometheus_snapshot and config.artifactdir:
            try:
                metrics_mod.retrieve_prometheus_snapshot(cluster, config.artifactdir)
            except Exception as exc:
                _LOG.warning("Prometheus snapshot retrieval failed: %s", exc)

        # Metrics extraction via prom-extract (when metrics_file is set)
        extracted_metrics: dict[str, Any] | None = None
        if config.metrics_file:
            try:
                extracted_metrics = metrics_mod.extract_metrics(
                    config, config.artifactdir or "",
                    ctx.prometheus_starting_timestamp,
                )
                if extracted_metrics:
                    _LOG.info("Extracted %d metric(s)", len(extracted_metrics))
            except Exception as exc:
                _LOG.warning("Metrics extraction failed: %s", exc)

        # Reporting
        try:
            _LOG.info("Generating run data")
            from .workload_registry import get_workload
            wl = get_workload(config.resolved_workload)
            if wl.supports_reporting():
                status_str = "Success" if status == 0 else config.failure_status
                report = reporting_mod.assemble_report(
                    config,
                    cluster,
                    worker_results=ctx.worker_results,
                    status=status_str,
                    first_start_ts=ctx.first_start_timestamp,
                    second_start_ts=ctx.second_start_timestamp,
                    prometheus_start_ts=ctx.prometheus_starting_timestamp,
                    presync_ts=ctx.first_local_ts,
                    sync_ts=ctx.remote_ts,
                    postsync_ts=ctx.second_local_ts,
                    metrics=extracted_metrics,
                )
                reporting_mod.write_report_to_artifacts(report, config)
                reporting_mod.print_report(report, config)
        except Exception as exc:
            _LOG.warning("Report generation failed: %s", exc)
            try:
                emergency = reporting_mod.generate_emergency_report(
                    config, cluster, ctx.first_start_timestamp,
                )
                reporting_mod.print_report(emergency, config)
            except Exception:
                _LOG.error("Emergency report generation also failed")

        # Object creation summary (matches bash report_object_creation)
        if ctx.total_objects_created > 0:
            _report_object_creation(ctx)

        # Artifacts
        if ctx.run_started.is_set():
            try:
                ctx.artifacts_retrieved = artifacts_mod.retrieve_artifacts(
                    cluster, config,
                    force=status != 0,
                    run_started=ctx.run_started.is_set(),
                    run_failed=ctx.run_failed.is_set(),
                    sync_namespace=ctx.sync_namespace,
                    already_retrieved=ctx.artifacts_retrieved,
                )
            except Exception as exc:
                _LOG.warning("Artifact collection failed: %s", exc)

        # Cleanup
        if config.cleanup_always or (config.cleanup and status == 0):
            try:
                _check_inject_error(ctx, "cleanup")
                cleanup_mod.do_cleanup(
                    cluster, config,
                    force=config.cleanup_always and status != 0,
                    override_remove_namespaces=ctx.effective_remove_namespaces,
                )
            except RunFailed as exc:
                _LOG.warning("Cleanup failed (injected): %s", exc)
            except Exception as exc:
                _LOG.warning("Cleanup failed: %s", exc)

        # Clean up logging handlers
        if console_handler:
            logging.getLogger().removeHandler(console_handler)
            console_handler.close()
        if stderr_handler:
            logging.getLogger().removeHandler(stderr_handler)
            stderr_handler.close()
        if monitor_handler:
            logging.getLogger().removeHandler(monitor_handler)
            monitor_handler.close()

        # Temp directory
        if not config.preserve_tmpdir and ctx.tempdir:
            shutil.rmtree(ctx.tempdir, ignore_errors=True)

        # Restore signal handlers
        for sig, handler in original_handlers.items():
            signal.signal(sig, handler)

    return status
