# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0

from __future__ import annotations

import argparse
import uuid
from pathlib import Path

from clusterbuster.ci.ci_options import add_ci_argument_group, merge_extra_ci_args
from clusterbuster.ci.config import ClusterbusterCISuiteConfig
from clusterbuster.ci.suite import ClusterbusterCISuite


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m clusterbuster.ci", description="ClusterBuster CI suite")
    p.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Pass -n through to clusterbuster (no changes)",
    )
    p.add_argument(
        "--artifactdir",
        type=Path,
        default=None,
        help="Artifact root directory",
    )
    p.add_argument(
        "--uuid",
        default="",
        help="Run UUID (default: random)",
    )
    p.add_argument(
        "--runtimeclasses",
        default="",
        help="Comma-separated runtime classes (empty = default pod)",
    )
    p.add_argument(
        "workloads",
        nargs="*",
        help="Workloads to run (default: all six CI workloads)",
    )
    add_ci_argument_group(p)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rc_list = [r.strip() for r in args.runtimeclasses.split(",") if r.strip()]
    workloads = tuple(w.lower() for w in args.workloads) if args.workloads else ClusterbusterCISuiteConfig().workloads
    extra, cb_extra = merge_extra_ci_args([], args.ci_extra_args, args.ci_clusterbuster_args)
    cfg = ClusterbusterCISuiteConfig(
        workloads=workloads,
        runtimeclasses=tuple(rc_list) if rc_list else ("",),
        dontdoit=args.dry_run,
        artifactdir=args.artifactdir,
        uuid=args.uuid or str(uuid.uuid4()),
        extra_args=extra,
        extra_clusterbuster_args=cb_extra,
        job_delay=args.ci_job_delay,
        unique_prefix=args.ci_unique_prefix,
        compress=not args.ci_no_compress,
        client_pin=args.ci_client_pin,
        server_pin=args.ci_server_pin,
        sync_pin=args.ci_sync_pin,
        report_format=args.ci_report_format,
        debug_args=args.ci_debug_args,
    )
    if args.ci_force_cleanup:
        cfg.force_cleanup_timeout = "1"
    suite = ClusterbusterCISuite(cfg)
    return suite.run()


if __name__ == "__main__":
    raise SystemExit(main())
