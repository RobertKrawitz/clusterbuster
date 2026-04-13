# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""Controller-side sync protocol: timestamp exchange, result retrieval."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .cluster import ClusterInterface
    from .config import ClusterbusterConfig

_LOG = logging.getLogger(__name__)

SYNC_FLAG_FILE = "/tmp/clusterbuster_sync_flag"
SYNC_ERROR_FILE = "/tmp/clusterbuster_sync_error"
SYNC_DONE_FILE = "/tmp/clusterbuster_sync_done"
CONTROLLER_TIMESTAMP_FILE = "/tmp/clusterbuster_controller_ts"

_API_READY_MIN_PODS = 10
_API_READY_POLL_INTERVAL = 5
_FLAG_REMOVE_MAX_RETRIES = 5


def get_pod_and_local_timestamps(
    cluster: ClusterInterface,
    sync_pod_args: list[str],
    *,
    pod_start_timeout: int = 60,
    shutdown_check: object = None,
) -> tuple[float, float, float]:
    """Wait for sync pod, exchange timestamps.

    Returns (first_local_ts, remote_ts, second_local_ts).
    Raises RuntimeError if sync pod fails or timeout expires.
    """
    start_wait = time.time()

    while True:
        if shutdown_check and shutdown_check():
            raise RuntimeError("Shutdown requested during sync pod wait")

        first_local = time.time()
        result = cluster.run(
            "exec", *sync_pod_args, "--", "date", "+%s.%N",
            dry_run_skip=False, filter_output=False, log_errors=False,
        )

        if result.returncode == 0 and result.stdout.strip():
            remote_ts = float(result.stdout.strip())
            second_local = time.time()

            ts_json = json.dumps({
                "first_controller_ts": first_local,
                "sync_ts": remote_ts,
                "second_controller_ts": second_local,
            })
            cluster.run(
                "exec", *sync_pod_args, "--stdin=true", "--",
                "sh", "-c",
                f"cat > '{CONTROLLER_TIMESTAMP_FILE}.tmp' && "
                f"mv '{CONTROLLER_TIMESTAMP_FILE}.tmp' '{CONTROLLER_TIMESTAMP_FILE}'",
                stdin_data=ts_json,
                dry_run_skip=False, filter_output=False,
            )

            return first_local, remote_ts, second_local

        elapsed = time.time() - start_wait
        if pod_start_timeout > 0 and elapsed > pod_start_timeout:
            raise RuntimeError(
                f"Sync pod not responsive after {elapsed:.0f}s "
                f"(timeout={pod_start_timeout}s)"
            )

        pod_phase = _get_pod_phase(cluster, sync_pod_args)
        if pod_phase in ("Error", "Failed"):
            raise RuntimeError(f"Sync pod entered {pod_phase} state")

        time.sleep(1)


