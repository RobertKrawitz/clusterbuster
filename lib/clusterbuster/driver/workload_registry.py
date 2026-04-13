# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""Workload plugin registry: base class, registration, dispatch, context dataclasses."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .cluster import ClusterInterface
    from .config import ClusterbusterConfig, ClusterbusterConfigBuilder
    from .manifests import ManifestBuilder

_LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context dataclasses passed to workload methods
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ArglistContext:
    """Passed to arglist / server_arglist / client_arglist."""

    mountdir: str
    namespace: str
    instance: int
    secret_count: int
    replicas: int
    containers_per_pod: int
    container_index: int
    config: ClusterbusterConfig

    # Runtime fields populated by the orchestrator (Phase 3C)
    sync_host: str = ""
    basetime: float = 0.0
    crtime: float = 0.0
    drop_cache_host: str = ""


@dataclass(frozen=True)
class DeploymentContext:
    """Passed to create_deployment."""

    namespace: str
    count: int
    secret_count: int
    replicas: int
    containers_per_pod: int
    config: ClusterbusterConfig
    manifest_builder: ManifestBuilder
    cluster: ClusterInterface


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class WorkloadBase:
    """Base workload with default no-op implementations for every API method.

    Subclass, set ``name`` and ``aliases``, and override only the methods
    the workload needs.
    """

    name: str = ""
    aliases: tuple[str, ...] = ()

    def process_options(
        self, builder: ClusterbusterConfigBuilder, parsed: Any
    ) -> bool:
        """Handle a workload-specific option.  Return True if consumed.

        ``parsed`` is a :class:`ParsedOption` with ``noptname1``,
        ``noptname``, and ``optvalue`` attributes.
        """
        return False

    def finalize_extra_cli_args(self, builder: ClusterbusterConfigBuilder) -> None:
        """Post-option-parsing hook for extra_args handling."""

    def arglist(self, ctx: ArglistContext) -> list[str]:
        """Container command arguments for single-role workloads."""
        raise NotImplementedError(f"{self.name} does not implement arglist")

    def server_arglist(self, ctx: ArglistContext) -> list[str]:
        """Server container command (client/server workloads)."""
        raise NotImplementedError(f"{self.name} does not implement server_arglist")

    def client_arglist(self, ctx: ArglistContext) -> list[str]:
        """Client container command (client/server workloads)."""
        raise NotImplementedError(f"{self.name} does not implement client_arglist")

    def create_deployment(self, ctx: DeploymentContext) -> bool:
        """Override to customize deployment creation.

        Return False to fall through to generic deployment path.
        Return True if the workload handles deployment itself.
        """
        return False

    def list_configmaps(self) -> list[str]:
        """Pod file names to include in the system configmap."""
        return [f"{self.name}.py"]

    def list_user_configmaps(self) -> list[str]:
        """Extra files to include in the user configmap."""
        return []

    def generate_metadata(self) -> dict[str, Any]:
        """Workload-specific metadata for report JSON."""
        return {}

    def generate_environment(self) -> dict[str, str]:
        """Extra environment variables for the workload container."""
        return {}

    def calculate_logs_required(
        self, ns: int, deps: int, replicas: int, containers: int,
        processes_per_pod: int,
    ) -> int:
        """Expected number of log entries."""
        return ns * deps * replicas * containers * processes_per_pod

    def supports_reporting(self) -> bool:
        """Whether the workload produces structured reports."""
        return True

    def workload_reporting_class(self) -> str:
        """Report class name used by clusterbuster-report."""
        return self.name

    def report_options(self) -> dict[str, Any]:
        """Workload options dict serialized into report JSON."""
        return {}

    def requires_drop_cache(self) -> bool:
        """Whether the workload requires per-pod drop-cache infrastructure."""
        return False

    def requires_writable_workdir(self) -> bool:
        """Whether the workload needs a writable common_workdir."""
        return False

    def sysctls(self, config: ClusterbusterConfig | None = None) -> dict[str, str]:
        """Kernel sysctls required for the workload pod."""
        return {}

    def namespace_policy(self) -> str:
        """Pod-security policy for the namespace (empty = driver default)."""
        return ""

    def help_options(self) -> str:
        """CLI help text for workload-specific options."""
        return ""

    def document(self) -> str:
        """One-line workload description for --help."""
        return ""

    def vm_required_packages(self) -> list[str]:
        """Packages to install in VM cloud-init."""
        return []

    def vm_setup_commands(self) -> list[str]:
        """Extra setup commands for VM cloud-init."""
        return []

    def listen_ports(self, config: Any = None) -> list[int]:
        """Ports the workload listens on (for service creation)."""
        return []


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_WORKLOADS: dict[str, WorkloadBase] = {}
_ALIASES: dict[str, str] = {}


def register(cls: type[WorkloadBase]) -> type[WorkloadBase]:
    """Class decorator: instantiate and register a workload."""
    instance = cls()
    if not instance.name:
        raise ValueError(f"Workload class {cls.__name__} has no name")
    _WORKLOADS[instance.name] = instance
    _ALIASES[instance.name.lower()] = instance.name
    for alias in instance.aliases:
        _ALIASES[alias.lower()] = instance.name
    return cls


