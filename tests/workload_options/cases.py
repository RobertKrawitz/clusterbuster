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

"""Load tests/workload-options/cases.yaml into CaseRow records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterator

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "workload_options: install PyYAML (e.g. python3-pyyaml)"
    ) from exc


@dataclass(frozen=True)
class CaseRow:
    id: str
    workload: str
    priority: str
    run_mode: str
    expect_fail: int
    deployment_target: str
    clusterbuster_args: list[str]


def _expect_fail_int(v: object) -> int:
    if isinstance(v, bool):
        return 1 if v else 0
    return int(v)


def parse_deployment_targets(s: str) -> list[str]:
    t = s.strip().lower()
    if not t or t == "all":
        return ["pod", "vm"]
    out: list[str] = []
    for p in t.split(","):
        p = p.strip()
        if p == "all":
            return ["pod", "vm"]
        if p in ("pod", "vm"):
            out.append(p)
        elif p:
            raise ValueError(f"unknown deployment target {p!r}")
    return out or ["pod"]


def _normalize_clusterbuster_args(row: dict[str, Any], case_id: str) -> list[str]:
    raw = row.get("clusterbuster_args")
    if raw is None:
        raise ValueError(f"case {case_id!r}: missing clusterbuster_args")
    if isinstance(raw, str):
        raise ValueError(
            f"case {case_id!r}: clusterbuster_args must be a YAML list of strings, not a string"
        )
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise ValueError(f"case {case_id!r}: clusterbuster_args must be a list of strings")
    return raw


def iter_case_rows(cases_yaml_path: str, deployment_targets: str) -> Iterator[CaseRow]:
    """Yield one CaseRow per case × selected deployment target."""
    selected = parse_deployment_targets(deployment_targets)
    with open(cases_yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    defaults = data.get("defaults") or {}
    default_targets = defaults.get("deployment_targets") or ["pod", "vm"]
    cases_block = data.get("cases")
    if not isinstance(cases_block, dict):
        raise ValueError("cases must be a mapping keyed by priority (e.g. P0:, P1:)")
    multi = len(selected) > 1
    for priority in sorted(cases_block.keys()):
        rows = cases_block[priority]
        if not isinstance(rows, list):
            raise ValueError(f"cases[{priority!r}] must be a list")
        for row in rows:
            row = dict(row)
            row["priority"] = priority
            row_targets = row.get("deployment_targets") or default_targets
            args_list = _normalize_clusterbuster_args(row, row["id"])
            for tgt in selected:
                if tgt not in row_targets:
                    continue
                base_id = row["id"]
                rid = f"{base_id}--{tgt}" if multi else base_id
                ef = _expect_fail_int(row["expect_fail"])
                yield CaseRow(
                    id=rid,
                    workload=str(row["workload"]),
                    priority=str(priority),
                    run_mode=str(row["run_mode"]),
                    expect_fail=ef,
                    deployment_target=tgt,
                    clusterbuster_args=args_list,
                )


def emit_loader_tsv_lines(cases_yaml_path: str, deployment_targets: str) -> Iterator[str]:
    """Legacy TSV stream for debugging; columns match the old load_cases_yaml.py."""
    for row in iter_case_rows(cases_yaml_path, deployment_targets):
        payload = json.dumps(row.clusterbuster_args, separators=(",", ":"))
        line = "\t".join(
            [
                row.id,
                row.workload,
                row.priority,
                row.run_mode,
                str(row.expect_fail),
                row.deployment_target,
                payload,
            ]
        )
        yield line


def main_loader() -> None:
    """CLI: same as legacy load_cases_yaml.py."""
    import sys

    if len(sys.argv) < 3:
        print("usage: load_cases_yaml.py <cases.yaml> <deployment-targets>", file=sys.stderr)
        raise SystemExit(2)
    path = sys.argv[1]
    targets = sys.argv[2]
    try:
        for line in emit_loader_tsv_lines(path, targets):
            print(line)
    except (ValueError, OSError) as e:
        print(f"load_cases_yaml.py: {e}", file=sys.stderr)
        raise SystemExit(2) from e
