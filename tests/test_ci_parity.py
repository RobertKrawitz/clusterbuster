# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0

"""Unit tests for clusterbuster.ci compat and scoped options."""

from pathlib import Path

from clusterbuster.ci.ci_options import parse_ci_option
from clusterbuster.ci.help_text import build_full_help
from clusterbuster.ci.compat.sizes import parse_size
from clusterbuster.ci.helpers import compute_timeout


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


def test_run_perf_help_text_self_contained() -> None:
    root = Path(__file__).resolve().parents[1]
    text = build_full_help(root)
    assert text.startswith("Usage: run-perf-ci-suite")
    assert "General options:" in text
    assert "Memory workload CI options:" in text
    assert "ClusterBuster is a tool" in text or "Workload-specific options:" in text
