# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0

from __future__ import annotations

from typing import Any

from clusterbuster.ci.config import ClusterbusterCISuiteConfig
from clusterbuster.ci.runner import ClusterbusterRunResult, ClusterbusterRunner
from clusterbuster.ci.suite import ClusterbusterCISuite

__all__ = [
    "ClusterbusterCISuite",
    "ClusterbusterCISuiteConfig",
    "ClusterbusterRunResult",
    "ClusterbusterRunner",
    "load_yaml_profile",
    "resolve_profile_path",
    "run_perf_ci_suite",
]


def __getattr__(name: str) -> Any:
    """Lazy-import profile helpers so ``python -m clusterbuster.ci.profile_yaml`` does not preload them."""
    if name in ("load_yaml_profile", "resolve_profile_path"):
        from clusterbuster.ci import profile_yaml as _py

        return getattr(_py, name)
    if name == "run_perf_ci_suite":
        from clusterbuster.ci.run_perf import run_perf_ci_suite as _fn

        return _fn
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
