# CI workload matrices (removed shell fragments)

Perf-CI workload loop logic previously lived in **`*.ci`** files sourced by the old bash driver. Those fragments and the bash driver are **removed** from the repository.

**Authoritative implementation:** Python modules under [`lib/clusterbuster/ci/workloads/`](../../clusterbuster/ci/workloads/) (registered via [`registry.py`](../../clusterbuster/ci/registry.py)). Profiles remain in [`../profiles/`](../profiles/).
