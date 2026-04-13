# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""cpusoaker workload: continuous tight CPU loop."""

from __future__ import annotations

from ..workload_registry import ArglistContext, WorkloadBase, pod_flags, register


@register
class CpuSoaker(WorkloadBase):
    name = "cpusoaker"
    aliases = ("cpu", "cpusoak")

    def arglist(self, ctx: ArglistContext) -> list[str]:
        cfg = ctx.config
        return [
            "python3", f"{ctx.mountdir}cpusoaker.py",
            *pod_flags(ctx),
            "--processes", str(cfg.processes_per_pod),
            "--runtime", str(cfg.workload_run_time),
        ]

    def document(self) -> str:
        return "cpusoaker: a simple CPU soaker running a continuous tight loop."
