# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""fio workload: front end for the Flexible I/O tester."""

from __future__ import annotations

import base64
import logging
import os
import re
import tempfile
from typing import TYPE_CHECKING, Any

from clusterbuster.ci.compat import bool_str, parse_size

from ..workload_registry import (
    ArglistContext,
    DeploymentContext,
    WorkloadBase,
    pod_flags,
    register,
)

if TYPE_CHECKING:
    from ..config import ClusterbusterConfigBuilder

_LOG = logging.getLogger(__name__)


def _expand_string(template: str, overrides: dict[str, str] | None = None) -> str:
    """Expand ``%{var}`` and ``%{var:-default}`` placeholders.

    First checks *overrides*, then falls back to the calling environment
    (matching bash ``expand_string``).
    """
    overrides = overrides or {}
    result = template

    pattern = re.compile(
        r"%\{([A-Za-z0-9_]+(?:\[[A-Za-z0-9]+\])?)"
        r"(?::-((?:[^}]*)))?}"
    )

    while True:
        m = pattern.search(result)
        if not m:
            break
        var = m.group(1)
        default = m.group(2)
        if var in overrides:
            replacement = overrides[var]
        else:
            replacement = os.environ.get(var, "")
            if not replacement:
                replacement = default if default is not None else f"UNKNOWN{var}"
        result = result[:m.start()] + replacement + result[m.end():]

    return result


