# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""Auto-import all workload modules to trigger registration."""

from . import (  # noqa: F401
    byo,
    cpusoaker,
    failure,
    files,
    fio,
    hammerdb,
    logger,
    memory,
    pausepod,
    server,
    sleep,
    sysbench,
    synctest,
    uperf,
    waitforever,
)
