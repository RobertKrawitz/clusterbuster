# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0

from __future__ import annotations

import fnmatch
from typing import Any

from clusterbuster.ci.compat import parse_size
from clusterbuster.ci.compat.options import bool_str, bool_str_list, parse_optvalues
from clusterbuster.ci.execution import RunJobParams
from clusterbuster.ci.helpers import compute_timeout, computeit, get_node_memory_bytes, roundup_fio


class FioWorkload:
    name = "fio"
    aliases: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.initialize_options()

    def initialize_options(self) -> None:
        self.blocksizes = [1048576, 4096]
        self.patterns = ["read", "write", "randread", "randwrite", "readwrite", "randrw"]
        self.directs = [1]
        self.fdatasyncs = [0]
        self.iodepths = [1, 4]
        self.numjobs = [1]
        self.ioengines = ["sync", "libaio"]
        self.ninst = [1, 4]
        self.job_runtime = 0
        self.workdir = "/var/opt/clusterbuster"
        self.absolute_filesize = 0
        self.max_absolute_filesize = 0
        self.relative_filesize = 2
        self.max_relative_filesize = 2
        self.ramptime = 5
        self.job_timeout = 9000
        self.drop_cache = 1
        self.pod_memsize = 0
        self.volumes: list[str] = []

    def process_option(self, option: str, workload: str, runtimeclass: str) -> bool:
        from clusterbuster.ci.ci_options import ParsedCiOption, parse_ci_option

        p: ParsedCiOption | None = parse_ci_option(option, workload, runtimeclass)
        if p is None:
            return False
        n1 = p.noptname1
        v = p.optvalue
        if fnmatch.fnmatch(n1, "fioblock*"):
            self.blocksizes = [int(parse_size(x)) for x in parse_optvalues(v)]
        elif fnmatch.fnmatch(n1, "fiopat*"):
            self.patterns = parse_optvalues(v, split_commas=True)
        elif fnmatch.fnmatch(n1, "fiodirect*"):
            self.directs = [int(bool_str(x)) for x in bool_str_list(v)]
        elif fnmatch.fnmatch(n1, "fiofdatasync*"):
            self.fdatasyncs = [int(bool_str(x)) for x in bool_str_list(v)]
        elif fnmatch.fnmatch(n1, "fioiodepth*"):
            self.iodepths = [int(parse_size(x)) for x in parse_optvalues(v)]
        elif fnmatch.fnmatch(n1, "fionumjobs*"):
            self.numjobs = [int(parse_size(x)) for x in parse_optvalues(v)]
        elif fnmatch.fnmatch(n1, "fioioeng*"):
            self.ioengines = [x.strip() for x in v.split() if x.strip()]
        elif fnmatch.fnmatch(n1, "fioninst*"):
            self.ninst = [int(parse_size(x)) for x in parse_optvalues(v)]
        elif n1 == "fioworkdir":
            self.workdir = v
        elif fnmatch.fnmatch(n1, "fio*runtime"):
            self.job_runtime = int(v)
        elif fnmatch.fnmatch(n1, "fioramp*"):
            self.ramptime = int(v)
        elif fnmatch.fnmatch(n1, "fioabs*file*"):
            self.absolute_filesize = int(parse_size(v))
        elif fnmatch.fnmatch(n1, "fiomaxabs*file*"):
            self.max_absolute_filesize = int(parse_size(v))
        elif fnmatch.fnmatch(n1, "fiorel*file*"):
            self.relative_filesize = int(v)
        elif fnmatch.fnmatch(n1, "fiomaxrel*file*"):
            self.max_relative_filesize = int(v)
        elif fnmatch.fnmatch(n1, "fio*timeout"):
            self.job_timeout = int(v)
        elif fnmatch.fnmatch(n1, "fiodrop*"):
            self.drop_cache = int(bool_str(v))
        elif fnmatch.fnmatch(n1, "fio*memsize"):
            self.pod_memsize = int(parse_size(v))
        elif n1 == "volume":
            self.volumes.append(v)
        else:
            return False
        return True

    def _expand_volume(self, volspec: str, filesize: int) -> str:
        args = volspec.split(":")
        nargs: list[str] = []
        for arg in args:
            if arg.startswith("size=auto"):
                rest = arg[len("size=auto"):].lstrip(",")
                if rest:
                    vs = int(parse_size(rest))
                else:
                    vs = int(filesize * 9 / 8)
                vs = roundup_fio(vs)
                nargs.append(f"size={vs}")
            elif arg.startswith("inodes="):
                continue
            else:
                nargs.append(arg)
        return ":".join(nargs)

    def run(self, suite: Any, default_job_runtime: int) -> None:
        for runtimeclass in suite.config.normalized_runtimeclasses():
            extra = suite.process_workload_options(self.name, runtimeclass)
            for ninst in self.ninst:
                jr = self.job_runtime
                if jr <= 0:
                    jr = default_job_runtime
                jt = compute_timeout(self.job_timeout, suite.config.job_timeout)
                abs_fs = self.absolute_filesize
                max_abs = self.max_absolute_filesize
                if abs_fs <= 0 or max_abs <= 0:
                    client = suite.config.client_pin
                    if not client:
                        raise RuntimeError(
                            "fio needs node memory when file sizes are relative; set --ci-client-pin"
                        )
                    node_mem = get_node_memory_bytes(client)
                    if abs_fs <= 0:
                        abs_fs = computeit(f"{node_mem} * {self.relative_filesize}")
                    if max_abs <= 0:
                        max_abs = computeit(f"{node_mem} * {self.max_relative_filesize}")
                mem_annot = ""
                if self.pod_memsize > 0 and runtimeclass == "kata":
                    mem_annot = (
                        f'--pod-annotation=io.katacontainers.config.hypervisor.default_memory: "{self.pod_memsize}"'
                    )
                fs = computeit(f"{abs_fs} // {ninst}") if ninst else abs_fs
                if fs > max_abs:
                    fs = max_abs
                job_name = f"{ninst}P"
                nvolumes: list[str] = []
                for volspec in self.volumes:
                    expanded = self._expand_volume(volspec, fs)
                    nvolumes.append(f"--volume={expanded}")
                tail = [
                    f"--replicas={ninst}",
                    f"--fio-blocksize={','.join(str(x) for x in self.blocksizes)}",
                    f"--fio-patterns={','.join(self.patterns)}",
                    f"--fio-ioengines={','.join(self.ioengines)}",
                    f"--fio-iodepths={','.join(str(x) for x in self.iodepths)}",
                    f"--fio-numjobs={','.join(str(x) for x in self.numjobs)}",
                    f"--fio-fdatasyncs={','.join(str(x) for x in self.fdatasyncs)}",
                    f"--fio-directs={','.join(str(x) for x in self.directs)}",
                    f"--fio_filesize={fs}",
                    f"--fio_ramp_time={self.ramptime}",
                    f"--fio_workdir={self.workdir}",
                    f"--fio-drop-cache={self.drop_cache}",
                    *nvolumes,
                ]
                if mem_annot:
                    tail.append(mem_annot)
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