def log_helper(
    cluster: ClusterInterface,
    config: ClusterbusterConfig,
    sync_pod_args: list[str],
    *,
    shutdown_check: object = None,
    on_complete: object = None,
    on_failure: object = None,
    exec_fn: object = None,
) -> str | None:
    """Wait for sync flag file, retrieve JSON results.

    Returns the JSON string on success, None on failure.

    Args:
        exec_fn: Optional callable(*args, **kwargs) -> CompletedProcess.
            When provided (e.g. an interruptible wrapper), used instead
            of ``cluster.run`` so the blocking exec can be killed on
            shutdown.
    """
    def _default_exec(*args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return cluster.run(*args, dry_run_skip=False, filter_output=False, **kwargs)

    _exec = exec_fn or _default_exec

    while True:
        if shutdown_check and shutdown_check():
            return None

        script = (
            f"while [ ! -f '{SYNC_FLAG_FILE}' ] && [ ! -f '{SYNC_ERROR_FILE}' ]; do "
            f"  echo KEEPALIVE 1>&2; "
            f"  sleep 2; "
            f"done; "
            f"if [ -f '{SYNC_FLAG_FILE}' ]; then "
            f"  cat '{SYNC_FLAG_FILE}'; "
            f"  touch '{SYNC_DONE_FILE}'; "
            f"  sleep 5; "
            f"fi"
        )

        result = _exec("exec", *sync_pod_args, "--", "sh", "-c", script)

        if result.returncode == 0 and result.stdout.strip():
            try:
                parsed = json.loads(result.stdout)
                if "worker_results" in parsed:
                    _remove_sync_flag(cluster, sync_pod_args)
                    if on_complete:
                        on_complete()
                    return result.stdout
            except json.JSONDecodeError:
                _LOG.warning("Invalid JSON from sync flag file, retrying")

        if result.returncode != 0:
            if not _wait_for_api_ready(cluster, shutdown_check):
                return None
            continue

        pod_phase = _get_pod_phase(cluster, sync_pod_args)
        if pod_phase not in ("Running", "Pending", ""):
            _LOG.info("Sync pod phase is %s, stopping log helper", pod_phase)
            return None

        if shutdown_check and shutdown_check():
            return None

        time.sleep(1)


def fail_helper(
    cluster: ClusterInterface,
    sync_pod_args: list[str],
    *,
    shutdown_check: object = None,
    on_failure: object = None,
    exec_fn: object = None,
) -> None:
    """Poll sync pod for error/flag/done files.

    Args:
        exec_fn: Optional callable for interruptible subprocess execution.
    """
    def _default_exec(*args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return cluster.run(*args, dry_run_skip=False, filter_output=False, **kwargs)

    _exec = exec_fn or _default_exec

    while True:
        if shutdown_check and shutdown_check():
            return

        script = (
            f"if [ -f '{SYNC_ERROR_FILE}' ]; then "
            f"  cat '{SYNC_ERROR_FILE}'; echo '___ERROR___'; "
            f"elif [ -f '{SYNC_FLAG_FILE}' ] || [ -f '{SYNC_DONE_FILE}' ]; then "
            f"  echo '___DONE___'; "
            f"fi"
        )

        result = _exec("exec", *sync_pod_args, "--", "sh", "-c", script)

        if result.returncode == 0 and result.stdout.strip():
            output = result.stdout.strip()
            if "___ERROR___" in output:
                error_text = output.replace("___ERROR___", "").strip()
                if on_failure:
                    on_failure(error_text or "Sync pod reported error")
                return
            if "___DONE___" in output:
                return

        time.sleep(2)


def notify_sync_pod_error(
    cluster: ClusterInterface,
    sync_pod_args: list[str],
    message: str = "Please see logs for details.",
) -> bool:
    """Write to the sync pod's error file.

    Returns True on success, False if the sync pod is unreachable.
    """
    try:
        result = cluster.run(
            "exec", *sync_pod_args, "--stdin=false", "--",
            "sh", "-c",
            f"echo '{message}' >> '{SYNC_ERROR_FILE}'",
            dry_run_skip=False, filter_output=False,
        )
        return result.returncode == 0
    except Exception:
        return False


# -- Internal helpers -------------------------------------------------------

def _get_pod_phase(
    cluster: ClusterInterface,
    sync_pod_args: list[str],
) -> str:
    """Get the current phase of the sync pod."""
    ns_flag = sync_pod_args[0]  # "-n"
    ns = sync_pod_args[1]
    pod_name = sync_pod_args[2]
    result = cluster.run(
        "get", "pod", ns_flag, ns, pod_name,
        "-o", "jsonpath={.status.phase}",
        dry_run_skip=False, filter_output=False, log_errors=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _remove_sync_flag(
    cluster: ClusterInterface,
    sync_pod_args: list[str],
) -> None:
    """Remove the sync flag file from the sync pod."""
    for attempt in range(_FLAG_REMOVE_MAX_RETRIES):
        result = cluster.run(
            "exec", *sync_pod_args, "--", "rm", "-f", SYNC_FLAG_FILE,
            dry_run_skip=False, filter_output=False,
        )
        if result.returncode == 0:
            return
        _LOG.warning("Failed to remove sync flag file (attempt %d/%d)",
                     attempt + 1, _FLAG_REMOVE_MAX_RETRIES)
        time.sleep(1)
    _LOG.error("Could not remove sync flag file after %d attempts", _FLAG_REMOVE_MAX_RETRIES)


def _wait_for_api_ready(
    cluster: ClusterInterface,
    shutdown_check: object = None,
) -> bool:
    """Wait for the API server to become responsive.

    Returns True when ready, False if shutdown requested.
    """
    _LOG.info("Waiting for API server to become responsive")
    while True:
        if shutdown_check and shutdown_check():
            return False

        result = cluster.run(
            "get", "pod", "-A", "--no-headers",
            dry_run_skip=False, filter_output=False,
        )
        if result.returncode == 0 and result.stdout:
            pod_count = len(result.stdout.strip().splitlines())
            if pod_count > _API_READY_MIN_PODS:
                _LOG.info("API server responsive (%d pods)", pod_count)
                return True

        time.sleep(_API_READY_POLL_INTERVAL)
