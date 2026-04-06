# Copyright 2026 Robert Krawitz/Red Hat
# SPDX-License-Identifier: Apache-2.0
#
# AI-assisted tooling (Cursor Agent).

"""Configure logging for :mod:`clusterbuster.ci`.

Phase 2 routes **INFO** and **DEBUG** to **stdout** and **WARNING** and above to
**stderr**, so CI pipelines can treat normal progress vs. problems distinctly.
Call :func:`configure_clusterbuster_ci_logging` once at process entry
(``run_perf.main`` / ``run-perf-ci-suite``).

Application code should use loggers named under the ``clusterbuster.ci.*``
hierarchy (not :data:`logging.getLogger(__name__)`) so output is consistent
whether the package is imported as ``clusterbuster.ci`` (``PYTHONPATH=lib``) or
``lib.clusterbuster.ci`` (repo-root launcher).
"""

from __future__ import annotations

import logging
import sys
from typing import Final

_LOG_NAMESPACE: Final = "clusterbuster.ci"


class _MaxLevelFilter(logging.Filter):
    """Pass only records strictly below *max_level* (e.g. WARNING)."""

    def __init__(self, max_level: int) -> None:
        super().__init__()
        self._max = max_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < self._max


def configure_clusterbuster_ci_logging(level: int = logging.INFO) -> logging.Logger:
    """Attach stdout/stderr handlers to the ``clusterbuster.ci`` logger tree.

    Idempotent: if handlers are already present, returns the existing logger.
    """
    log = logging.getLogger(_LOG_NAMESPACE)
    log.setLevel(level)
    if log.handlers:
        return log

    log.propagate = False

    stdout_h = logging.StreamHandler(sys.stdout)
    stdout_h.setLevel(logging.DEBUG)
    stdout_h.addFilter(_MaxLevelFilter(logging.WARNING))

    stderr_h = logging.StreamHandler(sys.stderr)
    stderr_h.setLevel(logging.WARNING)

    fmt = logging.Formatter("%(message)s")
    stdout_h.setFormatter(fmt)
    stderr_h.setFormatter(fmt)

    log.addHandler(stdout_h)
    log.addHandler(stderr_h)
    return log
