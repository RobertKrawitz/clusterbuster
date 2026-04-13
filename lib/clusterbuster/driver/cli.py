# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""CLI parsing: argv processing, option dispatch, YAML job files, help text."""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, NoReturn

import yaml

from clusterbuster.ci.compat import bool_str, parse_option, parse_size

from .config import ClusterbusterConfig, ClusterbusterConfigBuilder

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Boolean helper
# ---------------------------------------------------------------------------


def _bool(value: str) -> bool:
    """Convert option value to Python bool using bash ``bool`` semantics."""
    return bool_str(value) == "1"


# ---------------------------------------------------------------------------
# Option helper functions (non-trivial dispatch targets)
# ---------------------------------------------------------------------------


def _set_workload_bytes(builder: ClusterbusterConfigBuilder, value: str) -> None:
    """Comma-separated min[,max] via parse_size; auto-swap if inverted."""
    parts = value.split(",", 1)
    lo = int(parse_size(parts[0]))
    hi = int(parse_size(parts[1])) if len(parts) > 1 else 0
    if hi and lo > hi:
        lo, hi = hi, lo
    builder.bytes_transfer = lo
    builder.bytes_transfer_max = hi


def _set_runtime(builder: ClusterbusterConfigBuilder, value: str) -> None:
    """Comma-separated min[,max]; auto-swap if inverted."""
    parts = value.split(",", 1)
    lo = int(parts[0])
    hi = int(parts[1]) if len(parts) > 1 else 0
    if hi and lo > hi:
        lo, hi = hi, lo
    builder.workload_run_time = lo
    builder.workload_run_time_max = hi


def _process_pin_node(builder: ClusterbusterConfigBuilder, value: str) -> None:
    """Parse ``class=node`` mapping; bare value sets ``default`` class."""
    if "=" in value:
        classes, _, node = value.partition("=")
        for cls in classes.split(","):
            cls = cls.strip()
            if cls:
                builder.pin_nodes[cls] = node
    else:
        builder.pin_nodes["default"] = value


def _process_interface(builder: ClusterbusterConfigBuilder, value: str) -> None:
    """Parse ``class=interface`` mapping; same pattern as pin_node."""
    if "=" in value:
        classes, _, iface = value.partition("=")
        for cls in classes.split(","):
            cls = cls.strip()
            if cls:
                builder.net_interfaces[cls] = iface
    else:
        builder.net_interfaces["default"] = value


def _process_runtimeclass(builder: ClusterbusterConfigBuilder, value: str) -> None:
    """Handle runtime class: ``vm`` sets deployment_type, ``pod`` clears, else mapping."""
    if value.lower() == "vm":
        builder.deployment_type = "vm"
        builder.runtime_class = "vm"
    elif value.lower() == "pod":
        builder.runtime_class = ""
    elif "=" in value:
        classes, _, rt = value.partition("=")
        for cls in classes.split(","):
            cls = cls.strip()
            if cls:
                builder.runtime_classes[cls] = rt
        if not classes.strip():
            builder.runtime_class = rt
    else:
        builder.runtime_class = value


def _set_metrics_file(
    builder: ClusterbusterConfigBuilder, value: str, libdir: str = ""
) -> None:
    """Map ``default``/``1`` to library path; ``0``/``none`` to empty."""
    v = value.strip().lower()
    if v in ("default", "1"):
        default_name = "metrics-default.yaml"
        if libdir:
            resolved = os.path.join(libdir, default_name)
        else:
            resolved = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", "..", default_name,
            )
        builder.metrics_file = os.path.normpath(resolved)
    elif v in ("", "0", "none"):
        builder.metrics_file = ""
    else:
        builder.metrics_file = value


def _artifact_dirname(value: str) -> str:
    """``%T`` substitution with strftime timestamp."""
    if "%T" in value:
        return value.replace("%T", time.strftime("%Y_%m_%dT%H_%M_%S%z"))
    return value


def _parse_affinity(value: str) -> int:
    """Map affinity value: ``1``/empty -> 1, ``2``/``anti`` -> 2, else 0."""
    v = value.strip().lower()
    if v in ("1", ""):
        return 1
    if v in ("2", "anti"):
        return 2
    return 0


def _parse_antiaffinity(value: str) -> int:
    """Map anti-affinity value: ``1``/empty -> 2, else 0."""
    v = value.strip().lower()
    if v in ("1", ""):
        return 2
    return 0


_EXTERNALSYNC_RE = re.compile(r"^([A-Za-z0-9._-]+):(\d+)$")


def _parse_externalsync(builder: ClusterbusterConfigBuilder, value: str) -> None:
    """Parse ``host:port`` for external sync; validates port 1-65535."""
    m = _EXTERNALSYNC_RE.match(value)
    if not m:
        raise SystemExit(f"Invalid externalsync value {value!r}: expected host:port")
    host = m.group(1)
    port = int(m.group(2))
    if port < 1 or port > 65535:
        raise SystemExit(f"Invalid externalsync port {port}: must be 1-65535")
    builder.sync_host = host
    builder.sync_port = port
    builder.sync_start = True


def _set_sync(builder: ClusterbusterConfigBuilder, value: str) -> None:
    """Set or toggle sync_start.

    Explicit true/false/yes/no values are honoured directly.
    The values ``1`` and ``0`` (produced by ``parse_option`` for bare flags
    and ``--no-`` prefixed flags respectively) toggle, preserving the
    original bash behaviour where ``--sync`` toggles sync on/off.
    """
    v = value.strip().lower()
    if v in ("true", "yes", "y", "t"):
        builder.sync_start = True
    elif v in ("false", "no", "n", "f"):
        builder.sync_start = False
    else:
        builder.sync_start = not builder.sync_start


def _inject_error(builder: ClusterbusterConfigBuilder, value: str) -> None:
    if "=" in value:
        key, _, val = value.partition("=")
        builder.injected_errors[key] = val
    else:
        builder.injected_errors[value] = "1"
    _LOG.warning("Error injection: %s", value)


def _register_debug(builder: ClusterbusterConfigBuilder, value: str) -> None:
    if "=" in value:
        conds, _, opts = value.partition("=")
    else:
        conds = value
        opts = "1"
    for cond in conds.split(","):
        cond = cond.strip()
        if cond:
            builder.debug_conditions[cond] = opts


def _set_cleanup(builder: ClusterbusterConfigBuilder, value: str) -> None:
    b = _bool(value)
    builder.cleanup = b
    if not b:
        builder.cleanup_always = False


def _set_cleanup_always(builder: ClusterbusterConfigBuilder, value: str) -> None:
    b = _bool(value)
    builder.cleanup_always = b
    if b:
        builder.cleanup = True


# ---------------------------------------------------------------------------
# Option dispatch table
# ---------------------------------------------------------------------------

