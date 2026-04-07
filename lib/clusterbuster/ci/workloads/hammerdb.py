# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0

from __future__ import annotations

import fnmatch
import logging
from typing import Any

from clusterbuster.ci.compat.options import parse_optvalues
from clusterbuster.ci.execution import RunJobParams
from clusterbuster.ci.helpers import compute_timeout

_LOG = logging.getLogger("clusterbuster.ci.workloads.hammerdb")


class HammerdbWorkload:
    name = "hammerdb"
    aliases: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.initialize_options()

    def initialize_options(self) -> None:
        self.drivers = ["pg", "mariadb"]
        self.job_runtime = 0
        self.job_timeout = 0
        self.replicas = 2
        self.rampup = 1
        self.virtual_users = 4
        self.benchmark = "tpcc"
        self.params: list[str] = []

    def process_option(self, option: str, workload: str, runtimeclass: str) -> bool:
        from clusterbuster.ci.ci_options import ParsedCiOption, parse_ci_option

        p: ParsedCiOption | None = parse_ci_option(option, workload, runtimeclass)
        if p is None:
            return False
        n1 = p.noptname1
        v = p.optvalue
        if fnmatch.fnmatch(n1, "hammerdbdriver*"):
            self.drivers = [x.strip().lower() for x in parse_optvalues(v)]
        elif fnmatch.fnmatch(n1, "hammerdbruntime"):
            self.job_runtime = int(v)
        elif fnmatch.fnmatch(n1, "hammerdbtimeout"):
            self.job_timeout = int(v)
        elif fnmatch.fnmatch(n1, "hammerdbreplicas"):
            self.replicas = int(v)
        elif fnmatch.fnmatch(n1, "hammerdbrampup"):
            self.rampup = int(v)
        elif fnmatch.fnmatch(n1, "hammerdbvirtual*"):
            self.virtual_users = int(v)
        elif fnmatch.fnmatch(n1, "hammerdbbench*"):
            self.benchmark = v.strip()
        elif fnmatch.fnmatch(n1, "hammerdb*params"):
            self.params.extend(parse_optvalues(v))
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
            rc_label = runtimeclass or "runc"
            if not self.params:
                specs = [
                    f"{jr}:{driver}:{self.replicas}:{self.rampup}:{self.virtual_users}:{self.benchmark}"
                    for driver in self.drivers
                ]
            else:
                specs = list(self.params)
            for options in specs:
                parts = options.split(":")
                if len(parts) < 6:
                    _LOG.warning("Unparsable hammerdb options %r", options)
                    continue
                per_s, driver, replicas_s, rampup_s, vu_s, bench = parts[:6]
                if not per_s or not driver or not replicas_s or not rampup_s or not vu_s or not bench:
                    _LOG.warning("Unparsable hammerdb options %r", options)
                    continue
                if not (
                    per_s.isdigit()
                    and replicas_s.isdigit()
                    and rampup_s.isdigit()
                    and vu_s.isdigit()
                ):
                    _LOG.warning("Invalid hammerdb options %r", options)
                    continue
                driver_l = driver.lower()
                if driver_l not in ("pg", "mariadb"):
                    _LOG.warning("Invalid driver %r in hammerdb options", driver)
                    continue
                job_name = f"{driver_l}-{rc_label}-R{per_s}-r{replicas_s}-{vu_s}vu-{bench}"
                suite.run_clusterbuster_1(
                    RunJobParams(
                        workload=self.name,
                        jobname=job_name,
                        runtimeclass=runtimeclass,
                        timeout=jt,
                        job_runtime=int(per_s),
                        tail_argv=(
                            f"--replicas={replicas_s}",
                            f"--hammerdb-driver={driver_l}",
                            f"--hammerdb-rampup={rampup_s}",
                            f"--hammerdb-virtual-users={vu_s}",
                            f"--hammerdb-benchmark={bench}",
                        ),
                    ),
                    extra_clusterbuster_args=extra,
                )
