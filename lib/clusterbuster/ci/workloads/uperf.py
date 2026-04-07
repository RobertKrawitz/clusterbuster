# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0

from __future__ import annotations

import fnmatch
from typing import Any

from clusterbuster.ci.compat.options import bool_str_y_empty, parse_optvalues
from clusterbuster.ci.compat.sizes import parse_size_list
from clusterbuster.ci.execution import RunJobParams
from clusterbuster.ci.helpers import compute_timeout


class UperfWorkload:
    name = "uperf"
    aliases: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.initialize_options()

    def initialize_options(self) -> None:
        self.msg_sizes = [64, 1024, 8192]
        self.nthrs = [1, 4]
        self.ninst = [1, 4]
        self.test_types = ["stream", "rr"]
        self.job_runtime = 0
        self.job_timeout = 0
        self.use_annotation = "1"

    def process_option(self, option: str, workload: str, runtimeclass: str) -> bool:
        from clusterbuster.ci.ci_options import ParsedCiOption, parse_ci_option

        p: ParsedCiOption | None = parse_ci_option(option, workload, runtimeclass)
        if p is None:
            return False
        n1 = p.noptname1
        v = p.optvalue
        if fnmatch.fnmatch(n1, "uperfmsg*"):
            self.msg_sizes = parse_size_list(v)
        elif fnmatch.fnmatch(n1, "uperfnthr*"):
            self.nthrs = parse_size_list(v)
        elif fnmatch.fnmatch(n1, "uperfninst*"):
            self.ninst = parse_size_list(v)
        elif fnmatch.fnmatch(n1, "uperftest*"):
            self.test_types = parse_optvalues(v)
        elif fnmatch.fnmatch(n1, "uperf*runtime"):
            self.job_runtime = int(v)
        elif fnmatch.fnmatch(n1, "uperf*timeout"):
            self.job_timeout = int(v)
        elif fnmatch.fnmatch(n1, "uperfann*"):
            self.use_annotation = bool_str_y_empty(v)
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
            for msg_size in self.msg_sizes:
                for nthr in self.nthrs:
                    for ninst in self.ninst:
                        for test_type in self.test_types:
                            job_name = f"{msg_size}B-{nthr}i-{ninst}P-{test_type}"
                            tail = [
                                f"--replicas={ninst}",
                                f"--uperf_msg_size={msg_size}",
                                f"--uperf_test_type={test_type}",
                                "--uperf_proto=tcp",
                                f"--uperf_nthr={nthr}",
                            ]
                            if self.use_annotation == "1":
                                tail.append(
                                    '--pod-annotation=io.katacontainers.config.hypervisor.default_vcpus: "%s"'
                                    % nthr
                                )
                            suite.run_clusterbuster_1(
                                RunJobParams(
                                    workload=self.name,
                                    jobname=job_name,
                                    runtimeclass=runtimeclass,
                                    timeout=jt,
                                    job_runtime=jr,
                                    tail_argv=tuple(tail),
                                ),
                                extra_clusterbuster_args=extra,
                            )