# Maps noptname1 -> handler(builder, optvalue)
# Simple setattr entries use lambdas; complex logic uses the helpers above.
_OPTION_DISPATCH: dict[str, object] = {
    # Help / shell
    "help": lambda b, v: _print_help_and_exit(),
    "helpall": lambda b, v: _print_extended_help_and_exit(),
    "helpeverything": lambda b, v: _print_extended_help_and_exit(),
    "helpoptions": lambda b, v: _print_help_and_exit(),
    "forceabort": lambda b, v: _LOG.debug("forceabort (set -e) ignored in Python"),
    # Verbosity / run control
    "verbose": lambda b, v: setattr(b, "verbose", _bool(v)),
    "doit": lambda b, v: setattr(b, "doit", _bool(v)),
    "quiet": lambda b, v: setattr(b, "verbose", not _bool(v)),
    "preservetmpdir": lambda b, v: setattr(b, "preserve_tmpdir", _bool(v)),
    # Reporting / metrics
    "artifactdir": lambda b, v: setattr(b, "artifactdir", _artifact_dirname(v)),
    "metrics": lambda b, v: _set_metrics_file(b, v),
    "metricsfile": lambda b, v: _set_metrics_file(b, v),
    "metricsepoch": lambda b, v: setattr(b, "metrics_epoch", int(v)),
    "metricsinterval": lambda b, v: setattr(b, "metrics_interval", int(v)),
    "reportformat": lambda b, v: setattr(b, "report_format", v),
    "jsonreport": lambda b, v: setattr(b, "report_format", "json"),
    "rawreport": lambda b, v: setattr(b, "report_format", "raw"),
    "report": lambda b, v: setattr(b, "report_format", v if v and v != "1" else "summary"),
    "verbosereport": lambda b, v: setattr(b, "report_format", "verbose"),
    "reportobjectcreation": lambda b, v: setattr(b, "report_object_creation", _bool(v)),
    "prometheussnapshot": lambda b, v: setattr(b, "take_prometheus_snapshot", _bool(v)),
    "predelay": lambda b, v: setattr(b, "predelay", int(v)),
    "postdelay": lambda b, v: setattr(b, "postdelay", int(v)),
    "stepinterval": lambda b, v: setattr(b, "workload_step_interval", int(v)),
    "timeout": lambda b, v: setattr(b, "timeout", int(v)),
    "failurestatus": lambda b, v: setattr(b, "failure_status", v),
    "parallellogretrieval": lambda b, v: setattr(b, "parallel_log_retrieval", int(v)),
    "parallellog": lambda b, v: setattr(b, "parallel_log_retrieval", int(v)),
    "retrievesuccessfullogs": lambda b, v: setattr(b, "retrieve_successful_logs", _bool(v)),
    "retrievesuc": lambda b, v: setattr(b, "retrieve_successful_logs", _bool(v)),
    "logsuc": lambda b, v: setattr(b, "retrieve_successful_logs", _bool(v)),
    "logsuccessful": lambda b, v: setattr(b, "retrieve_successful_logs", _bool(v)),
    "compressreport": lambda b, v: setattr(b, "compress_report", _bool(v)),
    "compress": lambda b, v: setattr(b, "compress_report", _bool(v)),
    # Job / identity
    "jobname": lambda b, v: setattr(b, "job_name", v),
    "workload": lambda b, v: setattr(b, "requested_workload", v),
    "basename": lambda b, v: setattr(b, "basename", v),
    "arch": lambda b, v: setattr(b, "arch", v),
    "createnamespacesonly": lambda b, v: setattr(b, "create_namespaces_only", _bool(v)),
    "watchdogtimeout": lambda b, v: setattr(b, "sync_watchdog_timeout", int(parse_size(v))),
    # Object / image / deployment
    "workdir": lambda b, v: setattr(b, "common_workdir", v),
    "configmapfile": lambda b, v: b.configmap_files.append(v),
    "containerimage": lambda b, v: setattr(b, "container_image", v),
    "clusterbusterbaseimage": lambda b, v: setattr(b, "clusterbuster_base_image", v),
    "syncpodimage": lambda b, v: setattr(b, "sync_pod_image_override", v),
    "containers": lambda b, v: setattr(b, "containers_per_pod", int(v)),
    "containersperpod": lambda b, v: setattr(b, "containers_per_pod", int(v)),
    "deploymenttype": lambda b, v: setattr(b, "deployment_type", v),
    "deployments": lambda b, v: setattr(b, "deps_per_namespace", int(v)),
    "depspernamespace": lambda b, v: setattr(b, "deps_per_namespace", int(v)),
    "depspername": lambda b, v: setattr(b, "deps_per_namespace", int(v)),
    "exitatend": lambda b, v: setattr(b, "exit_at_end", _bool(v)),
    "imagepullpolicy": lambda b, v: setattr(b, "image_pull_policy", v),
    "namespaces": lambda b, v: setattr(b, "namespaces", int(v)),
    "nodeselector": lambda b, v: setattr(b, "node_selector", v),
    "volume": lambda b, v: b.volumes.append(v),
    "processes": lambda b, v: setattr(b, "processes_per_pod", int(v)),
    "processesperpod": lambda b, v: setattr(b, "processes_per_pod", int(v)),
    "jobfile": lambda b, v: process_job_file(b, v),
    "pinnode": lambda b, v: _process_pin_node(b, v),
    "interface": lambda b, v: _process_interface(b, v),
    "replicas": lambda b, v: setattr(b, "replicas", int(v)),
    "limit": lambda b, v: b.resource_limits.append(v),
    "limits": lambda b, v: b.resource_limits.append(v),
    "request": lambda b, v: b.resource_requests.append(v),
    "requests": lambda b, v: b.resource_requests.append(v),
    "kata": lambda b, v: _process_runtimeclass(b, "kata"),
    "podannotation": lambda b, v: b.pod_annotations.append(v),
    "label": lambda b, v: b.pod_labels.append(v),
    "labels": lambda b, v: b.pod_labels.append(v),
    "runtimeclass": lambda b, v: _process_runtimeclass(b, v),
    "uuid": lambda b, v: setattr(b, "uuid", v),
    "secrets": lambda b, v: setattr(b, "secrets", int(v)),
    "workloadruntime": lambda b, v: _set_runtime(b, v),
    "workloadsize": lambda b, v: _set_workload_bytes(b, v),
    "targetdatarate": lambda b, v: setattr(b, "target_data_rate", int(parse_size(v))),
    "tolerate": lambda b, v: b.tolerations.append(v),
    "toleration": lambda b, v: b.tolerations.append(v),
    # Caching / services / probes / privilege / scheduler / affinity
    "dropcache": lambda b, v: setattr(b, "drop_node_cache", _bool(v)),
    "dropallcache": lambda b, v: setattr(b, "drop_all_node_cache", _bool(v)),
    "headlessservices": lambda b, v: setattr(b, "headless_services", _bool(v)),
    "virtiofsdwriteback": lambda b, v: setattr(b, "virtiofsd_writeback", _bool(v)),
    "virtiofsddirect": lambda b, v: setattr(b, "virtiofsd_direct", _bool(v)),
    "virtiofsdthreadpoolsize": lambda b, v: setattr(b, "virtiofsd_threadpoolsize", int(v)),
    "virtiofsdthread": lambda b, v: setattr(b, "virtiofsd_threadpoolsize", int(v)),
    "livenessprobeinterval": lambda b, v: setattr(b, "liveness_probe_frequency", int(v)),
    "livenessprobeint": lambda b, v: setattr(b, "liveness_probe_frequency", int(v)),
    "livenessprobesleeptime": lambda b, v: setattr(b, "liveness_probe_sleep_time", int(v)),
    "livenessprobesleep": lambda b, v: setattr(b, "liveness_probe_sleep_time", int(v)),
    "privileged": lambda b, v: setattr(b, "create_pods_privileged", _bool(v)),
    "syncinfirstnamespace": lambda b, v: setattr(b, "sync_in_first_namespace", _bool(v)),
    "syncinfirst": lambda b, v: setattr(b, "sync_in_first_namespace", _bool(v)),
    "scheduler": lambda b, v: setattr(b, "scheduler", v),
    "affinity": lambda b, v: setattr(b, "affinity", _parse_affinity(v)),
    "antiaffinity": lambda b, v: setattr(b, "affinity", _parse_antiaffinity(v)),
    "syncaffinity": lambda b, v: setattr(b, "sync_affinity", _parse_affinity(v)),
    "syncantiaffinity": lambda b, v: setattr(b, "sync_affinity", _parse_antiaffinity(v)),
    # VM
    "vmcores": lambda b, v: setattr(b, "vm_cores", int(v)),
    "vmsockets": lambda b, v: setattr(b, "vm_sockets", int(v)),
    "vmthreads": lambda b, v: setattr(b, "vm_threads", int(v)),
    "vmmemory": lambda b, v: setattr(b, "vm_memory", v),
    "vmgraceperiod": lambda b, v: setattr(b, "vm_grace_period", int(v)),
    "vmimage": lambda b, v: setattr(b, "vm_image", v),
    "vmmigrate": lambda b, v: setattr(b, "vm_evict_migrate", _bool(v)),
    "vmrunascontainer": lambda b, v: setattr(b, "vm_run_as_container", _bool(v)),
    "vmuser": lambda b, v: setattr(b, "vm_user", v),
    "vmpassword": lambda b, v: setattr(b, "vm_password", v),
    "vmrunasroot": lambda b, v: setattr(b, "vm_run_as_root", _bool(v)),
    "vmsshkey": lambda b, v: setattr(b, "vm_ssh_keyfile", v),
    "vmsshkeyfile": lambda b, v: setattr(b, "vm_ssh_keyfile", v),
    "vmstart": lambda b, v: setattr(b, "vm_start_running", _bool(v)),
    "vmstartrunning": lambda b, v: setattr(b, "vm_start_running", _bool(v)),
    "vmrunstrategy": lambda b, v: setattr(b, "vm_run_strategy", v),
    "vmblockmultiqueue": lambda b, v: setattr(b, "vm_block_multiqueue", int(v)),
    "vmblockmultiq": lambda b, v: setattr(b, "vm_block_multiqueue", int(v)),
    # Object creation tuning
    "objectspercall": lambda b, v: setattr(b, "objs_per_call", int(v)),
    "objspercall": lambda b, v: setattr(b, "objs_per_call", int(v)),
    "parallel": lambda b, v: setattr(b, "parallel", int(v)),
    "sleep": lambda b, v: setattr(b, "sleeptime", float(v)),
    "podprefix": lambda b, v: setattr(b, "pod_prefix", f"{v}-" if v else ""),
    "firstdeployment": lambda b, v: setattr(b, "first_deployment", int(v)),
    "parallelconfigmaps": lambda b, v: setattr(b, "parallel_configmaps", int(v)),
    "parallelsecrets": lambda b, v: setattr(b, "parallel_secrets", int(v)),
    "parallelnamespaces": lambda b, v: setattr(b, "parallel_namespaces", int(v)),
    "paralleldeployments": lambda b, v: setattr(b, "parallel_deployments", int(v)),
    "objectspercallconfigmaps": lambda b, v: setattr(b, "objs_per_call_configmaps", int(v)),
    "objspercallconfigmaps": lambda b, v: setattr(b, "objs_per_call_configmaps", int(v)),
    "objectspercallsecrets": lambda b, v: setattr(b, "objs_per_call_secrets", int(v)),
    "objspercallsecrets": lambda b, v: setattr(b, "objs_per_call_secrets", int(v)),
    "objectspercallnamespaces": lambda b, v: setattr(b, "objs_per_call_namespaces", int(v)),
    "objspercallnamespaces": lambda b, v: setattr(b, "objs_per_call_namespaces", int(v)),
    "objectspercalldeployments": lambda b, v: setattr(b, "objs_per_call_deployments", int(v)),
    "objspercalldeployments": lambda b, v: setattr(b, "objs_per_call_deployments", int(v)),
    "sleepbetweenconfigmaps": lambda b, v: setattr(b, "sleep_between_configmaps", float(v)),
    "sleepbetweensecrets": lambda b, v: setattr(b, "sleep_between_secrets", float(v)),
    "sleepbetweennamespaces": lambda b, v: setattr(b, "sleep_between_namespaces", float(v)),
    "sleepbetweendeployments": lambda b, v: setattr(b, "sleep_between_deployments", float(v)),
    "waitsecrets": lambda b, v: setattr(b, "wait_for_secrets", _bool(v)),
    "scalens": lambda b, v: setattr(b, "scale_ns", _bool(v)),
    "scaledeployments": lambda b, v: setattr(b, "scale_deployments", _bool(v)),
    "precleanup": lambda b, v: setattr(b, "precleanup", _bool(v)),
    "cleanup": lambda b, v: _set_cleanup(b, v),
    "cleanupalways": lambda b, v: _set_cleanup_always(b, v),
    "removenamespaces": lambda b, v: setattr(b, "remove_namespaces", 1 if _bool(v) else 0),
    "removenamespace": lambda b, v: setattr(b, "remove_namespaces", 1 if _bool(v) else 0),
    "baseoffset": lambda b, v: setattr(b, "baseoffset", int(v)),
    # Sync / misc
    "sync": lambda b, v: _set_sync(b, v),
    "syncstart": lambda b, v: _set_sync(b, v),
    "waitforever": lambda b, v: setattr(b, "wait_forever", _bool(v)),
    "forcenometrics": lambda b, v: setattr(b, "metrics_support", 0 if _bool(v) else -1),
    "podstarttimeout": lambda b, v: setattr(b, "pod_start_timeout", int(v)),
    "podstarttime": lambda b, v: setattr(b, "pod_start_timeout", int(v)),
    "externalsync": lambda b, v: _parse_externalsync(b, v),
    "injecterror": lambda b, v: _inject_error(b, v),
    "debug": lambda b, v: _register_debug(b, v),
    "forcecleanupiknowthisisdangerous": lambda b, v: setattr(
        b, "force_cleanup_timeout", v if v and v != "1" else "600"
    ),
}


