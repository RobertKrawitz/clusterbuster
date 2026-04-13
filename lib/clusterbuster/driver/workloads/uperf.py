# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""uperf workload: partial front end to uperf (https://www.uperf.org)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..workload_registry import ArglistContext, WorkloadBase, pod_flags, register

if TYPE_CHECKING:
    from ..config import ClusterbusterConfig, ClusterbusterConfigBuilder

_LOG = logging.getLogger(__name__)


@register
class Uperf(WorkloadBase):
    name = "uperf"

    def __init__(self) -> None:
        self._msg_sizes: list[int] = [1024]
        self._test_types: list[str] = ["stream"]
        self._protocols: list[str] = ["tcp"]
        self._nthrs: list[int] = [1]
        self._ramp_time = 3
        self._workload_run_time = 0
        self._port = 30000
        self._port_addrs = 24
        self._tests: list[str] = []

    def process_options(
        self, builder: ClusterbusterConfigBuilder, parsed: Any
    ) -> bool:
        n = parsed.noptname1
        v = parsed.optvalue

        if n.startswith("uperfmsgsize"):
            self._msg_sizes = [int(s) for s in v.replace(",", " ").split() if s]
        elif n.startswith("uperftesttype"):
            self._test_types = [s for s in v.replace(",", " ").split() if s]
        elif n.startswith("uperfproto"):
            self._protocols = [s for s in v.replace(",", " ").split() if s]
        elif n.startswith("uperfnthr"):
            self._nthrs = [int(s) for s in v.replace(",", " ").split() if s]
        elif n.startswith("uperframp"):
            self._ramp_time = int(v)
        else:
            return False

        if self._msg_sizes and self._msg_sizes[0] <= 0:
            raise SystemExit("Message size must be positive, exiting!")
        if self._ramp_time < 0:
            self._ramp_time = 0

        self._tests = []
        for testtype in self._test_types:
            for proto in self._protocols:
                for msgsize in self._msg_sizes:
                    for nthr in self._nthrs:
                        self._tests.append(
                            f"{testtype},{proto},{msgsize},{nthr}"
                        )
        return True

    def finalize_extra_cli_args(
        self, builder: ClusterbusterConfigBuilder
    ) -> None:
        if not builder.container_image:
            builder.container_image = "quay.io/rkrawitz/clusterbuster-workloads:latest"
        builder.processes_per_pod = 1

    def _expand_port_addrs(
        self, replicas: int, containers_per_pod: int, processes_per_pod: int
    ) -> None:
        needed = (replicas * containers_per_pod * processes_per_pod) + 4
        if needed > self._port_addrs:
            self._port_addrs = needed

    def listen_ports(self, config: Any = None) -> list[int]:
        if config:
            self._expand_port_addrs(
                config.replicas, config.containers_per_pod,
                config.processes_per_pod,
            )
        return list(range(self._port, self._port + self._port_addrs + 1))

    def server_arglist(self, ctx: ArglistContext) -> list[str]:
        return [
            "python3", f"{ctx.mountdir}uperf-server.py",
            *pod_flags(ctx),
            "--listen-port", str(self._port),
        ]

    def client_arglist(self, ctx: ArglistContext) -> list[str]:
        cfg = ctx.config
        self._workload_run_time = cfg.workload_run_time
        net = cfg.net_interfaces.get("server", "")
        server_name = f"{ctx.namespace}-uperf-server-{ctx.instance}-1"
        server_host = f"{net}@{server_name}" if net else server_name

        args = [
            "python3", f"{ctx.mountdir}uperf-client.py",
            *pod_flags(ctx),
            "--runtime", str(cfg.workload_run_time),
            "--ramp-time", str(self._ramp_time),
            "--server", server_host,
            "--port", str(self._port),
        ]
        for t in self._tests:
            args.extend(["--test", t])
        return args

    def list_configmaps(self) -> list[str]:
        return [
            "uperf-client.py",
            "uperf-server.py",
            "uperf-mini.xml",
            "uperf-rr.xml",
            "uperf-stream.xml",
        ]

    def sysctls(self, config: ClusterbusterConfig | None = None) -> dict[str, str]:
        if config and config.net_interfaces.get("server"):
            return {}
        return {
            "net.ipv4.ip_local_port_range": f"{self._port} {self._port + self._port_addrs}"
        }

    def generate_metadata(self) -> dict[str, Any]:
        jobs: dict[str, Any] = {}
        idx = 1
        for testtype in self._test_types:
            for proto in self._protocols:
                for msgsize in self._msg_sizes:
                    for nthr in self._nthrs:
                        key = f"{idx:04d}-{proto}-{testtype}-{msgsize}B-{nthr}i"
                        jobs[key] = {
                            "test_type": testtype,
                            "proto": proto,
                            "msgsize": msgsize,
                            "nthr": nthr,
                        }
                        idx += 1
        run_time = self._workload_run_time - 2 * self._ramp_time
        return {
            "ramp_time": self._ramp_time,
            "run_time": run_time,
            "jobs": jobs,
        }

    def report_options(self) -> dict[str, Any]:
        return {
            "msg_size": list(self._msg_sizes),
            "test_types": list(self._test_types),
            "protocols": list(self._protocols),
            "nthrs": list(self._nthrs),
            "ramp_time": self._ramp_time,
        }

    def help_options(self) -> str:
        return """\
    Uperf Options:
        --pin-node=server=<node>
                       Specify node to which the server is bound.
        --pin-node=client=<node>
                       Specify node to which the client is bound.
        --uperf-ramp-time=<sec>
                       Specify the ramp time for uperf.
     the following options take a comma-separated list of each
     value to test.  The outer product of all specified tests
     is run.
        --uperf-msg-size=<sizes>
                       Specify the message size(s) to be tested.
        --uperf-test-type=<types>
                       Type of test to run (currently stream or rr)
        --uperf-protocol=protocol
                       Protocol (tcp or udp).
        --uperf-nthr=<n>
                       Number of threads to be tested."""

    def document(self) -> str:
        return "uperf: a partial front end to uperf (https://www.uperf.org)"
