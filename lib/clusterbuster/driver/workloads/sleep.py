# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""sleep workload: sleep for the specified workload runtime."""

from __future__ import annotations

from ..workload_registry import ArglistContext, WorkloadBase, pod_flags, register


@register
class Sleep(WorkloadBase):
    name = "sleep"
    aliases = ("clusterbuster",)

    def arglist(self, ctx: ArglistContext) -> list[str]:
        cfg = ctx.config
        runtime = cfg.workload_run_time
        return [
            "python3", f"{ctx.mountdir}sleep.py",
            *pod_flags(ctx),
            "--runtime", str(runtime),
            "--processes", str(cfg.processes_per_pod),
        ]

    def supports_reporting(self) -> bool:
        return False

    def workload_reporting_class(self) -> str:
        return "generic"

    def calculate_logs_required(
        self, ns: int, deps: int, replicas: int, containers: int,
        processes_per_pod: int,
    ) -> int:
        return 0

    def document(self) -> str:
        return "sleep: sleep for an interval specified by the workload runtime"