def _find_dispatch(noptname1: str) -> object | None:
    """Look up dispatch handler by exact match."""
    return _OPTION_DISPATCH.get(noptname1)


# ---------------------------------------------------------------------------
# process_option / process_job_file / parse_argv
# ---------------------------------------------------------------------------


def process_option(builder: ClusterbusterConfigBuilder, option_str: str) -> None:
    """Process a single ``name=value`` or bare-flag option string."""
    parsed = parse_option(option_str)
    builder.processed_options.append(f"--{option_str}")

    handler = _find_dispatch(parsed.noptname1)
    if handler is not None:
        try:
            handler(builder, parsed.optvalue)
        except ValueError as exc:
            raise SystemExit(
                f"Invalid value for --{parsed.noptname}: {exc}"
            ) from exc
    else:
        builder.unknown_opts.append(option_str)


def process_job_file(builder: ClusterbusterConfigBuilder, path: str) -> None:
    """Load a job file and apply its options to the builder.

    Tries YAML first.  Falls back to the legacy line-oriented format
    (bare flags, ``key=value``, ``#`` comments, backslash continuation)
    when the file does not parse as valid YAML or is not a mapping.
    """
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"Job file {path} cannot be read")

    text = p.read_text(encoding="utf-8")

    try:
        doc = yaml.safe_load(text)
        if isinstance(doc, dict):
            _apply_yaml_options(builder, doc, path)
            return
        if isinstance(doc, list):
            raise SystemExit(
                f"Job file {path}: expected a YAML mapping, "
                f"got {type(doc).__name__}"
            )
    except yaml.YAMLError:
        pass

    _apply_legacy_options(builder, text)


