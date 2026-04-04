#!/usr/bin/env python3

# Copyright 2026 Robert Krawitz/Red Hat
# AI-assisted tooling (Cursor Agent).
#
# Licensed under the Apache License, Version 2.0
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Legacy CLI entry: delegates to workload_options.cases.main_loader

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests" / "workload-options"))

from workload_options.cases import main_loader

if __name__ == "__main__":
    main_loader()
