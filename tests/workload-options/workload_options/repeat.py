# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0

"""Run the workload-options live suite up to N times (same as run-live-repeat)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from workload_options.cli import main as cli_main


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw:
        print("usage: run-live-repeat <max_iterations> [-- extra runner args]", file=sys.stderr)
        return 2
    max_s = raw[0]
    if not max_s.isdigit() or int(max_s) < 1:
        print("usage: run-live-repeat <max_iterations> [-- extra runner args]", file=sys.stderr)
        return 2
    max_iter = int(max_s)
    rest = raw[1:]
    if rest and rest[0] == "--":
        rest = rest[1:]

    root = Path(__file__).resolve().parents[3]
    tests_wl = root / "tests" / "workload-options"

    for i in range(1, max_iter + 1):
        ts = time.strftime("%Y%m%d_%H%M%S")
        report_dir = tests_wl / "reports" / f"run_{ts}_iter{i}"
        print(f"=== iteration {i}/{max_iter} report: {report_dir} ===")
        run_argv = ["--mode", "live", "--report-dir", str(report_dir), *rest]
        rc = cli_main(run_argv)
        if rc != 0:
            print(
                f"=== iteration {i} FAIL (exit {rc}) — inspect logs under {report_dir} "
                f"and artifacts under {report_dir}/artifacts/ ===",
                file=sys.stderr,
            )
            return rc
        print(f"=== iteration {i} PASS ===")
    print(f"=== all {max_iter} iterations passed ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