def _apply_yaml_options(
    builder: ClusterbusterConfigBuilder, doc: dict, path: str,
) -> None:
    """Apply options from a parsed YAML document."""
    options = doc.get("options", doc)
    if not isinstance(options, dict):
        raise SystemExit(f"Job file {path}: 'options' must be a mapping")
    for key, value in options.items():
        if value is None:
            process_option(builder, str(key))
        elif isinstance(value, bool):
            process_option(builder, f"{key}={'1' if value else '0'}")
        elif isinstance(value, (str, int, float)):
            process_option(builder, f"{key}={value}")
        else:
            raise SystemExit(
                f"Job file {path}: option '{key}' has unsupported type "
                f"{type(value).__name__}; only scalar values are allowed"
            )


def _apply_legacy_options(
    builder: ClusterbusterConfigBuilder, text: str,
) -> None:
    """Parse the legacy line-oriented option file format.

    Matches the bash ``process_job_file`` behavior: strip comments from
    first ``#``, support backslash continuation, skip blank lines,
    pass each remaining line to ``process_option``.
    """
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        while line.endswith("\\") and i + 1 < len(lines):
            line = line[:-1]
            i += 1
            next_line = lines[i]
            if not next_line:
                break
            line += next_line
        i += 1
        comment_pos = line.find("#")
        if comment_pos >= 0:
            line = line[:comment_pos]
        line = line.lstrip()
        if not line:
            continue
        process_option(builder, line)


def parse_argv(argv: list[str] | None = None) -> ClusterbusterConfigBuilder:
    """Parse command-line arguments into a :class:`ClusterbusterConfigBuilder`.

    Mirrors the bash ``getopts`` loop with the same short-option mapping.
    """
    if argv is None:
        argv = sys.argv[1:]

    builder = ClusterbusterConfigBuilder()
    args = list(argv)
    i = 0

    while i < len(args):
        arg = args[i]

        if arg == "--":
            builder.extra_args.extend(args[i + 1:])
            break

        if arg.startswith("--"):
            process_option(builder, arg[2:])
            i += 1
            continue

        if arg.startswith("-") and len(arg) > 1:
            j = 1
            while j < len(arg):
                ch = arg[j]
                if ch in _SHORT_OPTS_WITH_ARG:
                    optarg = arg[j + 1:] if j + 1 < len(arg) else ""
                    if not optarg:
                        i += 1
                        if i >= len(args):
                            raise SystemExit(f"Option -{ch} requires an argument")
                        optarg = args[i]
                    _SHORT_OPTS_WITH_ARG[ch](builder, optarg)
                    break
                elif ch in _SHORT_OPTS_NO_ARG:
                    _SHORT_OPTS_NO_ARG[ch](builder)
                    j += 1
                else:
                    raise SystemExit(f"Unknown option -{ch}")
            i += 1
            continue

        builder.extra_args.append(arg)
        i += 1

    return builder


# Short options that take an argument
_SHORT_OPTS_WITH_ARG: dict[str, object] = {
    "B": lambda b, v: process_option(b, f"basename={v}"),
    "f": lambda b, v: process_job_file(b, v),
    "o": lambda b, v: process_option(b, f"reportformat={v}"),
    "P": lambda b, v: process_option(b, f"workload={v}"),
    "w": lambda b, v: process_option(b, f"workload={v}"),
}

# Short options that are flags (no argument)
_SHORT_OPTS_NO_ARG: dict[str, object] = {
    "E": lambda b: process_option(b, "exitatend=0"),
    "e": lambda b: process_option(b, "exitatend=1"),
    "h": lambda b: _print_help_and_exit(),
    "H": lambda b: _print_extended_help_and_exit(),
    "N": lambda b: process_option(b, "createnamespacesonly=1"),
    "n": lambda b: process_option(b, "doit=0"),
    "Q": lambda b: process_option(b, "reportobjectcreation=0"),
    "q": lambda b: process_option(b, "verbose=0"),
    "v": lambda b: process_option(b, "verbose=1"),
    "z": lambda b: process_option(b, "compressreport=1"),
}


# ---------------------------------------------------------------------------
# Display-name mapping for human-readable -h output
# ---------------------------------------------------------------------------

