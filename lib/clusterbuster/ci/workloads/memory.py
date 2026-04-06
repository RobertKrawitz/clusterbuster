# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0

from __future__ import annotations

import fnmatch
from typing import Any

from clusterbuster.ci.compat import parse_size
from clusterbuster.ci.compat.options import parse_optvalues
from clusterbuster.ci.execution import RunJobParams
from clusterbuster.ci.helpers import compute_timeout


class MemoryWorkload:
    name = "memory"
    aliases: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.initialize_options()

    def initialize_options(self) -> None:
        self.job_runtime = 0
        self.job_timeout = 0
        self.replicas = 1
        self.processes = 1
        self.alloc = 67108864
        self.scan: str | int = 1
        self.params: list[str] = []

    def process_option(self, option: str, workload: str, runtimeclass: str) -> bool:
        from clusterbuster.ci.ci_options import ParsedCiOption, parse_ci_option

        p: ParsedCiOption | None = parse_ci_option(option, workload, runtimeclass)
        if p is None:
            return False
        n1 = p.noptname1
        if fnmatch.fnmatch(n1, "memory*runtime"):
            self.job_runtime = int(p.optvalue)
        elif fnmatch.fnmatch(n1, "memory*timeout"):
            self.job_timeout = int(p.optvalue)
        elif fnmatch.fnmatch(n1, "memoryreplica*"):
            self.replicas = int(p.optvalue)
        elif fnmatch.fnmatch(n1, "memoryproc*"):
            self.processes = int(p.optvalue)
        elif fnmatch.fnmatch(n1, "memoryscan*"):
            self.scan = p.optvalue.strip()
        elif fnmatch.fnmatch(n1, "memoryalloc*"):
            self.alloc = int(parse_size(p.optvalue))
        elif fnmatch.fnmatch(n1, "memory*params"):
            self.params.extend(parse_optvalues(p.optvalue))
        else:
            return False
        return True

    def run(self, suite: Any, default_job_runtime: int) -> None:
        for runtimeclass in suite.config.normalized_runtimeclasses():
            extra = suite.process_workload_options(self.name, runtimeclass)
            jr = self.job_runtime
            if jr <= 0:
                jr = default_job_runtime
            jt = compute_timeout(self.job_timeout, suite.config.job_timeout)
            if not self.params:
                specs = [f"{jr}:{self.replicas}:{self.processes}:{self.alloc}:{self.scan}"]
            else:
                specs = list(self.params)
            for spec in specs:
                parts = spec.split(":")
                if len(parts) < 5:
                    print(f"Unparsable memory options {spec!r}", flush=True)
                    continue
                per_runtime, replicas_s, processes_s, alloc_raw, scan_val = parts[:5]
                if not per_runtime or not scan_val or not alloc_raw or not replicas_s or not processes_s:
                    print(f"Unparsable memory options {spec!r}", flush=True)
                    continue
                if not (replicas_s.isdigit() and processes_s.isdigit() and per_runtime.isdigit()):
                    print(f"Invalid runtime, replicas, or processes in memory options {spec!r}", flush=True)
                    continue
                alloc = int(parse_size(alloc_raw))
                job_name = f"R{per_runtime}-{replicas_s}P-{processes_s}pr-{alloc}B-s{scan_val}"
                tail = [
                    f"--replicas={replicas_s}",
                    f"--processes={processes_s}",
                    f"--memorysize={alloc}",
                    f"--memoryscan={scan_val}",
                    "--failure-status=No Result",
                    "--cleanup-always=1",
                ]
                if suite.config.client_pin:
                    tail.append(f"--pin-node=sync={suite.config.client_pin}")
                st = suite.run_clusterbuster_1(
                    RunJobParams(
                        workload=self.name,
                        jobname=job_name,
                        runtimeclass=runtimeclass,
                        timeout=jt,
                        job_runtime=int(per_runtime),
                        tail_argv=tuple(tail),
                    ),
                    extra_clusterbuster_args=extra,
                )
                if st != 0:
                    return
