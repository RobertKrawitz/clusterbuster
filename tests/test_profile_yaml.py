# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0

from pathlib import Path

import pytest
import yaml

from clusterbuster.ci.profile_yaml import (
    document_to_option_lines,
    load_yaml_profile,
    resolve_profile_path,
)


def test_func_ci_emits_limit_hammerdb_twice() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "lib" / "CI" / "profiles" / "func_ci.yaml"
    lines = load_yaml_profile(path)
    limits = [ln for ln in lines if ln.startswith("limit:hammerdb=")]
    assert set(limits) == {"limit:hammerdb=memory=7Gi", "limit:hammerdb=cpu=4"}


def test_document_nested_clusterbuster_ci() -> None:
    doc = yaml.safe_load(
        """
        clusterbuster_ci:
          options:
            a: 1
            b: [x, y]
        """
    )
    lines = document_to_option_lines(doc)
    assert "a=1" in lines
    assert "b=x" in lines and "b=y" in lines


def test_resolve_profile_name(tmp_path: Path) -> None:
    (tmp_path / "p.yaml").write_text("options:\n  x: 1\n", encoding="utf-8")
    assert resolve_profile_path("p", tmp_path) == tmp_path / "p.yaml"


def test_resolve_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        resolve_profile_path("nope", tmp_path)