_OPTION_DISPLAY_NAMES: dict[str, str] = {
    "antiaffinity": "anti-affinity",
    "artifactdir": "artifact-dir",
    "cleanupalways": "cleanup-always",
    "clusterbusterbaseimage": "clusterbuster-base-image",
    "compressreport": "compress-report",
    "containerimage": "container-image",
    "containersperpod": "containers-per-pod",
    "createnamespacesonly": "create-namespaces-only",
    "deploymenttype": "deployment-type",
    "depspernamespace": "deps-per-namespace",
    "dropcache": "drop-cache",
    "dropallcache": "drop-all-cache",
    "exitatend": "exit-at-end",
    "externalsync": "external-sync",
    "failurestatus": "failure-status",
    "firstdeployment": "first-deployment",
    "forceabort": "force-abort",
    "forcecleanupiknowthisisdangerous": "force-cleanup-i-know-this-is-dangerous",
    "forcenometrics": "force-no-metrics",
    "headlessservices": "headless-services",
    "imagepullpolicy": "image-pull-policy",
    "injecterror": "inject-error",
    "livenessprobeinterval": "liveness-probe-interval",
    "livenessprobesleeptime": "liveness-probe-sleep-time",
    "metricsepoch": "metrics-epoch",
    "metricsfile": "metrics-file",
    "metricsinterval": "metrics-interval",
    "nodeselector": "node-selector",
    "objectspercall": "objects-per-call",
    "objectspercallconfigmaps": "objects-per-call-configmaps",
    "objectspercalldeployments": "objects-per-call-deployments",
    "objectspercallnamespaces": "objects-per-call-namespaces",
    "objectspercallsecrets": "objects-per-call-secrets",
    "parallelconfigmaps": "parallel-configmaps",
    "paralleldeployments": "parallel-deployments",
    "parallellogretrieval": "parallel-log-retrieval",
    "parallelnamespaces": "parallel-namespaces",
    "parallelsecrets": "parallel-secrets",
    "pinnode": "pin-node",
    "podannotation": "pod-annotation",
    "podprefix": "pod-prefix",
    "podstarttimeout": "pod-start-timeout",
    "preservetmpdir": "preserve-tmpdir",
    "privileged": "privileged-pods",
    "processesperpod": "processes-per-pod",
    "prometheussnapshot": "prometheus-snapshot",
    "removenamespaces": "remove-namespaces",
    "reportformat": "report-format",
    "reportobjectcreation": "report-object-creation",
    "retrievesuccessfullogs": "retrieve-successful-logs",
    "runtimeclass": "runtime-class",
    "scaledeployments": "scale-deployments",
    "scalens": "scale-ns",
    "configmapfile": "configmap-file",
    "sleepbetweenconfigmaps": "sleep-between-configmaps",
    "sleepbetweendeployments": "sleep-between-deployments",
    "sleepbetweennamespaces": "sleep-between-namespaces",
    "sleepbetweensecrets": "sleep-between-secrets",
    "stepinterval": "step-interval",
    "syncaffinity": "sync-affinity",
    "syncantiaffinity": "sync-anti-affinity",
    "syncinfirstnamespace": "sync-in-first-namespace",
    "syncpodimage": "sync-pod-image",
    "targetdatarate": "target-data-rate",
    "vmblockmultiqueue": "vm-block-multiqueue",
    "vmcores": "vm-cores",
    "vmgraceperiod": "vm-grace-period",
    "vmimage": "vm-image",
    "vmmemory": "vm-memory",
    "vmmigrate": "vm-migrate",
    "vmpassword": "vm-password",
    "vmrunascontainer": "vm-run-as-container",
    "vmrunasroot": "vm-run-as-root",
    "vmrunstrategy": "vm-run-strategy",
    "vmsockets": "vm-sockets",
    "vmsshkey": "vm-ssh-key",
    "vmstart": "vm-start",
    "vmthreads": "vm-threads",
    "vmuser": "vm-user",
    "virtiofsdwriteback": "virtiofsd-writeback",
    "virtiofsddirect": "virtiofsd-direct",
    "virtiofsdthreadpoolsize": "virtiofsd-threadpool-size",
    "waitforever": "wait-forever",
    "waitsecrets": "wait-secrets",
    "watchdogtimeout": "watchdog-timeout",
    "workloadruntime": "workload-runtime",
    "workloadsize": "workload-size",
}

_OPTION_ALIASES: set[str] = {
    "compress", "depspername", "help",
    "helpall", "helpeverything", "helpoptions",
    "jsonreport", "labels", "limits",
    "livenessprobeint", "livenessprobesleep",
    "logsuccessful", "logsuc", "objspercall",
    "objspercallconfigmaps", "objspercalldeployments",
    "objspercallnamespaces", "objspercallsecrets",
    "parallellog", "podstarttime", "rawreport",
    "removenamespace", "requests", "retrievesuc",
    "syncinfirst", "syncstart", "toleration",
    "vmblockmultiq", "vmsshkeyfile",
    "vmstartrunning", "virtiofsdthread", "verbosereport",
}


def _display_name(key: str) -> str:
    """Return the human-readable display name for a normalized option key."""
    return _OPTION_DISPLAY_NAMES.get(key, key)


# ---------------------------------------------------------------------------
# Help text
# ---------------------------------------------------------------------------

_USAGE_TEXT = """\
ClusterBuster is a tool to permit you to load a configurable workload
onto an OpenShift cluster.  ClusterBuster focuses primarily on workload
scalability, including synchronization of multi-instance workloads.

Usage: clusterbuster [options] [extra args]
    Help:
       -h              Print basic help information.
       -H              Print extended help.

    Options:
       -B basename     Base name of pods.  Default is
                       $CLUSTERBUSTER_DEFAULT_BASENAME if defined or
                       otherwise 'clusterbuster'.
                       All objects are labeled with this name.
       -E              Don't exit after all operations are complete.
       -e              Exit after all operations are complete (default).
       -f jobfile      Job file containing settings.
                       Continuations may be escaped with a trailing
                       backslash.
                       A number of examples are provided in the
                       examples/clusterbuster directory.
       -n              Print what would be done without doing it
       -o              Specify report format, as --report-format
       -q              Do not print verbose log messages (default)
       -Q              Don't report creation of individual objects (default
                       report them)
       -v              Print verbose log messages.
       -w workload     workload (mandatory)
       -z              Compress the report with gzip
       --opt[=val]     Set the specified option.
                       Use clusterbuster -H to list the available options.
"""

