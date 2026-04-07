# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# SPDX-License-Identifier: Apache-2.0
#
# AI-assisted tooling (Cursor Agent).
"""Restart / resume parity with ``scripts/run-perf-ci-suite.sh`` and ``lib/libclusterbuster.sh``."""

from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path

_LOG = logging.getLogger("clusterbuster.ci.restart")


def is_report_dir(path: Path) -> bool:
    """True iff directory contains a finished ClusterBuster report (bash ``is_report_dir``)."""
    if not path.is_dir():
        return False
    return (path / "clusterbuster-report.json").is_file() or (
        path / "clusterbuster-report.json.gz"
    ).is_file()


def extract_metadata_uuid(path: Path) -> str | None:
    """Read ``metadata.uuid`` from ``clusterbuster-report.json`` or ``.gz`` under ``path``."""
    plain = path / "clusterbuster-report.json"
    gz = path / "clusterbuster-report.json.gz"
    raw: str | None = None
    try:
        if plain.is_file():
            raw = plain.read_text(encoding="utf-8")
        elif gz.is_file():
            with gzip.open(gz, "rt", encoding="utf-8") as f:
                raw = f.read()
    except OSError as e:
        _LOG.warning("Could not read report under %s: %s", path, e)
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        _LOG.warning("Invalid JSON in report under %s: %s", path, e)
        return None
    md = data.get("metadata")
    if not isinstance(md, dict):
        return None
    u = md.get("uuid")
    return str(u) if u else None


def recover_uuid_from_artifact_root(artifact_root: Path) -> str | None:
    """First direct child (sorted by name) that ``is_report_dir`` wins; return its ``metadata.uuid``."""
    if not artifact_root.is_dir():
        return None
    children = sorted(p for p in artifact_root.iterdir() if p.is_dir())
    for child in children:
        if is_report_dir(child):
            u = extract_metadata_uuid(child)
            if u:
                _LOG.info("Restart: recovered uuid %s from %s", u, child.name)
                return u
    return None
