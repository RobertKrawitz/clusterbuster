# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0

"""Tests for ``clusterbuster.ci.restart`` (report-dir detection and UUID recovery)."""

import gzip
import json
import tempfile
from pathlib import Path

from clusterbuster.ci.restart import (
    extract_metadata_uuid,
    is_report_dir,
    recover_uuid_from_artifact_root,
)


def test_is_report_dir_plain_json() -> None:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        (p / "clusterbuster-report.json").write_text("{}", encoding="utf-8")
        assert is_report_dir(p)


def test_is_report_dir_gz_only() -> None:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        with gzip.open(p / "clusterbuster-report.json.gz", "wt", encoding="utf-8") as f:
            f.write("{}")
        assert is_report_dir(p)


def test_is_report_dir_rejects_report_json_without_clusterbuster_prefix() -> None:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        (p / "report.json").write_text("{}", encoding="utf-8")
        assert not is_report_dir(p)


def test_extract_metadata_uuid() -> None:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        doc = {"metadata": {"uuid": "abc-123"}}
        (p / "clusterbuster-report.json").write_text(json.dumps(doc), encoding="utf-8")
        assert extract_metadata_uuid(p) == "abc-123"


def test_recover_uuid_first_sorted_child() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        z = root / "zzz-job"
        z.mkdir()
        a = root / "aaa-job"
        a.mkdir()
        for d, u in ((a, "first-uuid"), (z, "zzz-uuid")):
            (d / "clusterbuster-report.json").write_text(
                json.dumps({"metadata": {"uuid": u}}),
                encoding="utf-8",
            )
        assert recover_uuid_from_artifact_root(root) == "first-uuid"