_EXTENDED_HELP_TEXT = """\

Extended Options:
    General Options (short equivalents):
       --doit=<1,0>     Run the command or not (default 1) (inverse of -n)
       --create-namespaces-only
                        Only create namespaces.
       --jobname=name   Name of the job, for logging purposes.
                        Defaults to the workload name
       --workload=type  Specify the workload (-P) (mandatory)
       --basename=name  Specify the base name for any namespaces (-B)
       --namespaces=N   Number of namespaces
       --jobfile=jobfile
                        Process job file (-f)
       --sync           Synchronize start of workload instances (default yes)
       --precleanup     Clean up any prior objects
       --cleanup        Clean up generated objects unless there's a failure
       --cleanup-always Clean up generated objects even if there is a failure
       --wait-forever   Don't exit if sync pod dies
       --remove-namespaces=<1,0>
                        Remove namespaces when cleaning up objects.  Only
                        applies when using clusterbuster-created namespaces.
                        By default, namespaces are removed only if no
                        namespaces previously existed.
       --predelay=N     Delay for the specified time after workload
                        starts before it starts running.
       --postdelay=N    Delay for the specified time after workload
                        completes.
       --step-interval=N
                        Delay the specified time between steps of the workload.
       --timeout=N      Time out reporting after N seconds
       --report-object-creation=<1,0>
                        Report creation of individual objects (default 1)
                        (inverse of -Q)
       --uuid=<uuid>    Use the specified UUID for the run.  Default is to
                        generate a random-based UUID.
       --exit-at-end    Exit upon completion of workload (-e/-E)
       --verbose        Print verbose log messages (-v)
       --arch=<architecture>
                        Use the specified architecture.  Default the
                        architecture of this platform.
       --container-image=<image>
                        Container image for workload pods/VMs only (not the sync pod).
       --clusterbuster-base-image=<image>
                        Override the sync pod base image and initial worker default.
                        Normally not needed (default quay.io/.../clusterbuster-base).
       --sync-pod-image=<image>
                        Force the sync pod image. Normally not needed; use only for
                        private mirrors etc. (overrides CLUSTERBUSTER_SYNC_IMAGE and base).
       --watchdog-timeout=<timeout>
                        Set watchdog timer for all pods/VMs.  After pods/VMs
                        start, if the watchdog is not reset within the
                        timeout, the run is aborted.  Currently not set
                        (timeout = 0); if set to a value greater than
                        zero, that is the watchdog period.
       --quiet          Do not print verbose log messages (-q)
       --compress-report=<0,1>
                        Compress the report (-z)

    Reporting Options:
       --report=<format>
                        Print report in specified format.  Meaning of
                        report types is by type.  Default is summary
                        if not specified; if that is not reported,
                        raw format will be used.
                        - none
                        - summary
                        - json
                        - raw
                        - verbose
       --artifact-dir=<dir>
                        Save artifacts to <dir>.  <dir> can have embedded
                        format codes:
                        %n              Job name
                        %s              Timestamp of run
                        %w              Workload
                        %{var}          Variable's value is substituted
                        %{var[item]}    to reference an array variable
                        %{var:-default} to use a default value if not set
       --prometheus-snapshot
                        Take a Prometheus snapshot and save to the
                        artifacts directory
       --metrics[=<file>]
                        benchmark-runner compatible metrics file
                        for metrics extraction.  If empty or 'none',
                        no metrics extraction is done.  If 'default',
                        the default (lib/metrics-default.yaml) is used.
       --metrics-file=<file>
                        Synonym for --metrics=<file>.
       --metrics-epoch=<seconds>
                        Number of seconds to look back for metrics prior
                        to start of run (default 0)
       --metrics-interval=<interval>
                        Interval between data points for metrics collection.
                        Default 30.
       --force-no-metrics
                        Do not attempt anything that would use metrics
                        or the prometheus pod.
       --failure-status=<status>
                        Failures should be reported as specified rather
                        than "Fail"
       --retrieve-successful-logs=<0|1>
                        If retrieving artifacts, retrieve logs for all
                        pods, not just failing pods.  Default 0.
       --parallel-log-retrieval=n
                        If retrieving artifacts, parallelize log retrieval.

    Workload sizing options:
       --containers-per-pod=N
                        Number of containers per pod
       --deployments=N  Number of deployments or pods per namespace
       --deps-per-namespace=N
                        Number of deployments per namespace
                        (synonym for --deployments)
       --processes=N    Number of processes per pod
       --processes-per-pod=N
                        Number of processes per pod
                        (synonym for --processes)
       --replicas=N     Number of replicas per deployment
       --secrets=N      Number of secrets

    Generic workload rate options:
       --bytes-transfer=N[,M]
                        Number of bytes for workloads operating on
                        fixed amounts of data.
       --workload-size=N
                        Amount of data for workloads operating on
                        fixed amounts of data (synonym for --bytes-transfer).
       --target-data-rate=N
                        Target data rate for workloads operating at fixed
                        data rates.  May have suffixes of K, Ki,
                        M, Mi, G, Gi, T, or Ti.
       --workload-runtime=N
                        Time to run the workload where applicable.
                        Two comma-separated numbers may be used to
                        specify maximum time.

    Workload placement options:
       --pin-node=[class1,class2...]=<node>
                        Force pod(s) of the specified class(es) onto the
                        specified node.  Multiple comma-separated classes
                        may be specified.  The following classes are
                        defined for general workloads:
                        - sync   (sync pods)
                        - client (worker/client pods)
                        Workloads may define other classes.
                        If no class is specified, pin node applies to all
                        pods.
       --sync-in-first-namespace=<0|1>
                        Place the sync pod in the first worker namespace.
       --affinity       Force affinity between client and server pods
                        in a client-server workload.  Default is neither
                        affinity nor anti-affinity.  Use of a pin node
                        overrides affinity.
       --anti-affinity  Force anti-affinity between client and server pods
                        in a client-server workload.  Default is neither
                        affinity nor anti-affinity.  Use of a pin node
                        overrides anti-affinity.
       --sync-affinity  Force affinity between sync and all worker pods.
       --sync-anti-affinity
                        Force anti-affinity between sync and all worker pods.
       --drop-cache     Drop the buffer cache in all pin nodes; if no
                        pin nodes are defined, drop all workers' caches.
       --drop-all-cache Drop the buffer cache on all workers.

    Generic workload storage options:
       --volume=name:type:mount_path:options
                        Mount a specified volume.
                        - name is the name of the volume
                        - type is the type of volume.
                          Currently supported volume types are:
                          - emptydir (pods only; no name required)
                          - emptydisk (VMs only; no name required)
                          - pvc or persistentvolumeclaim
                        - mount_path is the path on which to mount the volume
                            (required).
                        Options currently supported include the following:
                          - claimName is the name of a PVC if it differs
                            from the volume name.  This allows use of
                            different PVCs; all occurrences of %N
                            are replaced by the namespace of the pod
                            mounting the volume; all instances of %i
                            are replaced by the instance of the pod
                            within the namespace.
                          - size in bytes (required for emptydisk; ignored for
                            other volume types).
                          - inodes in number (optional for emptydisk; ignored
                            for other volume types).
                          - bus (VMs; ignored for pods): bus to be used
                            for the volume (default virtio)
                          - cache (VMs): caching mode (none, writeback,
                            writethrough; default none)
                          - dedicatedIOThread (VMs): whether to use a
                            dedicated I/O thread (true, false; default not
                            specified)
                          - fstype (VMs): filesystem type to format
                            the volume to; empty means to not format
                            the volume (filesystem must already be present).
                            fsopts (VMs): options to use for formatting
                            the filesystem.
                          - mountopts (VMs): mount options to be used.
                          - nfsserv (VMs): NFS server for NFS-based PVCs.
                          - nfsshare (VMs): NFS share name for NFS-based
                            PVCs.  Default to '/'.
                        Notes:
                          - A previously declared mount can be overridden
                            by specifying a later mount with the same
                            mountpoint.
                          - A previously declared mount can be removed by
                            specifying a mount with the same mountpoint
                            and empty name and type.  Example:
                            --volume=:emptydir:/var/tmp/clusterbuster
                            --volume=::/var/tmp/clusterbuster
                            will result in no mount on /var/tmp/clusterbuster
                            unless later overridden.
                          - All previously declared mounts can be removed
                            by specifying a mount with no name, type,
                            mountpoint, or options.  Example:
                            --volume=::
                            will remove all mounts from the list.
       --workdir=<dir>  Use the specified working directory for file I/O

    Pod Options:
       --container-image=<image>
                        Image for workload pods/VMs only (default: workload-specific or
                        clusterbuster-base-image). Does not apply to "pause" workloads.
                        The sync pod uses clusterbuster-base-image (defaults are fine;
                        CLUSTERBUSTER_SYNC_IMAGE or --sync-pod-image only if you must).
       --deployment-type=<pod,deployment,replicaset,vm>
                        Deploy via individual pods, deployments, replica sets,
                        or vms (default pod).
                        VM deployments are disabled on aarch64 worker clusters
                        until containerdisk/UEFI issues are resolved; set
                        CB_ALLOW_VM_AARCH64=1 to override. Note that
                        functionality that relies on fixed pod
                        names or recognition of distinct pods (e. g.
                        the %i functionality in volumes) will not work
                        correctly with deployments or replicasets.
       --external-sync=host:port
                        Sync to external host rather than internally
       --request=<resource=value>
                        Resource requests
       --limit=<resource=value>
                        Resource limits
       --runtime-class=[class1,class2...]=class
                        Run the pods in the designated runtimeclass.
                        --runtime-class=vm is a synonym for
                        --deployment-type=vm.
       --kata           Synonym for --runtime-class=kata
       --tolerate=<key:operator:effect>
                        Apply the specified tolerations to created pods.
       --image-pull-policy=<policy>
                        Image pull policy (system default)
       --node-selector=selector
                        Annotate pods with the specified node selector.
                        Specify empty value to not provide a node selector.
       --pod-annotation=[:class:]annotation
                        Apply the specified annotation to all pods of the
                        optionally specified class (same meaning as for
                        --pin-node as above).  This may be specified
                        multiple times.
       --headless-services=[0,1]
                        Use headless services for service creation.
       --liveness-probe-interval=<interval>
                        Execute a simple liveness probe every <interval>
                        seconds.
       --liveness-probe-sleep-time=<seconds>
                        Arrange for the liveness probe to sleep for specified
                        time.
       --privileged-pods=[0,1]
                        Create pods as privileged (default 0).
       --label=[:class:]label=value
                        Apply the specified label to all pods of the
                        optionally specified class (same meaning as for
                        --pin-node as above).  This may be specified
                        multiple times.
       --scheduler=<scheduler>
                        Use the specified scheduler to schedule pods.
       --interface[=:class:]=name[:internal-interface]
                        Provide the specified network interface to the pod/VM.
                        Class has the same meaning as for --pin-node.
                        Internal-interface, if specified, is the name of the
                        interface inside the pod/VM.  Normally this should not
                        be specified, and the default (net1 for pods, eth1 for
                        VMs) should be used.

    Kata Virtualization Tuning:
       --virtiofsd-writeback=[0,1]
                        Use writeback caching for virtiofsd (default 0).
       --virtiofsd-direct=[0,1]
                        Allow use of direct I/O for virtiofsd (default 0).
       --virtiofsd-threadpool-size=n
                        Use the specified thread pool size for virtiofsd
                        (default 1).

    OpenShift Virtualization Options:
       --vm-threads=<value>
                        Specify the number of threads on each core (default 1).
       --vm-cores=<value>
                        Specify the number of cores on each socket (default 1).
       --vm-sockets=<value>
                        Specify the number of sockets (default 1).
       --vm-memory=<value>
                        Specify the amount of memory (default 4Gi).
       --vm-grace-period=<value>
                        Specify the period between when a vm is signaled to
                        shutdown and the point when KubeVirt will force off
                        the vm (default 600).
       --vm-image=<image_url>
                        Containerdisk image to use.
       --vm-migrate=[0,1]
                        Allow VMs to migrate when evicted rather than be
                        deleted.
       --vm-run-as-container=[0,1]
                        Run the workload as a container rather than directly.
       --vm-user=<user>
                        Create the specified user on virtual machines.
                        Default clusterbuster.  Empty means no user.
       --vm-password=<password>
                        Create the specified password on virtual machines.
                        Default clusterbuster.  Empty means no password.
       --vm-ssh-key=file
                        Inject the public key of the specified key pair into
                        VMs for log retrieval or other access purposes.
                        Default none, in which case a temporary key
                        is generated.
       --vm-run-as-root=[0,1]
                        Run test command as root.  Default 0.
       --vm-start=[0,1]
                        Start the VMs in running state (otherwise are started
                        separately).
       --vm-run-strategy=<strategy>
                        Specify the desired run strategy for VMs.
       --vm-block-multiqueue=<n>
                        Specify the desired number of I/O threads for block
                        devices.

    Tuning object creation (short equivalents):
       --scale-ns=[0,1] Scale up the number of namespaces vs.
                        create new ones (default 0).
       --scale-deployments=[0,1]
                        Scale up the number of deployments vs.
                        create new ones (default 1)
       --first-deployment=N
                        Specify the index of the first deployment.
                        Default is 0 (but see --scale-deployments)
       --pod-prefix=prefix
                        Prefix all created pods with this prefix.
       --sleep-between-secrets=N
                        Number of seconds between secret creations.
       --sleep-between-namespaces=N
                        Number of seconds between namespace creations.
       --sleep-between-deployments=N
                        Number of seconds between deployment creations.
       --sleep-between-configmaps=N
                        Number of seconds between configmap creations.

       --objects-per-call=N
                        Number of objects per CLI call.  Only objects
                        within a namespace can be created this way;
                        to improve creation performance with multiple
                        namespaces, use --parallel.
       --objects-per-call-secrets=N
                        Number of secrets per CLI call.
       --objects-per-call-namespaces=N
                        Number of namespaces per CLI call.
       --objects-per-call-deployments=N
                        Number of deployments per CLI call.
       --objects-per-call-configmaps=N
                        Number of configmaps per CLI call.

       --parallel-namespaces=N
                        Number of namespace operations in parallel.
       --parallel-deployments=N
                        Number of deployment operations in parallel.
       --parallel-secrets=N
                        Number of secret operations in parallel.
       --parallel-configmaps=N
                        Number of configmap operations in parallel.
       --wait-secrets   Wait for secrets to be created (default 1)
       --pod-start-timeout=<seconds>
                        Wait specified time for pods to come on line.
       --configmap-file=<file>
                        Add an additional file to the configmap.

    Advanced options (generally not required):
       --force-cleanup-i-know-this-is-dangerous
                        Force cleanup of all clusterbuster objects
                        across the entire cluster.  Use with extreme
                        caution.
       --baseoffset=N   Add specified offset to base time
                        for calculation of start time offset
                        to correct for clock skew.  May be float.
                        This normally should not be needed, as
                        ClusterBuster can correct for clock skew
                        itself.
       --podsleep=N     Time for pod to sleep before exit
       --debug=<opt>
                        For testing purposes, print debugging information.
                        Options documented only in code.
       --inject-error=<opt>
                        For testing purposes, inject the specified error
                        condition (documented only in code).
       --force-abort    Abort the run on any error.
       --preserve-tmpdir
                        Do not remove the temporary directory at
                        end of the run
"""