def resolve_alias(name: str) -> str:
    """Resolve a possibly-aliased workload name to canonical.

    Raises ValueError for unknown names.
    """
    canonical = _ALIASES.get(name.lower())
    if canonical is None:
        raise ValueError(f"Unknown workload {name!r}")
    return canonical


def get_workload(name: str) -> WorkloadBase:
    """Get workload instance by name or alias."""
    return _WORKLOADS[resolve_alias(name)]


def all_workloads() -> list[WorkloadBase]:
    """All registered workload instances."""
    return list(_WORKLOADS.values())


def all_workload_names() -> list[str]:
    """All canonical workload names."""
    return list(_WORKLOADS.keys())


def all_aliases() -> dict[str, str]:
    """All alias -> canonical mappings (for testing)."""
    return dict(_ALIASES)


def all_help_options() -> list[str]:
    """Collect unique workload option names from all registered workloads.

    Returns option names (without leading ``--``) for inclusion in
    ``-h`` help output, each as a single string.  Multi-line
    ``help_options()`` returns are split on newlines.
    """
    seen: set[str] = set()
    result: list[str] = []
    for wl in _WORKLOADS.values():
        text = wl.help_options()
        if not text:
            continue
        for line in text.strip().splitlines():
            opt_line = line.strip()
            if not opt_line or not opt_line.startswith("--"):
                continue
            name = opt_line.split("=", 1)[0].split()[0].lstrip("-")
            if name not in seen:
                seen.add(name)
                result.append(name)
    return sorted(result)


def all_documentation() -> list[str]:
    """Collect workload documentation for extended help.

    Returns a list of strings, each combining the workload's
    ``document()`` description and its ``help_options()``
    detailed text.
    """
    docs: list[str] = []
    for wl in sorted(_WORKLOADS.values(), key=lambda w: w.name):
        parts: list[str] = []
        desc = wl.document()
        if desc:
            parts.append(f"* {desc}")
        help_text = wl.help_options()
        if help_text:
            parts.append(help_text.rstrip())
        if parts:
            docs.append("\n".join(parts))
    return docs


# ---------------------------------------------------------------------------
# Pod flags utility
# ---------------------------------------------------------------------------

def pod_flags(ctx: ArglistContext) -> list[str]:
    """Build the ``--cb-*`` flags array matching bash ``cb_pod_client_flags_array``.

    These are the ~14 sync/drop-cache parameters passed to every pod's
    Python entrypoint.  The actual values for sync_host, basetime, crtime,
    etc. are runtime values populated by the orchestrator (Phase 3C).
    For arglist generation and testing, placeholder values are used.
    """
    cfg = ctx.config
    flags = [
        f"--cb-sync-nonce={cfg.sync_nonce}",
        f"--cb-namespace={ctx.namespace}",
        f"--cb-container=c{ctx.container_index}",
        f"--cb-basetime={ctx.basetime or 0}",
        f"--cb-baseoffset={cfg.baseoffset}",
        f"--cb-crtime={ctx.crtime or 0}",
        f"--cb-exit-at-end={1 if cfg.exit_at_end else 0}",
        f"--cb-sync-host={ctx.sync_host}",
        f"--cb-sync-port={cfg.sync_port}",
        f"--cb-sync-ns-port={cfg.sync_ns_port}",
        f"--cb-sync-watchdog-port={cfg.sync_watchdog_port}",
        f"--cb-sync-watchdog-timeout={cfg.sync_watchdog_timeout}",
        f"--cb-drop-cache-host={ctx.drop_cache_host}",
    ]
    if cfg.drop_cache_port:
        flags.append(f"--cb-drop-cache-port={cfg.drop_cache_port}")
    return flags


# ---------------------------------------------------------------------------
# Callback factories for config.py integration
# ---------------------------------------------------------------------------

def make_process_options_callback(
    builder: ClusterbusterConfigBuilder,
) -> Any:
    """Return a callback for ``config.build(workload_process_options=...)``.

    The callback iterates unknown options through the workload's
    ``process_options`` method, returning any still-unknown options.
    Returns None if the workload is not registered.
    """
    try:
        wl = get_workload(builder.requested_workload)
    except (ValueError, KeyError):
        return None

    def callback(unknown_opts: list[str]) -> list[str]:
        from clusterbuster.ci.compat import parse_option

        still_unknown: list[str] = []
        for opt in unknown_opts:
            parsed = parse_option(opt)
            if not wl.process_options(builder, parsed):
                still_unknown.append(opt)
        return still_unknown

    return callback


def make_finalize_callback(
    builder: ClusterbusterConfigBuilder,
) -> Any:
    """Return a callback for ``config.build(workload_finalize_args=...)``.

    Returns None if the workload is not registered.
    """
    try:
        wl = get_workload(builder.requested_workload)
    except (ValueError, KeyError):
        return None

    def callback(b: ClusterbusterConfigBuilder) -> None:
        wl.finalize_extra_cli_args(b)

    return callback
