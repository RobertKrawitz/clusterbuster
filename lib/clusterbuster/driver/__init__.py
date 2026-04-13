# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""ClusterBuster Python driver — public API."""

from __future__ import annotations

import sys

from .config import ClusterbusterConfig, ClusterbusterConfigBuilder, ConfigError


def _ensure_workloads_loaded() -> None:
    """Import the workloads package to trigger @register decorators."""
    import clusterbuster.driver.workloads  # noqa: F401


def _build_with_registry(
    builder: ClusterbusterConfigBuilder,
    command_line: list[str] | None = None,
) -> ClusterbusterConfig:
    """Build config with workload registry callbacks wired in."""
    _ensure_workloads_loaded()
    from .workload_registry import make_finalize_callback, make_process_options_callback

    return builder.build(
        command_line=command_line,
        workload_process_options=make_process_options_callback(builder),
        workload_finalize_args=make_finalize_callback(builder),
    )


def main() -> None:
    """CLI entry point.  Captures ``sys.argv``, parses options, runs."""
    from .cli import parse_argv

    saved_argv = list(sys.argv)
    builder = parse_argv(sys.argv[1:])
    try:
        config = _build_with_registry(builder, command_line=saved_argv)
    except ConfigError as exc:
        print(f"clusterbuster: {exc}", file=sys.stderr)
        sys.exit(1)
    rc = _run(config)
    sys.exit(rc)


def run_clusterbuster(
    argv: list[str] | None = None,
    config: ClusterbusterConfig | None = None,
) -> int:
    """Programmatic entry point.

    Either pass *argv* (parsed like CLI) or a pre-built *config*.
    When *argv* is provided, it is captured as ``config.command_line``
    for report JSON and artifact output.

    Returns 0 for success, 1 for failure/abort.
    """
    if config is not None:
        return _run(config)

    from .cli import parse_argv

    raw_argv = argv if argv is not None else sys.argv[1:]
    builder = parse_argv(raw_argv)
    built = _build_with_registry(
        builder, command_line=["clusterbuster", *raw_argv]
    )
    return _run(built)


def run_from_argv(argv: list[str]) -> int:
    """Convenience for ``run-perf-ci-suite`` import path (Phase 3D)."""
    return run_clusterbuster(argv=argv)


def _run(config: ClusterbusterConfig) -> int:
    """Execute a clusterbuster run."""
    if not config.doit:
        from .cli import _print_dry_run
        _print_dry_run(config)
        return 0
    from .orchestrator import run
    return run(config)
