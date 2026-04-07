# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
"""Subprocess boundary for invoking clusterbuster (Phase 3 hook)."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class ClusterbusterRunResult:
    returncode: int
    stdout: str
    stderr: str


class ClusterbusterRunner:
    """Runs the clusterbuster executable; replace for dry-run or remote execution."""

    def __init__(self, executable: str | Path | None = None) -> None:
        self._exe = Path(executable) if executable else Path("clusterbuster")

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> ClusterbusterRunResult:
        merged = {**os.environ, **(env or {})}
        proc = subprocess.run(
            [str(self._exe), *list(argv)],
            cwd=str(cwd) if cwd else None,
            env=merged,
            capture_output=True,
            text=True,
        )
        return ClusterbusterRunResult(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )
