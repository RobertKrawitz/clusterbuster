# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0

from __future__ import annotations

import os
import subprocess


def compute_timeout(timeout: int, job_timeout: int) -> int:
    """Parity with ``compute_timeout`` in run-perf-ci-suite."""
    if timeout <= 0:
        timeout = job_timeout
    if timeout < 0:
        timeout = -timeout
    return timeout


def computeit(expr: str) -> int:
    """Integer result of a simple arithmetic expression (bash ``bc`` subset)."""
    # Expressions are built from trusted integers/floats in workload code.
    return int(float(eval(expr, {"__builtins__": {}}, {})))


def get_node_memory_bytes(node: str, oc: str | None = None) -> int:
    """Allocatable memory on a node, in bytes (``kubectl``/``oc``)."""
    from clusterbuster.ci.compat.sizes import parse_size

    cmd = oc or os.environ.get("OC") or os.environ.get("KUBECTL") or "oc"
    proc = subprocess.run(
        [cmd, "get", "node", node, "-ojsonpath={.status.allocatable.memory}"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or "oc get node failed")
    return int(parse_size(proc.stdout.strip()))


def roundup_fio(num: int, base: int = 1048576) -> int:
    answer = ((num + (base - 1)) // base) * base
    return max(answer, base)


def roundup_interval(base: int, interval: int) -> int:
    """``roundup`` from files.ci."""
    return ((base + interval - 1) // interval) * interval
