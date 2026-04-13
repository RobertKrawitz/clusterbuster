# ClusterBuster

ClusterBuster is (yet another) tool for running workloads on OpenShift
clusters.  Its purpose is to simplify running workloads from the
command line and does not require external resources such as
Elasticsearch to operate.  This was written by [Robert
Krawitz](mailto:rlk@redhat.com).  The driver, workload plugins,
in-pod workloads, and reporting are all written in Python.

It is also intended to be fairly straightforward to add new workloads
as plugins.

<!-- markdown-toc start - Don't edit this section. Run M-x markdown-toc-generate-toc again -->
**Table of Contents**

- [ClusterBuster](#clusterbuster)
  - [Introduction](#introduction)
  - [Running ClusterBuster](#running-clusterbuster)
  - [Internals](#internals)
    - [Architecture](#architecture)
    - [Workloads](#workloads)
      - [Workload Plugin API](#workload-plugin-api)
      - [Workload Client (Pod) API](#workload-client-pod-api)
        - [Public Members](#public-members)
        - [Running Workloads](#running-workloads)
        - [Protected Members](#protected-members)
      - [Create A Workload](#create-a-workload)
    - [CI Workflows](#ci-workflows)
    - [Create A Deployment Type](#create-a-deployment-type)
  - [Bring Your Own Workload](#bring-your-own-workload)

<!-- markdown-toc end -->

## Introduction

I started writing ClusterBuster in 2019 to simplify the process of
running scalable workloads as part of [testing to 500 pods per
node](https://cloud.redhat.com/blog/500_pods_per_node).  Since then,
it has gained new capabilities at a steady pace, and is now able to
run a variety of different workloads.

ClusterBuster monitors workloads that are running, and for most
workloads, will retrieve reporting data from them.  It can optionally
monitor metrics from Prometheus during a run and also take a snapshot
of the actual Prometheus database.  A snapshot contains the raw
Prometheus database, while metrics only contain the specific
information requested; the snapshot is much bigger and more difficult
to use, but more complete.  All of these outputs will be saved
locally; if you want to upload them to an Elasticsearch instance or
other external location, you'll currently need to make your own
arrangements.

## Running ClusterBuster

In the normal way of Linux utilities, running `clusterbuster -h`
prints a help message.  The help message is quite long, as there are a
lot of options available, but the best way of learning how to use it
is to look at one of the example files located in
`examples`.  If you have access to an OpenShift cluster
with admin privileges (since it needs to create namespaces and do a
few other privileged things) any of those files can be used via

```
clusterbuster -f <file>
```

Job files may use either the **YAML format** or the legacy
line-oriented format.  YAML job files use an `options:` top-level key
with dash-separated option names and standard YAML `true`/`false`
values:

```yaml
options:
  workload: cpusoaker
  namespaces: 2
  exit-at-end: true
```

Legacy line-oriented files (bare flags, `key=value`, `#` comments,
backslash continuation) are also supported.  ClusterBuster tries YAML
first and falls back to the legacy parser when the file is not a YAML
mapping.

## Internals

This section describes the architecture and internal interfaces of
ClusterBuster.

### Architecture

**Sync networking:** The sync controller runs as an ordinary Pod in the sync namespace. It
always uses the **pod network** (cluster CNI)—not `hostNetwork` or node IPs. Workload Pods
connect to the sync Service / pod address on that network. For VM deployments, guest traffic
to sync still leaves through the KubeVirt launcher Pod, so it is still pod-network path from
the cluster's perspective. Anything that blocks pod-to-pod traffic (NetworkPolicy, service
mesh sidecars on workload Pods, etc.) can prevent sync while VM paths behave differently.

**VM containerdisk images** (`clusterbuster-vm`, `clusterbuster-hammerdb-vm`): both qcow2 disks
are produced the same way under `lib/container-image/Makefile`: copy a cloud base image,
`virt-customize --firstboot <script>`, then **`virt-install --import --boot=hd`** so the guest
boots once and installs packages with **dnf/rpm on a live SELinux-enforcing system**. HammerDB
uses **amd64 only** (no published aarch64 HammerDB RPM for that install path). A separate
offline-only image build was avoided so SELinux labels and runtime behavior match mainline.

**aarch64 / arm64:** VM deployments are **disabled** on aarch64 worker clusters (KubeVirt guests
often fail past UEFI with current containerdisk flows). `clusterbuster` refuses
`--deployment-type=vm` unless `CB_ALLOW_VM_AARCH64=1`; `run-perf-ci-suite` skips the `vm`
runtime class on those clusters. The VM disk Makefile only builds **amd64** containerdisks
(`VM_DISK_ARCHES`, default `amd64`).

### Workloads

ClusterBuster supports extensible workloads.  At present, it supports
uperf, fio, HammerDB, many small files, CPU/startup test, and a number of others.

Each workload is a Python class that subclasses `WorkloadBase` and is
registered with the `@register` decorator.  All 15 workloads are
implemented under `lib/clusterbuster/driver/workloads/`.  A workload
plugin defines methods for option processing, arglist generation,
configmap listing, metadata, and help text.  Client-server workloads
(such as `server` and `uperf`) implement `server_arglist()` and
`client_arglist()` instead of `arglist()`.

Most workloads also require a component to run on the worker nodes.
The node components reside in `lib/pod_files` and are responsible for
initializing and running the workloads.  For most workloads, an
additional synchronization/control service is used to ensure that all
instances of the workload start simultaneously; the sync service also
manages distributed time and collection of results.

Finally, there are optional components for processing reports and
performing analysis of the data.

#### Workload Plugin API

Workloads subclass `WorkloadBase` from
`clusterbuster.driver.workload_registry` and use the `@register`
decorator.  The class must define a `name` attribute and may define
`aliases`.  The following methods may be overridden:

| Method | Purpose |
|--------|---------|
| `process_options(builder, parsed)` | Handle workload-specific options; return `True` if consumed |
| `finalize_extra_cli_args(builder)` | Post-parsing hook for extra args |
| `arglist(ctx)` | Container command for single-role workloads |
| `server_arglist(ctx)` / `client_arglist(ctx)` | Commands for client-server workloads |
| `create_deployment(ctx)` | Custom deployment creation; return `True` to skip generic path |
| `list_configmaps()` | Pod files for the system configmap |
| `list_user_configmaps()` | Extra user-provided configmap files |
| `calculate_logs_required(ns, deps, replicas, containers, processes)` | Expected log entry count |
| `report_options()` | Options dict for report JSON |
| `generate_metadata()` | Workload metadata for report JSON |
| `help_options()` | Help text for workload-specific options |
| `document()` | One-line description for `-H` output |
| `supports_reporting()` | Whether the workload produces structured reports |
| `workload_reporting_class()` | Report class name |
| `requires_drop_cache()` | Whether per-pod cache dropping is needed |
| `requires_writable_workdir()` | Whether the workdir must be writable |
| `sysctls(config)` | Kernel sysctl requirements |

Example minimal workload:

```python
from clusterbuster.driver.workload_registry import (
    ArglistContext, WorkloadBase, pod_flags, register,
)

@register
class MyWorkload(WorkloadBase):
    name = "myworkload"
    aliases = ("mywl",)

    def arglist(self, ctx: ArglistContext) -> list[str]:
        return ["python3", f"{ctx.mountdir}myworkload.py", *pod_flags(ctx)]

    def document(self) -> str:
        return "My custom workload."
```

#### Workload Client (Pod) API

The Python3 API for workload pods is provided by
`lib/pod_files/clusterbuster_pod_client.py`.  All
workloads should subclass this API.  The API is subject to change.
All workloads should implement a subclass of
`clusterbuster_pod_client` and invoke the `run_workload()` method of
the derived class.

```python
#!/usr/bin/env python3

import time
from clusterbuster_pod_client import clusterbuster_pod_client


class minimal_client(clusterbuster_pod_client):
    """
    Minimal workload for clusterbuster
    """

    def __init__(self):
        try:
            super().__init__()
            self._set_processes(int(self._args[0]))
            self.__sleep_time = float(self._args[1])
        except Exception as err:
            self._abort(f"Init failed! {err} {' '.join(self._args)}")

    def runit(self, process: int):
        user, system = self._cputimes()
        data_start_time = self._adjusted_time()
        time.sleep(self.__sleep_time)
        user, system = self._cputimes(user, system)
        data_end_time = self._adjusted_time()
        extras = {
            'sleep_time': self.__sleep_time
            }
        self._report_results(data_start_time, data_end_time, data_end_time - data_start_time, user, system, extras)


minimal_client().run_workload()
```

##### Public Members

The `clusterbuster_pod_client` class should not be instantiated
itself; only subclasses should be instantiated.

* `clusterbuster_pod_client.run_workload(self)`

  Run an instantiated workload.  This method, once called, will not
  return.

##### Running Workloads

The `run_workload` method will call back to the `runit` method of the
subclass, passing one argument, the process index number starting from
zero (not the process ID).  The `run_workload` method will be invoked
in parallel the number of times specified by the `_set_processes()`
method described below, each in a separate subprocess.

The `runit` method should call `self._report_results` (described
below) to report the results back.  This method does not return.  If
`runit` returns without calling `self._report_results`, or raises an
uncaught exception, the workload is deemed to have failed.  Raising an
exception is the preferred way to fail a run.  It should not call
`sys.exit()` or `os.exit()` on its own; the results of that are
undefined.

##### Protected Members

This currently only documents the most commonly used members.

* /class/ `clusterbuster_pod_client.clusterbuster_pod_client(initialize_timing_if_needed: bool = True, argv: list = sys.argv)

  Initialize the `clusterbuster_pod_client` class.

  `initialize_timing_if_needed` should be `True` if the workload is
  expected to use the synchronization and control services provided by
  ClusterBuster (this is normally the case).  It should only be `False`
  if the workload will not synchronize.  This is most commonly the
  case if the workload is part of a composite workload that does not
  need to synchronize independently, such as the server side of a
  client-server workload.

  `argv` is normally the command line arguments.  You should never
  need to provide anything else.  Arguments not consumed by the
  `clusterbuster_pod_client` are provided in the
  `self._args` variable, as a list.  The constructor will only be
  called once (as opposed to the `runit` method).

  If the /constructor/ needs to report an error, it should call
  `self.abort() with an error message rather than exiting.

* `clusterbuster_pod_client._set_processes(self, processes: int = 1)`

  Specify how many workload processes are to be run.  It is not
  necessary to call this if you intend for only one instance of the
  workload to run.

* `clusterbuster_pod_client._cputimes(self, olduser: float = 0, oldsys: float = 0)`

  Return a tuple of <user, system> cputime accrued by the process.  If
  non-zero cputimes are provided as arguments, they will be subtracted
  from the returned cputimes; this allows for convenient start/stop
  timing.  This includes both self time and child time.

* `clusterbuster_pod_client._cputime(self, otime: float = 0)`

  Return the total CPU time accrued by the process.  If a non-zero
  time value is provided, it is subtracted from the measured CPU time.

* `clusterbuster_pod_client._adjusted_time(self, otime: float = 0)`

  Return the wall clock time as a float, synchronized with the host.
  This should be used in preference to `time.time()`.  If a non-zero
  `otime` is provided, it returns the interval since that time.

* `clusterbuster_pod_client._timestamp(self, string)`

  Prints a message to stderr, with a timestamp prepended.  This is the
  preferred way to log a message.

* `clusterbuster_pod_client._report_results(self, data_start_time: float, data_end_time: float, data_elapsed_time: float, user_cpu: float, sys_cpu: float, extra: dict = None)`

  Report results at the end of a run.  This should always be called
  out of `runit()` unless `runit` raises an exception.  This method is
  likely to change in the future.

  `data_start_time` is the time that the job as a whole started work,
  as returned by `_adjusted_time()`.  It may not be the moment at
  which `_runit()` gets control, if that routine needs to perform
  preliminary setup or synchronize.

  `data_end_time` is the time that the job as a whole completed work,
  as returned by `_adjusted_time()`.

  `data_elapsed_time` is the total time spent running.  It may not be
  the same as `data_end_time - data_start_time` if the workload
  consists of multiple steps with synchronization or other
  setup/teardown required between them.

  `user_cpu` is the amount of user CPU time consumed by the workload;
  it may not be the total accrued CPU time of the process.  `sys_cpu`
  is similar.

  `extra` is any additional data, as a dictionary, that the workload
  wants to log.

* `clusterbuster_pod_client._idname(self, *args, separator: str = ':')`

  Generate an identifier based on namespace, pod name, container name,
  and child index along with any other tokens desired by the workload.
  If a separator is provided, it is used to separate the tokens.

* `clusterbuster_pod_client._sync_to_controller(self, *args, **kwargs)`

  Synchronize to the controller.  The number of times that the
  workload needs to synchronize should be computed on the host side;
  the pod side needs to ensure that it only synchronizes the desired
  number of times.  args and kwargs are passed to
  `clusterbuster_pod_client._idname()`.

* `clusterbuster_pod_client._podname(self)`
  `clusterbuster_pod_client._container(self)`
  `clusterbuster_pod_client._namespace(self)`

  Return the pod name (equivalent to the hostname of the pod), the
  container name, and the namespace of the pod respectively.

* `clusterbuster_pod_client._listen(self, port: int = None, addr: str = None, sock: socket = None, backlog=5)`

  Listen  on  the  specified  port  and  optionally  address.   As  an
  alternate option, an existing socket  may be provided; in this case,
  port and  addr must  both be  None.  If  `backlog` is  provided, the
  listener will listen with the specified queue length.

* `clusterbuster_pod_client._connect_to(self, addr: str = None, port: int = None, timeout: float=None)`

  Connect to the specified address on the specified port.  If a
  timeout is provided, it will time out after at least that long;
  otherwise it will not time out.

* `clusterbuster_pod_client._resolve_host(self, hostname: str)`

  Resolve a hostname.  This is not normally needed, as `_connect_to`
  will do what is needed.  This will retry as needed until it
  succeeds.  `hostname` can be the name of a pod/VM within the
  workload or an external (DNS) name.

* `clusterbuster_pod_client._toSize(self, arg: str)`
  `clusterbuster_pod_client._toSizes(self, *args)`

  Convert an argument to a size (non-negative integer).  Sizes can be
  decimal numbers, or numbers with a suffix of 'k', 'm', 'g', or 't'
  respectively to represent thousands, millions, billions, or
  trillions.  If the suffix has a further suffix of `i`, it is treated
  as binary (powers of 1024) rather than decimal (powers of 1000).

  If the argument is an integer or float, it is returned as an
  integer.

  If it cannot be parsed as an integer, a ValueError is raised.

  The `toSizes()` takes any of the following:

  * Integer or float: the value returned as an integer
  * String: the string is comma- and space-split, and each component
    is converted as described above.
  * List: each element of the list is treated according to the
    preceding rules.

  These methods are useful for parsing argument lists.

* `clusterbuster_pod_client._toBool(self, arg: str, defval: bool = None)`
  `clusterbuster_pod_client._toBools(self, *args)`

  Convert an argument to a Boolean.  The argument can be any of the
  following:

  * Boolean, integer, float, list, or dict: the Python rules for
    conversion to Boolean are used.
  * String (all case-insensitive:
    * `true`, `y`, `yes`: True
    * `false`, `n`, `no`: False
	* Can be converted to an integer: 0 is False, anything else is True
  * Anything else (including a string that cannot be converted as
    above): if `defval` is provided, it is used; if not, a ValueError
    is raised.

  The `toBools` method works the same way as `toSizes`.  This method
  cannot accept a default value.

  These methods are useful for parsing argument lists.

* `clusterbuster_pod_client._splitStr(self, regexp: str, arg: str)`

  Split `arg` into a list of strings per the provided `regexp`.  It
  differs from `re.split()` in that this routine returns an empty list
  if an empty string is passed; `re.split()` returns a list of a
  single element.

#### Create A Workload

To create a new workload, you need to do the following:

1. Create a Python file in `lib/clusterbuster/driver/workloads/`
   (e.g. `myworkload.py`).

2. Subclass `WorkloadBase`, set `name` (and optionally `aliases`),
   and decorate with `@register`.

3. Override `arglist()` (or `server_arglist()`/`client_arglist()` for
   client-server workloads) and any other methods needed.

4. (Optional) Create the in-pod workload script in `lib/pod_files/`
   as a subclass of `clusterbuster_pod_client`.

5. (Optional) Create Python scripts to generate reports.  If you don't
   do this and attempt to generate a report, you'll get only a generic
   report which won't have any workload-specific information.  You can
   create any of the following scripts, but each type of script
   requires the one before it.

   1. *reporter*: a reporter script is responsible for parsing the
      JSON data created by your workload and producing basic reports
      without analysis.  All existing workloads that report have
      reporter scripts.

   2. *loader*: a loader script is responsible for loading a JSON
      report and creating a data structure suitable for further
      analysis.  At present, only selected workloads have loaders.

   3. *analysis*: analysis scripts transform the loaded data into a
      form suitable for downstream consumption, be it by humans,
      databases, or spreadsheets.  There are several types of analysis
      scripts, and more may be added in the future.

The workload is auto-discovered when the `workloads` package is
imported; no manual registration step is needed beyond the
`@register` decorator.

### CI Workflows

ClusterBuster can be used as part of a CI workflow.  In addition to
individual workloads, ClusterBuster can run multiple workloads and
generate a combined report with the `run-perf-ci-suite`.  This was
created to support
[benchmark-runner](https://github.com/redhat-performance/benchmark-runner)
but may of course be used for other purposes.

The perf CI suite runs workloads in a loop with different test
configurations, and uses profiles under `lib/CI/profiles` to specify defaults.
Workload matrices are implemented in Python under `lib/clusterbuster/ci/workloads/`
(see `docs/clusterbuster-ci-python-phase2.md`). The profiles contain default
arguments for the run in addition to those for the individual workloads,
enabling tailoring a run for the desired tradeoff between thoroughness and runtime.

Entries in profiles are of the form `<name><:conditions>=<value>` where
the `name` and `value` are either ClusterBuster arguments or CI suite
arguments. Conditions are optional. For ClusterBuster arguments, the conditions can specify
which workloads and runtime types they apply to. Conditions are of the
form `<workload>:<runtime>` where both workload and runtime may be
singletons, comma-separated lists, or negations. For example, this
entry:

```
volume:files,fio:!vm=:emptydir:/var/tmp/clusterbuster
```
indicates that the `volume` argument
`:emptydir:/var/tmp/clusterbuster` should be used when running the
`files` or `fio` workloads and when the runtime (pod, VM, or Kata) is
not `VM`.

Detailed descriptions of arguments may be obtained by running
```
run-perf-ci-suite --help
```

### Create A Deployment Type

ClusterBuster currently supports running workloads as pods, VMs,
ReplicaSets, or Deployments (with very minimal differences between the
latter two).

Deployment manifests are constructed by the `ManifestBuilder` class
(`lib/clusterbuster/driver/manifests.py`), which dispatches to the
appropriate manifest construction method based on the value of
`deployment_type`.  VM manifests are produced by `VmManifestBuilder`.
Adding a new deployment type requires extending `ManifestBuilder` with
a new method and wiring it into `_create_all_deployments` in
`orchestrator.py`.

## Bring Your Own Workload

It is possible (although at present somewhat complicated) to use your
own workload without writing a full Python wrapper for it, by means of
the `byo` workload.  The ClusterBuster arguments are described in the
help message.

The workload command needs to accept an argument `--setup` as the
first and only option to indicate that any setup should be done at
this point, such as creating files, cloning repos, etc.

The command, along with any other files specified by the user by means
of `--byo-file` arguments, is placed in the working directory
specified by `--byo-workload` or a default location if not specified.
The working directory is writable.

The command should produce valid JSON (or empty output) on its stdout,
which is incorporated into the report generated by ClusterBuster.  All
other output should be to stderr.

There are two commands that can be used from the workload:

* `do-sync` synchronizes between all of the instances (pods,
  containers, and top level processes) comprising the workload.  All
  instances should call `do-sync` the same number of times throughout
  the run, and any subprocesses created by the workload command should
  not call `do-sync`.

* `drop-cache` may be used to drop the buffer cache, but can only
  usefully be used if `--byo-drop-cache=1` is used on the
  ClusterBuster command line.  If you do use this, it is suggested
  that you call `do-sync` following `drop-cache`, but it is not
  mandatory.

When called in setup, the command should not use either `do-sync` or
`drop-cache`.

The command can discover location information about itself by means of
the `CB_PODNAME`, `CB_CONTAINER`, and `CB_NAMESPACE` environment
variables.  In addition `CB_INDEX` contains the index number (not the
process ID) of the process for multiple processes in a container, and
`CB_ID` contains an identification string that may be used as an
identifier in JSON output.

An example workload command is located in
`examples/byo/cpusoaker-byo.sh`.
