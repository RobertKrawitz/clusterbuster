# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
"""Load ClusterBuster CI profiles from YAML into ``key=value`` lines for ``process_option``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Iterator

import yaml

_META_KEYS = frozenset(
    {
        "version",
        "name",
        "profile",
        "title",
        "description",
        "kind",
    }
)


def _stringify_value(v: Any) -> str:
    if v is None:
        return ""
    if v is True:
        return "1"
    if v is False:
        return "0"
    return str(v)


def iter_option_lines_from_mapping(options: dict[str, Any]) -> Iterator[str]:
    """Yield ``name=value`` strings (YAML mapping order preserved)."""
    for key, val in options.items():
        k = str(key)
        if isinstance(val, list):
            for item in val:
                yield f"{k}={_stringify_value(item)}"
        elif isinstance(val, dict):
            raise ValueError(f"Nested mapping not allowed under options (key {k!r})")
        else:
            yield f"{k}={_stringify_value(val)}"


def _extract_options_block(doc: dict[str, Any]) -> dict[str, Any]:
    if "options" in doc and isinstance(doc["options"], dict):
        return doc["options"]
    inner = doc.get("clusterbuster_ci")
    if isinstance(inner, dict) and isinstance(inner.get("options"), dict):
        return inner["options"]
    return {k: v for k, v in doc.items() if k not in _META_KEYS}


def document_to_option_lines(doc: dict[str, Any]) -> list[str]:
    """Return option lines from a loaded YAML document."""
    if not isinstance(doc, dict):
        raise ValueError("Profile root must be a mapping")
    raw = _extract_options_block(doc)
    if not raw:
        raise ValueError("No options found (expected top-level 'options:' or option keys)")
    return list(iter_option_lines_from_mapping(raw))


def load_yaml_profile(path: Path) -> list[str]:
    """Read a ``.yaml`` / ``.yml`` CI profile and return ``process_option`` lines."""
    text = path.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    if doc is None:
        return []
    return document_to_option_lines(doc)


def resolve_profile_path(name_or_path: str | Path, profile_dir: Path) -> Path:
    """Resolve a profile name (``func_ci``) or path to an existing file under ``profile_dir``."""
    p = Path(name_or_path)
    if p.is_file():
        return p.resolve()
    stem = p.name
    for ext in (".yaml", ".yml", ".profile"):
        cand = profile_dir / f"{stem}{ext}"
        if cand.is_file():
            return cand
    raise FileNotFoundError(f"No profile {name_or_path!r} in {profile_dir}")


def main(argv: list[str] | None = None) -> int:
    """Print one ``key=value`` per line for consumption by ``run-perf-ci-suite``."""
    p = argparse.ArgumentParser(description="Emit run-perf-ci-suite option lines from a YAML profile")
    p.add_argument("profile_path", type=Path, help="Path to .yaml / .yml profile")
    args = p.parse_args(argv)
    path = args.profile_path
    if not path.is_file():
        print(f"Not a file: {path}", file=sys.stderr)
        return 1
    try:
        for line in load_yaml_profile(path):
            print(line)
    except Exception as e:
        print(f"{path}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