def _build_help_text(*, include_workloads: bool = True) -> str:
    """Assemble help text.

    When *include_workloads* is False (``-h``), only the core usage and
    option descriptions are shown.  When True (``-H``), workload-specific
    options and descriptions are appended.
    """
    parts: list[str] = [_USAGE_TEXT, _EXTENDED_HELP_TEXT]

    if include_workloads:
        import clusterbuster.driver.workloads  # noqa: F401 — trigger @register
        from .workload_registry import all_workloads

        workloads = sorted(all_workloads(), key=lambda w: w.name)

        opts_parts: list[str] = []
        for wl in workloads:
            text = wl.help_options()
            if text:
                opts_parts.append(text.rstrip())
        if opts_parts:
            parts.append("Workload-specific options:")
            for opt_text in opts_parts:
                parts.append(opt_text)
                parts.append("")

        desc_parts: list[str] = []
        for wl in workloads:
            desc = wl.document()
            if desc:
                desc_parts.append(f"* {desc}")
        if desc_parts:
            parts.append("Here is a brief description of all available workloads:")
            parts.extend(desc_parts)
            parts.append("")

    return "\n".join(parts)


def _pager_output(text: str) -> None:
    """Display text through a pager if stderr is a TTY."""
    import shutil
    import subprocess as _sp

    if not sys.stderr.isatty():
        print(text, file=sys.stderr)
        return

    pager_cmd = os.environ.get("PAGER", "more")
    pager_path = shutil.which(pager_cmd.split()[0])
    if not pager_path:
        print(text, file=sys.stderr)
        return

    try:
        proc = _sp.Popen(
            pager_cmd.split(), stdin=_sp.PIPE, text=True
        )
        try:
            proc.communicate(input=text)
        except BrokenPipeError:
            pass
        proc.wait()
    except OSError:
        print(text, file=sys.stderr)


