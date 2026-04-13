# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""logger workload: emits log messages at a controllable rate."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..workload_registry import ArglistContext, WorkloadBase, pod_flags, register

if TYPE_CHECKING:
    from ..config import ClusterbusterConfigBuilder


@register
class Logger(WorkloadBase):
    name = "logger"
    aliases = ("log", "simple-log", "logging")

    def __init__(self) -> None:
        self._bytes_per_line = 1
        self._lines_per_io = 1
        self._xfer_count = 1
        self._delay: float = 0

    def process_options(
        self, builder: ClusterbusterConfigBuilder, parsed: Any
    ) -> bool:
        n = parsed.noptname1
        v = parsed.optvalue
        if n == "logbytesperline":
            self._bytes_per_line = int(v)
        elif n == "loglinesperio":
            self._lines_per_io = int(v)
        elif n == "logxfercount":
            self._xfer_count = int(v)
        elif n == "logdelay":
            self._delay = float(v)
        else:
            return False
        return True

    def arglist(self, ctx: ArglistContext) -> list[str]:
        cfg = ctx.config
        return [
            "python3", f"{ctx.mountdir}logger.py",
            *pod_flags(ctx),
            "--processes", str(cfg.processes_per_pod),
            "--runtime", str(cfg.workload_run_time),
            "--bytes-per-line", str(self._bytes_per_line),
            "--lines-per-io", str(self._lines_per_io),
            "--xfer-count", str(self._xfer_count),
            "--delay", f"{self._delay:g}",
        ]

    def workload_reporting_class(self) -> str:
        return "generic"

    def report_options(self) -> dict[str, Any]:
        return {
            "log_bytes_per_line": self._bytes_per_line,
            "log_lines_per_io": self._lines_per_io,
            "log_xfer_count": self._xfer_count,
            "log_delay": self._delay,
        }

    def help_options(self) -> str:
        return """\
    Log Options:
        --log-bytes-per-line=<bytes_per_line>
                        Number of bytes per line to log.
        --log-lines-per-io=<lines_per_io>
                        Number of lines per message to log.
        --log-xfer-count=<count>
                        Number of messages to log (in I/Os).
                        If zero, log continuously (subject to
                        workload runtime).
        --log-delay=<sec>
                        Time in seconds (may be fractional) to delay
                        between I/Os."""

    def document(self) -> str:
        return "log: a pod that emits log messages at a controllable rate."
