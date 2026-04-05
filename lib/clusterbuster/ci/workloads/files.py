# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0

from __future__ import annotations

import fnmatch
from typing import Any

from clusterbuster.ci.compat import parse_size
from clusterbuster.ci.compat.options import bool_str, bool_str_list, parse_optvalues
from clusterbuster.ci.compat.sizes import parse_size_colon_line
from clusterbuster.ci.execution import RunJobParams
from clusterbuster.ci.helpers import compute_timeout, roundup_interval


class FilesWorkload:
    name = "files"
    aliases: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.initialize_options()

    def initialize_options(self) -> None:
        self.ninst = [1, 4]
        self.dirs_per_volume = [256]
        self.per_dir = [256]
        self.block_sizes = [4096, 65536]
        self.sizes = [0, 4096, 256 * 1024]
        self.directs = [0, 1]
        self.job_timeout = 9000
        self.params: list[str] = []
        self.min_direct = 1024
        self.drop_cache = 1
        self.volumes: list[str] = []

    def process_option(self, option: str, workload: str, runtimeclass: str) -> bool:
        from clusterbuster.ci.ci_options import ParsedCiOption, parse_ci_option

        p: ParsedCiOption | None = parse_ci_option(option, workload, runtimeclass)
        if p is None:
            return False
        n1 = p.noptname1
        v = p.optvalue
        if fnmatch.fnmatch(n1, "filesninst*"):
            self.ninst = [int(parse_size(x)) for x in parse_optvalues(v)]
        elif fnmatch.fnmatch(n1, "filesdirs*"):
            self.dirs_per_volume = [int(parse_size(x)) for x in parse_optvalues(v)]
        elif fnmatch.fnmatch(n1, "filesperdir*"):
            self.per_dir = [int(parse_size(x)) for x in parse_optvalues(v)]
        elif fnmatch.fnmatch(n1, "filesblocksize*"):
            self.block_sizes = [int(parse_size(x)) for x in parse_optvalues(v)]
        elif fnmatch.fnmatch(n1, "filessize*"):
            self.sizes = [int(parse_size(x)) for x in parse_optvalues(v)]
        elif fnmatch.fnmatch(n1, "filesdirect*"):
            self.directs = [int(bool_str(x)) for x in bool_str_list(v)]
        elif fnmatch.fnmatch(n1, "files*timeout"):
            self.job_timeout = int(v)
        elif fnmatch.fnmatch(n1, "files*params"):
            self.params.extend(parse_optvalues(v))
        elif fnmatch.fnmatch(n1, "filesmindir*"):
            self.min_direct = int(parse_size(v))
        elif fnmatch.fnmatch(n1, "filesdrop*"):
            self.drop_cache = int(bool_str(v))
        elif n1 == "volume":
            self.volumes.append(v)
        else:
            return False
        return True

    def _build_params(self) -> list[str]:
        if self.params:
            return list(self.params)
        out: list[str] = []
        for ninst in self.ninst:
            for dirs in self.dirs_per_volume:
                for files in self.per_dir:
                    for blocksize in self.block_sizes:
                        for size in self.sizes:
                            for direct in self.directs:
                                if (not direct) or (blocksize >= self.min_direct):
                                    out.append(
                                        f"{ninst}:{dirs}:{files}:{blocksize}:{size}:{direct}"
                                    )
        return out

    def _expand_volume(
        self,
        volspec: str,
        bytes_required: int,
        inodes_required: int,
        fsopts: list[str],
    ) -> str:
        args = volspec.split(":")
        nargs: list[str] = []
        for arg in args:
            if arg.startswith("size=auto"):
                nargs.append(f"size={bytes_required}")
            elif arg.startswith("inodes=auto"):
                nargs.append(f"inodes={inodes_required}")
            elif arg.startswith("fsopts="):
                fsopts.extend(arg[len("fsopts="):].split())
            else:
                nargs.append(arg)
        if fsopts:
            nargs.append("fsopts=" + " ".join(fsopts))
        return ":".join(nargs)

    def run(self, suite: Any, default_job_runtime: int) -> None:
        for runtimeclass in suite.config.normalized_runtimeclasses():
            extra = suite.process_workload_options(self.name, runtimeclass)
            jt = compute_timeout(self.job_timeout, suite.config.job_timeout)
            fs_block_size = 4096
            inode_size_bytes = 256
            for options in self._build_params():
                vals = parse_size_colon_line(options)
                if len(vals) < 6:
                    print(f"Unparsable options {options!r}", flush=True)
                    continue
                ninst, dirs_v, files_pd, block_sz, file_size, direct = vals[:6]
                if block_sz > file_size and file_size > 0:
                    block_sz = file_size
                job_name = (
                    f"{ninst}P-{dirs_v}D-{files_pd}F-{block_sz}B-{file_size}S-{direct}T"
                )
                bytes_per_file = max(file_size, fs_block_size)
                bytes_per_file = roundup_interval(bytes_per_file, fs_block_size)
                inodes_required = 1024 + dirs_v + (dirs_v * files_pd)
                bytes_required = (inodes_required * inode_size_bytes) + (
                    bytes_per_file * dirs_v * files_pd
                )
                bytes_required = roundup_interval((bytes_required * 9) // 8, 1048576)
                bytes_required = max(bytes_required, 32 * 1048576)
                nvolumes: list[str] = []
                for volspec in self.volumes:
                    fsopts: list[str] = []
                    expanded = self._expand_volume(volspec, bytes_required, inodes_required, fsopts)
                    nvolumes.append(f"--volume={expanded}")
                tail = [
                    f"--replicas={ninst}",
                    f"--dirs_per_volume={dirs_v}",
                    f"--files_per_dir={files_pd}",
                    f"--file_block_size={block_sz}",
                    f"--files_direct={direct}",
                    f"--filesize={file_size}",
                    *nvolumes,
                ]
                suite.run_clusterbuster_1(
                    RunJobParams(
                        workload=self.name,
                        jobname=job_name,
                        runtimeclass=runtimeclass,
                        timeout=jt,
                        job_runtime=None,
                        tail_argv=tuple(tail),
                    ),
                    extra_clusterbuster_args=extra,
                )
