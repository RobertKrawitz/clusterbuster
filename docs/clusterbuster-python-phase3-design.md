# ClusterBuster Phase 3: Python Driver — Comprehensive Design Specification

This document consolidates the requirements, project plan, detailed
designs for all four implementation phases (3A–3D), and the test plan
for converting the bash `clusterbuster` driver to Python.

---

## Table of Contents

1. [Requirements](#1-requirements)
2. [Project Plan](#2-project-plan)
3. [Phase 3A: Core Framework](#3-phase-3a-core-framework)
4. [Phase 3B: Workload Plugin System](#4-phase-3b-workload-plugin-system)
5. [Phase 3C: Orchestration, Sync, Reporting, Metrics](#5-phase-3c-orchestration-sync-reporting-metrics)
6. [Phase 3D: Integration and End-to-End Testing](#6-phase-3d-integration-and-end-to-end-testing)
7. [Test Plan](#7-test-plan)
8. [Appendix A: Bash-to-Python Parity Audit](#appendix-a-bash-to-python-parity-audit)
9. [Appendix B: Supplementary Parity Review](#appendix-b-supplementary-parity-review)

---

## 1. Requirements

This section is the requirements specification for the project.

### 1.1 Deliverables

1. Project plan with delivery phases.

*For each phase:*

2. Project plan and design, reviewed iteratively.
3. Code with unit tests, reviewed iteratively.
4. Debug and test phase.

*Finally:*

5. PR and merge.

### 1.2 General

- **Full behavioral parity** with the existing bash implementation,
  including namespace management, metrics collection, reporting, YAML
  generation, VM cloud-init, virtctl fallback, logging, debug logging,
  artifact collection, labeling, and node pinning/affinity.

  Exceptions (initial; see the parity audit for the full list of 15
  intentional differences documented during CI parity testing):
  - Shell stack traces replaced by Python exceptions.
  - `-f <file>` accepts YAML; the legacy `option=value` format is
    supported as a fallback.

- **All 15 workloads** converted to Python.  No Python wrappers around
  shell scripts.

- Multiple concurrent instances supported via different namespaces.

- Invocable from command line or by Python import.  The user does not
  need `python -m`.

- CLI returns 0 for success, 1 for failure/abort.

- `run-perf-ci-suite` invokes clusterbuster by import.

- Examples converted to YAML.

- Pythonic interface to Kubernetes where possible; direct `oc` calls
  where unavoidable.  Container images unchanged.

- Initially rename `clusterbuster` to `clusterbuster.sh`, then create
  a Python `clusterbuster` launcher.

### 1.3 Error and Termination Handling

Failure causes include:

- Object creation failures (bad YAML, bad KUBECONFIG, missing CRDs)
- Sync timeout (pods don't connect in time)
- Sync watchdog timeout (missed checkins)
- Pod failures (worker pods fail/deleted)
- User termination (Ctrl-C, kill)

Handling depends on cleanup flags, metrics requests, and repeated
signals.  A mocked test suite is required.

---

## 2. Project Plan

Convert **~7600 lines of bash** to a Python package under
`lib/clusterbuster/`.

### 2.1 Phase Overview

| Phase | Scope | Key Deliverables |
|-------|-------|-----------------|
| **3A** | Core framework | CLI parsing, config builder/dataclass, cluster interface, manifest generation, VM support |
| **3B** | Workload plugins | Workload registry, dispatch API, all 15 workload modules |
| **3C** | Orchestration | Run lifecycle, sync protocol, monitoring, reporting, artifact collection, metrics, cleanup, error/termination handling |
| **3D** | Integration | Python launcher, example conversion, `run-perf-ci-suite` import path, end-to-end testing |

### 2.2 Files to Retain

- Container images (no changes)
- `lib/workloads/*.workload` — required by `clusterbuster.sh`;
  will be removed when `clusterbuster.sh` is removed
- `lib/libclusterbuster.sh` — required by `clusterbuster.sh`;
  will be removed when `clusterbuster.sh` is removed
- `lib/CI/profiles/*.yaml` (no changes)
- `lib/clusterbuster/reporting/` (no changes except import)

Note: `clusterbuster.sh` is now deprecated (with a runtime banner)
but is retained as a fallback until the Python driver is validated
in production.

### 2.3 Key Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| YAML parity | Diff dry-run output for every workload × deployment type |
| Report JSON schema drift | Parity test with `clusterbuster-report` |
| Error handling complexity | Clean context-manager design; dedicated test suite |
| `oc` vs K8s client | Keep `oc` subprocess where unavoidable |
| VM cloud-init | Build as Python dict, serialize with `yaml.dump()` |
| Sync protocol ordering | Preserve remove-after-read invariant |

### 2.4 Out of Scope

- Replacing in-container workload scripts
- Rebuilding container images
- Kubernetes (non-OpenShift) support
- Structured return type beyond success/failure/aborted

---

## 3. Phase 3A: Core Framework

### 3.1 Module Layout

```
lib/clusterbuster/driver/
    __init__.py          # exports main(), run_clusterbuster(), run_from_argv()
    config.py            # ClusterbusterConfigBuilder, ClusterbusterConfig
    cli.py               # parse_argv(), process_option(), help text
    cluster.py           # ClusterInterface
    manifests.py         # ManifestBuilder — all K8s YAML generation
    manifest_plan.py     # build_manifest_plan() — unified manifest generation
    vm.py                # VmManifestBuilder, cloud-init, VirtctlInterface
    orchestrator.py      # run() — full run lifecycle
    sync.py              # Controller-side sync protocol
    monitoring.py        # Pod failure detection via streaming watch
    reporting.py         # JSON report assembly
    artifacts.py         # Artifact collection (logs, describe, VM paths)
    metrics.py           # Prometheus integration, prom-extract
    cleanup.py           # Label-based cleanup, host cache drop
    workload_registry.py # WorkloadPlugin protocol, registry, dispatch
    workloads/           # 15 workload plugin modules
lib/clusterbuster/compat/
    options.py           # parse_option, bool_str (no changes needed)
    sizes.py             # parse_size (no changes needed)
```

### 3.2 `config.py` — Two-Stage Configuration

Builder pattern following Phase 2's `CISuiteState` →
`ClusterbusterCISuiteConfig`.  The `ClusterbusterConfigBuilder`
accumulates mutable state during CLI/job-file parsing; `build()`
produces a frozen `ClusterbusterConfig` for the run, applying
defaults and validation (equivalent to bash `validate_options`).

The builder contains ~90 fields organized by functional area:
identity/naming, namespace layout, deployment topology, parallelism,
sync/coordination, timing, workload sizing, images, affinity/placement,
resources/security, volumes, reporting/artifacts/metrics, cleanup,
verbosity, VM (KubeVirt), services/probes, drop cache, Kata/virtiofs,
and debug/testing.

The `build()` method performs validation equivalent to bash
`validate_options`: workload must be specified, deployment type
normalized, resources validated, volumes validated (type checking,
duplicate detection, mountpoint dedup), parallelism defaults filled,
metrics file readability checked, aarch64 VM guard, SSH key generation
for VMs, and workload finalization hooks.

### 3.3 `cli.py` — Option Parsing and Help

`parse_argv()` maps the bash `getopts` loop with 10 short options and
~128 long option branches dispatched via a dict keyed on normalized
option names.  Helper functions handle compound options (pin-node,
runtime class, affinity, external sync, etc.).

`process_job_file()` loads YAML with an `options:` mapping.  A legacy
`option=value` fallback is provided for backward compatibility.

Help text assembles short usage, long option listing, and
workload-contributed options.

### 3.4 `cluster.py` — ClusterInterface

Maps 5 bash `oc` wrapper functions to a clean API:

| Bash Function | Python Method | Behavior |
|---------------|--------------|----------|
| `__OC` | `run(check=False)` | Return status, dry-run skip |
| `_OC` | `run_fatal(kubefail=True)` | Fatal on failure |
| `___OC` | `run_fatal(kubefail=False)` | Fatal, plain message |
| `____OC` | `run(dry_run_skip=False)` | Always run |
| `_____OC` | `watch()` | Streaming context manager |

Additional methods: `exec_()`, `get_json()`, `create()`, `apply()`,
`delete()`, `label()`, `logs()`, `describe()`, `wait()`, `adm()`,
`debug_node()`.

The `run()` method accepts a `log_output: bool` parameter (default
`False`) that controls whether `oc` command stdout is logged.
Mutating operations (`create`, `apply`, `delete`) pass
`log_output=True` so their output appears in `stderr.log`.

### 3.5 `manifests.py` — ManifestBuilder

All YAML generation returns Python dicts, serialized to YAML only at
the creation boundary.  Methods grouped by object kind: namespace,
secret, configmap, service, pod, deployment, replicaset.

Shared spec building: `_pod_spec()`, `_container()`,
`_volume_mounts()`, `_volumes()`, `standard_labels()`,
`_annotations()`, `_affinity()`, `_tolerations()`, `_resources()`,
`_security_context()`, `_liveness_probe()`.

`ObjectBatcher` accumulates manifests and flushes in configurable
batch sizes.  When an `artifact_dir` is configured, `ObjectBatcher`
also saves each applied manifest as YAML to
`<artifact_dir>/<Kind>/<ns>:<name>`.

`ObjectBatcher._stamp_crtime()` patches `--cb-crtime=` in each
manifest's container command (or VM cloud-init `userData`) with the
current `time.time()` at the moment the manifest is added to the
batch.  Manifests are built during the planning phase (a tight loop),
so without this rewrite, all pods would share nearly identical
`crtime` values.  Bash stamps the time at `oc create`/`apply`
submission; `_stamp_crtime()` provides the same semantics for Python.

### 3.6 `manifest_plan.py` — Unified Manifest Generation

During parity testing, the dry-run path (`cli.py`) and the live path
(`orchestrator.py`) each had ~300 lines of duplicated
manifest-building logic, leading to divergence bugs (audit bugs 5, 6,
7).  `manifest_plan.py` was created to eliminate this duplication.

`build_manifest_plan()` is the single entry point, called by both
`_print_dry_run()` (dry-run mode) and `_create_all_deployments()`
(live mode).  It returns a list of manifest dicts for all namespaces,
deployments, and replicas.

Helpers:
- `_build_containers()`: Constructs container specs with workload
  arglists, environment, and volume mounts.
- `_build_single_role_deployment()`: Builds manifests for workloads
  with a single pod role (cpusoaker, files, fio, etc.).
- `_build_client_server_deployments()`: Builds manifests for
  client/server workloads (server, uperf) with separate server and
  client pods, services, and affinity rules.

**Per-replica deployment model:** For Deployment and ReplicaSet types,
Python creates N separate objects with `replicas: 1` and unique
per-replica labels, rather than bash's single object with
`replicas: N`.  This is an intentional behavioral improvement
(parity audit intentional difference Q) that enables drop-cache pod
affinity to target individual worker replicas — a pre-existing bash
bug where drop-cache pods could never match specific replicas.

### 3.7 `vm.py` — VM Manifest Generation

`VmManifestBuilder` generates KubeVirt `VirtualMachine` manifests with
domain spec, cloud-init userData/networkData, disk/interface/network
configuration.  `VirtctlInterface` wraps `virtctl` commands.

### 3.8 Public API (`__init__.py`)

```python
def main(): ...          # CLI entry point
def run_clusterbuster(argv=None, config=None) -> int: ...
def run_from_argv(argv) -> int: ...  # for run-perf-ci-suite import
```

### 3.9 Bash Quirks Addressed

1. `watchdogtimeout` vs `sync_watchdog_timeout` — unified to single field.
2. `sync`/`syncstart` toggle — preserved for CLI parity.
3. `xuuid` not updated after `--uuid` — computed in `build()` from
   final value.
4. `port` (7777) not CLI-exposed — kept as constant.

---

## 4. Phase 3B: Workload Plugin System

### 4.1 Registry and Base Class

`WorkloadBase` defines ~25 optional methods (process_options, arglist,
server_arglist, client_arglist, create_deployment, list_configmaps,
generate_metadata, supports_reporting, sysctls, help_options,
document, vm hooks, etc.).  `@register` decorator registers instances.

### 4.2 Workload Inventory

| Tier | Workloads | Complexity |
|------|-----------|-----------|
| 1 (trivial) | cpusoaker, sleep, failure, waitforever, pausepod | Arglist only |
| 2 (options) | synctest, logger, memory, hammerdb, sysbench | Options + arglist |
| 3 (custom) | files, byo, fio | Configmaps, jobfile generation |
| 4 (client/server) | server, uperf | Multi-pod topology, services, affinity |

### 4.3 Config Integration

`build()` wires workload callbacks for option processing
(`workload_process_options`) and finalization
(`workload_finalize_args`).  Alias resolution maps names like `"cpu"`
→ `"cpusoaker"`.

### 4.4 Design Points

| Concern | Bash | Python |
|---------|------|--------|
| Registration | `register_workload` + sourced functions | `@register` decorator on class |
| Dispatch | `dispatch_generic` checks function existence | Method on class; base defaults |
| State | `___workload_variable` globals | Instance attributes |
| Global mutation | Sets globals like `container_image` | Sets on builder during `process_options` |

---

## 5. Phase 3C: Orchestration, Sync, Reporting, Metrics

### 5.1 Architecture: Single-Process, Threads for Concurrency

Replaces bash's nested subshell architecture with:
- No subshells.  All orchestration in a single process.
- `threading.Thread` for concurrent activities, coordinated via
  `threading.Event`.
- `try/finally` for cleanup obligations.
- Signal handlers only at the top level.

### 5.2 `orchestrator.py` — Run Lifecycle

`RunContext` dataclass tracks all run state: config, cluster,
manifests, namespaces, timing, sync state, thread-safe events
(`shutdown_event`, `run_failed`, `run_complete`, `any_thread_done`),
and active subprocess handles.

`run()` executes the full lifecycle: create context → temp directory →
signal handlers → pre-cleanup → timestamps → artifact setup →
namespace allocation → create objects (namespaces → configmaps →
secrets → deployments) → monitoring + sync retrieval → report →
cleanup.

Object creation is split into two phases within
`_create_all_deployments()`:

1. **Sync infrastructure** (`_create_sync_services()`): creates only
   sync-specific objects — ConfigMaps for the sync namespace,
   Services, and the sync Pod — one-by-one with `force_flush=True`.
2. **Worker objects** (`_create_all_parallel()`): creates all worker
   Pods, Deployments, ReplicaSets, and VirtualMachines using strided
   parallelism via `ThreadPoolExecutor` and batched submission via
   `ObjectBatcher`, respecting `--parallel` and
   `--objects-per-call` settings.

The separation ensures that sync infrastructure is fully available
before worker pods start, while worker pods benefit from parallel
and batched creation for performance.

Additional `RunContext` fields added during CI parity work:
`effective_remove_namespaces` (threads precleanup auto-detection
through the run), `effective_first_deployment` (first deployment
index when scaling), and `worker_results` (JSON results from sync
pod).

### 5.3 Logging Architecture

The bash driver uses `exec 2> >(tee … > stderr.log)` for stderr
capture and `oc get pod -w | tee >(grep … | timestamp > monitor.log)`
for monitoring output.  Python replaces this with Python's `logging`
module and per-file handlers, configured in `orchestrator.run()`:

- **`_IsoTimestampFormatter`**: Formats all log records with ISO 8601
  timestamps including microseconds (e.g.,
  `2026-03-23T22:31:35.462426`), matching the bash timestamp format.

- **`_StderrLogFilter`**: Routes WARNING+ records from all loggers
  and INFO+ records from `clusterbuster.driver.*` loggers to
  `stderr.log` and the console.  Explicitly excludes
  `clusterbuster.driver.monitoring` loggers (monitoring messages go
  only to `monitor.log`).  INFO records from
  `clusterbuster.driver.cluster` (object creation messages) are
  gated by the `report_object_creation` flag on the console handler
  but always pass on the `stderr.log` file handler.  This mirrors
  bash behavior where `oc` mutating command output appears on stderr
  and in `stderr.log`, while monitoring output goes only to the
  monitor log.

- **`_MonitorLogFilter`**: Routes records from the
  `clusterbuster.driver.monitoring` logger to `monitor.log`.  This
  captures pod state changes (Pending → Running, Running →
  Completed, etc.) from the streaming `oc get pod -w` watch.

- **Console handler ordering**: The console `StreamHandler` is set
  up *before* the precleanup phase in `run()`, ensuring that
  `oc delete` and `oc label` messages during precleanup are visible
  on the terminal.  Without this ordering, precleanup deletion
  messages would be silently discarded.

- **`ClusterInterface.run()` `log_output` parameter**: Controls
  whether `oc` command stdout is logged.  Set to `True` for mutating
  operations (`create`, `apply`, `delete`, `label`) so their output
  appears in `stderr.log`.  Non-mutating operations (queries, watches)
  do not log output by default.

- **Transient error suppression**: During sync pod startup, `oc exec`
  failures are retried silently (matching bash's behavior) rather than
  logging warnings for expected transient errors like "container not
  found."

Handlers are cleaned up in the `finally` block of `run()`.

### 5.4 `sync.py` — Controller-Side Sync Protocol

Critical ordering invariant: read sync flag file → validate JSON →
remove file (signals sync pod to exit).

`get_pod_and_local_timestamps()`: waits for sync pod, exchanges
timestamps via `oc exec`.  Retries silently for transient errors
(matching bash behavior) until the sync pod is responsive or enters
a terminal state.

`get_sync_logs()`: starts monitor + log helper threads, waits for
either to finish (`wait -n` semantics via `any_thread_done.wait()`),
assembles report.

`_log_helper()`: polls for sync flag file via `oc exec`, handles API
transients (cert rotation), removes flag file on success.

`_fail_helper()`: polls for error/flag/done files.

### 5.5 `monitoring.py` — Pod Failure Detection

Streaming pod watch with heartbeat via `cluster.watch()`.  Pod state
machine tracks phases (Error, Failed, Pending, Running, Completed).
Uses `dict[str, str]` for pod state (eliminates bash counter
asymmetry bugs).  Enforces pod-start timeout and global timeout.

### 5.6 `reporting.py` — JSON Report Assembly

`assemble_report()` builds the full report JSON with metadata, worker
results, options, cluster info, metrics.
`generate_emergency_report()` provides minimal fallback.
`print_report()` dispatches to `clusterbuster-report` for formatting.

### 5.7 `artifacts.py` — Artifact Collection

Parallel log/describe retrieval via `ThreadPoolExecutor` with
configurable concurrency cap (`parallel_log_retrieval`).

Additional artifact behaviors for bash parity:

- **Manifest artifacts**: `ObjectBatcher._save_manifest_artifact()`
  saves each applied manifest as YAML to
  `<artifact_dir>/<Kind>/<ns>:<name>` (e.g., `Pod/work-ns:worker-0`).
- **System pod files**: `_save_sysfiles()` copies system pod files
  (workload scripts) to `<artifact_dir>/SYSFILES/`.
- **Sync namespace pods**: Artifacts are collected for sync namespace
  pods (not skipped), matching bash behavior.
- **VM-specific directories**: `VMLogs/` and `VMXML/` subdirectories
  are only created when `deployment_type == "vm"`.
- **Command line**: The `commandline` artifact file uses
  `shlex.join()` for proper shell quoting of arguments containing
  spaces.

VM artifact collection includes `oc describe vm/vmi`, libvirt XML
retrieval via `oc exec` into virt-launcher pods, and `virtctl scp`
for cloud-init logs.

### 5.8 `metrics.py` — Prometheus Integration

Metrics extraction is decoupled from Prometheus snapshot retrieval:
extraction runs whenever `config.metrics_file` is set, regardless
of `config.take_prometheus_snapshot`.

Key behaviors:

- **`metrics_file` resolution**: The strings `"default"` and `"1"`
  are resolved to `lib/metrics-default.yaml` during config build.  Empty string or `"0"` disables metrics.
- **`prom-extract` invocation**: Arguments match bash's
  `_extract_metrics`: `--indent=`, `--define namespace_re=...`,
  `-m`, `-s`, `--metrics-only`, `--start_time`,
  `--post-settling-time`.
- **60-second wait**: A 60-second sleep precedes `prom-extract`
  invocation, matching bash behavior to allow Prometheus scraping
  to complete.
- **Path resolution**: `prom-extract` is looked up at
  `lib/prom-extract` (not the repo root).

Prometheus snapshot start/retrieve: deletes and recreates the
`prometheus-k8s-0` pod, waits for Ready, aligns timestamps via
`oc exec date +%s.%N` on the Prometheus pod.

### 5.9 `cleanup.py` — Cleanup and Host Cache Drop

`do_cleanup()` implements label-based cleanup with several
parity-critical behaviors:

- **Precleanup auto-detection**: `_has_labeled_namespaces()` queries
  the cluster for existing labeled namespaces.  When
  `remove_namespaces == -1` (default) and precleanup finds existing
  namespaces, it sets `effective_remove_namespaces = 0` to preserve
  them, threading this decision through the entire run via
  `RunContext`.
- **Three-step force-cleanup escalation** in `_force_cleanup()`:
  (1) VM-specific sync-label `oc delete all`, (2) basename-label
  `oc delete all`, (3) namespace delete.
- **`oc delete all` semantics**: Uses the Kubernetes `all` resource
  alias (which covers Pods, Services, Deployments, ReplicaSets, etc.)
  rather than enumerating specific resource types.

`drop_host_caches()` runs `oc debug node/` in parallel across
worker nodes.  When `pin_nodes` is set and `drop_all_node_cache` is
false, only pinned nodes are targeted.

### 5.10 Signal and Error Handling

Signal handler sets `shutdown_event` and `run_failed`.  Double-signal
protection restores `SIG_DFL`.  First-writer-wins semantics for
`failure_reason` via lock.  `_interruptible_run()` pattern for
blocking subprocess calls.  Thread join with 30-second timeout.

Thread hierarchy:
```
Main thread (orchestrator.run)
├── do_logging() — blocks on any_thread_done.wait()
│   ├── monitor_pods thread
│   └── _log_helper thread
│       └── _fail_helper thread
```

---

## 6. Phase 3D: Integration and End-to-End Testing

### 6.1 Python Launcher

Thin Python script at repo root (`./clusterbuster`) that discovers
`lib/` via `CB_LIBPATH` and delegates to `clusterbuster.driver.main()`.
`pyproject.toml` adds `[project.scripts]` entry point.

Bash script renamed to `clusterbuster.sh` (preserved as fallback).

### 6.2 Example YAML Conversion

18 option-format job files converted to YAML with `options:` mapping.
Legacy format supported as fallback in `process_job_file()`.
Shell scripts updated with explicit `examples/*.yaml` paths.

### 6.3 Help Text Parity

`-h` shows core usage and option descriptions without
workload-specific options.  `-H` shows the full help text including
per-workload options, descriptions, and `document()` output.  Both
are routed through `_pager_output()` for terminal paging.

`_build_help_text(include_workloads)` assembles the text:
`include_workloads=False` for `-h`, `True` for `-H`.  Workload
option groups are separated by blank lines for readability,
matching the bash format.  `--help-options` is an alias for `-h`.
Workload-contributed options via `help_options()` hooks.  Display
name mapping for readability.

### 6.4 Dry-Run Parity

`-n` prints config summary, namespace plan, and generated YAML
manifests using the same `ManifestBuilder` as real runs.

### 6.5 `run-perf-ci-suite` Integration

Zero-change integration: Python launcher at same path accepts same
CLI arguments.  `run_from_argv()` provides the import path.

---

## 7. Test Plan

### 7.1 Strategy

Three tiers:

- **Tier 1 (offline):** Unit and integration tests via pytest.
  Gates every commit.
- **Tier 2 (cluster):** End-to-end workload runs, cleanup, termination,
  report compatibility.
- **Tier 3 (CI suite):** `test_ci` and `func_ci` profiles.

### 7.2 Tier 1 Coverage

| Area | Tests | File |
|------|-------|------|
| Config builder/validation | 39 | `test_driver_config.py` |
| Manifest generation | 38 | `test_driver_manifests.py` |
| Cluster interface | 10 | `test_driver_cluster.py` |
| Workload plugins | 171 | `test_driver_workloads.py` |
| CLI/option parsing | 69 | `test_driver_cli.py` |
| Integration/launcher | ~118 | `test_driver_integration.py` |
| Orchestrator/threads/signals | 99 | `test_driver_orchestrator.py` |
| Golden file parity | varies | `test_golden_parity.py` |
| CI parity | varies | `test_ci_parity.py` |
| Profile YAML validation | varies | `test_profile_yaml.py` |
| Workload option parsing | varies | `test_workload_options.py` |
| Restart behavior | varies | `test_restart.py` |
| E2E (Tier 2, cluster) | 16 | `test_e2e.py` |

Covers: CLI parity, job file compatibility, input validation,
signal handling, timeout, thread coordination, failure propagation,
error injection, container image override semantics, cleanup, metrics,
structural parity against bash golden files.

### 7.3 Tier 2: Cluster E2E

16 tests in `test_e2e.py`:

- **`TestE2EPodWorkloads`** (4 tests): cpusoaker, sleep, memory,
  files in pod mode
- **`TestE2EKata`** (4 tests): cpusoaker, sleep, memory, fio with
  Kata runtime class
- **`TestE2ECNV`** (4 tests): cpusoaker, sleep, memory, fio as VMs
- **`TestE2ENamespaceReuse`** (4 tests): namespace reuse across runs,
  cleanup removes objects but keeps ns, precleanup with
  removenamespaces=0, autodetect removes namespaces with -1

Additionally, manual Tier 2 testing covers:
- Termination tests (SIGINT, timeout, error injection, pod failure)
- Cleanup verification (--cleanup, --precleanup, --force-cleanup,
  --cleanup-always)
- Report compatibility with `clusterbuster-report` and
  `analyze-clusterbuster-report`

### 7.4 Tier 3: CI Suite

- `test_ci` profile (~24 jobs, ~55 minutes)
- `func_ci` profile (~72 jobs, ~4 hours)
- All workloads across runc/kata/vm runtime classes

### 7.5 Pass/Fail Criteria

- Tier 1: All tests pass, zero failures.
- Tier 2: All pod-mode workloads exit 0 with valid reports; cleanup
  and termination tests pass.
- Tier 3: `test_ci` and `func_ci` complete with all workloads passing.

### 7.6 Current Status

- 1557 Tier 1 tests pass
- 16/16 E2E tests pass
- 72/72 `func_ci` jobs pass
- 24/24 `test_ci` jobs pass
- 73 parity bugs fixed across 8 rounds (74 audit entries; #56
  reclassified as intentional difference), 15 intentional
  differences documented

See [Appendix A](#appendix-a-bash-to-python-parity-audit) for the
complete parity audit: every bug fixed, every intentional difference
documented, methodology, and lessons learned.

---

## Appendix A: Bash-to-Python Parity Audit

This appendix catalogues every structural difference found between
the Kubernetes manifests generated by `clusterbuster.sh` (bash) and
`clusterbuster` (Python) during the Phase 3 conversion.  It is
organized into three sections:

1. **Bugs fixed** — differences that were genuine omissions or errors
   in the Python version, resolved to match bash behavior.
2. **Intentional differences** — places where the Python version
   deliberately departs from bash, with justification.
3. **Methodology** — tools, discovery patterns, architectural fixes,
   and lessons learned for future conversion projects.

**Current status (2026-04-15):** 73 bugs fixed across 8 rounds of
analysis.  The latest `func_ci` run on `scale` passes **72/72 jobs**
(memory, fio, uperf, files, cpusoaker, hammerdb across runc/kata/vm
runtime classes).  `fio-kata`, previously failing due to virtiofsd
page-cache OOM, now passes with an explicit memory limit
(`filesize + VM_size + 1 GiB`).  All 16 E2E tests pass.
1557 unit and integration tests pass.  Round 8 fixed runtime
behavioral issues (object creation timing and parallelism, metrics
extraction, console logging ordering, monitoring log routing) and
CLI help text parity (`-h` vs `-H` differentiation, format code
escaping, workload group spacing).

The analysis was performed using a combination of:

- Field-by-field deep comparison tool (`tests/deep_compare.py`) that
  normalizes UUIDs, timestamps, and SSH keys, then recursively diffs
  every manifest field across all 14 workloads x 4 deployment types.
- End-to-end cluster runs on `scale` with failure log analysis.
- Manual reading of `clusterbuster.sh` source functions
  (`create_spec`, `create_standard_containers`,
  `sysctls_security_context`, `create_vm_deployment`, etc.).
- Golden-file parity tests (`tests/test_golden_parity.py`) comparing
  extracted structural fields.
- Supplementary review (`docs/bash-python-parity-supplementary-review.md`)
  providing per-workload, per-subsystem comparison of arglists,
  affinity, tolerations, volumes, annotations, cloud-init, and
  infrastructure pods.

### A.1 — Bugs Fixed

#### Original Audit (Bugs 1–12)

| # | Difference | Analysis | Resolution | Files Changed |
|---|-----------|----------|------------|---------------|
| 1 | **Worker pods missing `restartPolicy: Never`**. Python worker pods had no `restartPolicy`, defaulting to `Always`. Bash explicitly sets `restartPolicy: Never` so that failed workers terminate cleanly rather than being restarted by the kubelet. | `deep_compare.py` did not flag this because bash dry-run and Python dry-run both set timestamps to `0` for some fields; the issue was found by reading `create_spec` in bash and comparing the pod template side-by-side. Confirmed by running `./clusterbuster -w cpusoaker -n` and checking for the field. | Added `spec["restartPolicy"] = "Never"` in the `pod()` method of `ManifestBuilder`. | `lib/clusterbuster/driver/manifests.py` |
| 2 | **`exit_at_end` incorrectly set to `False` for VMs**. The Python `_validate_options` method set `self.exit_at_end = False` when `dtype == "vm"`, preventing workload processes inside VMs from terminating. Bash keeps `exit_at_end` at its default (`True`) for VMs; only `deployment` and `replicaset` types set it to `False` (because their pods are long-lived). | Discovered by reading the bash `create_vm_deployment` function and tracing `--cb-exit-at-end` handling. The bash version only sets `exit_at_end=0` via `pod_flags` for deployment/replicaset types. VMs should terminate their workload and shut down. | Removed the line `self.exit_at_end = False` from the `elif dtype == "vm"` branch in `_validate_options`. | `lib/clusterbuster/driver/config.py` |
| 3 | **Restricted security context missing `runAsNonRoot: True`**. The Python `security_context()` method omitted `runAsNonRoot: True` from its restricted (non-privileged) return value. Bash includes it in `restricted_security_context_content`. This field is required for Kubernetes Pod Security Admission (PSA) `restricted` policy compliance. | Found by `deep_compare.py` field diff on `.spec.containers[0].securityContext.runAsNonRoot`. Cross-referenced with the bash function `restricted_security_context_content` which emits `runAsNonRoot: true`. | Added `"runAsNonRoot": True` to the restricted security context dict returned by `security_context()`. | `lib/clusterbuster/driver/manifests.py` |
| 4 | **Workload sysctls not threaded to container `securityContext`**. Workloads like `uperf` and `server` define kernel parameters (e.g., `net.ipv4.ip_local_port_range`) via their `sysctls()` method. The Python code had partial plumbing — `_build_single_role_deployment` accepted a `sysctls` kwarg and tried to pass it to `pod()` — but the pipe was never connected end-to-end. Containers were always built without sysctls in their security context. | `deep_compare.py` flagged `.spec.containers[0].securityContext.sysctls: MISSING in python` for all uperf and server pods. Traced through bash's `sysctls_security_context` function, which injects `sysctls:` entries inside each container's `securityContext:` block. | Refactored the plumbing: (a) added `sysctls` parameter to `security_context()`; (b) moved sysctl injection from the never-reached `pod()` call into `_build_containers()`, which builds the security context once and passes it to each `container()` call; (c) mirrored the same change in the dry-run path in `cli.py`. | `lib/clusterbuster/driver/manifests.py`, `lib/clusterbuster/driver/orchestrator.py`, `lib/clusterbuster/driver/cli.py` |
| 5 | **Drop-cache pod missing `podAffinity` in dry-run path**. Bash's drop-cache pods have a `requiredDuringSchedulingIgnoredDuringExecution` pod affinity that co-locates them on the same node as their corresponding worker pod (matched by `replica` label). The orchestrator path already passed `worker_replica_label` correctly, but the dry-run path in `cli.py` did not, so `clusterbuster -n` output omitted the affinity block. | `deep_compare.py` flagged `.spec.affinity` differences. Compared the two call sites — `orchestrator.py` line 565 (correct) vs `cli.py` line 963 (missing `worker_replica_label`). | Added `worker_replica_label=f"{dep_name}-{replica}"` to the `drop_cache_pod()` call in the dry-run path. | `lib/clusterbuster/driver/cli.py` |
| 6 | **VM builder missing workload packages, setup commands, and sysctls in dry-run path**. The orchestrator path correctly passed `workload_packages`, `workload_setup_commands`, and `workload_sysctls` to `vm_builder.virtual_machine()`, but the dry-run path in `cli.py` omitted all three. This meant `clusterbuster -n` output for VM deployments was missing cloud-init package installation and sysctl setup commands. | Side-by-side comparison of the `virtual_machine()` call in `orchestrator.py` (line 633–639) vs `cli.py` (line 1022–1031). The dry-run call had only `workload_args` and `workload_env`. | Added `workload_packages=wl.vm_required_packages()`, `workload_setup_commands=wl.vm_setup_commands()`, and `workload_sysctls=wl.sysctls(cfg)` to the dry-run `virtual_machine()` call. | `lib/clusterbuster/driver/cli.py` |
| 7 | **Sync service had only 1 TCP port; bash creates 4**. The dry-run path created a sync `Service` with a single TCP port for `sync_port`. Bash creates 4 ports: TCP and UDP for both `sync_port` (7778) and `sync_ns_port` (7753). The orchestrator path was already correct. | `deep_compare.py` showed sync service port count differences. Compared the orchestrator's `_create_sync_services` (lines 481–489, which loops over both ports and both protocols) with the dry-run path's single-port dict. | Replaced the single-port literal with a loop over `(cfg.sync_port, cfg.sync_ns_port)` x `("TCP", "UDP")`, matching the orchestrator. Also fixed the selector from `{basename}-sync: uuid` to `{basename}-sync-sync: "true"` to match. | `lib/clusterbuster/driver/cli.py` |
| 8 | **ExternalName services missing ports entirely**. Both the orchestrator and dry-run paths created `ExternalName` services for cross-namespace sync access without any `ports` array. Bash includes two port entries (one per sync port number). | `deep_compare.py` flagged `.spec.ports` as `MISSING in python` on ExternalName services. Confirmed by reading bash `create_service_external_name`. | Added `ports` parameter to `external_service()` in `ManifestBuilder`; updated both orchestrator and dry-run callers to pass ports for `sync_port` and `sync_ns_port`. | `lib/clusterbuster/driver/manifests.py`, `lib/clusterbuster/driver/orchestrator.py`, `lib/clusterbuster/driver/cli.py` |
| 9 | **FIO VM missing `user-configmap` disk**. The KubeVirt `VirtualMachine` manifest did not include the `user-configmap` volume/disk, so the FIO job file (delivered via ConfigMap) was not mounted inside the VM. The workload failed with `"no jobfiles provided!"` at runtime. | Discovered during E2E `func_ci` run on `scale`. The error log showed `fio` could not find its job file. Compared bash's `create_vm_deployment` (which always adds the `user-configmap` disk) with the Python `_domain_spec` method (which conditionally added it only when `cfg.configmap_files` was set — but the FIO workload uses `list_user_configmaps()` instead). | Made the `user-configmap` disk inclusion unconditional in `_domain_spec`. | `lib/clusterbuster/driver/vm.py` |
| 10 | **FIO VM `emptydisk` missing `serial` field**. Without a `serial`, the guest VM could not locate the disk at `/dev/disk/by-id/virtio-<name>`, causing FIO to write to a non-existent path. | Discovered during E2E run — FIO inside the VM failed with `"No space left on device"` because it wrote to the root filesystem instead of the emptydisk. Compared the bash `emptydisk` stanza (which includes `serial: <vname>`) with the Python `_domain_spec` (which omitted it). | Added `"serial": vname` to the `disk_entry` dict for emptydisk volumes. | `lib/clusterbuster/driver/vm.py` |
| 11 | **FIO VM `emptydisk` not formatted (`mkfs` not run)**. The `cloud_init_userdata` method checked `if fstype is None` to decide whether to run `mkfs`, but `kv_get` returns `""` (empty string) for a missing key, not `None`. So the `mkfs` command was never generated, leaving the emptydisk unformatted. | Second `"No space left on device"` failure after fix #10. The disk now existed but had no filesystem. Compared bash cloud-init output (which includes `mkfs.ext4`) with Python output (which omitted it). Traced through `kv_get` return semantics. | Changed the condition from `if fstype is None` to `if not fstype`. | `lib/clusterbuster/driver/vm.py` |
| 12 | **Namespace cleanup not waiting indefinitely**. The bash `--cleanup` path waits indefinitely for namespace deletion (polling until the namespace is gone). The Python cleanup attempted deletion but moved on after a limited wait or if the namespace was already absent, leading to races where the next run found the namespace still terminating. | Discovered during E2E runs where sequential workloads failed with `"namespace already exists"` or `"namespace being terminated"` errors. Read the bash cleanup logic which loops `while oc get namespace ...` succeeds. | Made `_wait_for_namespace_deletion` unconditional (always called after namespace delete) and changed it to poll indefinitely until the namespace is fully gone. Added `--ignore-not-found=true` to `oc delete` calls. | `lib/clusterbuster/driver/cleanup.py` |

#### Supplementary Review (Bugs 13–39)

| # | Ref | Difference | Resolution | Files Changed |
|---|-----|-----------|------------|---------------|
| 13 | H1 | **Liveness probe exec command and `initialDelaySeconds` mismatch**. Python used `exec: command: ["true"]` with `initialDelaySeconds: cfg.liveness_probe_sleep_time`; bash uses `exec: command: ["/bin/sleep", "$sleep_time"]` with `initialDelaySeconds: 10`. | Changed probe command to `["/bin/sleep", str(sleep_time)]` and hardcoded `initialDelaySeconds: 10`. | `manifests.py` |
| 14 | H2 | **Privileged security context missing `runAsUser: 0`**. Python's `security_context(privileged=True)` had `privileged: True` and `allowPrivilegeEscalation: True` but no `runAsUser: 0`. Bash includes `runAsUser: 0`. | Added `"runAsUser": 0` to the privileged branch. | `manifests.py` |
| 15 | H3 | **`--vm-run-as-container` not implemented in VM cloud-init**. Config field existed but `cloud_init_userdata()` never used it. | Implemented all four bash behaviors: skip workload packages, install podman, skip sysctl bootcmds, wrap command in `podman run --rm --privileged ...`. | `vm.py` |
| 16 | H4 | **`vm_block_multiqueue` missing `ioThreadsPolicy`, `ioThreads`, `blockMultiQueue`**. Python only set `dedicatedCpuPlacement`. | Added `ioThreadsPolicy: supplementalPool`, `ioThreads: {supplementalPoolThreadCount: N}` at domain level, `blockMultiQueue: true` at devices level. Removed stale `dedicatedCpuPlacement: true` (Round 2 F1). | `vm.py` |
| 17 | H5 | **No ServiceAccount or SCC binding for privileged namespaces**. Python used only PSA labels; bash also creates a SA and binds the privileged SCC. | Added SA creation, labeling, and `oc adm policy add-scc-to-user` in `_create_all_namespaces()`. | `orchestrator.py` |
| 18 | H6 | **Multus CNI annotation missing on non-VM pods**. Only injected on VM template metadata. | `annotations()` now injects `k8s.v1.cni.cncf.io/networks` for all pod types when `net_interfaces` is configured. | `manifests.py` |
| 19 | H7 | **Kata/virtiofsd annotation not injected**. `virtiofsd_args` was computed but never used. | `annotations()` now injects `io.katacontainers.config.hypervisor.virtio_fs_extra_args` when runtime class starts with `kata` (covers variants like `kata-qemu`). Uses compact JSON (no spaces after separators), matching bash's format (Round 2 C2/C3). | `manifests.py` |
| 20 | H8 | **Client-server affinity used wrong selector**. Python used `key={basename}-uuid, value=uuid` (pins all pods together); bash uses `key=instance, value=<server-name>` (pins client to its server). | Added `client_affinity_label` parameter through `_build_client_server_deployments` → `_build_single_role_deployment` → `pod()`/`deployment()`/`replicaset()`. | `manifest_plan.py`, `manifests.py` |
| 21 | H9 | **Sync pod affinity used wrong selector**. Python used `key={basename}-uuid, value=uuid`; bash uses `key={basename}-worker, value="true"`. | Sync pod affinity now uses `label_key=f"{basename}-worker"` and `label_value="true"`. | `manifests.py` |
| 22 | H10 | **Pin-node `nodeSelector` too strict**. Python set both worker label and hostname; bash sets only hostname. | All four `nodeSelector` sites now set only `kubernetes.io/hostname` when pinned. | `manifests.py`, `vm.py` |
| 23 | M1 | **Secrets: naming, data keys, volume wiring all wrong**. Names, keys, values, volumes, and mounts diverged from bash. | Secret names: `secret-{ns}-{dep}-{k}`. Data keys: `key1`/`key2`. Per-secret volumes and mounts at `/etc/{secret-name}`. | `manifest_plan.py`, `manifests.py` |
| 24 | M2 | **Sleep `runtime=0` became `10`**. `cfg.workload_run_time or 10` treats `0` as falsy. | Changed to `cfg.workload_run_time` (pass raw value). | `workloads/sleep.py` |
| 25 | M3 | **Server `--server-expected-clients` wrong with `containers_per_pod > 1`**. Python multiplied by `containers_per_pod`. | Changed to `ctx.replicas` only. | `workloads/server.py` |
| 26 | M4 | **Drop-cache pod `--cb-basetime` / `--cb-crtime` hardcoded to 0**. | `drop_cache_pod()` now accepts and passes through `basetime`; computes `crtime` from wall clock. | `manifests.py`, `manifest_plan.py` |
| 27 | M5 | **Sync/drop-cache `VERBOSE` hardcoded to `"0"`**. | Both pods now use `str(int(cfg.verbose))`. | `manifests.py` |
| 28 | M6 | **Sync/drop-cache pods had extra `workdir` emptyDir**. Bash uses `-V` to suppress it. | Removed workdir volume and mount from both pods. | `manifests.py` |
| 29 | M7 | **Volume name template expansion (`%N`, `%i`, `%r`) missing**. | Added `expand_volume_name()` and plumbed `namespace`/`instance`/`replica` through `volume_mounts()` and `volumes()`. | `manifests.py` |
| 30 | M8 | **VM cloud-init network data: wrong subnet, no DHCP, no NAD parsing**. | Changed `/24` → `/16`, added `dhcp4: false` / `dhcp6: false`, parse `nad:iface` format. | `vm.py` |
| 31 | M9 | **VM `chpasswd` conditional on `vm_user`**. Bash always emits `chpasswd`. | `chpasswd: {expire: False}` now unconditional; `user`/`username` conditional on `vm_user`; `password` conditional on `vm_password`. | `vm.py` |
| 32 | L1 | **Drop-cache naming `{name}-dc` vs `{name}-1-dc` when replicas=1**. | Always uses `{name}-{replica}-dc`. | `manifest_plan.py` |
| 33 | L2 | **Missing configmap file silently skipped**. | `user_configmap()` raises `FileNotFoundError` for user-supplied `--configmap` files, matching bash's `exit 1`. `system_configmap()` still debug-skips missing files — these are internal pod files from `_SHARED_POD_FILES` that should always exist in a proper installation; a missing one is a packaging issue, not a user error (Round 2 C1). | `manifests.py` |
| 34 | L3 | **Sysbench `--fileio-tests`/`--fileio-modes` always emitted**. | Only emitted when non-empty. | `workloads/sysbench.py` |
| 35 | L5 | **FIO `--fio-options` emitted as empty string**. | Only emitted when `_fio_options` is non-empty. | `workloads/fio.py` |
| 36 | L6 | **Sync container `containerPort` was 7778, bash uses 7777**. | Changed to `cfg.container_port` (7777). Previously listed as intentional difference F; reclassified as bug. | `manifests.py` |
| 37 | L7 | **Logger `--delay` formatted as `0.0` instead of `0`**. | Uses `:g` format specifier. | `workloads/logger.py` |
| 38 | O1 | **`generate_environment()` hook not wired**. | `build_manifest_plan()` now calls `wl.generate_environment()` and merges the result. | `manifest_plan.py` |
| 39 | O2 | **VM log collection: wrong method and wrong SCP user**. Python used `virsh dumpxml 1` / `VMLogs/` / `config.vm_user`; bash uses `cat` from virt-launcher / `VMXML/` / `root`. | XML retrieval: find pod via `vm.kubevirt.io/name` label, `cat` the QEMU XML, write to `VMXML/`. SCP: use `root@`, require `vm_ssh_keyfile`, match SSH options. | `artifacts.py` |
| 40 | O3 | **Cleanup deletes pre-existing namespaces**. Bash sets `remove_namespaces=0` when namespaces already exist. | Added pre-existence check; `effective_remove_namespaces` tracked on `RunContext` and passed to cleanup. | `orchestrator.py`, `cleanup.py` |
| 41 | O4 | **`_domain_spec` uses only `default` net interface**. Ignores `class_name`, causing inconsistent VM spec when client/server have different NADs. | `_domain_spec()` now accepts `class_name` and uses class-specific interface lookup. | `vm.py` |

#### Self-Audit (Bugs 42–43)

| # | Ref | Difference | Resolution | Files Changed |
|---|-----|-----------|------------|---------------|
| 42 | R3 | **`_is_sync()` misidentifies `synctest` workload as sync infrastructure**. The helper function used `"sync" in name` substring matching, which incorrectly classified all `synctest` worker objects (whose names contain "synctest", e.g. `clusterbuster-0-synctest-0`) as sync infrastructure. This caused `_worker_objects()` to filter them out, silently skipping 2 golden-parity tests (`test_template_labels_match_pod_labels` for `synctest_deployment` and `synctest_replicaset`). | Changed `_is_sync()` to use `\bsync\b` word-boundary regex. "sync" as a standalone word (e.g. `clusterbuster-sync`) matches; "sync" as part of "synctest" does not. | `test_golden_parity.py` |
| 43 | R3-O2 | **`%r` replica expansion not plumbed for bare-pod multi-replica**. `_build_single_role_deployment()` computed `volumes()` once (without `replica`) and shared pre-built containers (whose `volume_mounts()` also lacked `replica`) across all replicas. Volume names and PVC `claimName` values containing `%r` were never expanded. In bash, `volumes_yaml` and `volume_mounts_yaml` are called inside the per-replica loop where `$replica` is in scope. | For the `pod` deployment type, containers and volumes are now built per-replica with the replica value passed to `volume_mounts()` and `volumes()`. Deployment/replicaset types share a single template (no per-replica expansion possible — same limitation as bash). | `manifest_plan.py` |

#### E2E / CI Parity (Bugs 44–65)

These were found through `func_ci` runs on `scale`, comparing artifacts and behavior between the bash and Python versions.

| # | Ref | Difference | Resolution | Files Changed |
|---|-----|-----------|------------|---------------|
| 44 | E1 | **`oc apply` used bare `create` instead of `apply`**. Object creation used `oc create`, which fails with `AlreadyExists` for pre-existing resources. Bash uses `oc apply` for idempotent creation. | `ObjectBatcher` now uses `cluster.apply()` instead of `cluster.create()`. Added `apply()` method to `ClusterInterface`. | `manifests.py`, `cluster.py` |
| 45 | E2 | **Pre-existing namespaces caused `AlreadyExists` failures**. Python did not check whether namespaces already existed before creating them; bash reuses existing namespaces and sets `remove_namespaces=0`. | Added `_namespace_exists()` check; existing namespaces are skipped during creation. `effective_remove_namespaces` tracked on `RunContext` and set to `0` when pre-existing namespaces are found with `remove_namespaces=-1`. | `orchestrator.py` |
| 46 | E3 | **Cleanup resource types incomplete**. Python's cleanup used a limited set of resource types for `oc delete all`. Bash's `oc delete all` includes all API-server-defined "all" resources. | Expanded cleanup to use `oc delete all` directly, matching bash semantics. | `cleanup.py` |
| 47 | E4 | **Force cleanup missing VM-specific escalation**. Bash force-cleanup has a multi-step escalation path including a separate `oc delete all` for VMs. Python lacked this. | Extracted `_force_cleanup()` with VM-specific `delete all` step. Added `test_force_cleanup_vm`. | `cleanup.py`, `test_driver_orchestrator.py` |
| 48 | E5 | **Manifest YAML artifacts not saved**. Bash saves each applied manifest to `<artifactdir>/<Kind>/<ns>:<name>`. Python did not. | Added `_save_manifest_artifact()` in `ObjectBatcher.add()`, saving each manifest as YAML. | `manifests.py` |
| 49 | E6 | **`SYSFILES/` directory not populated**. Bash copies shared pod files to `<artifactdir>/SYSFILES/`. Python did not. | Added `_save_sysfiles()` to `_create_all_configmaps()`. | `orchestrator.py` |
| 50 | E7 | **Sync namespace pod artifacts skipped**. A filter in `_collect_pod_artifacts` skipped pods in the sync namespace. Bash collects them. | Removed the `if ns == sync_namespace: continue` filter. | `artifacts.py` |
| 51 | E8 | **`VMLogs/` and `VMXML/` directories created unconditionally**. Bash only creates these for VM deployment types. | `_ensure_artifact_dirs()` now checks `config.deployment_type == "vm"` before creating VM artifact directories. | `artifacts.py` |
| 52 | E9 | **`report.none` file created when report format is `"none"`**. Bash does not write a report file in this case. | Added `"none"` to the skip condition in `reporting.py`. | `reporting.py` |
| 53 | E10 | **`stderr.log` empty for successful runs**. Bash captures `oc apply`/`create`/`delete` output to stderr.log. Python's logging did not route `oc` command output to file handlers. | Added `log_output` parameter to `ClusterInterface.run()`. Mutating operations (`create`, `apply`, `delete`, `label`, `adm`) now log stdout to `clusterbuster.driver.cluster` logger. Added `_StderrLogFilter` and `_MonitorLogFilter` to route messages to the correct files. | `cluster.py`, `orchestrator.py` |
| 54 | E11 | **`monitor.log` empty**. Pod state transitions were logged at `DEBUG` level, below the file handler threshold. | Changed pod state transition logging in `monitoring.py` from `_LOG.debug` to `_LOG.info`. | `monitoring.py` |
| 55 | E12 | **Log timestamps not in ISO format**. Bash uses `%Y-%m-%dT%H:%M:%S.%f` (ISO with microseconds). Python used the default logging format. | Added `_IsoTimestampFormatter` class producing ISO timestamps with microsecond precision. Applied to both `stderr_handler` and `monitor_handler`. | `orchestrator.py` |
| ~~56~~ | ~~E13~~ | ~~Reclassified as intentional difference Q.~~ | | |
| 57 | E14 | **Kata `default_memory` annotation had literal quotes**. FIO CI workload generated `"4096"` (with embedded quotes) instead of `4096`, causing Kata to reject the pod with `Error parsing annotation`. | Removed literal quotes from f-string in `fio.py`. | `ci/workloads/fio.py` |
| 58 | E15 | **Kata `default_vcpus` annotation had literal quotes**. Uperf CI workload generated `"1"` instead of `1`, same Kata annotation parsing failure. All four uperf-kata variants failed with `FailedCreatePodSandBox`. | Removed literal quotes from format string in `uperf.py`. | `ci/workloads/uperf.py` |
| 59 | E16 | **CI suite job serial numbers reset per workload**. `_global_job_counter` was reset to `0` at the start of each workload loop iteration, causing artifact directories to collide and overwrite each other. | Removed the counter reset from the workload loop in `suite.py`. Counter now increments monotonically across the entire suite run. | `ci/suite.py` |
| 60 | E17 | **Failed jobs reported as "done"**. Job completion logging always said "done" regardless of exit code. | Modified `execution.py` to print "Job FAILED (rc=N) after X at Y" for non-zero return codes. | `ci/execution.py` |
| 61 | E18 | **Metrics not collected by default**. Metrics extraction was gated behind `config.take_prometheus_snapshot` (requires `--prometheus-snapshot`). Bash runs `prom-extract` whenever `metrics_file` is set (via `--metrics`). Additionally, `prom-extract` was looked up at the repo root instead of `lib/prom-extract`, and the default `metrics_file` value `"default"` was never resolved to the actual file path. | Decoupled extraction from snapshot in `orchestrator.py`. Fixed `prom-extract` path in `metrics.py`. Resolved `"default"` → actual path during config build in `config.py`. | `orchestrator.py`, `metrics.py`, `config.py` |
| 62 | E19 | **`oc` read commands flooding `stderr.log`**. All `oc` commands logged their stdout, including verbose JSON from `oc get -ojson` and `oc version -ojson`. Bash only captures output from mutating commands. | Added `log_output` parameter defaulting to `False`. Set to `True` only for `create()`, `apply()`, `delete()`, and explicit mutating calls (`label`, `adm policy`). | `cluster.py`, `orchestrator.py` |
| 63 | E20 | **`commandline` artifact file not shell-quoted**. Python used `" ".join(config.command_line)`, producing unquoted output. Arguments containing spaces (e.g., `--pod-annotation=...default_memory: 4096`) are ambiguous without quoting. Bash naturally produces shell-quoted output since it echoes `"$@"`. | Used `shlex.join(config.command_line)` to produce properly shell-quoted output. | `orchestrator.py` |
| 64 | E21 | **FIO Kata pods OOM-killed by virtiofsd page cache**. Kata imposes a cgroup memory limit based on `default_memory` alone. With large FIO files, virtiofsd page cache grows beyond the limit, causing `qemu-kvm` to be OOM-killed (`SandboxChanged`). Bash CI did not set a memory limit, but the Kata runtime's default limit was sufficient for the file sizes used; with the Python CI's explicit `default_memory` annotation, the runtime-imposed limit was too tight. | CI FIO workload now computes `--limit=memory=<filesize + VM_size + 1 GiB>Mi` for Kata pods. With func_ci defaults (32 GiB file, 4 GiB VM): 37888 MiB. | `ci/workloads/fio.py` |
| 65 | E22 | **FIO Kata e2e test used wrong volume mount**. Test mounted an emptydir at `/scratch` but FIO defaults to `/var/opt/clusterbuster`. Under Kata's `restricted-v2` SCC, the default workdir is not writable without an explicit volume. | Changed e2e test volume to `:emptydir:/var/opt/clusterbuster`, matching the func_ci profile. | `tests/test_e2e.py` |

#### Runtime and CLI Parity (Bugs 66–73)

These were found through live cluster runs on `scale` comparing
Python and bash terminal output and performance characteristics,
plus user-reported CLI issues.

| # | Ref | Difference | Resolution | Files Changed |
|---|-----|-----------|------------|---------------|
| 66 | R1 | **`crtime` stamped at plan-build time, not submission time**. Manifests were built in a tight planning loop, so `--cb-crtime=` reflected the plan-build timestamp rather than the `oc create`/`apply` submission timestamp. This caused the reported `Pod creation interval` to be near-zero (all pods had nearly identical `crtime` values), whereas bash stamps the time at the point each manifest is submitted to the API server. | Added `ObjectBatcher._stamp_crtime()` static method that patches `--cb-crtime=` with `time.time()` just before each manifest is buffered. Handles container `command` args for Pods/Deployments/ReplicaSets and regex substitution in VM cloud-init `userData`. Called from `ObjectBatcher.add()`. | `manifests.py` |
| 67 | R2 | **Worker pods created one-by-one, bypassing `--parallel` and `--objects-per-call`**. `_create_sync_services()` iterated over `build_manifest_plan()` output and applied every manifest (including all worker Pods, Deployments, and VMs) one-by-one with `force_flush=True`. `_create_all_deployments()` then had no worker manifests left to process. As a result, `--parallel` and `--objects-per-call` had no effect on object creation speed. | `_create_sync_services()` now filters manifests to only sync infrastructure: ConfigMaps for the sync namespace, Services, and the sync Pod (identified by name `{basename}-sync`). `_create_all_deployments()` filters out infrastructure kinds (`Namespace`, `ConfigMap`, `Secret`, `Service`) and the sync pod by name, then passes remaining worker manifests through `_create_all_parallel()` with proper batching and parallelism. | `orchestrator.py` |
| 68 | R3 | **Metrics extraction gated on `artifactdir`**. The `extract_metrics()` call in `orchestrator.run()` was guarded by `if config.metrics_file and config.artifactdir:`, preventing metrics collection when no artifact directory was specified. Bash runs `prom-extract` whenever `metrics_file` is set, regardless of artifact directory. | Changed the guard to `if config.metrics_file:`, removing the `artifactdir` requirement. | `orchestrator.py` |
| 69 | R4 | **Console logging not active during precleanup**. The `StreamHandler(sys.stderr)` for console output was configured *after* the precleanup phase in `orchestrator.run()`. Messages from `oc delete` and `oc label` during precleanup were not visible on the terminal. | Moved console handler setup to *before* the precleanup block, with a comment noting the ordering requirement. | `orchestrator.py` |
| 70 | R5 | **Monitoring messages printed to console/stderr**. The `_StderrLogFilter` accepted all INFO+ records from `clusterbuster.driver.*` loggers, including `clusterbuster.driver.monitoring`. Pod state transition messages (e.g., "Pod namespace/name: Pending → Running") appeared on the terminal instead of only in `monitor.log`. Bash's monitoring output goes only to the monitor log file. | Added `_MONITOR_PREFIX = "clusterbuster.driver.monitoring"` to `_StderrLogFilter` and an early-return `False` for records from that prefix. Monitoring messages now go only to `monitor.log` via `_MonitorLogFilter`. | `orchestrator.py` |
| 71 | R6 | **Help text missing blank line between workload option groups**. Bash's help output inserts a blank line between each workload's option block for readability. Python concatenated all workload option groups without separators. | Added `parts.append("")` after each workload's options in `_build_help_text()`. | `cli.py` |
| 72 | R7 | **`--artifactdir` format codes displayed as `%%` instead of `%`**. The `_EXTENDED_HELP_TEXT` string literal used `%%n`, `%%s`, `%%w`, etc. for format code documentation, but these are not Python format strings — they are printed verbatim. The doubled `%` characters appeared in the output. | Replaced all `%%` with `%` in the `_EXTENDED_HELP_TEXT` string. | `cli.py` |
| 73 | R8 | **`-h` and `-H` displayed identical output**. Both short help (`-h`) and extended help (`-H`) called `_build_help_text()` without differentiation, so `-h` displayed workload-specific options that should only appear with `-H`. | Added `include_workloads: bool` parameter to `_build_help_text()`. `-h` calls `_build_help_text(include_workloads=False)` (core options only); `-H` calls `_build_help_text(include_workloads=True)` (full help with workload options and descriptions). | `cli.py` |

### A.2 — Intentional Differences (Not Changed)

| # | Difference | Analysis | Justification |
|---|-----------|----------|---------------|
| A | **Pod Security Admission (PSA) labels on namespaces**. Python adds `pod-security.kubernetes.io/enforce`, `/audit`, and `/warn` labels to workload namespaces. Bash does not. | `deep_compare.py`: `.metadata.labels.pod-security.kubernetes.io/*: MISSING in bash`. | Python improvement. PSA labels are required on modern Kubernetes (1.25+) to declare the security policy a namespace operates under. Bash bypasses this with `--validate=false`. Including these labels is correct Kubernetes practice and prevents admission rejections. |
| B | **Volume and ConfigMap naming**. Python uses generic names like `user-configmap`, `system-configmap`; bash uses namespace-scoped names like `userconfigmap-clusterbuster-0`, `systemconfigmap-clusterbuster-0`. | `deep_compare.py`: volume names and `configMap.name` differ on every worker pod/deployment. | Intentional simplification. The volume names only need to be unique within a pod spec, not globally. The generic names are clearer and the ConfigMap objects they reference are correctly namespaced. The bash naming convention encodes the namespace into the name, which is redundant since ConfigMaps are namespace-scoped. |
| C | **`workdir` emptyDir volume: unconditional vs conditional**. Python mounts an `emptyDir` at `/var/tmp/clusterbuster` on all worker pods unconditionally. Bash adds the same volume only when `requires_writable_workdir()` returns true (requires: no `runtime_class`, `common_workdir` at default, pods are privileged, workload implements the API, and no existing writable emptydir at that path). | `deep_compare.py`: extra volume and volumeMount present in Python for workloads that do not satisfy bash's conditions. | Intentional simplification. Providing the volume unconditionally is harmless (an empty `emptyDir` costs nothing) and avoids the fragile multi-condition gate in bash. Infrastructure pods (sync, drop-cache) do not include this volume (see bug 28), matching bash's `-V` behavior. |
| D | **Sync service name**: bash uses `svc-clusterbuster-sync-sync`, Python uses `clusterbuster-sync-0`. Port names and ExternalName FQDN differ accordingly. | `deep_compare.py`: `metadata.name` and all derived port name / externalName fields differ. | Intentional redesign. The Python naming convention (`{basename}-sync-{index}`) is simpler and consistent with the general naming scheme. The names are internally consistent — every reference to the sync service uses the same name. No functional difference, as the service is only referenced by its own components. |
| E | **Sync pod internal file paths**. Bash: `/tmp/syncfile`, `/tmp/syncerror`, `/tmp/timing.json`. Python: `/tmp/clusterbuster_sync_flag`, `/tmp/clusterbuster_sync_error`, `/tmp/clusterbuster_controller_ts`. | `deep_compare.py`: command arguments `--cb-sync-file`, `--cb-error-file`, `--cb-controller-timestamp-file` differ. | Intentional improvement. The Python names are more descriptive and namespaced to avoid collision with other processes in the container. The old names like `syncfile` are ambiguous. These are internal implementation details consumed only by the sync pod's own code. |
| F | ~~Sync pod `containerPort`: bash 7777, Python 7778.~~ | **Reclassified as bug 36.** Python now uses `cfg.container_port` (7777), matching bash. | *(No longer an intentional difference.)* |
| G | **`nodeSelector` on sync pod when not pinned**. Python adds `node-role.kubernetes.io/worker: ""` to the sync pod. Bash does not. | `deep_compare.py`: `.spec.nodeSelector: MISSING in bash`. | Python improvement. Ensures the sync pod runs on a worker node rather than potentially landing on an infra or control-plane node. Bash relies on chance or cluster-level defaults. |
| H | **`.metadata.selector` on pods**. Bash adds a `selector` field with `matchLabels` on raw `Pod` objects. Python does not. | `deep_compare.py`: `.metadata.selector: MISSING in python`. | Bash bug. The `selector` field is not part of the `Pod` spec — it belongs on `Deployment`, `ReplicaSet`, `StatefulSet`, etc. Kubernetes accepts it silently (or via `--validate=false`) but ignores it. Omitting it is correct. |
| I | **Deployment `.spec.strategy`**. Bash includes `strategy: {type: RollingUpdate}`. Python omits it. | `deep_compare.py`: `.spec.strategy: MISSING in python`. | `RollingUpdate` is the Kubernetes default strategy for Deployments. Omitting it produces identical behavior because the API server applies the default. Including it is redundant. |
| J | **`--cb-drop-cache-port` argument: unconditional vs conditional**. Python includes `--cb-drop-cache-port=7779` in all worker container commands. Bash includes it only when `get_drop_cache` returns a non-empty port (i.e., for workloads that require drop-cache). | `deep_compare.py`: worker command length differs by 1 for non-drop-cache workloads (e.g. cpusoaker, sleep). | Intentional simplification. Including the flag unconditionally is harmless — workers that do not use drop-cache ignore it. Avoiding the conditional keeps `pod_flags()` uniform. |
| K | **`clusterbuster-workload` label on sync namespace**. Python adds `clusterbuster-workload: "true"` to the sync namespace. Bash does not. | `deep_compare.py`: `.metadata.labels.clusterbuster-workload: MISSING in bash`. | Python improvement. Labels the sync namespace for consistent cleanup. Ensures `--cleanup` can find and delete all clusterbuster-related namespaces using a single label selector. |
| L | **`basetime` / `crtime` values in dry-run**. Bash uses actual timestamps; Python dry-run uses `0`. | `deep_compare.py`: `--cb-basetime=TIMESTAMP` vs `--cb-basetime=0`. | Intentional. In dry-run mode (`-n`) there is no actual cluster, so timestamps are meaningless. Using `0` makes the output deterministic and diff-stable. During real runs, both versions use actual timestamps. |
| M | ~~**Sync service selector format**. Previously claimed: bash uses `{basename}-sync: {uuid}`, Python uses `{basename}-sync-sync: "true"`.~~ | **Retracted.** Self-audit (Round 3) verified that bash also uses `{basename}-sync-sync: "true"` — the service is created with `create_service -k "${basename}-sync-sync" -v "true"` and the sync pod carries the matching label. The original `deep_compare.py` observation was based on an older version of `clusterbuster.sh`; the current bash code matches Python. | *(No longer a difference.)* |
| N | **Toleration operator for empty segment**. Bash emits `operator: ""` for empty middle field in `key::effect`. Python emits `operator: "Exists"`. | Supplementary review M10. | Python improvement. `"Exists"` is the correct Kubernetes toleration operator for "match any value." An empty string is not a valid operator and would be server-defaulted to `"Equal"`, which requires a `value` field — not the intended semantics. |
| O | **ExternalName service port naming**. Python uses `svc-{sync_svc_name}-{port}` (no protocol suffix); bash includes per-protocol port name entries. | Supplementary review O5. | Informational metadata only. Kubernetes does not use port names for ExternalName service routing. The simpler naming is clearer. |
| P | **HammerDB/Sysbench `--workdir` fallback path**. Bash falls back through `emptydirs[0]` before `/tmp`; Python uses `common_workdir` or `/tmp`. | Supplementary review L4. | The bash fallback is an artifact of bash's volume management model where `emptydirs` is an indexed array of mount points. Python's `common_workdir` provides the same function. All current workloads produce identical behavior. |
| Q | **Per-replica Deployment/ReplicaSet model**. Bash creates one Deployment/ReplicaSet with `replicas: N` and a single template label (no per-replica suffix). Python creates N objects, each with `replicas: 1` and a unique per-replica `replica` label. *(Reclassified from bug 56.)* | Supplementary review Round 7 I1. Bash's `create_replication_deployment` calls `standard_labels_yaml` without a replica argument, producing `replica: clusterbuster-0-foo-0` for all pods. Drop-cache pods target `replica: clusterbuster-0-foo-0-1` (with per-replica suffix), so affinity never matches under the bash model. | Python improvement. The per-replica model fixes a pre-existing bash bug where drop-cache pod affinity for Deployment/ReplicaSet types could never match individual worker replicas. Each Python-generated object gets a unique `replica` label that the corresponding drop-cache pod can target. |

### A.3 — Methodology

#### Tools Used

1. **`tests/deep_compare.py`** — Purpose-built comparison script that
   runs both `clusterbuster.sh` and `clusterbuster` (Python) in
   dry-run mode for every workload x deployment-type combination,
   parses the YAML output, normalizes dynamic values (UUIDs,
   timestamps, SSH keys), matches objects by kind/name, and produces a
   field-by-field diff.  Takes ~5 minutes for a full run across all 56
   combinations.

2. **`tests/generate_golden.py`** — Generates JSON golden files from
   bash output, capturing structural fields (labels, ports, selectors,
   VM domain structure, security context fields, restart policy,
   affinity).  Golden files are stored in `tests/golden/`.

3. **`tests/test_golden_parity.py`** — Pytest suite that compares
   Python dry-run output against golden files.  Test classes cover
   object counts, worker labels, namespace labels, VM labels,
   service ports, deployment/replicaset structure, VM domain
   structure, restart policy, runAsNonRoot, sysctls, and drop-cache
   affinity.  The full test suite (all test files) currently runs
   1557 tests.

4. **E2E cluster runs** — `func_ci` profile on `scale`, which
   exercises all workloads including VMs on a real OpenShift cluster.
   Runtime failures (e.g., "no jobfiles provided", "no space left on
   device") revealed issues that dry-run comparison alone could not
   catch.  The latest `func_ci` run (72 jobs: memory, fio, uperf,
   files, cpusoaker, hammerdb across runc/kata/vm) passes **72/72**.

5. **Supplementary review** — Per-workload comparison of arglist
   functions, per-subsystem tracing of affinity, tolerations,
   annotations, volumes, security context, and cloud-init code paths.
   Identified 32 additional discrepancies not caught by the original
   audit tools.

6. **CI suite parity** — Comparison of `run-perf-ci-suite` Python
   output (artifact structure, log files, job numbering, metrics
   collection) against the bash `run-perf-ci-suite` reference.
   Identified 22 additional issues (E1–E22) in the driver, CI suite,
   and workload configuration layers.

#### Discovery Pattern

The bugs in Section A.1 were found through one of five paths:

- **E2E failure → log analysis → bash source comparison** (bugs 9–12):
  These caused hard runtime failures visible in cluster logs.  The fix
  required understanding what bash generates and why.

- **Systematic dry-run diff → bash source confirmation** (bugs 1–8):
  These were structurally visible in the dry-run YAML but did not
  necessarily cause immediate runtime failures.  Some (like missing
  `restartPolicy`) would only manifest under specific failure
  scenarios; others (like missing sysctls) would cause subtle
  performance issues.

- **Manual code review → bash source cross-reference** (bugs 13–41):
  Found by the supplementary review through systematic per-function
  comparison.  These included complete feature gaps (H3, H5), wrong
  selector values (H8, H9), and broken volume wiring (M1).

- **CI suite comparison → artifact/log diff → bash source tracing**
  (bugs 44–65): Found by running the full `func_ci` profile and
  comparing the Python output (artifact directories, log files, job
  numbering, metrics) against the bash reference run.  These spanned
  the driver layer (missing artifacts, log routing, deployment
  model), the CI suite layer (counter reset, failure reporting,
  metrics collection), and the workload layer (Kata annotation
  quoting).

- **Live cluster output comparison → bash terminal diff** (bugs
  66–73): Found by comparing terminal output and performance
  characteristics between bash and Python during real cluster runs.
  These included runtime behavioral issues (object creation
  timing, parallelism bypass, metrics extraction gating, console
  logging ordering, monitoring log routing) and CLI help text
  parity (`-h` vs `-H` differentiation, format code escaping,
  workload option group spacing).

#### Architectural Fix: Unified Manifest Builder

After discovering that bugs 5, 6, and 7 all existed only in the
dry-run code path (because `cli.py` and `orchestrator.py` each had
their own ~300-line copy of the manifest-building logic), the two
paths were unified.  A new module `manifest_plan.py` contains a
single `build_manifest_plan()` function that:

- Takes `ClusterbusterConfig` + namespace list + optional parameters
  (`basetime`, `first_deployment`).
- Returns the complete ordered list of manifest dicts.
- Is called by **both** the dry-run printer (which just prints the
  YAML) and the live orchestrator (which applies via `ObjectBatcher`).

This eliminates the class of bugs where a fix is applied to one path
but forgotten in the other.  The `_build_containers`,
`_build_single_role_deployment`, and `_build_client_server_deployments`
helper functions were moved from `orchestrator.py` to
`manifest_plan.py`; the orchestrator's versions were deleted.

#### Lessons for Future Conversions

1. **Build the deep-comparison tool early.** A field-by-field manifest
   diff tool would have caught bugs 1–8 before any E2E run.

2. **Dry-run parity is necessary but not sufficient.** Bugs 9–11 were
   in the VM builder's internal logic (disk serials, filesystem
   formatting) that only manifests at runtime inside a guest VM.

3. **Never duplicate code paths.** The dry-run path (`cli.py`) and the
   real run path (`orchestrator.py`) originally built manifests
   independently with ~300 lines of duplicated logic.  Bugs 5, 6, and
   7 existed only in the dry-run path because the orchestrator was
   already correct.  This was resolved by extracting a shared
   `manifest_plan.py` module.

4. **Partial plumbing is worse than no plumbing.** Bug 4 (sysctls) had
   the parameter threaded halfway through the call chain, giving the
   illusion that it worked.  The dangling `sysctls=sysctls` argument
   to `pod()` would have raised `TypeError` if it were ever non-None —
   but since the callers never populated it, the error was hidden.

5. **Test extraction functions must match generation functions.** The
   golden file generator and the parity test both had their own copy
   of `_extract_object_info`.  A bug in the generator (wrong `yaml`
   import scope) was silently caught by a broad `except Exception`
   block, causing golden files to lack VM boot command data.  Keep
   extraction logic in a single shared module.

6. **Manual code review catches what automated tools miss.** The
   supplementary review found 32 additional issues — including
   complete feature gaps, wrong affinity selectors, and broken
   secrets — that neither the deep-compare tool nor the E2E tests
   caught.  Per-function manual comparison is essential for a
   high-fidelity conversion.

7. **CI suite artifact comparison is a distinct verification layer.**
   Bugs 44–65 were invisible to manifest-level comparison and most
   E2E workload tests.  They only appeared when comparing the full
   CI suite output (directory layout, log files, job numbering,
   metrics collection) side-by-side with the bash reference.
   Artifact parity testing should be a standard step.

8. **Kubernetes deployment model details matter for affinity.**
   The per-replica deployment model change (intentional difference
   Q, originally bug 56) showed that structurally valid YAML can
   still produce wrong scheduling behavior.  Bash's single
   ReplicaSet with N replicas could never match drop-cache pod
   affinity — a pre-existing bug that the Python model fixes.

9. **Annotation values are parsed by the runtime, not just stored.**
   Bugs 57–58 (Kata annotation quoting) showed that pod annotations
   are not merely metadata — Kata parses them as typed values.
   Literal quotes around numeric annotation values are silently
   accepted by the Kubernetes API but rejected by Kata at sandbox
   creation time.

10. **Object creation routing determines whether tuning knobs work.**
    Bug 67 showed that `--parallel` and `--objects-per-call` had no
    effect because worker pods were routed through the sync
    infrastructure creation path (one-by-one with `force_flush`)
    instead of the parallel batching path.  Correctness of routing
    logic must be verified under real load, not just by checking
    that manifests are structurally correct.

11. **Terminal output comparison catches behavioral drift.**  Bugs
    66–73 were invisible to manifest-level diff tools and most unit
    tests.  They only appeared when a user compared the Python
    terminal output side-by-side with the bash output during a real
    cluster run.  Automated terminal output comparison (or at least
    a checklist of expected output patterns) should be part of the
    parity verification process.

---

## Appendix B: Supplementary Parity Review

This appendix contains the per-round supplementary review of the
bash-to-Python conversion.  Each round documents findings from
systematic code review, the developer's response, and the
reviewer's verification.  Rounds 1–3 cover manifest-level parity
(audit bugs 13–43).  Rounds 4–7 cover CI suite parity, deployment
model, artifacts, and cleanup lifecycle (audit bugs 44–65).
Round 8 covers the design specification review.

### Round 1 — Initial Findings (32 issues identified)

The initial supplementary review identified 32 discrepancies between
the bash and Python implementations, organized as 10 HIGH, 10 MEDIUM,
7 LOW, and 5 OBSERVATIONS.  All findings were incorporated into the
parity audit (Appendix A) as bugs 13–41
and intentional differences N–P.

The analysis was performed by:

- Reading the unified `manifest_plan.py` module end-to-end and
  comparing its output structure with bash's `create_spec`,
  `create_standard_containers`, `create_vm_deployment`,
  `create_drop_cache_deployment`, etc.
- Per-workload comparison of every `*_arglist` function (bash) vs
  `arglist()` / `server_arglist()` / `client_arglist()` (Python)
  across all 14 workloads.
- Comparing `pod_flags` / `cb_pod_client_flags_array`, environment
  variables (`standard_environment`), and infrastructure pods
  (sync, drop-cache).
- Tracing affinity, toleration, label, annotation, security context,
  volume mount, and VM cloud-init code paths in both implementations.

---

### Round 2 — Re-Review After Developer Fixes

#### Scope

This re-review examines the developer's response to the 32 issues
from round 1.  The developer has incorporated all items into the
parity audit as bugs 13–41 (29 fixed bugs) plus intentional
differences N, O, P (3 reclassified items).  All code changes were
verified against the actual source and all 1536 tests pass (3
skipped).

#### Overall Assessment

The developer has done excellent and thorough work.  All 29 bug fixes
are correctly implemented.  The parity audit is now a comprehensive,
high-quality document covering 41 bugs fixed and 16 intentional
differences (with F reclassified as bug 36).  The code changes are
well-structured and follow the established patterns in the codebase.

The following items need attention, ranging from a likely bug to
minor observations.

---

#### Items Needing Fix

**F1. `dedicatedCpuPlacement: true` set by Python but not by bash
when `vm_block_multiqueue` is enabled**

In `lib/clusterbuster/driver/vm.py` line 229:

    if cfg.vm_block_multiqueue:
        cpu["dedicatedCpuPlacement"] = True

Bash's `_vm_block_multiqueue_cpu()` (`clusterbuster.sh` line 4026)
does NOT set `dedicatedCpuPlacement`.  It only sets
`ioThreadsPolicy` and `ioThreads`.  The `dedicatedCpuPlacement`
field was part of the pre-fix code that predated the supplementary
review; when the developer added the correct
`ioThreadsPolicy`/`ioThreads`/`blockMultiQueue` fields (bug 16),
the stale `dedicatedCpuPlacement` was not removed.

This is functionally significant: `dedicatedCpuPlacement: true`
requires KubeVirt to allocate dedicated CPU cores to the VM, which
changes scheduling behavior and may prevent VMs from being placed on
nodes without available dedicated cores.  Bash does not impose this
constraint.

**Recommendation**: Remove `cpu["dedicatedCpuPlacement"] = True`
from the `vm_block_multiqueue` block, OR reclassify it as an
intentional improvement in the audit document with justification
(e.g., "block multiqueue benefits from dedicated CPUs for consistent
I/O performance").

---

#### Items Needing Clarification in the Audit Document

**C1. Bug 33 (L2): Audit says "Raises FileNotFoundError" but
`system_configmap` still does debug-skip**

The audit says bug 33's resolution is "Raises `FileNotFoundError`,
matching bash's `exit 1`."  However, `system_configmap()`
(`manifests.py` line 706) still does
`_LOG.debug("Configmap file not found: %s", path)` and skips.

Only `user_configmap()` raises `FileNotFoundError` for user-provided
`cfg.configmap_files` paths.

This might be intentional: system pod files from `_SHARED_POD_FILES`
should always exist in a proper installation, so a debug-skip is a
packaging issue rather than a user error.  But the audit document's
description does not match the code.  A one-sentence clarification
would resolve this.

**C2. Bug 19 (H7): Kata annotation `runtime_class == "kata"` vs
non-exact match**

Bash injects the
`io.katacontainers.config.hypervisor.virtio_fs_extra_args` annotation
whenever `virtiofsd_args` is non-empty, without checking the runtime
class.  Python (`manifests.py` line 175) requires
`self._cfg.runtime_class == "kata"` (exact string match).

If a user configures a Kata variant like `kata-qemu` or `kata-fc`,
the annotation would not be injected in Python.  This is probably
fine in practice (the `--kata` CLI option maps to
`runtime_class = "kata"`), but the exact-match check is stricter
than bash.  Worth a brief note in the audit document or a
`startswith("kata")` check if Kata variants are a real use case.

**C3. Bug 19 (H7): Kata annotation format differs from bash**

Bash wraps the virtiofsd args in single-quoted JSON:
`'["-o","allow_direct_io"]'`.  Python uses `json.dumps(...)` which
produces unquoted JSON with spaces: `["-o", "allow_direct_io"]`.
These produce different annotation string values.  Both should be
valid for the Kata runtime, but this is a minor format difference
that could be noted as intentional if verified to work.

---

#### Minor Observations (no action needed unless desired)

**O1. `vm_run_as_container` podman command: missing `__CB_HOSTNAME`
and per-port `-p` flags**

Bash's `_run_as_container` includes:

- `standard_environment -C` which adds
  `-e __CB_HOSTNAME=$(hostname -s)` as a podman env flag
- `${nports[*]}` which are explicit per-port `-p port:port` mappings
  from `workload_service_ports`

Python's implementation (`vm.py` lines 397–413) omits both.  The
`-P` flag ("publish all exposed ports") is present in both, which
provides equivalent but not identical port mapping behavior.  The
missing `__CB_HOSTNAME` falls back to `socket.gethostname()` in the
pod client, which may return a container ID instead of the VM
hostname — cosmetic only for log identification.

**O2. Volume name `%r` (replica) expansion is defined but
unreachable for bare pods**

`expand_volume_name()` supports `%r` replacement, but
`_build_single_role_deployment` never passes `replica` to
`volumes()` or `volume_mounts()`.  The volumes are built once
(`manifest_plan.py` line 435) and shared across all replicas in the
pod loop (lines 457–466).  For Deployment/ReplicaSet types this is
inherent (only one pod template), but for bare pods with multiple
replicas, per-replica PVC names via `%r` would silently not expand.
The same limitation may exist in bash for some code paths, so this
may be a non-issue in practice.

**O3. `cloud_init_networkdata` still uses `default` interface only**

Bug 41 correctly fixed `_domain_spec` to use `class_name` for
interface lookup, but `cloud_init_networkdata()` (`vm.py` line 425)
still uses `cfg.net_interfaces.get("default", "eth1")`.  For
client/server workloads with different NADs per role, the cloud-init
network configuration inside the guest would use the default
interface rather than the role-specific one.  This matches bash
behavior (bash also uses a single call site for network data), so it
is parity-correct.

---

#### Per-Bug Verification Summary

| Bug | Ref | Verified | Notes |
|-----|-----|----------|-------|
| 13 | H1 | OK | Liveness probe: `/bin/sleep` + `initialDelaySeconds: 10` |
| 14 | H2 | OK | `runAsUser: 0` in privileged `security_context()` |
| 15 | H3 | OK | All four `vm_run_as_container` behaviors implemented |
| 16 | H4 | See F1 | `ioThreads`/`blockMultiQueue` correct; stale `dedicatedCpuPlacement` |
| 17 | H5 | OK | SA creation + `oc adm policy add-scc-to-user` in `_create_all_namespaces` |
| 18 | H6 | OK | `annotations()` injects `k8s.v1.cni.cncf.io/networks` for all pod types |
| 19 | H7 | See C2/C3 | Annotation injected; exact-match and format caveats noted |
| 20 | H8 | OK | `client_affinity_label` threaded through plan/manifests; `key=instance` |
| 21 | H9 | OK | Sync affinity uses `{basename}-worker` / `"true"` |
| 22 | H10 | OK | All four `nodeSelector` sites: hostname-only when pinned |
| 23 | M1 | OK | Secret names `secret-{ns}-{dep}-{k}`, keys `key1`/`key2`, per-secret volumes |
| 24 | M2 | OK | `cfg.workload_run_time` passed raw (no `or 10` fallback) |
| 25 | M3 | OK | `ctx.replicas` only (no `containers_per_pod` multiply) |
| 26 | M4 | OK | `drop_cache_pod()` accepts `basetime`; `crtime` from wall clock |
| 27 | M5 | OK | Both pods use `str(int(cfg.verbose))` |
| 28 | M6 | OK | Workdir volume/mount removed from sync and drop-cache pods |
| 29 | M7 | OK | `expand_volume_name()` implemented; `namespace`/`instance` passed to callers |
| 30 | M8 | OK | `/16`, `dhcp4`/`dhcp6: false`, `nad:iface` parsed |
| 31 | M9 | OK | `chpasswd` unconditional; `user`/`password` conditional |
| 32 | L1 | OK | Always `{name}-{replica}-dc` |
| 33 | L2 | See C1 | `user_configmap` raises; `system_configmap` still debug-skips |
| 34 | L3 | OK | `--fileio-tests`/`--fileio-modes` only when non-empty |
| 35 | L5 | OK | `--fio-options` only when non-empty |
| 36 | L6 | OK | `cfg.container_port` (7777) |
| 37 | L7 | OK | `:g` format specifier |
| 38 | O1 | OK | `wl.generate_environment()` called and merged |
| 39 | O2 | OK | `cat` in virt-launcher, `VMXML/`, `root@` for SCP |
| 40 | O3 | OK | Pre-existence check sets `effective_remove_namespaces = 0` |
| 41 | O4 | OK | `_domain_spec()` accepts and uses `class_name` |

Intentional differences N (toleration operator), O (ExternalName
port naming), and P (HammerDB/Sysbench workdir fallback) are
correctly classified.  Former intentional difference F (sync
`containerPort`) is correctly reclassified as bug 36.

---

#### Test Results

    1536 passed, 3 skipped in 54.65s

Breakdown:

- 311 workload/config/orchestrator unit tests
- 879 golden parity tests (3 skipped: hammerdb VM tests requiring
  VM-specific setup)
- 346 other tests (deep compare, integration, etc.)

---

#### Disposition

**APPROVED** — pending resolution of F1 (`dedicatedCpuPlacement`)
and clarification of C1–C3.  None of these are blockers for normal
testing; F1 only matters when `--vm-block-multiqueue` is explicitly
used.  The codebase is in excellent shape for the next round of E2E
validation.

---

### Round 3 — Final Re-Review

#### Scope

This round verifies the developer's response to the four items
flagged in round 2 (F1, C1–C3), examines the new self-audit bug
(bug 42), and confirms the retraction of intentional difference M.

#### Resolution of Round 2 Items

All four items from round 2 have been resolved:

**F1 (`dedicatedCpuPlacement`) — FIXED.**  The stale
`cpu["dedicatedCpuPlacement"] = True` line has been removed from
`vm.py`.  The `vm_block_multiqueue` block now correctly sets only
`blockMultiQueue` (on `devices`), `ioThreadsPolicy`, and
`ioThreads` (on `domain`) — exactly matching bash's
`_vm_block_multiqueue_cpu()` and `_vm_block_multiqueue_disk()`.
The audit (bug 16) notes the removal with "(Round 2 F1)".

**C1 (bug 33 — `system_configmap` error handling) — CLARIFIED.**
The audit now accurately describes the dual behavior:
`user_configmap()` raises `FileNotFoundError` for user-supplied
`--configmap` files; `system_configmap()` debug-skips missing
internal pod files from `_SHARED_POD_FILES` (a packaging issue,
not a user error).  The audit text references "(Round 2 C1)".

**C2 (bug 19 — Kata annotation matching) — FIXED.**  Code now
uses `self._cfg.runtime_class.startswith("kata")` instead of the
previous exact `== "kata"` match, covering Kata variants like
`kata-qemu` and `kata-fc`.  The audit notes "covers variants
like `kata-qemu`" and references "(Round 2 C2/C3)".

**C3 (bug 19 — Kata annotation format) — FIXED.**  Code now uses
`json.dumps(..., separators=(",", ":"))` to produce compact JSON
(`["-o","allow_direct_io"]`) matching bash's format.  The audit
notes "compact JSON (no spaces after separators), matching bash's
format".

#### Bug 42 (Self-Audit)

The developer discovered that `_is_sync()` in
`test_golden_parity.py` used `"sync" in name` substring matching,
which incorrectly identified `synctest` worker objects as sync
infrastructure.  This caused `_worker_objects()` to filter them
out, silently skipping 2 golden parity tests for `synctest`.

The fix replaces substring matching with a `\bsync\b`
word-boundary regex.  "sync" as a standalone word (e.g.,
`clusterbuster-sync`) matches; "sync" as part of "synctest" does
not.  This is correct: hyphens are non-word characters, so `\b`
fires at `…-sync-…` boundaries but not at `…sync…t…`.

Verified in code (`tests/test_golden_parity.py` lines 238–249).

#### Intentional Difference M — Correctly Retracted

The original claim was that bash uses `{basename}-sync: {uuid}` as
the sync service selector while Python uses
`{basename}-sync-sync: "true"`.  The developer's self-audit
verified that bash also uses `{basename}-sync-sync: "true"` —
confirmed at `clusterbuster.sh` line 1451:

    create_service -W -h -k "${basename}-sync-sync" -v "true" ...

The Python sync service selector (`manifest_plan.py` line 155)
matches exactly.  The original observation was based on an older
version of `clusterbuster.sh`.

#### Updated Per-Bug Verification

Bugs 16, 19, and 33 — previously marked "See F1", "See C2/C3",
and "See C1" — are now all verified OK:

| Bug | Ref | Verified | Notes |
|-----|-----|----------|-------|
| 16 | H4 | OK | `dedicatedCpuPlacement` removed; `ioThreads`/`blockMultiQueue` correct |
| 19 | H7 | OK | `startswith("kata")` + compact JSON `separators=(",", ":")` |
| 33 | L2 | OK | `user_configmap` raises; `system_configmap` debug-skips (documented) |
| 42 | R3 | OK | `_is_sync()` uses `\bsync\b` word-boundary regex |

#### Observations O1–O3

The three minor observations from round 2 remain unchanged.  None
require action:

- **O1**: `vm_run_as_container` podman command omits
  `__CB_HOSTNAME` and explicit per-port `-p` flags (uses `-P`
  instead) — cosmetic only.
- **O2**: `%r` replica expansion in volume names is defined but
  not plumbed through for bare-pod multi-replica scenarios —
  unlikely to matter in practice.
- **O3**: `cloud_init_networkdata()` uses `default` interface
  only — matches bash behavior (parity-correct).

#### Test Results

    1553 passed, 8 skipped, 4 failed

The 8 skips are E2E tests requiring Kata or CNV (not installed
locally).  The 4 failures are E2E tests failing due to expired
cluster credentials — not code issues.  All golden parity tests
pass with zero skips (up from 3 skipped in round 2, resolved by
bug 42).

#### Disposition

**APPROVED — no remaining items.**  All round 2 findings (F1,
C1–C3) are resolved.  Bug 42 and the M retraction are correct.
The audit now covers 42 bugs fixed and 14 active intentional
differences (F reclassified as bug 36, M retracted).  The
codebase is ready for E2E validation.

---

### Round 4 — Code and Test Review (Post-Audit Commits)

#### Scope

This round reviews the 5 most recent commits:

- `2ba83f7` Remove incorrect `_BASH_FORCES_POD` constant
- `5f68a96` Fix incorrect VM drop-cache skip in golden parity test
- `76d459b` Fix workload singleton leak in `TestDryRunKata`
- `0f24ed9` Address Round 2 review: fix F1/C1–C3, add kata/CNV
  e2e tests
- `06d2819` Fix VM emptydisk: add serial field for `/dev/disk/by-id`
  mapping

Changes span 23 non-golden files: 11 library modules, 8 test
files, `generate_golden.py`, `pyproject.toml`, `func_ci.yaml`,
and 56 regenerated golden JSON files.

#### Overall Assessment

The code changes are well-executed.  All audit bug fixes and
round 2/3 findings are correctly reflected in the code.  The
test additions are substantial and well-structured — 470+ new
lines of golden parity tests, 95 lines of Kata integration
tests, and 234 lines of E2E tests.  The `manifest_plan.py`
module successfully eliminates the old dual-path divergence.

One issue in the live orchestrator path needs the developer's
attention (see I1 below).

---

#### Library Code Verification

**`manifest_plan.py` (467 lines, new).** The unified builder is
well-structured.  Key patterns verified:

- Secret naming `secret-{ns}-{dep}-{k}` with `key1`/`key2`.
- Sync service selector `{basename}-sync-sync: "true"`.
- Drop-cache naming always includes replica index.
- `generate_environment()` called and merged.
- `vm_run_as_container`, `workload_packages`,
  `workload_setup_commands`, `workload_sysctls`, and
  `class_name` all threaded to the VM builder.
- Client-server affinity uses `client_affinity_label` with
  `key=instance`, `value=server_name`.

**`manifests.py`.** All changes match audit resolutions:

- `annotations()`: Multus CNI for all pod types;
  `startswith("kata")` + compact JSON for Kata annotation.
- `security_context()`: `runAsUser: 0` when privileged;
  `runAsNonRoot: True` when restricted; `sysctls` parameter.
- `liveness_probe()`: `/bin/sleep` + `initialDelaySeconds: 10`.
- `expand_volume_name()`: `%N`, `%i`, `%r` expansion.
- `volume_mounts()` / `volumes()`: Per-secret volumes/mounts
  with namespace/instance parameters.
- `pod()` / `deployment()` / `replicaset()`:
  `client_affinity_label` support; `restartPolicy: Never` on
  pods.
- `drop_cache_pod()` / `sync_pod()`: `basetime`/`crtime` from
  clock; `VERBOSE` from `cfg.verbose`; workdir volume removed;
  hostname-only `nodeSelector`; sync affinity uses
  `{basename}-worker` / `"true"`.
- `external_service()`: Accepts `ports` parameter.
- `user_configmap()`: Raises `FileNotFoundError`.

**`vm.py`.** All changes match audit resolutions:

- `_domain_spec()`: Accepts `class_name` for interface lookup;
  `blockMultiQueue` on devices; `ioThreadsPolicy`/`ioThreads`
  on domain; `dedicatedCpuPlacement` removed; emptydisk gets
  `serial` field.
- `cloud_init_userdata()`: `chpasswd` unconditional;
  `vm_run_as_container` fully implemented (skip workload
  packages, install podman, skip sysctl bootcmds, podman run
  wrapper); `vm_password` conditional.
- `cloud_init_networkdata()`: `/16` subnet, DHCP flags, NAD
  `nad:iface` parsing.

**`artifacts.py`.** VM XML retrieval now uses
`vm.kubevirt.io/name` label to find virt-launcher pods and
`cat`s the QEMU XML file to `VMXML/`.  Cloud-init log retrieval
now requires `vm_ssh_keyfile`, uses `root@`, adds SSH options,
and renames from a temp file.  Both match bash.

**`orchestrator.py`.** Major refactoring:

- `_namespace_policy()` delegates to `manifest_plan`.
- `_create_all_namespaces()`: Pre-existence check for
  `remove_namespaces == -1`; ServiceAccount creation and SCC
  binding for privileged namespaces.
- `_create_sync_services()`: Delegates to `build_manifest_plan`.
- `_create_all_deployments()`: Delegates to
  `build_manifest_plan` per namespace; old `_build_*` functions
  removed.
- Cleanup passes `effective_remove_namespaces` override.

**`cleanup.py`.** `override_remove_namespaces` parameter
correctly threaded through `do_cleanup()` and `_do_cleanup()`.

**Workload files.** All match audit bug fixes:

- `sleep.py`: Raw `cfg.workload_run_time` (no `or 10`).
- `server.py`: `ctx.replicas` only for expected clients.
- `fio.py`: `--fio-options` only when non-empty.
- `sysbench.py`: `--fileio-tests`/`--fileio-modes` only when
  non-empty.
- `logger.py`: `:g` format for `--delay`.

---

#### Issue Found

**I1. `_create_sync_services` captures worker pods and workload
services in the live orchestrator path**

The `kind in {"Service", "Pod"}` filter at
`orchestrator.py:504–519` is too broad.  `build_manifest_plan()`
returns ALL manifests (including worker pods, drop-cache pods,
and workload services), and the filter selects every object with
kind `Service` or `Pod` — not just sync infrastructure.

Meanwhile, `_create_all_deployments` (`orchestrator.py:546–566`)
filters OUT all `Pod` and `Service` kinds:

    non_deploy_kinds = frozenset({
        "Namespace", "ConfigMap", "Secret", "Service", "Pod",
    })

This means:

- **Worker pods** (for pod-type deployments) are created in
  `_create_sync_services` rather than
  `_create_all_deployments`.  They are serialized via
  `ObjectBatcher(batch_size=1, force_flush=True)` instead of
  being parallelized by `_create_all_parallel`.
- **Drop-cache pods** (for all deployment types) are likewise
  serialized in `_create_sync_services` and are applied before
  their target worker pods, causing a brief unschedulable window
  (the drop-cache affinity references a worker pod that has not
  been created yet).
- **Workload services** (e.g. uperf server service) are created
  in `_create_sync_services` rather than with their deployments.

This does not affect dry-run output or manifest correctness.
All manifests are eventually created and are structurally
identical to what the old code produced.  But for live runs:

1. **Performance regression**: Pod-type multi-namespace runs
   will be slower because workers are serialized instead of
   parallelized across namespaces.
2. **Ordering concern**: Drop-cache pods are applied before
   workers.  The API server accepts them (they become Pending),
   and workers follow shortly after, so this works in practice.
   The old code had the same ordering within each namespace but
   parallelized across namespaces.
3. **Architectural confusion**: `_create_sync_services` now
   creates non-sync objects, and `_create_all_deployments` only
   creates Deployment/ReplicaSet/VirtualMachine kinds.

**Recommended fix**: Tighten the filter in
`_create_sync_services` to only select sync-related objects.
For example, restrict by namespace (only `sync_ns`) and/or by
name pattern (containing "sync"), leaving all other Pod and
Service objects for `_create_all_deployments`.  The
complementary filter in `_create_all_deployments` would then
need to keep Pod and Service kinds (removing them from
`non_deploy_kinds`) while still excluding Namespace, ConfigMap,
and Secret.

---

#### Test Changes

**`test_e2e.py` (234 lines, new).** Three test classes with
proper skip markers:

- `TestE2EPodWorkloads` — cpusoaker, sleep, memory, files.
- `TestE2EKata` — cpusoaker, sleep, memory, fio.  Requires
  Kata RuntimeClass.
- `TestE2ECNV` — cpusoaker, sleep, memory, fio-emptydisk.
  Requires HyperConverged CR.

Feature-detection functions (`_has_kata`, `_has_cnv`) are
evaluated at import time, which is correct for `skipif` markers.

**`test_golden_parity.py` (~470 new lines).**  Major expansion:

- `_BASH_FORCES_POD` constant removed (no longer needed since
  golden files reflect bash's actual behavior).
- `_is_sync()` uses `\bsync\b` word-boundary regex (bug 42).
- `_extract_object_info` expanded: `restart_policy`,
  `run_as_non_root`, `container_sysctls`, `has_pod_affinity`,
  `vm_disk_serials`, `vm_volume_types`,
  `vm_bootcmd_structural`.
- `TestGoldenVMDomain` — disk serials, volume types, bootcmd
  structure for all VM workloads.
- `TestGoldenPodSpec` — `restartPolicy`, `runAsNonRoot`,
  container sysctls (uperf/server), drop-cache affinity
  (fio/files).
- `TestMultiNamespace` — namespace count/names, ExternalName
  services (count and ports), worker distribution, sync pod
  placement, configmaps per namespace, PSA labels, uperf server
  services, fio drop-cache pods.

**`test_driver_integration.py` — `TestDryRunKata` (95 lines,
new).**  Covers Kata annotation injection, compact JSON format,
`startswith` matching for `kata-qemu`, writeback option,
threadpool option, and multi-workload (fio, uperf).  The
`_reset_workloads` fixture correctly re-instantiates workload
singletons after each test to prevent mutation leak between
tests.

**`test_driver_manifests.py` — 7 new Kata annotation tests.**
Injection, compact JSON, `kata-qemu` variant, non-injection
for `runc`, non-injection without virtiofsd args, multiple
args, threadpoolsize.  Liveness probe test updated for new
`initialDelaySeconds: 10` and `/bin/sleep` command.

**`test_driver_workloads.py`.**  Sleep zero-runtime test
updated (`"0"` not `"10"`).  FIO options split into two tests:
omitted when empty, present when set.

**`test_driver_orchestrator.py`.**  Mock workloads expanded with
`sysctls()`, `namespace_policy()`, `vm_required_packages()`,
`vm_setup_commands()` to match the new API surface.  Worker pod
filtering changed from `restartPolicy != "Never"` to name-based
(since all worker pods now have `restartPolicy: Never`).

**`test_driver_config.py`.**  VM test updated: `exit_at_end`
stays `True` (not `False`), matching bash behavior.

**`generate_golden.py`.**  `yaml` import moved to top level.
`extract_object_info` expanded with the same new fields as
`test_golden_parity.py`.  Note: the extraction logic remains
duplicated between these two files — the audit's lesson 5
recommended a shared module but this is minor technical debt.

---

#### Test Results

    1553 passed, 8 skipped, 4 failed

The 8 skips are E2E tests requiring Kata (4) or CNV (4), which
are not installed locally.  The 4 failures are E2E tests failing
due to expired cluster credentials — not code issues.  All
golden parity tests pass with zero skips.

---

#### Disposition

**APPROVED** — pending developer review of I1
(`_create_sync_services` scope).  The issue does not affect
dry-run output, manifest correctness, or any test.  It only
affects live-run performance for pod-type deployments and can
be addressed independently of the parity work.

---

### Round 5 — Cleanup & Namespace Creation Review

#### Scope

This round reviews the 6 commits `2b1dd27` through `da4ffb5`,
which address namespace lifecycle and cleanup parity with bash:

- `2b1dd27` Handle pre-existing namespaces instead of failing
  with `AlreadyExists`
- `f3f6ebb` Use `oc apply` and existence checks for idempotent
  object creation
- `44cb6bb` e2e: use `--cleanup-always=1` to prevent cascading
  failures
- `c451b87` Expand cleanup resource types and add
  `--removenamespaces=0` e2e tests
- `da4ffb5` Use `oc delete all` for cleanup, matching bash
  semantics

Changes span `cleanup.py`, `orchestrator.py`, `cluster.py`
(new `apply()` method), `manifests.py` (`ObjectBatcher`
`use_apply` parameter), `test_e2e.py` (new
`TestE2ENamespaceReuse` class), and
`test_driver_integration.py` (new `%r`/`%N`/`%i` volume
expansion tests).

#### Overall Assessment

The changes significantly improve namespace lifecycle handling
and are well-supported by new E2E tests.  Three important
fixes are correct and match bash semantics (G1, G2, G4).  The
`oc apply` switch (G6) is a pragmatic improvement beyond bash.
However, three issues remain: one is a high-severity gap in
precleanup auto-detection (I1), and two are medium-severity
structural differences from bash's force-cleanup path (I2, I3).

---

#### Good Changes (Correctly Match Bash)

**G1. `oc delete all` instead of explicit resource list**
(`da4ffb5`)

The old `_RESOURCE_TYPES` tuple in `cleanup.py` explicitly
listed `pvc`, `pv`, `sa`, and `configmap` — types that
`oc delete all` does NOT cover.  Bash cleanup
(`clusterbuster.sh` line 4642–4645) uses `oc delete all` or
`oc delete ns`, never individual resource types.  The old
Python behavior would have destroyed PVCs during
`--removenamespaces=0` workflows.  This fix is critical and
correct.

**G2. Namespace existence checks** (`2b1dd27`)

`_create_all_namespaces()` now tracks an `existing` set and
skips re-creation of namespaces that already exist, matching
bash's `create_namespace` (line 3496–3498) which checks
`namespace_exists "$namespace"` and only calls `create_object`
if the namespace is new.  The auto-detection of
`effective_remove_namespaces = 0` when `remove_namespaces ==
-1` also matches bash (line 3497).

**G3. Sync namespace existence check** (`2b1dd27`)

The sync namespace is now checked for existence before
creation (`orchestrator.py` line 388), avoiding `AlreadyExists`
errors.  Matches bash's `create_namespace` call for the sync
namespace in `setup_namespaces` (line 4865).

**G4. ServiceAccount existence check** (`2b1dd27`)

`_create_all_namespaces()` now queries for an existing
ServiceAccount before creating one, matching bash (line
3507–3508).  Previously, unconditional `oc create
serviceaccount` would fail if the SA already existed from a
previous run.

**G5. PSA labels on all namespaces unconditionally** (`2b1dd27`)

PSA `oc label --overwrite` is now applied to all namespaces
(including existing ones) outside the `if policy ==
"privileged"` block.  Matches bash line 3505 where PSA
labeling runs regardless of policy.

**G6. `oc apply` for idempotent object creation** (`f3f6ebb`)

The `ObjectBatcher` now supports `use_apply=True`, switching
from `oc create` to `oc apply` for configmaps, secrets, sync
objects, and deployments.  Bash uses `oc create` (line 3343)
and relies on precleanup to delete existing objects first.
Python's `apply` approach is more resilient to partial-cleanup
scenarios.  This is an intentional improvement, not a strict
parity match.

**G7. `--cleanup-always=1` in E2E tests** (`44cb6bb`)

Using `--cleanup-always=1` instead of `--cleanup=1` ensures
cleanup runs even on failure, preventing cascading failures
across E2E tests.

---

#### Issues

**I1 (HIGH). `pre` parameter is accepted but unused in
`do_cleanup` — precleanup deletes namespaces when it should
not**

In bash, precleanup (`_do_cleanup_1 -p`) performs
auto-detection of pre-existing namespaces
(`clusterbuster.sh` lines 4638–4641):

    if [[ $use_namespaces -ne 0 && $precleanup -ne 0 &&
          $remove_namespaces -eq -1 &&
          -n "$(__OC get ns -l "${basename}-sync" 2>/dev/null;
                __OC get ns -l "${basename}" 2>/dev/null)" ]]
    then
        remove_namespaces=0
    fi

When `remove_namespaces == -1` (the default) and the
precleanup flag is set, bash checks whether labeled namespaces
already exist on the cluster.  If they do, it sets
`remove_namespaces = 0`, causing cleanup to use
`oc delete all -A` (which preserves namespaces and their PVCs)
instead of `oc delete namespace`.

In Python, the `pre` parameter is defined in `do_cleanup()`'s
signature (`cleanup.py` line 25) but is never read anywhere
in the function body.  The function unconditionally uses
`rm_ns = config.remove_namespaces`:

    rm_ns = override_remove_namespaces \
        if override_remove_namespaces is not None \
        else config.remove_namespaces

When `config.remove_namespaces == -1`, `rm_ns` is `-1`, which
is `!= 0`, so `_do_cleanup` deletes namespaces.  The cascade:

1. Precleanup deletes namespaces (destroying PVCs).
2. `_create_all_namespaces` finds no existing namespaces (they
   were just deleted), so `effective_remove_namespaces` stays
   at `-1`.
3. Post-cleanup also deletes namespaces.

This breaks the core use case that `--removenamespaces=-1`
(auto-detect) is designed to protect: preserving PVCs in
pre-existing namespaces across runs.

**Recommended fix**: When `pre=True` and
`config.remove_namespaces == -1`, query the cluster for
existing labeled namespaces.  If found, set `rm_ns = 0` for
this precleanup pass.

**I2 (MEDIUM). Sync namespace not included in
`effective_remove_namespaces` auto-detection**

Python's `_create_all_namespaces` auto-detection loop
(`orchestrator.py` lines 371–375) only iterates over
`ctx.namespaces_to_create`, which does NOT include the sync
namespace when `sync_in_first_namespace == 0`.  The sync
namespace is stored separately in `ctx.sync_namespace`.

In bash, `setup_namespaces` (line 4862–4867) calls
`create_namespace` for the sync namespace BEFORE workload
namespaces are created.  `create_namespace` (line 3497) sets
`remove_namespaces = 0` for ANY namespace that exists,
including the sync namespace.

If only the sync namespace survives from a previous run
(workload namespaces were deleted but the sync namespace was
orphaned), Python would not detect it, leaving
`effective_remove_namespaces` at the default.

**I3 (MEDIUM). Force cleanup does not match bash's multi-step
escalation**

Bash's force cleanup in `_do_cleanup` (lines 4671–4684) is a
three-step process:

1. If deployment is VM: force-delete ALL objects (`all`) with
   the `{basename}-sync` label (line 4676).
2. Force-delete ALL objects (`all`) with the `{basename}` label
   (line 4678).
3. If removing namespaces: force-delete namespaces with the
   `{basename}` label (line 4680).

This sequence first removes individual resources (steps 1–2),
clearing finalizers, and THEN deletes namespaces (step 3).
Deleting the namespace directly can hang if resources inside
it have stuck finalizers; removing those resources first
prevents that hang.

Python's force cleanup (`cleanup.py` lines 42–48) calls a
single `_do_cleanup` with `force=True`, which does EITHER
`oc delete namespace` (when `rm_ns != 0`) OR `oc delete all`
(when `rm_ns == 0`), but never both.  When removing
namespaces, it skips the individual-resource cleanup step
entirely.

---

#### Observation

**O1. Post-cleanup behavioral difference (arguably an
improvement)**

In bash, `run_clusterbuster_2` runs in a subshell
(`(run_clusterbuster_2)` at line 4938).  Any
`remove_namespaces=0` auto-detection from precleanup or
`create_namespace` is lost when the subshell exits.
Post-cleanup at line 4941 uses the parent's original
`remove_namespaces=-1` and always deletes namespaces.

In Python, `effective_remove_namespaces` persists on `ctx`
(line 1052) and is passed to post-cleanup.  If namespaces
were pre-existing, post-cleanup uses `0` and preserves them.

This is arguably MORE correct than bash (where the subshell
boundary appears accidental), but it IS a behavioral
difference.

---

#### E2E Test Coverage

The new `TestE2ENamespaceReuse` class (`test_e2e.py` lines
246–400) provides good coverage:

- `test_namespace_reuse_across_runs`: Two consecutive runs with
  `--removenamespaces=0` reuse the same namespaces.
- `test_cleanup_removes_all_objects_but_keeps_ns`: Verifies
  `oc delete all` semantics — pods and services are removed
  but configmaps and the namespace survive.
- `test_precleanup_with_removenamespaces_zero`: Precleanup with
  `--removenamespaces=0` preserves namespaces.

---

#### Disposition

**APPROVED** — pending developer review of I1–I3.

| ID | Severity | Description | Status |
|----|----------|-------------|--------|
| I1 | HIGH | `pre` parameter unused; precleanup deletes namespaces on `remove_namespaces=-1` | Needs fix |
| I2 | MEDIUM | Sync namespace not included in auto-detection | Needs fix |
| I3 | MEDIUM | Force cleanup doesn't match bash's multi-step escalation | Needs fix |
| O1 | LOW | Post-cleanup preserves namespaces (differs from bash, arguably better) | Developer decision |

---

### Round 6 — Cleanup/Namespace Lifecycle Fixes

#### Scope

This round reviews commit `9247903 Fix cleanup/namespace
lifecycle parity issues from Round 5 review`, which addresses
all three issues (I1, I2, I3) raised in round 5.  Changes
span `cleanup.py`, `orchestrator.py`,
`test_driver_orchestrator.py`, and `test_e2e.py`.

#### Resolution of Round 5 Items

**I1 (`pre` parameter unused in `do_cleanup`) — FIXED.**

A new helper `_has_labeled_namespaces()` (`cleanup.py` lines
22–35) queries the cluster for namespaces with either the
`{basename}-sync` or `{basename}` label.

`do_cleanup()` now uses the `pre` parameter (`cleanup.py`
lines 61–63):

    if pre and config.use_namespaces and rm_ns == -1:
        if _has_labeled_namespaces(cluster, config):
            rm_ns = 0

This exactly mirrors bash's `_do_cleanup_1` precleanup
auto-detection.

**I2 (Sync namespace not in auto-detection) — FIXED.**

`_create_all_namespaces()` now builds an `all_check` list
that includes the sync namespace (`orchestrator.py` lines
372–374):

    all_check = list(ctx.namespaces_to_create)
    if ctx.sync_namespace and ctx.sync_namespace not in all_check:
        all_check.append(ctx.sync_namespace)

**I3 (Force cleanup multi-step escalation) — FIXED.**

A new `_force_cleanup()` function (`cleanup.py` lines
114–142) implements bash's three-step escalation:

1. If `deployment_type == "vm"`: force-delete all objects
   (`all`) with the `{basename}-sync` label.
2. Always: force-delete all objects (`all`) with the
   `{basename}` label.
3. If removing namespaces (`remove_namespaces != 0`):
   force-delete namespaces with the `{basename}` label.

#### Disposition

**APPROVED — all Round 5 items resolved.**

| ID | Severity | Round 5 Status | Round 6 Status |
|----|----------|----------------|----------------|
| I1 | HIGH | Needs fix | FIXED |
| I2 | MEDIUM | Needs fix | FIXED |
| I3 | MEDIUM | Needs fix | FIXED |
| O1 | LOW | Developer decision | Unchanged |

---

### Round 7 — CI Suite Parity, Deployment Model, and Artifacts

#### Scope

This round reviews 15 commits (`4fae7be` through `6d5bb4b`)
encompassing:

- Bug fixes 44–63 found through `func_ci` runs on `scale`
- Per-replica Deployment/ReplicaSet model change (bug 56)
- Artifact collection parity (bugs 48–51)
- Log routing and formatting (bugs 53–55, 62)
- CI suite fixes (bugs 57–60)
- Metrics collection (bug 61)
- Bash deprecation banner
- VM force-cleanup unit test

Changes span 24 files: 13 library modules, 4 test files,
`clusterbuster.sh`, `README.md`, `.gitignore`, and the
parity audit.  The audit now covers 63 bugs and 14 active
intentional differences, with a `func_ci` pass rate of
71/72 jobs.

---

#### Verified Bug Fixes

**Bugs 44–46 (E1–E3): Cleanup and namespace lifecycle.**
Already verified in rounds 5–6.  The audit correctly
documents these.

**Bug 47 (E4): VM force-cleanup escalation.**  Already
verified in round 6.  New `test_force_cleanup_vm` test
correctly expects 5 delete calls (sync + basename normal,
then VM sync all-delete, basename all-delete, namespace
delete).

**Bug 48 (E5): Manifest YAML artifacts.**
`ObjectBatcher._save_manifest_artifact()` saves each
manifest to `<artifact_dir>/<Kind>/<ns>:<name>` matching
bash's `create_object`.  Correct.

**Bug 49 (E6): SYSFILES/ directory.**  `_save_sysfiles()`
copies system pod files from `lib/pod_files/` to
`<artifact_dir>/SYSFILES/`.  Matches bash behavior.

**Bug 50 (E7): Sync namespace pod artifacts.**  Removed the
`if ns == sync_namespace: continue` filter in
`artifacts.py`.  Bash collects pods from all namespaces
including the sync namespace.  Correct.

**Bug 51 (E8): VM artifact directories.**
`_ensure_artifact_dirs()` now takes `vm=` parameter and only
creates `VMLogs/` and `VMXML/` when `deployment_type ==
"vm"`.  Correct.

**Bug 52 (E9): `report.none` file.**  `reporting.py` now
skips both `"raw"` and `"none"` when deciding whether to
write a formatted report file.  Correct.

**Bug 53 (E10): `stderr.log` population.**  The `log_output`
parameter on `ClusterInterface.run()` controls whether
stdout/stderr from `oc` commands is logged.  Correct.

**Bug 54 (E11): `monitor.log` population.**  Pod state
transition logging in `monitoring.py` changed from
`_LOG.debug` to `_LOG.info`.  Correct.

**Bug 55 (E12): ISO timestamps.**  `_IsoTimestampFormatter`
produces `%Y-%m-%dT%H:%M:%S.%f` format.  Correct.

**Bug 57 (E14): Kata `default_memory` annotation quoting.**
Correct.

**Bug 58 (E15): Kata `default_vcpus` annotation quoting.**
Correct.

**Bug 59 (E16): CI suite job counter reset.**  Counter now
increments monotonically across the entire suite.  Correct.

**Bug 60 (E17): Failed job reporting.**  Correct.

**Bug 61 (E18): Metrics collection.**  Three sub-fixes all
correct: `metrics_file` resolution, `prom-extract` path,
decoupled extraction from snapshot.

**Bug 62 (E19): `oc` read commands flooding `stderr.log`.**
`log_output` defaults to `False`.  Correct.

**Bug 63 (E20): `commandline` artifact quoting.**
`shlex.join()`.  Correct.

---

#### Issue Found

**I1 (MEDIUM). Bug 56 audit description does not match bash
behavior**

The audit incorrectly characterizes the per-replica
Deployment/ReplicaSet model as "matching bash behavior."
Bash creates ONE Deployment/ReplicaSet with `replicas: N`.
Python creates N per-replica objects with `replicas: 1`.
This intentionally departs from bash because the bash model
prevents drop-cache pod affinity from targeting individual
worker replicas — a pre-existing bash bug.

**Recommendation**: Reclassify bug 56 as an intentional
difference/improvement (now intentional difference Q in the
parity audit).

---

#### PrometheusMetrics Off-By-One Fixes

Three correct fixes in `PrometheusMetrics.py`:

1. `__filter_metrics_by_time` rate mode: `range(1,
   len(values) - 1)` to `range(1, len(values))`.
2. `__find_metric_value_by_time`: Same range fix.
3. `__get_max_rate`: `len(values) > 2` to `len(values) >= 2`
   and same range fix.

**Pre-existing bug (not introduced by these commits)**: In
`__filter_metrics_by_time` rate mode, the code uses
`answer[i - 1]` where it should use `values[i - 1]`.
Currently dead code — all callers pass `start=None`.

---

#### Disposition

**APPROVED** — with one audit correction needed (bug 56
reclassified as intentional difference Q).

---

### Round 8 — Design Specification Review

#### Scope

Review of the consolidated design document
`docs/clusterbuster-python-phase3-design.md`, assessing
accuracy, completeness, and currency against the implemented
code.

#### Overall Assessment

The document is well-structured and captures the high-level
architecture accurately.  Several areas needed updating to
reflect modules, subsystems, and behavioral changes that
emerged during implementation and CI parity testing.

---

#### Issues Found

| ID | Severity | Description |
|----|----------|-------------|
| I1 | HIGH | `manifest_plan.py` not described |
| I2 | MEDIUM | Logging architecture not described |
| I3 | MEDIUM | Cleanup description incomplete |
| I4 | MEDIUM | Artifact collection missing details |
| I5 | MEDIUM | Metrics pipeline incomplete |
| I6 | LOW | Per-replica deployment model not mentioned |
| I7 | LOW | Tier 2 E2E description inaccurate |
| I8 | LOW | Test file inventory incomplete |
| I9 | LOW | Bug count ambiguity (64 vs 65) |

---

### Round 8 Re-Review — Design Specification Update

#### Scope

Re-review of the design document after the developer addressed
all 9 issues from Round 8.

#### Disposition of Round 8 Items

All 9 issues have been resolved:

| ID | Severity | Round 8 Status | Re-Review Status |
|----|----------|----------------|------------------|
| I1 | HIGH | `manifest_plan.py` not described | FIXED — new Section 3.6 |
| I2 | MEDIUM | Logging architecture missing | FIXED — new Section 5.3 |
| I3 | MEDIUM | Cleanup description incomplete | FIXED — expanded Section 5.9 |
| I4 | MEDIUM | Artifact collection missing | FIXED — expanded Section 5.7 |
| I5 | MEDIUM | Metrics pipeline incomplete | FIXED — expanded Section 5.8 |
| I6 | LOW | Per-replica model not mentioned | FIXED — in Section 3.6 |
| I7 | LOW | E2E description inaccurate | FIXED — Section 7.3 rewritten |
| I8 | LOW | Test inventory incomplete | FIXED — all 13 files listed |
| I9 | LOW | Bug count ambiguity | FIXED — Section 7.6 clarified |

#### New Issue

**I1 (LOW). Metrics file path incorrect.**

Section 5.8 says `"default"` resolves to
`lib/clusterbuster/metrics-default.yaml`.  The actual
resolved path is `lib/metrics-default.yaml`.

---

#### Overall

**APPROVED.**  The design document now accurately and
comprehensively describes the implemented system.  The one
remaining issue is a minor path typo.
