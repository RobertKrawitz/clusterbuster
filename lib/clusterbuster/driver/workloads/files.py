# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""files workload: simple filesystem stressor creating and removing files."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from clusterbuster.ci.compat import bool_str, parse_size

from ..workload_registry import (
    ArglistContext,
    DeploymentContext,
    WorkloadBase,
    pod_flags,
    register,
)

if TYPE_CHECKING:
    from ..config import ClusterbusterConfigBuilder


@register
class Files(WorkloadBase):
    name = "files"
    aliases = ("file",)

    def __init__(self) -> None:
        self._file_size = 4096
        self._block_size = 0
        self._dirs_per_volume = 1
        self._files_per_dir = 1
        self._direct = 0
        self._drop_cache = 1
        self._dirs: list[str] = []

    def process_options(
        self, builder: ClusterbusterConfigBuilder, parsed: Any
    ) -> bool:
        n = parsed.noptname1
        v = parsed.optvalue
        if n == "dirspervolume":
            self._dirs_per_volume = int(v)
        elif n == "filesperdir":
            self._files_per_dir = int(v)
        elif n == "fileblocksize":
            self._block_size = int(parse_size(v))
        elif n == "filesize":
            self._file_size = int(parse_size(v))
        elif n == "filesdirect":
            self._direct = int(bool_str(v))
        elif n.startswith("filesdrop"):
            self._drop_cache = int(bool_str(v))
        elif n.startswith("filesdir"):
            if not v:
                self._dirs = []
            else:
                self._dirs.append(v)
        else:
            return False
        return True

    def _effective_block_size(self) -> int:
        if self._block_size <= 0:
            return self._file_size
        return self._block_size

    def create_deployment(self, ctx: DeploymentContext) -> bool:
        return False

    def arglist(self, ctx: ArglistContext) -> list[str]:
        cfg = ctx.config
        block_size = self._effective_block_size()
        block_count = self._file_size // block_size if block_size else 0
        dirs = self._dirs if self._dirs else [cfg.common_workdir]
        args = [
            "python3", f"{ctx.mountdir}files.py",
            *pod_flags(ctx),
            "--dirs-per-volume", str(self._dirs_per_volume),
            "--files-per-dir", str(self._files_per_dir),
            "--blocksize", str(block_size),
            "--block-count", str(block_count),
            "--processes", str(cfg.processes_per_pod),
            "--direct", str(self._direct),
        ]
        for d in dirs:
            args.extend(["--dir", d])
        return args

    def requires_drop_cache(self) -> bool:
        return bool(self._drop_cache)

    def requires_writable_workdir(self) -> bool:
        return True

    def generate_metadata(self) -> dict[str, Any]:
        return self.report_options()

    def report_options(self) -> dict[str, Any]:
        return {
            "dirs_per_volume": self._dirs_per_volume,
            "files_per_dir": self._files_per_dir,
            "file_block_size": self._effective_block_size(),
            "file_size": self._file_size,
            "files_direct": self._direct,
            "files_drop_cache": self._drop_cache,
            "files_dirs": list(self._dirs) if self._dirs else [],
        }

    def help_options(self) -> str:
        return """\
    Many Files Options:
        --dirs-per-volume=N
                        Create the specified number of directories per volume.
                        Default 1.
        --files-per-dir=N
                        Create the specified number of files per directory.
        --file-size=N
                        Each file should be of the specified size.
                        Sizes may be in bytes, [KMGT]iB, or [KMGT]B.
        --file-block-size=N
                        Write files using the specified I/O chunk size.
                        If unspecified, it defaults to the file size.
                        This should be a divisor of the file size; if not,
                        the results are unspecified.
        --files-direct  Use direct I/O (default no)
        --files-drop-cache=[0|1]
                        Drop cache, don't merely sync."""

    def document(self) -> str:
        return ("files: a simple filesystem stressor that creates and removes "
                "a large\n  number of files.")
