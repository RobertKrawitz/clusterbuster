#!/usr/bin/env python3
# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests"))

from workload_options.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
