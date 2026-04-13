# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""sysbench workload: scriptable multi-threaded benchmark."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..workload_registry import ArglistContext, WorkloadBase, pod_flags, register

if TYPE_CHECKING:
    from ..config import ClusterbusterConfigBuilder


@register
class Sysbench(WorkloadBase):
    name = "sysbench"

    def __init__(self) -> None:
        self._workload = "fileio"
        self._fileio_test_string = "seqwr seqrd rndwr rndrd"
        self._fileio_mode_string = "sync"
        self._passthrough: dict[str, str] = {}

    def process_options(
        self, builder: ClusterbusterConfigBuilder, parsed: Any
    ) -> bool:
        n = parsed.noptname1
        v = parsed.optvalue
        if n == "sysbenchworkload":
            self._workload = v
        elif n.startswith("sysbenchfileiotest"):
            self._fileio_test_string = v.replace(",", " ")
        elif n.startswith("sysbenchfileiomode"):
            self._fileio_mode_string = v.replace(",", " ")
        elif n == "sysbenchtime":
            builder.workload_run_time = int(v)
        elif n.startswith("sysbench"):
            stripped = parsed.noptname.replace("sysbench_", "", 1)
            self._passthrough[n] = f"--{stripped}={v}"
        else:
            return False

        return True

    def finalize_extra_cli_args(
        self, builder: ClusterbusterConfigBuilder
    ) -> None:
        if not builder.container_image:
            builder.container_image = "quay.io/rkrawitz/clusterbuster-workloads:latest"

    def arglist(self, ctx: ArglistContext) -> list[str]:
        cfg = ctx.config
        workdir = cfg.common_workdir or "/tmp"
        args = [
            "python3", f"{ctx.mountdir}sysbench.py",
            *pod_flags(ctx),
            "--processes", str(cfg.processes_per_pod),
            "--rundir", workdir,
            "--runtime", str(cfg.workload_run_time),
            "--workload", self._workload,
        ]
        if self._fileio_test_string:
            args.extend(["--fileio-tests", self._fileio_test_string])
        if self._fileio_mode_string:
            args.extend(["--fileio-modes", self._fileio_mode_string])
        for val in self._passthrough.values():
            args.extend(["--sysbench-option", val])
        return args

    def namespace_policy(self) -> str:
        if self._workload == "fileio":
            return "privileged"
        return "restricted"

    def requires_drop_cache(self) -> bool:
        return self._workload == "fileio"

    def report_options(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self._workload == "fileio":
            tests = self._fileio_test_string.replace(",", " ").split()
            modes = self._fileio_mode_string.replace(",", " ").split()
            result["sysbench_fileio_tests"] = tests
            result["sysbench_fileio_modes"] = modes
        result["sysbench_workload"] = self._workload
        result["sysbench_options"] = list(self._passthrough.values())
        return result

    def help_options(self) -> str:
        return """\
    Sysbench Options:
        --sysbench-workload=<workload>
                        Which sysbench workload to run (fileio, cpu, memory,
                        threads, mutex; oltp is not currently supported)
        --sysbench-fileio-tests=<modes>
                        Space or comma separated list of file test modes
                        to use (seqwr, seqrewr, seqrd, rndrd, rndwr, rndrw).
        All other options prefixed with "--sysbench-" are treated as sysbench
        options, with the "sysbench" prefix removed."""

    def document(self) -> str:
        return ("sysbench: scriptable multi-threaded benchmark tool based on "
                "LuaJIT.\n  Currently supports cpu, fileio, memory, mutex, "
                "and threads workloads.\n  See https://github.com/akopytov/"
                "sysbench")
