# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""Pod failure detection via streaming watch, heartbeat, state machine."""

from __future__ import annotations

import logging
import selectors
import subprocess
import time
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from .config import ClusterbusterConfig

_LOG = logging.getLogger(__name__)

_FAILURE_PHASES = frozenset({"failed", "error", "oomkilled"})
_STARTING_PHASES = frozenset({"pending", "containercreating"})
_FINISHED_PHASES = frozenset({"completed", "succeeded", "terminated"})


def watch_pods_with_heartbeat(
    oc_path: str,
    watch_args: list[str],
    *,
    line_timeout: float = 1.0,
) -> Iterator[str | None]:
    """Stream pod status lines with heartbeat.

    Yields lines from ``oc get pod -w``.  Yields ``None`` on timeout
    (heartbeat sentinel — 1-second tick).

    Uses binary mode + selectors to avoid Python TextIOWrapper
    buffering issues with selector-based timeout.
    """
    proc = subprocess.Popen(
        [oc_path, *watch_args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    sel = selectors.DefaultSelector()
    assert proc.stdout is not None
    sel.register(proc.stdout, selectors.EVENT_READ)
    remainder = b""
    try:
        while proc.poll() is None:
            ready = sel.select(timeout=line_timeout)
            if ready:
                chunk = proc.stdout.read1(8192) if hasattr(proc.stdout, 'read1') else proc.stdout.read(8192)
                if not chunk:
                    break
                remainder += chunk
                while b"\n" in remainder:
                    line, remainder = remainder.split(b"\n", 1)
                    yield line.decode("utf-8", errors="replace").rstrip("\r")
            else:
                yield None
        if remainder:
            yield remainder.decode("utf-8", errors="replace").rstrip("\r\n")
    finally:
        sel.unregister(proc.stdout)
        proc.kill()
        proc.wait()


def monitor_pods(
    config: ClusterbusterConfig,
    oc_path: str,
    *,
    on_failure: object = None,
    on_complete: object = None,
    shutdown_check: object = None,
) -> str | None:
    """Run the pod status state machine.

    Args:
        config: Run configuration.
        oc_path: Path to oc/kubectl binary.
        on_failure: Callable(reason: str) -> None, called on pod failure.
        on_complete: Callable() -> None, called when non-reporting
            workload completes.
        shutdown_check: Callable() -> bool, returns True if shutdown requested.

    Returns failure reason string, or None if completed normally.
    """
    cfg = config
    watch_args = [
        "get", "pod", "-A",
        f"-l{cfg.basename}-monitor={cfg.xuuid}",
        "-w",
        "-o", "jsonpath={.metadata.namespace} {.metadata.name} {.status.phase}{\"\\n\"}",
    ]

    pod_phases: dict[str, str] = {}
    last_starting_set: frozenset[str] = frozenset()
    last_progress_time = time.time()

    supports_reporting = True
    try:
        from .workload_registry import get_workload
        wl = get_workload(cfg.resolved_workload)
        supports_reporting = wl.supports_reporting()
    except (ValueError, KeyError, ImportError):
        pass

    for line in watch_pods_with_heartbeat(oc_path, watch_args, line_timeout=1.0):
        if shutdown_check and shutdown_check():
            return None

        if line is None:
            elapsed = time.time() - last_progress_time
            current_starting = frozenset(
                k for k, v in pod_phases.items() if v in _STARTING_PHASES
            )
            if current_starting and current_starting == last_starting_set:
                if cfg.pod_start_timeout > 0 and elapsed > cfg.pod_start_timeout:
                    reason = (
                        f"Pods stuck in Pending for {elapsed:.0f}s "
                        f"(timeout={cfg.pod_start_timeout}s): "
                        + ", ".join(sorted(current_starting))
                    )
                    if on_failure:
                        on_failure(reason)
                    return reason
            else:
                last_starting_set = current_starting
                last_progress_time = time.time()

            if not supports_reporting:
                running = sum(1 for v in pod_phases.values() if v == "running")
                other = sum(
                    1 for v in pod_phases.values()
                    if v not in _FINISHED_PHASES and v != "running"
                )
                if pod_phases and running > 0 and other == 0:
                    _LOG.info("Non-reporting workload: all %d pods running, sleeping %ds",
                              running, cfg.workload_run_time)
                    time.sleep(cfg.workload_run_time)
                    if on_complete:
                        on_complete()
                    return None
            continue

        parts = line.split(None, 2)
        if len(parts) < 3:
            continue

        namespace, name, phase_raw = parts
        phase = phase_raw.lower()
        pod_key = f"{namespace}/{name}"

        if pod_phases.get(pod_key) == phase:
            continue

        _LOG.info("Pod %s: %s → %s", pod_key, pod_phases.get(pod_key, "<new>"), phase)
        pod_phases[pod_key] = phase

        if phase in _FAILURE_PHASES:
            reason = f"Pod {pod_key} entered {phase_raw} state"
            _LOG.error("%s", reason)
            if on_failure:
                on_failure(reason)
            return reason

        if phase == "running" or phase in _FINISHED_PHASES:
            last_progress_time = time.time()

    return None
