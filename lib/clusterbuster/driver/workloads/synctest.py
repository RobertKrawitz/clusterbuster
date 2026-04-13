# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""synctest workload: tests internal sync."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..workload_registry import ArglistContext, WorkloadBase, pod_flags, register

if TYPE_CHECKING:
    from ..config import ClusterbusterConfigBuilder


@register
class SyncTest(WorkloadBase):
    name = "synctest"

    def __init__(self) -> None:
        self._count = 5
        self._cluster_count = 1
        self._sleep = 0

    def process_options(
        self, builder: ClusterbusterConfigBuilder, parsed: Any
    ) -> bool:
        n = parsed.noptname1
        v = parsed.optvalue
        if n == "synctestcount":
            self._count = int(v)
        elif n == "synctestclustercount":
            self._cluster_count = int(v)
        elif n == "synctestsleep":
            self._sleep = int(v)
        else:
            return False
        return True

    def arglist(self, ctx: ArglistContext) -> list[str]:
        cfg = ctx.config
        return [
            "python3", f"{ctx.mountdir}synctest.py",
            *pod_flags(ctx),
            "--count", str(self._count),
            "--cluster-count", str(self._cluster_count),
            "--sleep", str(self._sleep),
            "--processes", str(cfg.processes_per_pod),
        ]

    def workload_reporting_class(self) -> str:
        return "generic"

    def report_options(self) -> dict[str, Any]:
        return {
            "synctest_count": self._count,
            "synctest_sleep": self._sleep,
        }

    def help_options(self) -> str:
        return """\
    Synctest General Options:
        --synctest-count=n
                        Run the test for n iterations
        --synctest-cluster-count=n
                        Run n syncs per iteration
        --synctest-sleep=n
                        Sleep for the specified time between iterations"""

    def document(self) -> str:
        return "synctest: tests internal sync"