def _print_help_and_exit() -> NoReturn:
    _pager_output(_build_help_text(include_workloads=False))
    sys.exit(0)


def _print_extended_help_and_exit() -> NoReturn:
    """Print full extended help including workload options."""
    _pager_output(_build_help_text(include_workloads=True))
    sys.exit(0)


def _print_dry_run(config: ClusterbusterConfig) -> None:
    """Print configuration summary and manifest plan without cluster contact."""
    print(f"Workload: {config.resolved_workload}")
    print(f"Deployment type: {config.deployment_type}")
    print(f"Namespaces: {config.namespaces}")
    print(f"Deployments per namespace: {config.deps_per_namespace}")
    print(f"Containers per pod: {config.containers_per_pod}")
    print(f"Replicas: {config.replicas}")
    print(f"Processes per container: {config.processes_per_pod}")
    print(f"UUID: {config.uuid}")
    print(f"Basename: {config.basename}")
    print(f"Sync: {config.sync_start}")
    print(f"Timeout: {config.timeout}")
    print(f"Workload runtime: {config.workload_run_time}")
    if config.pin_nodes:
        print(f"Pin nodes: {config.pin_nodes}")
    if config.runtime_class:
        print(f"Runtime class: {config.runtime_class}")
    print(f"Image: {config.container_image}")
    print(f"Image pull policy: {config.image_pull_policy}")

    namespaces = _get_namespace_plan(config)
    print(f"\nNamespaces ({len(namespaces)}):")
    for ns in namespaces:
        print(f"  {ns}")

    _print_manifests(config, namespaces)


def _get_namespace_plan(config: ClusterbusterConfig) -> list[str]:
    """Compute planned namespaces without cluster contact.

    Delegates to ``orchestrator.plan_namespace_names`` — the same
    function used by the live ``allocate_namespaces``, so the names
    cannot diverge.
    """
    from .orchestrator import plan_namespace_names
    return plan_namespace_names(config)


class _NoAliasDumper(yaml.Dumper):
    """YAML dumper that never emits anchors/aliases.

    Multi-line strings are rendered as literal block scalars (``|``)
    for readability (e.g. cloud-init ``userData``).
    """

    def ignore_aliases(self, data: object) -> bool:
        return True


def _literal_str_representer(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_NoAliasDumper.add_representer(str, _literal_str_representer)


def _yaml_dump(data: object) -> str:
    return yaml.dump(data, Dumper=_NoAliasDumper, default_flow_style=False)


def _print_manifests(
    config: ClusterbusterConfig, namespaces: list[str],
) -> None:
    """Generate and print every manifest that a live run would create.

    Uses the shared ``build_manifest_plan()`` to produce the same
    manifest list that the live orchestrator builds, ensuring the
    dry-run output always matches real runs.
    """
    from .manifest_plan import build_manifest_plan

    manifests = build_manifest_plan(config, namespaces, basetime=0.0)

    def _summarize_configmap_data(cm: dict[str, Any]) -> None:
        if "data" in cm:
            cm["data"] = [{"file": k} for k in cm["data"]]

    print("\n--- Manifests ---")
    for m in manifests:
        if m.get("kind") == "ConfigMap":
            _summarize_configmap_data(m)
        print("---")
        print(_yaml_dump(m))
