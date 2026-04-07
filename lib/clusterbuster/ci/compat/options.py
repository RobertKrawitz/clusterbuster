# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
"""Option token parsing: ``parse_option``, ``parse_optvalues``, boolean coercion."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedOption:
    noptname1: str
    noptname: str
    optvalue: str


def parse_option(option: str) -> ParsedOption:
    opt = option.strip()
    if "=" not in opt:
        low = opt.lower()
        nopt = low.replace("-", "_")
        if nopt.startswith(("no_", "dont_")) or nopt.startswith(("no-", "dont-")):
            for prefix in ("dont_", "no_", "dont-", "no-"):
                if nopt.startswith(prefix):
                    nopt = nopt[len(prefix):]
                    break
            noptname = nopt.replace("-", "_")
            noptname1 = noptname.replace("_", "")
            return ParsedOption(noptname1=noptname1, noptname=noptname, optvalue="0")
        noptname = nopt.replace("-", "_")
        noptname1 = noptname.replace("_", "")
        return ParsedOption(noptname1=noptname1, noptname=noptname, optvalue="1")
    optname, _, optvalue = opt.partition("=")
    optname = optname.strip().lower()
    noptname = optname.replace("-", "_")
    noptname1 = noptname.replace("_", "")
    return ParsedOption(noptname1=noptname1, noptname=noptname, optvalue=optvalue)


def parse_optvalues(value: str, *, split_commas: bool = True) -> list[str]:
    s = value.replace(",", " ") if split_commas else value
    return [t for t in s.split() if t]


def bool_str(value: str, *, yes: str = "1", no: str = "0") -> str:
    v = value.strip().lower()
    if v in ("", "1", "y", "yes", "true", "t"):
        return yes
    return no


def bool_str_y_empty(value: str) -> str:
    v = value.strip().lower()
    if v in ("1", "y", "yes", "true"):
        return "1"
    if v in ("0", "n", "no", "false", ""):
        return ""
    return value


def bool_str_list(value: str) -> list[str]:
    """Boolean from comma/space-separated tokens (``0``/``1`` / ``true``/``false`` style)."""
    parts = [t for t in re.split(r"[\s,]+", value.strip()) if t]
    return [bool_str(t) for t in parts]
