# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
"""Human-readable size strings (e.g. ``1Gi``, ``512Mi``) for CI options."""

from __future__ import annotations

import re
from typing import Iterable

_SIZE_RE = re.compile(r"^(-?[0-9]+)([a-zA-Z]*)$")


def parse_size(*sizes: str, delimiter: str = "\n") -> str:
    """Return delimiter-joined integer byte counts.

    Supports a delimiter between number and unit by passing delimiter ``" "``.
    """
    answers: list[int] = []
    for raw in _expand_sizes(sizes):
        raw = raw.strip()
        if not raw:
            continue
        m = _SIZE_RE.match(raw)
        if not m:
            raise ValueError(f"Cannot parse size {raw!r}")
        sizen = int(m.group(1))
        mod = m.group(2).lower()
        mult = _multiplier(mod)
        answers.append(sizen * mult)
    return delimiter.join(str(a) for a in answers)


def parse_size_list(sizes: str, *, split_re: str = r"[\s,]+") -> list[int]:
    parts = [p for p in re.split(split_re, sizes.strip()) if p]
    return [int(parse_size(p)) for p in parts]


def parse_size_colon_line(line: str) -> list[int]:
    """``parse_size -n`` applied to tokens from a colon-separated spec (files workload)."""
    tokens = [t for t in line.replace(":", " ").split() if t]
    if not tokens:
        return []
    joined = parse_size(*tokens, delimiter=" ")
    return [int(x) for x in joined.split()]


def _expand_sizes(sizes: Iterable[str]) -> list[str]:
    out: list[str] = []
    for block in sizes:
        for token in block.replace(",", " ").split():
            out.append(token)
    return out


def _multiplier(mod: str) -> int:
    if mod in ("", "b"):
        return 1
    if mod in ("k", "kb", "kilobytes"):
        return 1000
    if mod in ("ki", "kib", "kibibytes"):
        return 1024
    if mod in ("m", "mb", "megabytes"):
        return 1000000
    if mod in ("mi", "mib", "mebibytes"):
        return 1048576
    if mod in ("g", "gb", "gigabytes"):
        return 1000000000
    if mod in ("gi", "gib", "gibibytes"):
        return 1073741824
    if mod in ("t", "tb", "terabytes"):
        return 1000000000000
    if mod in ("ti", "tib", "tebibytes"):
        return 1099511627776
    raise ValueError(f"Cannot parse size modifier {mod!r}")
