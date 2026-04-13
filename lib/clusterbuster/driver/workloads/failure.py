# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""failure workload: fail after the specified workload runtime."""

from __future__ import annotations

from ..workload_registry import ArglistContext, WorkloadBase, pod_flags, register


@register
class Failure(WorkloadBase):
    name = "failure"

    def arglist(self, ctx: ArglistContext) -> list[str]:
        cfg = ctx.config
        return [
            "python3", f"{ctx.mountdir}failure.py",
            *pod_flags(ctx),
            "--processes", str(cfg.processes_per_pod),
            "--runtime", str(cfg.workload_run_time),
        ]

    def workload_reporting_class(self) -> str:
        return "generic"

    def document(self) -> str:
        return "failure: Fail after the specified workload runtime."
