# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""pausepod workload: minimal pod that does nothing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..workload_registry import ArglistContext, WorkloadBase, register

if TYPE_CHECKING:
    from ..config import ClusterbusterConfigBuilder


_PAUSE_IMAGE = "gcr.io/google_containers/pause-amd64:3.2"


@register
class PausePod(WorkloadBase):
    name = "pausepod"
    aliases = ("simple-pausepod", "pause")

    def finalize_extra_cli_args(
        self, builder: ClusterbusterConfigBuilder
    ) -> None:
        builder.processes_per_pod = 1
        if not builder.container_image:
            builder.container_image = _PAUSE_IMAGE

    def arglist(self, ctx: ArglistContext) -> list[str]:
        return []

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
        return ("pausepod: a minimal pod that does nothing.  Useful for "
                "stressing\n  the control plane.  See\n  "
                "https://console.cloud.google.com/gcr/images/"
                "google-containers/global/pause-amd64")
