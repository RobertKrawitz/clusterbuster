#!/usr/bin/env python3

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
#
# Expand tests/workload-options/cases.yaml to tab-separated rows on stdout for the bash runner.
# Columns: id, workload, priority, run_mode, expect_fail, deployment_target, clusterbuster_args (JSON array).
# Usage: load_cases_yaml.py <cases.yaml> <deployment-targets>
#   deployment-targets: comma-separated pod, vm, and/or all (all => pod + vm).

from __future__ import annotations

import json
import sys

try:
    import yaml
except ImportError as exc:
    print("load_cases_yaml.py: install PyYAML (e.g. python3-pyyaml)", file=sys.stderr)
    raise SystemExit(2) from exc


def _expect_fail_int(v: object) -> int:
    if isinstance(v, bool):
        return 1 if v else 0
    return int(v)


def _parse_targets(s: str) -> list[str]:
    t = s.strip().lower()
    if not t or t == "all":
        return ["pod", "vm"]
    out = []
    for p in t.split(","):
        p = p.strip()
        if p == "all":
            return ["pod", "vm"]
        if p in ("pod", "vm"):
            out.append(p)
        elif p:
            print(f"load_cases_yaml.py: unknown target {p!r}", file=sys.stderr)
            raise SystemExit(2)
    return out or ["pod"]


def _normalize_clusterbuster_args(row: dict, case_id: str) -> list[str]:
    raw = row.get("clusterbuster_args")
    if raw is None:
        print(f"load_cases_yaml.py: case {case_id!r}: missing clusterbuster_args", file=sys.stderr)
        raise SystemExit(2)
    if isinstance(raw, str):
        print(
            f"load_cases_yaml.py: case {case_id!r}: clusterbuster_args must be a YAML list of strings, not a string",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        print(
            f"load_cases_yaml.py: case {case_id!r}: clusterbuster_args must be a list of strings",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return raw


def main() -> None:
    if len(sys.argv) < 3:
        print("usage: load_cases_yaml.py <cases.yaml> <deployment-targets>", file=sys.stderr)
        raise SystemExit(2)
    path = sys.argv[1]
    selected = _parse_targets(sys.argv[2])
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    defaults = data.get("defaults") or {}
    default_targets = defaults.get("deployment_targets") or ["pod", "vm"]
    cases_block = data.get("cases")
    if not isinstance(cases_block, dict):
        print("load_cases_yaml.py: cases must be a mapping keyed by priority (e.g. P0:, P1:)", file=sys.stderr)
        raise SystemExit(2)
    multi = len(selected) > 1
    for priority in sorted(cases_block.keys()):
        rows = cases_block[priority]
        if not isinstance(rows, list):
            print(f"load_cases_yaml.py: cases[{priority!r}] must be a list", file=sys.stderr)
            raise SystemExit(2)
        for row in rows:
            row = dict(row)
            row["priority"] = priority
            row_targets = row.get("deployment_targets") or default_targets
            args_list = _normalize_clusterbuster_args(row, row["id"])
            args_payload = json.dumps(args_list, separators=(",", ":"))
            for tgt in selected:
                if tgt not in row_targets:
                    continue
                base_id = row["id"]
                rid = f"{base_id}--{tgt}" if multi else base_id
                ef = _expect_fail_int(row["expect_fail"])
                # deployment_target before clusterbuster_args; last column is JSON array of argv tokens.
                line = "\t".join(
                    [
                        rid,
                        str(row["workload"]),
                        str(row["priority"]),
                        str(row["run_mode"]),
                        str(ef),
                        tgt,
                        args_payload,
                    ]
                )
                print(line)


if __name__ == "__main__":
    main()
