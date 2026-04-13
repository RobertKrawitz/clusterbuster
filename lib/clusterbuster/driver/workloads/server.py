# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""server workload: client-server with optional bidirectional data transfer."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..workload_registry import ArglistContext, WorkloadBase, pod_flags, register

if TYPE_CHECKING:
    from ..config import ClusterbusterConfigBuilder

_LOG = logging.getLogger(__name__)

_DEFAULT_BYTES_TRANSFER = 1000000000


@register
class Server(WorkloadBase):
    name = "server"

    def __init__(self) -> None:
        self._msg_size = 32768
        self._port = 30000
        self._port_addrs = 24

    def process_options(
        self, builder: ClusterbusterConfigBuilder, parsed: Any
    ) -> bool:
        n = parsed.noptname1
        v = parsed.optvalue
        if n == "msgsize":
            self._msg_size = int(v)
            if self._msg_size <= 0:
                raise SystemExit("Message size must be positive, exiting!")
        else:
            return False
        return True

    def finalize_extra_cli_args(
        self, builder: ClusterbusterConfigBuilder
    ) -> None:
        builder.processes_per_pod = 1

    def _expand_port_addrs(
        self, replicas: int, containers_per_pod: int, processes_per_pod: int
    ) -> None:
        needed = (replicas * containers_per_pod * processes_per_pod) + 4
        if needed > self._port_addrs:
            self._port_addrs = needed

    def server_arglist(self, ctx: ArglistContext) -> list[str]:
        return [
            "python3", f"{ctx.mountdir}server.py",
            *pod_flags(ctx),
            "--listen-port", str(self._port),
            "--msg-size", str(self._msg_size),
            "--server-expected-clients",
            str(ctx.replicas),
        ]

    def client_arglist(self, ctx: ArglistContext) -> list[str]:
        cfg = ctx.config
        net = cfg.net_interfaces.get("server", "")
        server_name = f"{ctx.namespace}-server-server-{ctx.instance}-1"
        server_host = f"{net}@{server_name}" if net else server_name

        bytes_transfer = cfg.bytes_transfer
        bytes_transfer_max = cfg.bytes_transfer_max
        if (
            cfg.target_data_rate != 0
            and cfg.target_data_rate != ""
            and cfg.workload_run_time_max == 0
            and bytes_transfer_max == 0
        ):
            bytes_transfer = _DEFAULT_BYTES_TRANSFER
            bytes_transfer_max = _DEFAULT_BYTES_TRANSFER

        return [
            "python3", f"{ctx.mountdir}client.py",
            *pod_flags(ctx),
            "--server", server_host,
            "--port", str(self._port),
            "--data-rate", str(cfg.target_data_rate),
            "--bytes", str(bytes_transfer),
            "--bytes-max", str(bytes_transfer_max),
            "--msg-size", str(self._msg_size),
            "--xfer-time", str(cfg.workload_run_time),
            "--xfer-time-max", str(cfg.workload_run_time_max),
        ]

    def listen_ports(self, config: Any = None) -> list[int]:
        if config:
            self._expand_port_addrs(
                config.replicas, config.containers_per_pod,
                config.processes_per_pod,
            )
        return list(range(self._port, self._port + self._port_addrs + 1))

    def sysctls(self, config: Any = None) -> dict[str, str]:
        cfg = config
        if cfg and cfg.net_interfaces.get("server", ""):
            return {}
        port_end = self._port + self._port_addrs
        return {
            "net.ipv4.ip_local_port_range": f"{self._port} {port_end}",
        }

    def list_configmaps(self) -> list[str]:
        return ["client.py", "server.py"]

    def report_options(self) -> dict[str, Any]:
        return {"msg_size": self._msg_size}

    def help_options(self) -> str:
        return """\
    Client/server Options:
        --msgsize       Message size in data transfer
        --pin-node=server=<node>
                        Specify node to which the server is bound.
        --pin-node=client=<node>
                        Specify node to which the client is bound."""

    def document(self) -> str:
        return ("server: a client-server workload with optional bidirectional "
                "data\n  transfer, optionally at a specified data rate.")
