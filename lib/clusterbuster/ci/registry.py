# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class WorkloadPlugin(Protocol):
    name: str
    aliases: tuple[str, ...]

    def initialize_options(self) -> None: ...

    def process_option(self, option: str, workload: str, runtimeclass: str) -> bool:
        """Return True if the option was consumed (workload-specific)."""
        ...

    def run(self, suite: Any, default_job_runtime: int) -> None: ...


def default_registry() -> dict[str, WorkloadPlugin]:
    from clusterbuster.ci.workloads.cpusoaker import CpusoakerWorkload
    from clusterbuster.ci.workloads.files import FilesWorkload
    from clusterbuster.ci.workloads.fio import FioWorkload
    from clusterbuster.ci.workloads.hammerdb import HammerdbWorkload
    from clusterbuster.ci.workloads.memory import MemoryWorkload
    from clusterbuster.ci.workloads.uperf import UperfWorkload

    plugins: list[WorkloadPlugin] = [
        MemoryWorkload(),
        FioWorkload(),
        UperfWorkload(),
        FilesWorkload(),
        CpusoakerWorkload(),
        HammerdbWorkload(),
    ]
    m: dict[str, WorkloadPlugin] = {}
    for p in plugins:
        m[p.name] = p
        for a in p.aliases:
            m[a] = p
    return m
