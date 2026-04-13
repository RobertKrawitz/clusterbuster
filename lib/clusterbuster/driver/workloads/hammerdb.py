# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""hammerdb workload: TPC-C / TPROC-C database benchmark."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..workload_registry import ArglistContext, WorkloadBase, pod_flags, register

if TYPE_CHECKING:
    from ..config import ClusterbusterConfigBuilder

_LOG = logging.getLogger(__name__)


@register
class HammerDB(WorkloadBase):
    name = "hammerdb"
    aliases = ("hammer",)

    def __init__(self) -> None:
        self._driver = "pg"
        self._database = "hammerdb"
        self._benchmark = "tpcc"
        self._virtual_users = 4
        self._rampup = 1
        self._workdir = ""

    def process_options(
        self, builder: ClusterbusterConfigBuilder, parsed: Any
    ) -> bool:
        n = parsed.noptname1
        v = parsed.optvalue
        if n == "hammerdbdriver":
            self._driver = v
        elif n == "hammerdbdatabase":
            self._database = v
        elif n == "hammerdbbenchmark":
            self._benchmark = v
        elif n == "hammerdbvirtualusers":
            self._virtual_users = int(v)
        elif n == "hammerdbrampup":
            self._rampup = int(v)
        elif n == "hammerdbworkdir":
            self._workdir = v
        else:
            return False

        if not self._driver:
            _LOG.warning("HammerDB: driver is required; defaulting to pg")
            self._driver = "pg"
        if self._driver not in ("pg", "mariadb"):
            _LOG.warning(
                "HammerDB: only pg and mariadb are supported; got %s",
                self._driver,
            )
            self._driver = "pg"

        return True

    def finalize_extra_cli_args(
        self, builder: ClusterbusterConfigBuilder
    ) -> None:
        if not builder.container_image:
            builder.container_image = "quay.io/rkrawitz/clusterbuster-hammerdb:latest"
        if builder.vm_image == "quay.io/rkrawitz/clusterbuster-vm:latest":
            builder.vm_image = "quay.io/rkrawitz/clusterbuster-hammerdb-vm:latest"
        if builder.vm_cores <= 1:
            builder.vm_cores = 2
        if builder.vm_memory == "2Gi":
            builder.vm_memory = "4Gi"
        if (
            builder.deployment_type.lower() == "vm"
            and builder.pod_start_timeout <= 180
        ):
            builder.pod_start_timeout = 600

    def arglist(self, ctx: ArglistContext) -> list[str]:
        cfg = ctx.config
        workdir = self._workdir or cfg.common_workdir
        return [
            "python3", f"{ctx.mountdir}hammerdb.py",
            *pod_flags(ctx),
            "--processes", str(cfg.processes_per_pod),
            "--workdir", workdir,
            "--runtime", str(cfg.workload_run_time),
            "--driver", self._driver,
            "--database", self._database,
            "--benchmark", self._benchmark,
            "--virtual-users", str(self._virtual_users),
            "--rampup", str(self._rampup),
        ]

    def report_options(self) -> dict[str, Any]:
        return {
            "hammerdb_driver": self._driver,
            "hammerdb_database": self._database,
            "hammerdb_benchmark": self._benchmark,
            "hammerdb_virtual_users": self._virtual_users,
            "hammerdb_rampup": self._rampup,
        }

    def help_options(self) -> str:
        return """\
    HammerDB Options (initial support: PostgreSQL and MariaDB only):
       The HammerDB client and database server run colocated (same pod or VM).

        --hammerdb-driver=<driver>
                        Database driver: pg (PostgreSQL) or mariadb (default: pg).
        --hammerdb-database=<name>
                        Database/schema name (default: hammerdb).
        --hammerdb-benchmark=<benchmark>
                        Benchmark type: tpcc or tprocc (default: tpcc).
        --hammerdb-virtual-users=<n>
                        Number of virtual users (default: 4).
        --hammerdb-rampup=<minutes>
                        Ramp-up time in minutes before timed run (default: 1).
        --hammerdb-workdir=<dir>
                        Work directory (default: common workdir).
       Run duration is controlled by --workload-runtime.

       Use the clusterbuster-hammerdb container and VM images; they include
       HammerDB plus PostgreSQL and MariaDB. The VM disk is built like
       clusterbuster-vm (virt-install + firstboot).
       HammerDB is not supported on arm64 (no package shipped yet);
       see https://github.com/TPC-Council/HammerDB/discussions/767"""

    def document(self) -> str:
        return ("hammerdb: TPC-C and TPROC-C database benchmark reporting "
                "NOPM and TPM.\n  Client and database run in the same pod or "
                "VM. See https://www.hammerdb.com/")
