# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0

from __future__ import annotations

import fnmatch
import logging
from typing import Any

from clusterbuster.ci.compat.options import parse_optvalues
from clusterbuster.ci.execution import RunJobParams
from clusterbuster.ci.helpers import compute_timeout

_LOG = logging.getLogger("clusterbuster.ci.workloads.cpusoaker")


class CpusoakerWorkload:
    name = "cpusoaker"
    aliases: tuple[str, ...] = ("cpu", "cpusoak", "scaling")

    def __init__(self) -> None:
        self.initialize_options()

    def initialize_options(self) -> None:
        self.starting_replicas = 0
        self.replica_increment = 5
        self.max_replicas = -1
        self.job_runtime = 0
        self.job_timeout = 0
        self.initial_replicas: list[int] = []

    def process_option(self, option: str, workload: str, runtimeclass: str) -> bool:
        from clusterbuster.ci.ci_options import ParsedCiOption, parse_ci_option

        p: ParsedCiOption | None = parse_ci_option(option, workload, runtimeclass)
        if p is None:
            return False
        n1 = p.noptname1
        v = p.optvalue
        if fnmatch.fnmatch(n1, "cpusoakerstarting*"):
            self.starting_replicas = int(v)
        elif fnmatch.fnmatch(n1, "cpusoakerreplicai*"):
            self.replica_increment = int(v)
        elif fnmatch.fnmatch(n1, "cpusoaker*runtime"):
            self.job_runtime = int(v)
        elif fnmatch.fnmatch(n1, "cpusoaker*timeout"):
            self.job_timeout = int(v)
        elif fnmatch.fnmatch(n1, "cpusoakermax*"):
            self.max_replicas = int(v)
        elif fnmatch.fnmatch(n1, "cpusoakerinit*"):
            self.initial_replicas = [int(x) for x in parse_optvalues(v)]
        else:
            return False
        return True

    def run(self, suite: Any, default_job_runtime: int) -> None:
        if self.replica_increment < 1:
            _LOG.warning("Replica increment must be at least 1")
            return
        for runtimeclass in suite.config.normalized_runtimeclasses():
            extra = suite.process_workload_options(self.name, runtimeclass)
            jr = self.job_runtime
            if jr <= 0:
                jr = default_job_runtime
            jt = compute_timeout(self.job_timeout, suite.config.job_timeout)
            if self.starting_replicas > 0:
                current = self.starting_replicas
            else:
                current = self.replica_increment
            idx = 0
            n_runs = 0
            while True:
                if idx < len(self.initial_replicas):
                    replicas = self.initial_replicas[idx]
                    idx += 1
                elif self.max_replicas == -1 or current <= self.max_replicas:
                    replicas = current
                    current += self.replica_increment
                else:
                    break
                job_name = str(replicas)
                st = suite.run_clusterbuster_1(
                    RunJobParams(
                        error_is_failure=False,
                        workload=self.name,
                        jobname=job_name,
                        runtimeclass=runtimeclass,
                        timeout=jt,
                        job_runtime=jr,
                        tail_argv=(
                            f"--replicas={replicas}",
                            "--failure-status=No Result",
                            "--cleanup-always=1",
                        ),
                    ),
                    extra_clusterbuster_args=extra,
                )
                if st != 0:
                    return
                n_runs += 1
                if suite.config.dontdoit and n_runs > 10:
                    break
