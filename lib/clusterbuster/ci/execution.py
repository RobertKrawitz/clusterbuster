# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
"""Per-job argv and subprocess run (bash ``run_clusterbuster_1`` parity)."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from clusterbuster.ci.runner import ClusterbusterRunner

_LOG = logging.getLogger("clusterbuster.ci.execution")


@dataclass
class RunJobParams:
    error_is_failure: bool = True
    jobname: str = ""
    runtimeclass: str = ""
    workload: str = ""
    timeout: int | None = None
    job_runtime: int | None = None
    tail_argv: tuple[str, ...] = ()


def _to_hms(start: int, end: int) -> str:
    interval = end - start
    h = interval // 3600
    m = (interval % 3600) // 60
    s = interval % 60
    if h > 0:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


def build_clusterbuster_argv(
    *,
    dontdoit: bool,
    uuid: str,
    compress_report: bool,
    report_format: str,
    workload: str,
    debugargs: list[str],
    job_runtime: int | None,
    client_pin_node: str,
    server_pin_node: str,
    sync_pin_node: str,
    timeout: int | None,
    jobname: str,
    tmp_jobdir: Path | None,
    force_cleanup_timeout: str | None,
    unique_job_prefix: bool,
    job_prefix: str,
    extra_clusterbuster_args: Sequence[str],
    runtimeclass: str,
    tail: Sequence[str],
) -> list[str]:
    argv: list[str] = []
    if dontdoit:
        argv.append("-n")
    argv.append(f"--uuid={uuid}")
    if compress_report:
        argv.append("-z")
    argv.extend(
        [
            "--precleanup",
            "--image-pull-policy=IfNotPresent",
            "--retrieve-successful-logs=1",
            "--metrics",
            f"--report={report_format}",
            f"--workload={workload}",
        ]
    )
    argv.extend(debugargs)
    if job_runtime is not None:
        argv.append(f"--workload_runtime={job_runtime}")
    if client_pin_node:
        argv.append(f"--pin-node=client={client_pin_node}")
    if server_pin_node:
        argv.append(f"--pin-node=server={server_pin_node}")
    if sync_pin_node:
        argv.append(f"--pin-node=sync={sync_pin_node}")
    if timeout is not None:
        argv.append(f"--timeout={timeout}")
    if jobname:
        argv.append(f"--jobname={jobname}")
    if tmp_jobdir is not None:
        argv.append(f"--artifactdir={tmp_jobdir}")
    if force_cleanup_timeout:
        argv.append(f"--force-cleanup-i-know-this-is-dangerous={force_cleanup_timeout}")
    if unique_job_prefix:
        argv.append(f"--pod-prefix={job_prefix}")
    argv.extend(extra_clusterbuster_args)
    if runtimeclass:
        argv.append(f"--runtimeclass={runtimeclass}")
    argv.extend(tail)
    return argv


def run_clusterbuster_job(
    runner: ClusterbusterRunner,
    *,
    clusterbuster_exe: Path,
    artifactdir: Path | None,
    params: RunJobParams,
    counter: int,
    dontdoit: bool,
    uuid: str,
    compress_report: bool,
    report_format: str,
    debugargs: list[str],
    client_pin_node: str,
    server_pin_node: str,
    sync_pin_node: str,
    job_delay: int,
    unique_job_prefix: bool,
    extra_clusterbuster_args: Sequence[str],
    force_cleanup_timeout: str | None,
    cwd: Path | None,
    debugonly: bool,
    restart: bool,
    is_report_dir: Callable[[Path], bool] | None = None,
) -> tuple[int, str]:
    """Run one job; return (exit status, human-readable duration)."""
    workload = params.workload
    runtimeclass = params.runtimeclass
    jobname = params.jobname
    if not jobname or not workload:
        raise ValueError("job name and workload are required")

    rc_label = runtimeclass or "runc"
    job_prefix = f"{workload}-{rc_label}-{counter:04d}"
    full_jobname = f"{workload}-{rc_label}-{counter:04d}-{jobname}"
    jobdir = artifactdir / full_jobname if artifactdir else None
    tmp_jobdir = Path(str(jobdir) + ".tmp") if jobdir else None

    if (
        not debugonly
        and jobdir
        and is_report_dir
        and is_report_dir(jobdir)
    ):
        if restart:
            _LOG.info("%s is already present", full_jobname)
            return (0, "0:00")
        shutil.rmtree(jobdir, ignore_errors=True)

    time.sleep(job_delay)
    t0 = int(time.time())
    _LOG.info("")
    _LOG.info("*** Running %s at (epoch %s)", full_jobname, t0)

    argv = build_clusterbuster_argv(
        dontdoit=dontdoit,
        uuid=uuid,
        compress_report=compress_report,
        report_format=report_format,
        workload=workload,
        debugargs=debugargs,
        job_runtime=params.job_runtime,
        client_pin_node=client_pin_node,
        server_pin_node=server_pin_node,
        sync_pin_node=sync_pin_node,
        timeout=params.timeout,
        jobname=full_jobname,
        tmp_jobdir=tmp_jobdir,
        force_cleanup_timeout=force_cleanup_timeout,
        unique_job_prefix=unique_job_prefix,
        job_prefix=job_prefix,
        extra_clusterbuster_args=extra_clusterbuster_args,
        runtimeclass=runtimeclass,
        tail=params.tail_argv,
    )
    res = runner.run(argv, cwd=cwd)
    out = (res.stdout or "") + (res.stderr or "")
    if out:
        _LOG.info("%s", out.rstrip("\n"))
    status = res.returncode
    t1 = int(time.time())
    hms = _to_hms(t0, t1)
    _LOG.info("Job took %s, done at (epoch %s)", hms, t1)

    if debugonly:
        return status, hms

    if status == 0 and jobdir and tmp_jobdir:
        # Bash always passes ``--artifactdir=$tmp_jobdir`` (``…/jobname.tmp``) and
        # renames to ``jobdir`` on success. Some clusterbuster builds also rename
        # ``.tmp`` → final internally, leaving ``jobdir`` present before we run.
        if tmp_jobdir.exists():
            if jobdir.exists():
                shutil.rmtree(jobdir)
            tmp_jobdir.rename(jobdir)
        elif not jobdir.exists():
            raise RuntimeError(
                f"success but no artifacts at {tmp_jobdir} or {jobdir}"
            )
        # else: only ``jobdir`` exists — driver already finalized; nothing to rename.
    elif status != 0 and jobdir and tmp_jobdir and tmp_jobdir.exists():
        base = Path(f"{jobdir}.FAIL")
        fail_jobdir = base
        idx = 1
        while fail_jobdir.exists():
            fail_jobdir = Path(f"{base}.{idx}")
            idx += 1
        tmp_jobdir.rename(fail_jobdir)

    return status, hms


_CB_OPTION_CACHE: set[str] | None = None


def load_known_clusterbuster_options(clusterbuster: Path) -> set[str]:
    """Options from ``clusterbuster -h`` (normalized alnum only), cached."""
    global _CB_OPTION_CACHE
    if _CB_OPTION_CACHE is not None:
        return _CB_OPTION_CACHE
    proc = subprocess.run(
        [str(clusterbuster), "-h"],
        capture_output=True,
        text=True,
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    opts: set[str] = set()
    for m in re.finditer(r"^\s*--([-_a-zA-Z0-9]+)", text, re.MULTILINE):
        opts.add(re.sub(r"[-_]", "", m.group(1).lower()))
    _CB_OPTION_CACHE = opts
    return opts
