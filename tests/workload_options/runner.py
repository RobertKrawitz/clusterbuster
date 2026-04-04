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

"""Run workload option bundles from cases.yaml; write results.json/tsv and SUMMARY.md."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from workload_options.cases import CaseRow, iter_case_rows


@dataclass
class RunConfig:
    mode: str = "dry"
    cb: Path | None = None
    cases_file: Path | None = None
    report_dir: Path | None = None
    deployment_targets: str = "pod"
    filter_priority: str | None = None
    filter_workload: str | None = None
    metrics_choice: int | None = None  # None -> default 0 for live
    report_format: str = "raw"
    global_timeout: int = 2400
    save_artifacts: bool = True
    results_format: str = "json"


def _tsv_args_cell(args: list[str]) -> str:
    return " ".join(args)


def _append_jsonl(
    path: Path,
    row: CaseRow,
    *,
    status: str,
    exit_code: int | None,
    seconds: int | None,
    skip_reason: str | None,
) -> None:
    obj: dict[str, Any] = {
        "id": row.id,
        "workload": row.workload,
        "priority": row.priority,
        "run_mode": row.run_mode,
        "expect_fail": row.expect_fail,
        "deployment_target": row.deployment_target,
        "status": status,
    }
    if exit_code is not None:
        obj["exit_code"] = int(exit_code)
    else:
        obj["exit_code"] = None
    if seconds is not None:
        obj["seconds"] = int(seconds)
    else:
        obj["seconds"] = None
    if skip_reason:
        obj["skip_reason"] = skip_reason
    obj["clusterbuster_args"] = list(row.clusterbuster_args)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _write_results_json(
    jsonl_path: Path,
    out_path: Path,
    *,
    run_id: str,
    cfg: RunConfig,
    pass_count: int,
    fail_count: int,
    skip_count: int,
) -> None:
    results = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                results.append(json.loads(line))

    doc: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "mode": cfg.mode,
        "cases_file": str(cfg.cases_file),
        "clusterbuster": str(cfg.cb),
        "deployment_targets": cfg.deployment_targets,
        "counts": {
            "pass": pass_count,
            "fail": fail_count,
            "skip": skip_count,
        },
        "results": results,
    }
    fp = cfg.filter_priority or ""
    fw = cfg.filter_workload or ""
    if fp or fw:
        doc["filters"] = {}
        if fp:
            doc["filters"]["priority"] = fp
        if fw:
            doc["filters"]["workload"] = fw
    if cfg.mode == "live":
        mc = cfg.metrics_choice if cfg.metrics_choice is not None else 0
        doc["live_options"] = {
            "metrics": mc == 1,
            "report_format": cfg.report_format,
            "global_timeout": cfg.global_timeout,
            "artifacts": cfg.save_artifacts,
        }

    with open(out_path, "w", encoding="utf-8") as out:
        json.dump(doc, out, indent=2, ensure_ascii=False)
        out.write("\n")


def _write_summary_md(
    path: Path,
    *,
    run_id: str,
    cfg: RunConfig,
    report_dir: Path,
    pass_count: int,
    fail_count: int,
    skip_count: int,
    fail_lines: list[str],
    skip_lines: list[str],
) -> None:
    lines: list[str] = [
        f"# Workload option tests — {run_id}",
        "",
        f"- **mode**: {cfg.mode}",
        f"- **Cases file**: `{cfg.cases_file}`",
        f"- **clusterbuster**: `{cfg.cb}`",
        f"- **deployment-targets**: {cfg.deployment_targets}",
    ]
    if cfg.filter_priority:
        lines.append(f"- **priority filter**: {cfg.filter_priority}")
    if cfg.filter_workload:
        lines.append(f"- **workload filter**: {cfg.filter_workload}")
    if cfg.mode == "live":
        mc = cfg.metrics_choice if cfg.metrics_choice is not None else 0
        lines.append(f"- **metrics**: {'enabled' if mc == 1 else 'disabled'}")
        lines.append(f"- **report-format**: {cfg.report_format}")
        lines.append(f"- **global-timeout**: {cfg.global_timeout}")
        art = f"yes (`{report_dir}/artifacts/`)" if cfg.save_artifacts else "no"
        lines.append(f"- **artifacts**: {art}")
    lines.append(f"- **results-format**: {cfg.results_format}")
    lines.extend(
        [
            "",
            "## Counts",
            "",
            "| Result | Count |",
            "|--------|-------|",
            f"| PASS | {pass_count} |",
            f"| FAIL | {fail_count} |",
            f"| SKIP | {skip_count} |",
            "",
        ]
    )
    if fail_lines:
        lines.append("## Failures")
        lines.append("")
        for fl in fail_lines:
            lines.append(f"- {fl}")
        lines.append("")
    if skip_lines:
        lines.append("## Skips (first 20)")
        lines.append("")
        for sl in skip_lines[:20]:
            lines.append(f"- {sl}")
        lines.append("")
    rf = cfg.results_format
    if rf == "json":
        lines.append("Full results: `results.json` (clusterbuster_args as JSON arrays)")
    elif rf == "tsv":
        lines.append("Full table: `results.tsv` (clusterbuster_args last column, space-joined)")
    else:
        lines.append("Full results: `results.json` and `results.tsv`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_suite(cfg: RunConfig) -> int:
    """Run all cases; return 0 on success, 1 if any FAIL."""
    if cfg.cb is None or cfg.cases_file is None:
        raise ValueError("cb and cases_file are required")
    if not cfg.cb.exists():
        print(f"clusterbuster not found at {cfg.cb}", file=sys.stderr)
        return 2
    if not cfg.cases_file.exists():
        print(f"cases file not found at {cfg.cases_file}", file=sys.stderr)
        return 2

    run_id = time.strftime("%Y%m%d_%H%M%S")
    # __file__ is tests/workload_options/runner.py -> parents[2] = repo root
    repo_root = Path(__file__).resolve().parents[2]
    tests_wl = repo_root / "tests" / "workload-options"
    if cfg.report_dir is None:
        report_dir = tests_wl / "reports" / f"run_{run_id}"
    else:
        report_dir = Path(cfg.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    results_json = report_dir / "results.json"
    results_tsv = report_dir / "results.tsv"
    jsonl_path = report_dir / ".results.jsonl"
    summary_md = report_dir / "SUMMARY.md"

    rf = cfg.results_format
    if rf not in ("json", "tsv", "both"):
        print(f"Invalid --results-format: {rf} (use json, tsv, or both)", file=sys.stderr)
        return 2

    if rf in ("tsv", "both"):
        header = (
            "id\tworkload\tpriority\trun_mode\texpect_fail\texit_code\tstatus\tseconds\t"
            "deployment_target\tclusterbuster_args\n"
        )
        results_tsv.write_text(header, encoding="utf-8")

    if rf in ("json", "both"):
        jsonl_path.write_text("", encoding="utf-8")

    pass_count = fail_count = skip_count = 0
    fail_lines: list[str] = []
    skip_lines: list[str] = []

    metrics = cfg.metrics_choice if cfg.metrics_choice is not None else 0

    try:
        rows = list(iter_case_rows(str(cfg.cases_file), cfg.deployment_targets))
    except (ValueError, OSError) as e:
        print(e, file=sys.stderr)
        return 2

    for row in rows:
        should_run = False
        if row.run_mode == "dry":
            should_run = cfg.mode == "dry"
        elif row.run_mode == "live":
            should_run = cfg.mode == "live"
        elif row.run_mode == "both":
            should_run = True
        else:
            print(f"Invalid run_mode {row.run_mode!r} in row {row.id}", file=sys.stderr)
            return 1

        def _record_skip(reason: str) -> None:
            nonlocal skip_count
            skip_count += 1
            skip_lines.append(f"{row.id}: {reason}")
            if rf in ("tsv", "both"):
                with open(results_tsv, "a", encoding="utf-8") as tf:
                    tf.write(
                        f"{row.id}\t{row.workload}\t{row.priority}\t{row.run_mode}\t"
                        f"{row.expect_fail}\t\tSKIP\t\t{row.deployment_target}\t"
                        f"{_tsv_args_cell(row.clusterbuster_args)}\n"
                    )
            if rf in ("json", "both"):
                _append_jsonl(
                    jsonl_path,
                    row,
                    status="SKIP",
                    exit_code=None,
                    seconds=None,
                    skip_reason=reason,
                )

        if cfg.filter_priority and row.priority != cfg.filter_priority:
            _record_skip(f"priority filter ({row.priority} != {cfg.filter_priority})")
            continue
        if cfg.filter_workload and row.workload != cfg.filter_workload:
            _record_skip("workload filter")
            continue
        if not should_run:
            _record_skip(f"run_mode={row.run_mode} with --mode={cfg.mode}")
            continue

        cmd: list[str] = [str(cfg.cb)]
        if cfg.mode == "dry":
            cmd.append("-n")
        elif cfg.mode == "live":
            if metrics != 1:
                cmd.append("--force-no-metrics")
            cmd.append(f"--report={cfg.report_format}")
            if cfg.global_timeout != 0:
                cmd.append(f"--timeout={cfg.global_timeout}")
        if row.deployment_target == "vm":
            cmd.append("--deployment_type=vm")
        if cfg.mode == "live" and cfg.save_artifacts:
            adir = report_dir / "artifacts" / row.id
            adir.mkdir(parents=True, exist_ok=True)
            cmd.append(f"--artifactdir={adir}")
            cmd.append("--retrieve-successful-logs=1")
        cmd.extend(row.clusterbuster_args)

        log_path = report_dir / f"{row.id}.log"
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                cwd=str(repo_root),
            )
        except OSError as e:
            print(f"{row.id}: failed to execute clusterbuster: {e}", file=sys.stderr)
            return 2
        dur = int(time.monotonic() - t0)
        ec = int(proc.returncode)
        log_path.write_bytes(proc.stdout or b"")

        ok = (ec != 0) if row.expect_fail == 1 else (ec == 0)
        if ok:
            status = "PASS"
            pass_count += 1
        else:
            status = "FAIL"
            fail_count += 1
            art_hint = ""
            if cfg.save_artifacts and cfg.mode == "live":
                art_hint = f" artifacts: {report_dir}/artifacts/{row.id}/"
            fail_lines.append(
                f"{row.id} exit={ec} expect_fail={row.expect_fail} target={row.deployment_target} "
                f"args: {_tsv_args_cell(row.clusterbuster_args)} (see {log_path}{art_hint})"
            )

        if rf in ("tsv", "both"):
            with open(results_tsv, "a", encoding="utf-8") as tf:
                tf.write(
                    f"{row.id}\t{row.workload}\t{row.priority}\t{row.run_mode}\t{row.expect_fail}\t"
                    f"{ec}\t{status}\t{dur}\t{row.deployment_target}\t"
                    f"{_tsv_args_cell(row.clusterbuster_args)}\n"
                )
        if rf in ("json", "both"):
            _append_jsonl(
                jsonl_path,
                row,
                status=status,
                exit_code=ec,
                seconds=dur,
                skip_reason=None,
            )

    _write_summary_md(
        summary_md,
        run_id=run_id,
        cfg=cfg,
        report_dir=report_dir,
        pass_count=pass_count,
        fail_count=fail_count,
        skip_count=skip_count,
        fail_lines=fail_lines,
        skip_lines=skip_lines,
    )

    if rf in ("json", "both"):
        _write_results_json(
            jsonl_path,
            results_json,
            run_id=run_id,
            cfg=cfg,
            pass_count=pass_count,
            fail_count=fail_count,
            skip_count=skip_count,
        )
        jsonl_path.unlink(missing_ok=True)

    print(f"Wrote {summary_md}")
    if rf in ("json", "both"):
        print(f"Wrote {results_json}")
    if rf in ("tsv", "both"):
        print(f"Wrote {results_tsv}")

    return 1 if fail_count > 0 else 0
