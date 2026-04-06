# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from clusterbuster.ci.ci_options import parse_ci_option, splitarg
from clusterbuster.ci.config import ClusterbusterCISuiteConfig
from clusterbuster.ci.execution import RunJobParams, load_known_clusterbuster_options, run_clusterbuster_job
from clusterbuster.ci.registry import WorkloadPlugin, default_registry
from clusterbuster.ci.runner import ClusterbusterRunner


def _is_report_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    for name in ("report.json", "report.yaml", "report.yml"):
        if (path / name).is_file():
            return True
    return False


def _norm_opt(s: str) -> str:
    return re.sub(r"[-_]", "", s.lower())


class ClusterbusterCISuite:
    """Orchestrates CI workloads (``run-perf-ci-suite`` / ``*.ci`` parity)."""

    def __init__(
        self,
        config: ClusterbusterCISuiteConfig,
        *,
        runner: ClusterbusterRunner | None = None,
        clusterbuster_exe: Path | str | None = None,
        registry: dict[str, WorkloadPlugin] | None = None,
    ) -> None:
        self.config = config
        self.runner = runner or ClusterbusterRunner(clusterbuster_exe)
        top = Path(__file__).resolve().parents[3]
        self._cb_exe = Path(clusterbuster_exe) if clusterbuster_exe else top / "clusterbuster"
        self._registry = registry or default_registry()
        self._known_cb: set[str] | None = None
        self.jobs: list[str] = []
        self.failures: list[str] = []
        self.job_runtimes: dict[str, str] = {}
        self._global_job_counter = 0

    def process_workload_options(self, workload: str, runtimeclass: str) -> list[str]:
        plugin = self._registry.get(workload)
        if plugin is None:
            raise KeyError(workload)
        plugin.initialize_options()
        extra_cb: list[str] = []
        rc = runtimeclass or "pod"
        for option in self.config.extra_args:
            p = parse_ci_option(option, workload, rc)
            if p is None or not p.noptname1:
                continue
            optvalue = splitarg(p.optvalue)
            handled = plugin.process_option(option, workload, rc)
            if not handled:
                if self._known_cb is None:
                    try:
                        self._known_cb = load_known_clusterbuster_options(self._cb_exe)
                    except OSError:
                        self._known_cb = set()
                key = _norm_opt(p.noptname1)
                if key in self._known_cb:
                    extra_cb.append(f"--{p.noptname}={optvalue}")
        return extra_cb

    def run_clusterbuster_1(
        self,
        params: RunJobParams,
        *,
        extra_clusterbuster_args: Sequence[str] | None = None,
        increment_global_counter: bool = False,
    ) -> int:
        merged = list(extra_clusterbuster_args or []) + list(self.config.extra_clusterbuster_args)
        counter = self._global_job_counter
        from clusterbuster.ci.ci_options import split_quoted_args

        debugargs = split_quoted_args(self.config.debug_args)
        status, hms = run_clusterbuster_job(
            self.runner,
            clusterbuster_exe=self._cb_exe,
            artifactdir=self.config.artifactdir,
            params=params,
            counter=counter,
            dontdoit=self.config.dontdoit,
            uuid=self.config.uuid or "local-uuid",
            compress_report=self.config.compress,
            report_format=self.config.report_format or "none",
            debugargs=debugargs,
            client_pin_node=self.config.client_pin,
            server_pin_node=self.config.server_pin,
            sync_pin_node=self.config.sync_pin,
            job_delay=self.config.job_delay,
            unique_job_prefix=self.config.unique_prefix,
            extra_clusterbuster_args=merged,
            force_cleanup_timeout=self.config.force_cleanup_timeout or None,
            cwd=self._cb_exe.parent,
            debugonly=False,
            restart=self.config.restart,
            is_report_dir=_is_report_dir,
        )
        rc_label = params.runtimeclass or "runc"
        full_jobname = f"{params.workload}-{rc_label}-{counter:04d}-{params.jobname}"
        self.job_runtimes[full_jobname] = hms
        if status == 0:
            self.jobs.append(full_jobname)
        elif params.error_is_failure:
            self.failures.append(full_jobname)
        if increment_global_counter:
            self._global_job_counter += 1
        return status

    def run(self) -> int:
        default_rt = self.config.default_job_runtime
        for wl in self.config.normalized_workloads():
            self._global_job_counter = 0
            plugin = self._registry.get(wl)
            if plugin is None:
                print(f"Unsupported workload {wl}", flush=True)
                return 1
            plugin.run(self, default_rt)
        return 1 if self.failures else 0
