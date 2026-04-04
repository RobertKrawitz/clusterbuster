# Copyright 2026 Robert Krawitz/Red Hat
# AI-assisted tooling (Cursor Agent).
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""CLI for workload option regression tests."""

from __future__ import annotations

import argparse
from pathlib import Path

from workload_options.runner import RunConfig, run_suite


def _repo_root() -> Path:
    # tests/workload-options/workload_options/cli.py -> repo root
    return Path(__file__).resolve().parents[3]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Run workload option bundles from cases.yaml; write results.json (default), "
            "optional results.tsv, and SUMMARY.md."
        )
    )
    p.add_argument("-m", "--mode", choices=("dry", "live"), default="dry")
    p.add_argument("--cb", type=Path, help="Path to clusterbuster script (default: ./clusterbuster)")
    p.add_argument("--cases-file", "--cases", type=Path, dest="cases_file", help="Path to cases.yaml")
    p.add_argument("--report-dir", type=Path, help="Output directory (default: timestamped under reports/)")
    p.add_argument(
        "--deployment-targets",
        default="pod",
        help="pod, vm, pod,vm, or all",
    )
    p.add_argument("-p", "--priority", dest="filter_priority", help="Only rows with this priority (P0, P1, …)")
    p.add_argument(
        "-w",
        "--workload",
        dest="filter_workloads",
        action="append",
        metavar="NAME",
        help="Only rows for this workload (repeat for multiple, e.g. -w byo -w fio)",
    )
    m = p.add_mutually_exclusive_group()
    m.add_argument("--metrics", action="store_true", help="Live: do not pass --force-no-metrics")
    m.add_argument("--no-metrics", action="store_true", help="Live: pass --force-no-metrics (default for live)")
    p.add_argument("--report-format", default="raw", help="Live: value for --report (default raw)")
    p.add_argument(
        "--global-timeout",
        type=int,
        default=2400,
        help="Live: --timeout seconds; 0 omits --timeout",
    )
    p.add_argument("--artifacts", dest="save_artifacts", action="store_true", default=True)
    p.add_argument("--no-artifacts", dest="save_artifacts", action="store_false")
    p.add_argument(
        "--results-format",
        choices=("json", "tsv", "both"),
        default="json",
        help="Primary machine-readable output",
    )
    return p


def _normalize_workload_filters(raw: list[str] | None) -> tuple[str, ...] | None:
    if not raw:
        return None
    # First-occurrence order; drop duplicates (e.g. repeated -w same name)
    return tuple(dict.fromkeys(raw))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = _repo_root()
    tests_dir = root / "tests" / "workload-options"
    cb = args.cb if args.cb else root / "clusterbuster"
    cases_file = args.cases_file if args.cases_file else tests_dir / "cases.yaml"

    metrics_choice: int | None = None
    if args.metrics:
        metrics_choice = 1
    elif args.no_metrics:
        metrics_choice = 0

    cfg = RunConfig(
        mode=args.mode,
        cb=cb,
        cases_file=cases_file,
        report_dir=args.report_dir,
        deployment_targets=args.deployment_targets,
        filter_priority=args.filter_priority,
        filter_workloads=_normalize_workload_filters(args.filter_workloads),
        metrics_choice=metrics_choice,
        report_format=args.report_format,
        global_timeout=args.global_timeout,
        save_artifacts=args.save_artifacts,
        results_format=args.results_format,
    )
    return run_suite(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
