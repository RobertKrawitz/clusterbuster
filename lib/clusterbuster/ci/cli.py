# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0

"""Thin compatibility shim: forwards to :func:`clusterbuster.ci.run_perf.main` (single CLI surface)."""

from __future__ import annotations

from clusterbuster.ci.run_perf import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
