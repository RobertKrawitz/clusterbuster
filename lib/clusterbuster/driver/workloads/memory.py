# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""memory workload: allocate and optionally scan a block of memory."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

from clusterbuster.ci.compat import bool_str, parse_size

from ..workload_registry import ArglistContext, WorkloadBase, pod_flags, register

if TYPE_CHECKING:
    from ..config import ClusterbusterConfigBuilder


def _scan_order(value: str) -> int:
    """Map scan option value: rand*->2, else bool (0 or 1)."""
    if value.lower().startswith("rand"):
        return 2
    return int(bool_str(value))


@register
class Memory(WorkloadBase):
    name = "memory"

    def __init__(self) -> None:
        self._size = "1048576"
        self._scan = 0
        self._stride = 0
        self._iteration_runtime = "10"
        self._iterations = "1"
        self._idle = "0"
        self._idle_first = 2
        self._random_seed = ""
        self._sync = 1
        self._start_probability = ""
        self._subproc = 0

    def process_options(
        self, builder: ClusterbusterConfigBuilder, parsed: Any
    ) -> bool:
        n = parsed.noptname1
        v = parsed.optvalue
        if n == "memorysize":
            self._size = parse_size(v, delimiter=",")
        elif n == "memoryscan":
            self._scan = _scan_order(v)
        elif n.startswith("memorystride"):
            self._stride = int(parse_size(v))
        elif n == "memoryiterations":
            self._iterations = parse_size(v)
        elif n.startswith("memoryiterationrun"):
            self._iteration_runtime = parse_size(v, delimiter=",")
        elif n.startswith("memoryidle"):
            if n == "memoryidlefirst":
                self._idle_first = int(v)
            else:
                self._idle = parse_size(v, delimiter=",")
        elif n.startswith("memoryrandom"):
            self._random_seed = base64.b64encode(v.encode()).decode()
        elif n.startswith("memorysync"):
            self._sync = int(bool_str(v))
        elif n.startswith("memorysubproc"):
            self._subproc = int(bool_str(v))
        elif n.startswith("memorystartprob"):
            self._start_probability = v
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
        return [
            "python3", f"{ctx.mountdir}memory.py",
            *pod_flags(ctx),
            "--processes", str(cfg.processes_per_pod),
            "--runtime", str(cfg.workload_run_time),
            "--memory-size", self._size,
            "--scan", str(self._scan),
            "--stride", str(self._stride),
            "--iterations", self._iterations,
            "--idle", self._idle,
            "--random-seed", self._random_seed,
            "--sync-between-iterations", str(self._sync),
            "--iteration-runtime", self._iteration_runtime,
            "--idle-first", str(self._idle_first),
            "--subproc", str(self._subproc),
            "--start-probability", self._start_probability,
        ]

    def report_options(self) -> dict[str, Any]:
        def _mk_num_list(val: str) -> list[int]:
            if "," in val:
                parts = val.split(",", 1)
                return [int(parts[0]), int(parts[1])]
            n = int(val)
            return [n, n]

        result: dict[str, Any] = {
            "memory_size": _mk_num_list(self._size),
            "memory_scan": self._scan,
            "memory_stride": self._stride,
            "memory_iterations": self._iterations,
            "memory_iteration_time": _mk_num_list(self._iteration_runtime),
            "memory_idle": _mk_num_list(self._idle),
            "memory_random_seed": self._random_seed,
            "memory_sync_between_iterations": str(self._sync),
            "memory_subproc": self._subproc,
        }
        if self._start_probability:
            result["memory_start_probability"] = float(self._start_probability)
        else:
            result["memory_start_probability"] = -1
        return result

    def help_options(self) -> str:
        return """\
    Memory Options:
        --memory-size=<size[,max_size][,step]>
                        Amount of memory to allocate.  If two values
                        are provided, a random value between the two
                        is used for each iteration.  Step allows specifying
                        the step size.
        --memory-scan=<0,1,random>
                        Write-scan memory continuously.  "Random" results
                        in pages being scanned in random order.
        --memory-stride=<size>
                        Stride the specified number of bytes
                        when scanning.  Default is system pagesize.
                        Set to 1 to scan every byte.
        --memory-iteration-runtime=<seconds[,max_seconds][,step]>
                        The time for each iteration of the test.
                        Default is not set, in which case the
                        --workload-runtime and --memory-iterations
                        control how long the test is run.  This may not
                        be used if --memory-sync-between-iterations is set.
        --memory-iterations=<n>
                        Run the scan for the specified number of
                        iterations.  Default is 1.
        --memory-idle=<seconds[,max_seconds][,step]>
                        Sleep for the specified time between
                        iterations.
        --memory-idle-first=<0,1,2>
                        Sleep first before starting operations.
                        Value of 2 means to randomly sleep or not
                        for the first operation.
        --memory-random-seed=<seed>
                        Use the specified value in combination with
                        the pod ID to randomize the run.  The seed
                        may be an arbitrary string.
        --memory-sync-between-iterations=<0,1>
                        Sync between each iteration.  Default is yes.
                        Most useful to set this to no is when running
                        random workloads when it is desired there to be
                        overlap between operations.
        --memory-start-probability=<"" | 0.000 - 1.000>
                        Probability of memory allocation starting with
                        memory allocation as opposed to sleeping.
                        If empty, probability is calculated based on
                        average duty cycle computed as the average
                        iteration runtime divided by the sum of the
                        average iteration runtime and the average idle
                        runtime.
        --memory-subproc=<0,1>
                        Run iterations as subprocesses rather than function
                        calls, to be certain that memory is released."""

    def document(self) -> str:
        return "memory: Allocate a block of memory and optionally scan it continuously"
