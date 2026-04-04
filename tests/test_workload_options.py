# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0

"""Smoke tests for tests/workload-options/workload_options (no cluster required)."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def cases_path() -> Path:
    p = REPO / "tests" / "workload-options" / "cases.yaml"
    assert p.is_file(), f"missing {p}"
    return p


def test_parse_deployment_targets():
    from workload_options.cases import parse_deployment_targets

    assert parse_deployment_targets("pod") == ["pod"]
    assert parse_deployment_targets("all") == ["pod", "vm"]
    assert parse_deployment_targets("pod,vm") == ["pod", "vm"]


def test_iter_case_rows_pod(cases_path: Path):
    from workload_options.cases import iter_case_rows

    rows = list(iter_case_rows(str(cases_path), "pod"))
    assert len(rows) >= 1
    r = rows[0]
    assert r.id
    assert r.clusterbuster_args
    assert isinstance(r.clusterbuster_args, list)


def test_emit_loader_tsv_lines(cases_path: Path):
    from workload_options.cases import emit_loader_tsv_lines

    lines = list(emit_loader_tsv_lines(str(cases_path), "pod"))
    assert len(lines) >= 1
    parts = lines[0].split("\t")
    assert len(parts) == 7
    assert parts[-1].startswith("[")


def test_run_config_defaults():
    from workload_options.runner import RunConfig

    c = RunConfig()
    assert c.mode == "dry"
    assert c.results_format == "json"
