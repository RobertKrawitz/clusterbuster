# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0

"""Unit tests for clusterbuster.ci compat and scoped options."""

import json
import tempfile
from pathlib import Path

from clusterbuster.ci.ci_options import parse_ci_option
from clusterbuster.ci.compat.sizes import parse_size
from clusterbuster.ci.help_text import build_full_help
from clusterbuster.ci.helpers import compute_timeout, computeit
from clusterbuster.ci.run_perf import main, write_ci_results_json


def test_parse_size_mebibytes() -> None:
    assert parse_size("64Mi") == str(64 * 1024 * 1024)


def test_compute_timeout_negative_default() -> None:
    assert compute_timeout(0, -1200) == 1200
    # Negative workload timeout → inherit job_timeout, then abs if still negative.
    assert compute_timeout(-30, 100) == 100


def test_parse_ci_option_volume_scope() -> None:
    opt = "volume:files,fio:!vm=test-pvc:pvc:/var/opt/clusterbuster:size=auto:inodes=auto"
    assert parse_ci_option(opt, "files", "vm") is None
    p = parse_ci_option(opt, "files", "pod")
    assert p is not None
    assert p.noptname.startswith("volume")


def test_parse_ci_option_fio_vm() -> None:
    opt = "volume:files,fio:vm=test-pvc:pvc:/var/opt/clusterbuster:size=auto:inodes=auto"
    p = parse_ci_option(opt, "fio", "vm")
    assert p is not None


def test_computeit_safe_arithmetic() -> None:
    assert computeit("64 * 1024") == 65536
    assert computeit("10 // 3") == 3
    assert computeit("1000 + 7") == 1007


def test_write_ci_results_json_valid_utf8() -> None:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        write_ci_results_json(
            p,
            jobs=['job"a', "b"],
            failures=["x"],
            job_runtimes={},
            status="FAIL",
            start_ts=100,
            end_ts=200,
        )
        raw = (p / "clusterbuster-ci-results.json").read_text(encoding="utf-8")
        data = json.loads(raw)
        assert data["result"] == "FAIL"
        assert 'job"a' in data["ran"]


def test_profile_yaml_subcommand_exits_zero() -> None:
    root = Path(__file__).resolve().parents[1]
    prof = root / "lib" / "CI" / "profiles" / "test_ci.yaml"
    assert prof.is_file()
    assert main(["profile-yaml", str(prof)]) == 0


def test_run_perf_help_text_self_contained() -> None:
    root = Path(__file__).resolve().parents[1]
    text = build_full_help(root)
    assert text.startswith("Usage: run-perf-ci-suite")
    assert "General options:" in text
    assert "Memory workload CI options:" in text
    assert "ClusterBuster is a tool" in text or "Workload-specific options:" in text
