# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""JSON report assembly, emergency report, and print_report."""

from __future__ import annotations

import datetime
import gzip
import json
import logging
import os
import platform
import subprocess
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .cluster import ClusterInterface
    from .config import ClusterbusterConfig

_LOG = logging.getLogger(__name__)


def _format_timestamp(ts: float) -> str:
    """Format a Unix timestamp as an ISO-8601 local-time string."""
    if ts <= 0:
        return ""
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S%z")


def assemble_report(
    config: ClusterbusterConfig,
    cluster: ClusterInterface,
    *,
    worker_results: dict[str, Any] | None = None,
    status: str = "Success",
    first_start_ts: float = 0.0,
    second_start_ts: float = 0.0,
    prometheus_start_ts: float = 0.0,
    presync_ts: float = 0.0,
    sync_ts: float = 0.0,
    postsync_ts: float = 0.0,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the full report JSON dict."""
    end_ts = time.time()
    elapsed = end_ts - second_start_ts if second_start_ts > 0 else 0

    k8s_version = _get_k8s_version(cluster)
    nodes = _get_nodes(cluster, config)
    api_objects = _get_api_objects(cluster, config)
    csvs = _get_csvs(cluster)

    workload_instance = _get_workload_instance(config)
    workload_metadata = workload_instance.generate_metadata() if workload_instance else {}
    workload_reporting = workload_instance.workload_reporting_class() if workload_instance else config.resolved_workload
    workload_options = workload_instance.report_options() if workload_instance else {}

    report: dict[str, Any] = {
        "Results": worker_results or {},
        "Status": status,
        "metadata": {
            "kind": "clusterbusterResults",
            "controller_first_start_timestamp": first_start_ts,
            "prometheus_starting_timestamp": prometheus_start_ts,
            "controller_second_start_timestamp": second_start_ts,
            "controller_end_timestamp": end_ts,
            "controller_elapsed_time": elapsed,
            "cluster_start_time": _format_timestamp(prometheus_start_ts),
            "job_name": config.job_name,
            "uuid": config.uuid,
            "workload": config.resolved_workload,
            "workload_reporting_class": workload_reporting,
            "kubernetes_version": k8s_version,
            "command_line": config.command_line,
            "expanded_command_line": config.processed_options,
            "run_host": platform.node(),
            "workload_metadata": workload_metadata,
            "controller_presync_timestamp": presync_ts,
            "sync_timestamp": sync_ts,
            "controller_postsync_timestamp": postsync_ts,
            "artifact_directory": config.artifactdir,
            "options": _build_options(config, workload_options),
        },
    }

    if metrics:
        report["metrics"] = metrics
    if nodes:
        report["nodes"] = nodes
    if api_objects:
        report["api_objects"] = api_objects
    if csvs:
        report["csvs"] = csvs

    return report


def generate_emergency_report(
    config: ClusterbusterConfig,
    cluster: ClusterInterface,
    first_start_ts: float = 0.0,
) -> dict[str, Any]:
    """Minimal report for when the normal path cannot complete."""
    return assemble_report(
        config,
        cluster,
        worker_results={},
        status=config.failure_status,
        first_start_ts=first_start_ts,
    )


def print_report(
    report: dict[str, Any],
    config: ClusterbusterConfig,
    *,
    output_file: str = "",
) -> None:
    """Print or pipe the report JSON."""
    report_json = json.dumps(report, indent=2)

    if config.report_format == "raw":
        if output_file:
            with open(output_file, "w") as f:
                f.write(report_json)
        else:
            print(report_json)
        return

    cb_report_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))),
        "clusterbuster-report",
    )

    cmd = [cb_report_path, "-o", config.report_format]
    try:
        result = subprocess.run(
            cmd, input=report_json, capture_output=True, text=True, check=False,
        )
        output = result.stdout
        if output_file:
            with open(output_file, "w") as f:
                f.write(output)
        else:
            print(output, end="")
    except (subprocess.SubprocessError, OSError) as exc:
        _LOG.warning("clusterbuster-report failed: %s; falling back to raw", exc)
        if output_file:
            with open(output_file, "w") as f:
                f.write(report_json)
        else:
            print(report_json)


def write_report_to_artifacts(
    report: dict[str, Any],
    config: ClusterbusterConfig,
) -> None:
    """Write report JSON and formatted output to the artifact directory."""
    if not config.artifactdir:
        return

    raw_path = os.path.join(config.artifactdir, "clusterbuster-report.json")
    try:
        with open(raw_path, "w") as f:
            json.dump(report, f, indent=2)
        if config.compress_report:
            gz_path = raw_path + ".gz"
            with open(raw_path, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
                f_out.writelines(f_in)
            os.remove(raw_path)
    except OSError as exc:
        _LOG.warning("Failed to write report to %s: %s", raw_path, exc)

    if config.report_format not in ("raw", "none"):
        fmt_path = os.path.join(config.artifactdir, f"report.{config.report_format}")
        print_report(report, config, output_file=fmt_path)


# -- Internal helpers -------------------------------------------------------

def _build_options(
    config: ClusterbusterConfig,
    workload_options: dict[str, Any],
) -> dict[str, Any]:
    """Build the options sub-dict of the report."""
    opts: dict[str, Any] = {
        "basename": config.basename,
        "containers_per_pod": config.containers_per_pod,
        "deployments_per_namespace": config.deps_per_namespace,
        "namespaces": config.namespaces,
        "bytes_transfer": config.bytes_transfer,
        "bytes_transfer_max": config.bytes_transfer_max,
        "workload_run_time": config.workload_run_time,
        "workload_run_time_max": config.workload_run_time_max,
        "headless_services": config.headless_services,
        "drop_cache": config.drop_node_cache,
        "always_drop_cache": config.drop_all_node_cache,
        "pin_nodes": config.pin_nodes,
        "volumes": config.volumes,
        "secrets": config.secrets,
        "replicas": config.replicas,
        "container_image": config.container_image,
        "clusterbuster_base_image": config.clusterbuster_base_image,
        "sync_pod_image": config.sync_pod_image_override or config.clusterbuster_base_image,
        "node_selector": config.node_selector,
        "resource_requests": {
            k: v for s in config.resource_requests for k, v in [s.split("=", 1)]
        } if config.resource_requests else {},
        "resource_limits": {
            k: v for s in config.resource_limits for k, v in [s.split("=", 1)]
        } if config.resource_limits else {},
        "runtime_classes": config.runtime_classes,
        "target_data_rate": config.target_data_rate,
    }
    if config.liveness_probe_frequency:
        opts["liveness_probe"] = {
            "frequency": config.liveness_probe_frequency,
            "sleep_time": config.liveness_probe_sleep_time,
        }
    if workload_options:
        opts["workload_options"] = workload_options
    return opts


def _get_workload_instance(config: ClusterbusterConfig) -> Any:
    """Get workload instance, or None if not available."""
    try:
        from .workload_registry import get_workload
        return get_workload(config.resolved_workload)
    except (ValueError, KeyError, ImportError):
        return None


def _get_k8s_version(cluster: ClusterInterface) -> dict[str, Any]:
    """Query Kubernetes version info."""
    result = cluster.run(
        "version", "-ojson",
        dry_run_skip=False, filter_output=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            pass
    return {}


def _get_nodes(cluster: ClusterInterface, config: ClusterbusterConfig) -> Any:
    """Get full node JSON for the report (matches bash: oc get nodes -ojson)."""
    return cluster.get_json("node")


def _get_api_objects(cluster: ClusterInterface, config: ClusterbusterConfig) -> list[Any]:
    """Get labeled workload objects (matches bash: oc get all -A -l ...-ojson .items)."""
    label = f"{config.basename}-id={config.uuid}"
    data = cluster.get_json("all", "-A", f"-l{label}")
    return data.get("items", [])


def _get_csvs(cluster: ClusterInterface) -> list[Any]:
    """List ClusterServiceVersions (OLM operators)."""
    data = cluster.get_json("csv", "-A")
    items = data.get("items", [])
    return [
        {
            "name": item.get("metadata", {}).get("name", ""),
            "namespace": item.get("metadata", {}).get("namespace", ""),
            "display": item.get("spec", {}).get("displayName", ""),
            "version": item.get("spec", {}).get("version", ""),
        }
        for item in items
    ]
