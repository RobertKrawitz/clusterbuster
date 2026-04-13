# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""waitforever workload: sleep forever without returning."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..workload_registry import ArglistContext, WorkloadBase, pod_flags, register

if TYPE_CHECKING:
    from ..config import ClusterbusterConfigBuilder


@register
class WaitForever(WorkloadBase):
    name = "waitforever"

    def finalize_extra_cli_args(
        self, builder: ClusterbusterConfigBuilder
    ) -> None:
        builder.processes_per_pod = 1

    def arglist(self, ctx: ArglistContext) -> list[str]:
        return [
            "python3", f"{ctx.mountdir}waitforever.py",
            *pod_flags(ctx),
        ]

    def supports_reporting(self) -> bool:
        return False

    def workload_reporting_class(self) -> str:
        return "generic_nodata"

    def calculate_logs_required(
        self, ns: int, deps: int, replicas: int, containers: int,
        processes_per_pod: int,
    ) -> int:
        return 0

    def document(self) -> str:
        return ("waitforever: Sleep forever without returning.\n  Useful for "
                "creating pods/VMs intended to run\n  processes without "
                "themselves doing any work.")
