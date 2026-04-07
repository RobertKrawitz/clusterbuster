# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
"""CI-specific options (--ci-*) and scoped option parsing for ``run-perf-ci-suite``."""

from __future__ import annotations

import argparse
import shlex
from dataclasses import dataclass
from typing import Sequence

from clusterbuster.ci.compat.options import parse_option


@dataclass(frozen=True)
class ParsedCiOption:
    """Output of :func:`parse_ci_option`: scope-stripped option names and value."""

    noptname1: str
    noptname: str
    optvalue: str


def splitarg(value: str) -> str:
    """Replace commas with spaces in a CI option value (size lists, etc.)."""
    return value.replace(",", " ")


def _split_scoped_optname(noptname: str) -> tuple[str, str, str]:
    parts = noptname.split(":", 2)
    a = parts[0] if parts else ""
    b = parts[1] if len(parts) > 1 else ""
    c = parts[2] if len(parts) > 2 else ""
    return a, b, c


def check_ci_option(noptname: str, workload: str, runtime: str) -> bool:
    """Return whether a scoped CI option applies to this workload/runtime."""
    if ":" not in noptname or (not workload and not runtime):
        return True
    _optbase, optworkload, optruntime = _split_scoped_optname(noptname)
    del _optbase
    if (not workload or not optworkload or workload == optworkload) and (
        not runtime or not optruntime or runtime == optruntime
    ):
        return True
    if "," not in optworkload and "!" not in optworkload and "," not in optruntime and "!" not in optruntime:
        return False
    if workload and optworkload:
        found = False
        for item in optworkload.split(","):
            item = item.strip()
            if item == workload:
                found = True
                break
            if item.startswith("!") and item[1:] == workload:
                return False
        if not found:
            return False
    if runtime and optruntime:
        for item in optruntime.split(","):
            item = item.strip()
            if item == runtime or (item.startswith("!") and item[1:] != runtime):
                return True
        return False
    return True


def parse_ci_option(option: str, workload: str = "", runtime: str = "") -> ParsedCiOption | None:
    """Parse a CI option; return ``None`` if scoped to another workload/runtime."""
    po = parse_option(option.strip())
    if not check_ci_option(po.noptname, workload, runtime):
        return None
    # Strip workload/runtime scope suffixes; keep base name + value for dispatch.
    n1 = po.noptname1.split(":", 1)[0]
    nn = po.noptname.split(":", 1)[0]
    return ParsedCiOption(noptname1=n1, noptname=nn, optvalue=po.optvalue)


def add_ci_argument_group(parser: argparse.ArgumentParser) -> argparse._ArgumentGroup:
    g = parser.add_argument_group("CI suite options")
    g.add_argument(
        "--ci-extra-args",
        default="",
        metavar="ARGS",
        help="Extra arguments for CI workloads (quoted tokens)",
    )
    g.add_argument(
        "--ci-clusterbuster-args",
        default="",
        metavar="ARGS",
        help="Extra arguments passed to clusterbuster (quoted tokens)",
    )
    g.add_argument(
        "--ci-job-delay",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Delay between jobs (seconds)",
    )
    g.add_argument(
        "--ci-unique-prefix",
        action="store_true",
        help="Use a unique prefix for each job",
    )
    g.add_argument(
        "--ci-force-cleanup",
        action="store_true",
        help="Force cleanup after each job",
    )
    g.add_argument(
        "--ci-no-compress",
        action="store_true",
        help="Do not compress artifacts",
    )
    g.add_argument(
        "--ci-client-pin",
        default="",
        metavar="NODE",
        help="Pin client pods to this node",
    )
    g.add_argument(
        "--ci-server-pin",
        default="",
        metavar="NODE",
        help="Pin server pods to this node",
    )
    g.add_argument(
        "--ci-sync-pin",
        default="",
        metavar="NODE",
        help="Pin sync pods to this node",
    )
    g.add_argument(
        "--ci-report-format",
        default="",
        metavar="FMT",
        help="Report format (e.g. json)",
    )
    g.add_argument(
        "--ci-debug-args",
        default="",
        metavar="ARGS",
        help="Debug arguments for clusterbuster",
    )
    return g


def split_quoted_args(s: str) -> list[str]:
    if not s or not s.strip():
        return []
    return shlex.split(s)


def merge_extra_ci_args(
    base: Sequence[str],
    ci_extra_args: str,
    ci_clusterbuster_args: str,
) -> tuple[list[str], list[str]]:
    """Return (extra_args_for_workloads, extra_clusterbuster_argv)."""
    workload_extra = list(base)
    workload_extra.extend(split_quoted_args(ci_extra_args))
    cb_extra = split_quoted_args(ci_clusterbuster_args)
    return workload_extra, cb_extra
