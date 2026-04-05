# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ClusterbusterCISuiteConfig:
    """Configuration for :class:`ClusterbusterCISuite`."""

    workloads: tuple[str, ...] = (
        "memory",
        "fio",
        "uperf",
        "files",
        "cpusoaker",
        "hammerdb",
    )
    runtimeclasses: tuple[str, ...] = ("",)
    default_job_runtime: int = 600
    # Negative values follow run-perf-ci-suite (use absolute value after defaulting).
    job_timeout: int = -1200
    error_is_failure: bool = True
    dontdoit: bool = False
    artifactdir: Path | None = None
    uuid: str = ""
    extra_args: list[str] = field(default_factory=list)
    extra_clusterbuster_args: list[str] = field(default_factory=list)
    job_delay: int = 0
    unique_prefix: bool = False
    force_cleanup: bool = False
    compress: bool = True
    client_pin: str = ""
    server_pin: str = ""
    sync_pin: str = ""
    report_format: str = ""
    debug_args: str = ""
    force_cleanup_timeout: str = ""
    restart: bool = False

    def normalized_workloads(self) -> tuple[str, ...]:
        return tuple(w.strip().lower() for w in self.workloads if w.strip())

    def normalized_runtimeclasses(self) -> tuple[str, ...]:
        return tuple(r for r in self.runtimeclasses)
