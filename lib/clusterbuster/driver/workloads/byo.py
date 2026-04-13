# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""byo (bring-your-own) workload: run a user-supplied command."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from clusterbuster.ci.compat import bool_str

from ..workload_registry import ArglistContext, WorkloadBase, pod_flags, register

if TYPE_CHECKING:
    from ..config import ClusterbusterConfigBuilder

_LOG = logging.getLogger(__name__)


@register
class Byo(WorkloadBase):
    name = "byo"
    aliases = ("byowl", "bringyourown")

    def __init__(self) -> None:
        self._workdir = ""
        self._drop_cache = 0
        self._byo_name = ""
        self._byo_workload = ""
        self._extra_files: list[str] = []
        self._args: list[str] = []

    def process_options(
        self, builder: ClusterbusterConfigBuilder, parsed: Any
    ) -> bool:
        n = parsed.noptname1
        v = parsed.optvalue
        if n.startswith("byofile"):
            self._extra_files.append(v)
        elif n.startswith("byoname"):
            self._byo_name = v
        elif n.startswith("byoworkload"):
            self._byo_workload = v
            self._byo_name = v
        elif n.startswith("byoworkdir"):
            self._workdir = v
        elif n == "byodropcache":
            self._drop_cache = int(bool_str(v))
        else:
            return False
        return True

    def finalize_extra_cli_args(
        self, builder: ClusterbusterConfigBuilder
    ) -> None:
        self._args = list(builder.extra_args)
        if not self._args:
            raise SystemExit("No command specified for byo workload")

        if self._args:
            builder.processed_options.append("--")
        builder.processed_options.extend(builder.extra_args)

    def list_user_configmaps(self) -> list[str]:
        result: list[str] = []
        if self._args:
            result.append(self._args[0])
        result.extend(self._extra_files)
        return result

    def arglist(self, ctx: ArglistContext) -> list[str]:
        cfg = ctx.config
        workdir = self._workdir or f"{cfg.common_workdir}/cb-work"
        args_copy = list(self._args)
        if args_copy:
            args_copy[0] = os.path.basename(args_copy[0])
        return [
            "python3", f"{ctx.mountdir}byo.py",
            *pod_flags(ctx),
            "--processes", str(cfg.processes_per_pod),
            "--workdir", workdir,
            "--",
            *args_copy,
        ]

    def workload_reporting_class(self) -> str:
        if self._byo_name:
            return self._byo_name
        if self._args:
            base = os.path.basename(self._args[0])
            return f"byo_{base.replace('-', '_')}"
        return "byo"

    def requires_drop_cache(self) -> bool:
        return bool(self._drop_cache)

    def requires_writable_workdir(self) -> bool:
        return True

    def report_options(self) -> dict[str, Any]:
        return {
            "byo_workload": self._byo_workload,
            "byo_workdir": self._workdir,
            "byo_drop_cache": str(self._drop_cache),
            "byo_files": list(self._extra_files),
            "byo_args": list(self._args),
        }

    def help_options(self) -> str:
        return """\
    Bring Your Own Workload Options:
        Usage: clusterbuster [clusterbuster_args] -- command args...
        The first argument after -- is the command: an absolute path, a path
        relative to the workload file directory, or a name resolved there first
        and then on PATH (e.g. sleep -> /usr/bin/sleep).  Further arguments are
        passed to the command.
        --byo-file=<file>
                        Additional file to insert into the pod.
                        The command to run is automatically inserted
                        into the pod.  There is a limit of 1 MB on
                        the total size of all files inserted into the
                        pod.
        --byo-workload=<name>
                        Name of the workload for report generation.
                        Name should start with a letter and consist of
                        alphanumeric characters and underscores.
        --byo-workdir=<name>
                        Path of the directory into which all workload
                        files are installed.
        --byo-drop-cache=[0|1]
                        Allow the workload to drop buffer cache by means
                        of running 'drop-cache'."""

    def document(self) -> str:
        return "byo: bring your own workload"