@register
class Fio(WorkloadBase):
    name = "fio"

    def __init__(self) -> None:
        self._patterns: list[str] = ["read"]
        self._blocksizes: list[int] = [4096]
        self._ioengines: list[str] = ["libaio"]
        self._iodepths: list[int] = [1]
        self._numjobs: list[int] = [1]
        self._directs: list[int] = [0]
        self._fdatasyncs: list[int] = [1]
        self._ramp_time = 5
        self._filesize = int(parse_size("4Gi"))
        self._workdir = ""
        self._drop_cache = 1
        self._job_file = "generic.jobfile"
        self._fio_options: list[str] = []
        self._processed_job_file = ""
        self._processed_job_content = ""

    def process_options(
        self, builder: ClusterbusterConfigBuilder, parsed: Any
    ) -> bool:
        n = parsed.noptname1
        v = parsed.optvalue

        tmp_pattern = ""
        tmp_blocksize = ""
        tmp_iodepth = ""
        tmp_numjobs = ""
        tmp_direct = ""
        tmp_fdatasync = ""
        tmp_ioengine = ""

        if n.startswith("fiopattern"):
            tmp_pattern = v
        elif n.startswith("fioblocksize"):
            tmp_blocksize = v
        elif n.startswith("fioop"):
            self._fio_options.append(v)
        elif n == "fiojobfile":
            self._job_file = v
        elif n.startswith("fioioengine"):
            tmp_ioengine = v
        elif n.startswith("fioiodepth"):
            tmp_iodepth = v
        elif n == "fionumjobs":
            tmp_numjobs = v
        elif n.startswith("fiodirect"):
            tmp_direct = v
        elif n == "fioramptime":
            self._ramp_time = int(v)
        elif n.startswith("fiofdatasync"):
            tmp_fdatasync = v
        elif n == "fiofilesize":
            self._filesize = int(parse_size(v))
        elif n == "fioworkdir":
            self._workdir = v
        elif n.startswith("fiodrop"):
            self._drop_cache = int(bool_str(v))
        else:
            return False

        if tmp_blocksize:
            self._blocksizes = [int(parse_size(s)) for s in tmp_blocksize.replace(",", " ").split() if s]
        if tmp_iodepth:
            self._iodepths = [int(parse_size(s)) for s in tmp_iodepth.replace(",", " ").split() if s]
        if tmp_numjobs:
            self._numjobs = [int(parse_size(s)) for s in tmp_numjobs.replace(",", " ").split() if s]
        if tmp_pattern:
            self._patterns = [s for s in tmp_pattern.replace(",", " ").split() if s]
        if tmp_ioengine:
            self._ioengines = [s for s in tmp_ioengine.replace(",", " ").split() if s]
        if tmp_direct:
            self._directs = [int(bool_str(s)) for s in tmp_direct.replace(",", " ").split() if s]
        if tmp_fdatasync:
            self._fdatasyncs = [int(parse_size(s)) for s in tmp_fdatasync.replace(",", " ").split() if s]

        return True

    def _resolve_and_expand_jobfile(self) -> None:
        """Resolve the jobfile path and expand templates."""
        job_file = self._job_file
        if "/" not in job_file:
            libdir = os.environ.get("CB_LIBPATH", "")
            if libdir:
                job_file = os.path.join(libdir, "workloads", "fio", job_file)
            else:
                candidate = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "..", "..", "..", "workloads", "fio", job_file,
                )
                if os.path.isfile(candidate):
                    job_file = candidate
        self._job_file = job_file

        if not os.path.isfile(self._job_file):
            raise SystemExit(f"Can't find job file {self._job_file}")

        with open(self._job_file) as fh:
            template = fh.read()

        env_vars = {
            "___fio_ramp_time": str(self._ramp_time),
            "___fio_drop_cache": str(self._drop_cache),
            "___fio_workdir": self._workdir,
            "___fio_filesize": str(self._filesize),
        }

        content = _expand_string(template, env_vars)
        self._processed_job_content = content

        tmpfd, tmppath = tempfile.mkstemp(prefix="02-fiojob", suffix="")
        with os.fdopen(tmpfd, "w") as fh:
            fh.write(content)
        self._processed_job_file = tmppath

    def finalize_extra_cli_args(
        self, builder: ClusterbusterConfigBuilder
    ) -> None:
        if not builder.container_image:
            builder.container_image = "quay.io/rkrawitz/clusterbuster-workloads:latest"
        if builder.workload_run_time == 0:
            builder.workload_run_time = 60
        if not self._workdir:
            self._workdir = builder.common_workdir
        self._resolve_and_expand_jobfile()

    def list_user_configmaps(self) -> list[str]:
        if self._processed_job_file:
            return [self._processed_job_file]
        return []

    def create_deployment(self, ctx: DeploymentContext) -> bool:
        return False

    def arglist(self, ctx: ArglistContext) -> list[str]:
        cfg = ctx.config
        args = [
            "python3", f"{ctx.mountdir}fio.py",
            *pod_flags(ctx),
            "--processes", str(cfg.processes_per_pod),
            "--rundir", self._workdir or cfg.common_workdir,
            "--runtime", str(cfg.workload_run_time),
            "--jobfiles-dir", "/etc/clusterbuster",
            "--blocksizes", " ".join(str(b) for b in self._blocksizes),
            "--patterns", " ".join(self._patterns),
            "--iodepths", " ".join(str(d) for d in self._iodepths),
            "--numjobs", " ".join(str(j) for j in self._numjobs),
            "--fdatasyncs", " ".join(str(f) for f in self._fdatasyncs),
            "--directs", " ".join(str(d) for d in self._directs),
            "--ioengines", " ".join(self._ioengines),
            "--ramptime", str(self._ramp_time),
            "--drop-cache", str(self._drop_cache),
        ]
        if self._fio_options:
            args.extend(["--fio-options", " ".join(self._fio_options)])
        return args

    def generate_metadata(self) -> dict[str, Any]:
        jobs: dict[str, Any] = {}
        idx = 1
        for blocksize in self._blocksizes:
            for pattern in self._patterns:
                for iodepth in self._iodepths:
                    for numjobs in self._numjobs:
                        for fdatasync in self._fdatasyncs:
                            for direct in self._directs:
                                for ioengine in self._ioengines:
                                    key = f"{idx:04d}-{pattern}-{blocksize}-{iodepth}-{numjobs}-{fdatasync}-{direct}-{ioengine}"
                                    jobs[key] = {
                                        "pattern": pattern,
                                        "blocksize": blocksize,
                                        "iodepth": iodepth,
                                        "numjobs": numjobs,
                                        "fdatasync": fdatasync,
                                        "direct": direct,
                                        "ioengine": ioengine,
                                    }
                                    idx += 1
        return {"jobs": jobs}

    def requires_drop_cache(self) -> bool:
        return bool(self._drop_cache)

    def requires_writable_workdir(self) -> bool:
        return True

    def report_options(self) -> dict[str, Any]:
        job_file_b64 = ""
        if self._processed_job_content:
            job_file_b64 = base64.b64encode(
                self._processed_job_content.encode()
            ).decode()

        return {
            "fio_options": " ".join(self._fio_options) if self._fio_options else "",
            "fio_job_file": job_file_b64,
            "fio_ioengine": list(self._ioengines),
            "fio_iodepth": list(self._iodepths),
            "fio_numjobs": list(self._numjobs),
            "fio_direct": list(self._directs),
            "fio_fdatasync": list(self._fdatasyncs),
            "fio_ioengines": list(self._ioengines),
            "fio_ramp_time": self._ramp_time,
            "fio_filesize": self._filesize,
            "fio_workdir": self._workdir,
            "fio_drop_cache": self._drop_cache,
        }

    def help_options(self) -> str:
        return """\
    Fio Options:
        --fio-patterns=<patterns>
                        Comma-separated list of patterns to use.
                        Any pattern supported by fio may be used.
                        Most common patterns are:
                        - read      (sequential read)
                        - write     (sequential write)
                        - randread  (random read)
                        - randwrite (random write)
                        - readwrite (sequential mixed read/write)
                        - randrw    (random mixed read/write)
        --fio-blocksizes=<sizes>
                        Comma-separated list of I/O blocksizes to use.
        --fio-option=<option>
                        Miscellaneous fio option.  May be repeated.
        --fio-jobfile=<file>
                        Name of fio job file to use (defaults to generic).
        --fio-ioengines=<engines>
                        Comma-separated list of names of ioengines to use
                        (default libaio)
        --fio-iodepth=<n>
                        Comma-separated list of I/O depths to use
                        (default 1)
        --fio-numjobs=<n>
                        Comma-separated list of job counts to use
                        (default 1)
        --fio-direct=<directs>
                        Comma-separated list of whether to use direct I/O
                        (default 0), values are 0 or 1.
        --fio-ramptime=<ramp time>
                        Ramp-up and down time (default 0)
        --fio-fdatasync=<fdatasyncs>
                        Comma-separated list of whether to use fdatasync
                        (default 0), values are 0 or 1.
        --fio-filesize=<size>
                        File size (default 32GiB)
        --fio-workdir=<dir>
                        Work directory (default /var/opt/clusterbuster)
        --fio-drop-cache=[0|1]
                        Drop cache, don't merely sync (default 0)"""

    def document(self) -> str:
        return ("fio: a front end for the Flexible I/O tester.\n"
                "  See https://fio.readthedocs.io/en/latest/fio_doc.html "
                "for more\n  details.")
