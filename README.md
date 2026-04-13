# ClusterBuster: Run workloads on [OpenShift](https://www.redhat.com/en/technologies/cloud-computing/openshift) clusters from the command line

ClusterBuster is a command line tool to run a variety of workloads on
OpenShift 4 clusters.  It was created with the following goals in
mind:

- *Stress the cluster*: it was originally written in 2019 to simplify
  running scalable workloads as part of [testing to 500 pods per
  node](https://cloud.redhat.com/blog/500_pods_per_node).  It now
  supports many additional workloads, but all of them can be used for
  stress testing.

- *Benchmarking clusters*: this tool is used by the Red Hat
  Performance and Scalability organization to benchmark clusters.  It
  collects performance data about each benchmark it runs, and also
  collects Prometheus metrics data.

- *Command-line focus and scriptability*: ClusterBuster is a
  traditional Linux command line tool.  It is script-friendly and easy
  to use as a component of a larger test system.  One of the tools
  included is a performance CI suite that runs ClusterBuster many times
  and packages the results.  If you prefer using the command line to
  writing YAML, ClusterBuster may be the tool for you.

- *Modularity*: it is possible to add additional workloads by simply
  adding a few files.

- *Reporting*: ClusterBuster generates reports for both single runs
  and multiple ones, creating text output that can be further
  processed with traditional command line tools.

ClusterBuster supports all workloads running in pods, [Red Hat
OpenShift
Virtualization](https://www.redhat.com/en/technologies/cloud-computing/openshift/virtualization)
VMs, and [OpenShift sandboxed
containers](https://docs.redhat.com/en/documentation/openshift_sandboxed_containers/1.4/html-single/openshift_sandboxed_containers_user_guide/index).
ClusterBuster arranges for orchestration of workloads and
synchronization between all pods/VMs so that all benchmark steps start
simultaneously.

## Supported Workloads

ClusterBuster currently supports the following workloads:

- *cpusoaker* -- a simple loop in Python that can be used either as a
  very rough measure of CPU performance or a load generator.

- *files* -- create, read, and delete large numbers of files to stress
  filesystem handling.

- *fio* -- the [Flexible I/O
  tester](https://fio.readthedocs.io/en/latest/fio_doc.html) for
  testing storage performance.  This is not a fully general front end
  to fio; it supports basic I/O patterns.

- *logger* -- generate log messages.

- *memory* -- allocate, free, and optionally use large chunks of
  memory.

- *server* -- client-server message exchange.

- *sysbench* -- a [multi-threaded system stress/benchmark
  tool](https://github.com/akopytov/sysbench).

- *hammerdb* -- [HammerDB](https://www.hammerdb.com/) TPC-C and TPROC-C
  database benchmark (NOPM/TPM).  Client and database run colocated (same
  pod or VM).  Supports PostgreSQL and MariaDB.  Not supported on arm64
  (no package shipped yet; see [HammerDB #767](https://github.com/TPC-Council/HammerDB/discussions/767)).  Use the dedicated
  `clusterbuster-hammerdb` container and `clusterbuster-hammerdb-vm` VM
  images (see `lib/container-image/`).

- *uperf* -- a [network performance tool](https://uperf.org/) front end.

In addition, these special purpose workloads are available:

- *byo* -- run a workload of your choice under ClusterBuster.

- *failure* -- run pods that deliberately fail. Intended for testing
  ClusterBuster itself.

- *pausepod* -- run a minimal pause pod that sleeps forever
  and cannot be used for other purposes.

- *sleep* -- sleep for configured interval

- *synctest* -- synchronization-only workload for validating sync
  infrastructure.

- *waitforever* -- dummy test, runs a workload that waits forever.
  This can be used to create pods or VMs that persist as
  infrastructure for running non-ClusterBuster workloads.

Workload plugins are under
[lib/clusterbuster/driver/workloads](lib/clusterbuster/driver/workloads).

## Deprecation of clusterbuster.sh

The original bash implementation has been renamed to **`clusterbuster.sh`**
and is **deprecated**.  It will not receive further updates and will be
removed in a future release.  Please use the new Python-based
**`./clusterbuster`** driver instead, which provides the same CLI
interface with improved maintainability and test coverage.

## Python package

The repository includes a **`pyproject.toml`** so you can install
ClusterBuster in editable mode:

```bash
pip install -e .
```

Declared dependencies include **PyYAML**, **kubernetes**, and
**openshift** (dynamic client).  OpenShift-specific behavior (for
example Prometheus under `openshift-monitoring`) is preserved.

The driver (`./clusterbuster` at the repo root) provides full CLI
parsing, YAML and legacy job-file support, dry-run output, and
orchestration of all supported workloads.

Job files may use either **YAML format** (an `options:` mapping with
dash-separated keys and standard `true`/`false` values) or the legacy
line-oriented format.  YAML examples are in
[`examples/`](examples/).

Perf CI orchestration is **Python** via [`run-perf-ci-suite`](run-perf-ci-suite) and [`lib/clusterbuster/ci/`](lib/clusterbuster/ci/) (the legacy bash CI driver and `lib/CI/workloads/*.ci` fragments have been removed).

### Package structure

- `lib/clusterbuster/driver/` — driver: config, CLI, orchestrator,
  sync, monitoring, reporting, metrics, cleanup, artifacts, manifests.
- `lib/clusterbuster/driver/workloads/` — workload plugins (15
  workloads, `WorkloadBase` + `@register` decorator).
- `lib/clusterbuster/ci/` — CI compatibility layer (`run-perf-ci-suite`
  support).
- `lib/pod_files/` — in-pod workload scripts
  (`clusterbuster_pod_client` subclasses).
- `tests/` — pytest suite (529+ tests).

Please peruse the [full documentation](docs/clusterbuster.md)
