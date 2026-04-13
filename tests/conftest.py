"""Shared pytest fixtures and hooks."""

from __future__ import annotations

from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--save-dryrun-output",
        default=None,
        help="Directory to save dry-run YAML output for manual verification",
    )


@pytest.fixture(autouse=True)
def _wire_dryrun_save_dir(request):
    """Set TestDryRunFullPipeline._DRYRUN_DIR from CLI option."""
    save_dir = request.config.getoption("--save-dryrun-output", default=None)
    if save_dir and request.cls and request.cls.__name__ == "TestDryRunFullPipeline":
        request.cls._DRYRUN_DIR = Path(save_dir)
