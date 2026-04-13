# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""Prometheus integration, timestamps, and metric extraction."""

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

_PROM_NS = "openshift-monitoring"
_PROM_POD = "prometheus-k8s-0"


def supports_metrics(cluster: ClusterInterface) -> bool:
    """Probe for the Prometheus pod in openshift-monitoring."""
    result = cluster.run(
        "get", "pod", "-n", _PROM_NS, _PROM_POD,
        dry_run_skip=False, filter_output=False,
    )
    return result.returncode == 0


def set_start_timestamps(
    cluster: ClusterInterface,
) -> tuple[float, float, float]:
    """Capture start timestamps.

    If Prometheus is available, uses its pod's ``date`` for a
    cluster-aligned timestamp.  Otherwise falls back to local time.

    Returns (first_ts, prometheus_ts, second_ts).
    """
    if supports_metrics(cluster):
        first_ts = time.time()
        result = cluster.run(
            "exec", "-n", _PROM_NS, _PROM_POD, "-c", "prometheus",
            "--", "date", "+%s.%N",
            dry_run_skip=False, filter_output=False,
        )
        prom_ts = float(result.stdout.strip()) if result.returncode == 0 else time.time()
        second_ts = time.time()
    else:
        first_ts = time.time()
        prom_ts = first_ts
        second_ts = first_ts

    return first_ts, prom_ts, second_ts


def start_prometheus_snapshot(
    cluster: ClusterInterface,
    config: ClusterbusterConfig,
) -> tuple[float, float, float]:
    """Delete and restart Prometheus pod, capture timestamps.

    Returns (first_ts, prometheus_ts, second_ts).
    """
    _LOG.info("Deleting Prometheus pod for snapshot baseline")
    cluster.delete("pod", "-n", _PROM_NS, _PROM_POD)

    _LOG.info("Waiting for Prometheus pod to become Ready")
    cluster.wait(
        "pod", "-n", _PROM_NS, _PROM_POD,
        "--for=condition=Ready",
        "--timeout=300s",
    )

    timestamps = set_start_timestamps(cluster)

    if config.metrics_epoch > 0:
        _LOG.info("Sleeping %ds for metrics epoch", config.metrics_epoch)
        time.sleep(config.metrics_epoch)

    return timestamps


def retrieve_prometheus_snapshot(
    cluster: ClusterInterface,
    artifactdir: str,
) -> bool:
    """Tar the Prometheus data directory to the artifact dir.

    Returns True on success.
    """
    if not artifactdir:
        return False

    import os
    import shutil

    _LOG.info("Retrieving Prometheus snapshot")
    snapshot_path = os.path.join(artifactdir, "prometheus-snapshot.tar")
    cmd = [
        cluster.oc_path,
        "exec", "-n", _PROM_NS, _PROM_POD, "-c", "prometheus",
        "--", "tar", "cf", "-", "/prometheus",
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with open(snapshot_path, "wb") as f:
            assert proc.stdout is not None
            shutil.copyfileobj(proc.stdout, f)
        proc.wait()
        if proc.returncode != 0:
            _LOG.warning("Failed to retrieve Prometheus snapshot (rc=%d)", proc.returncode)
            return False
        return True
    except OSError as exc:
        _LOG.warning("Failed to write Prometheus snapshot: %s", exc)
        return False


def extract_metrics(
    config: ClusterbusterConfig,
    artifactdir: str,
    prometheus_starting_timestamp: float,
) -> dict[str, Any]:
    """Run ``prom-extract`` and return parsed metrics JSON.

    Matches bash ``_extract_metrics`` which calls::

        prom-extract --indent= \\
            --define "namespace_re=BASENAME-.*" \\
            -m METRICS_FILE -s INTERVAL --metrics-only \\
            --start_time=START --post-settling-time=EPOCH*2
    """
    import os

    if not config.metrics_file:
        return {}

    metrics_file = config.metrics_file
    if not os.path.isfile(metrics_file):
        _LOG.warning("Metrics file not readable: %s", metrics_file)
        return {}

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    prom_extract = os.path.join(repo_root, "lib", "prom-extract")
    if not os.path.isfile(prom_extract):
        _LOG.warning("prom-extract not found at %s", prom_extract)
        return {}

    epoch = config.metrics_epoch
    start_time = int(prometheus_starting_timestamp - epoch)
    post_settling = epoch * 2

    _LOG.info("Waiting 60 seconds for metrics data collection to complete")
    time.sleep(60)
    _LOG.info("Retrieving system metrics data")

    args = [
        prom_extract,
        "--indent=",
        "--define", f"namespace_re={config.basename}-.*",
        "-m", metrics_file,
        "-s", str(config.metrics_interval),
        "--metrics-only",
        f"--start_time={start_time}",
        f"--post-settling-time={post_settling}",
    ]

    try:
        result = subprocess.run(
            args, capture_output=True, text=True, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        if result.returncode != 0:
            _LOG.warning("prom-extract failed (rc=%d): %s",
                         result.returncode, result.stderr.strip())
    except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
        _LOG.warning("prom-extract failed: %s", exc)

    return {}
