# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0

"""Full ``--help`` text for ``run-perf-ci-suite`` (parity with the former shell driver)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from clusterbuster.ci.workloads.fio import FioWorkload
from clusterbuster.ci.workloads.files import FilesWorkload
from clusterbuster.ci.workloads.hammerdb import HammerdbWorkload
from clusterbuster.ci.workloads.uperf import UperfWorkload


def _comma_int(xs: list[int]) -> str:
    return ",".join(str(x) for x in xs)


def _comma_str(xs: list[str]) -> str:
    return ",".join(xs)


def _list_profile_basenames(profile_dir: Path) -> list[str]:
    if not profile_dir.is_dir():
        return []
    seen: set[str] = set()
    for pattern in ("*.yaml", "*.yml", "*.profile"):
        for f in profile_dir.glob(pattern):
            base = f.name
            for suf in (".yaml", ".yml", ".profile"):
                if base.endswith(suf):
                    base = base[: -len(suf)]
                    break
            seen.add(base)
    return sorted(seen)


def _workload_documentation() -> str:
    return """\
    Here is a brief description of all available workloads.  If not provided,
        all workloads are run:

* memory: allocate and optionally scan memory in the guest.
* fio: a front end for the Flexible I/O tester.
  See https://fio.readthedocs.io/en/latest/fio_doc.html for more
  details.
* uperf: a partial front end to uperf (https://www.uperf.org)
* files: a simple filesystem stressor that creates and removes a large
  number of files.
* cpusoaker: a simple CPU soaker running a continuous tight loop.
* hammerdb: TPC-C and TPROC-C database benchmark (PostgreSQL and MariaDB).
  See https://www.hammerdb.com/
"""


def _memory_help_options() -> str:
    return """\
    Memory workload CI options:
        --memory-runtime=seconds
        --memory-timeout=seconds
        --memory-replicas=n
        --memory-processes=n
        --memory-scan=<1,0,random>
        --memory-alloc=bytes (e.g. 64Mi)
        --memory-params=runtime:replicas:processes:alloc:scan[,...]
                                Space or comma separated list of test
                                specifications (--memory-runtime in seconds, then
                                same order as --memory-replicas,
                                --memory-processes, --memory-alloc, and
                                --memory-scan).  Use scan=1 for sequential scan,
                                random for random scan (not the numeric code 2).
                                If set, those five options are not used to build
                                the run list.
"""


def _fio_help_options() -> str:
    f = FioWorkload()
    return f"""\
   Fio test options:
        There should be sufficient space under the work directory to hold the desired file
        size, provided if need be by a volume.
        --fio-block-sizes=n[,n...]
                                Space or comma separated list of block
                                sizes to test.  Default is {_comma_int(f.blocksizes)}.
        --fio-patterns=pattern[,pattern...]
                                Space or comma separated list of block sizes
                                to test.  Default is
                                {_comma_str(f.patterns)}.
        --fio-direct=bool[,bool]
                                Space or comma separated list of whether to use
                                direct I/O.  Default is {_comma_int(f.directs)}.
        --fio-fdatasync=bool[,bool]
                                Space or comma separated list of whether to use
                                the fdatasync option.  Default is {_comma_int(f.fdatasyncs)}.
        --fio-iodepths=n[,n...]
                                Space or comma separated list of I/O depths
                                to test.  Default is {_comma_int(f.iodepths)}.
        --fio-numjobs=n[,n...]
                                Space or comma separated list of number of
                                jobs to run.
                                to test.  Default is {_comma_int(f.numjobs)}.
        --fio-ioengines=engine[,engine...]
                                Space or comma separated list of I/O engines
                                to use.  Default is {_comma_str(f.ioengines)}.
        --fio-ninst=n[,n...]    Space or comma separated list of pod counts
                                to test.  Default is {_comma_int(f.ninst)}.
        --fio-workdir=dir       Directory to run test on inside the pod.
                                Default is {f.workdir}.
        --fio-ramptime=n        Ramp time in seconds before measurements are
                                taken.  Default is {f.ramptime}
        --fio-absolute-filesize=size
                                File size in bytes per pod to test with.  If 0,
                                use fio-relative-filesize to specify the
                                filesize.  Default is {f.absolute_filesize}.
        --fio-max-absolute-filesize=size
                                Limit for total filesize across all pods.
                                If zero, there is no absolute filesize limit.
                                Default is {f.max_absolute_filesize}.
        --fio-relative-filesize=fraction
                                File size as a fraction of node memory to use.
                                May be a decimal fraction.  If zero, use
                                fio-absolute-filesize (one or the other should
                                be non-zero).  Default is {f.relative_filesize}.
        --fio-max-relative-filesize=fraction
                                Limit for total filesize across all pods as
                                a fraction of node memory.  If zero, no
                                pre-defined limit.  Default is {f.max_relative_filesize}.
        --fio-timeout=seconds   Time the job out after specified time.  Default
                                is the global timeout default.
        --fio-pod-memsize=size  Memory size to allocate to sandboxed pods.
                                Default is the system default (normally 2GiB).
        --fio-drop-cache=[0,1]
                                Drop cache, don't merely sync (default {f.drop_cache})
"""


def _uperf_help_options(default_job_runtime: int) -> str:
    u = UperfWorkload()
    return f"""\
    Uperf test options:
        --uperf-msg-sizes=n[,n...]
                                Space or comma separate list of message sizes
                                to test.  Default is {_comma_int(u.msg_sizes)}
        --uperf-nthr=n[,n...]   Space or comma separated list of thread counts
                                to test.  Default is {_comma_int(u.nthrs)}
        --uperf-ninst=n[,n...]  Space or comma separated list of number of
                                pairs of pods to test.  Default is {_comma_int(u.ninst)}
        --uperf-test-types=test[,test...]
                                Space or comma separated list of test types
                                to run.  Default is {_comma_str(u.test_types)}
        --uperf-runtime=seconds
                                Allow the pods to run for the specified time.
                                Default is {default_job_runtime} seconds.
        --uperf-timeout=seconds
                                Time the job out after specified time.  Default
                                is the global timeout default.
        --uperf-annotate_vcpus  Provide a pod annotation for the number of
                                vCPUs to use (default {u.use_annotation})
"""


def _files_help_options() -> str:
    fl = FilesWorkload()
    return f"""\
    Files test options:
    	The files
        --files-timeout=seconds
                                Time the job out after specified time.  Default
                                is the global timeout default.
        --files-min-direct=size
                                If direct I/O is in use, specifies the minimum
                                block size that will be tested.
                                Default is {fl.min_direct}.
        --files-params=ninst:dirs:files:blocksize:filesize:direct[,...]
                                Space or comma separated list of test
                                specifications.  If this is provided, the
                                options below are not used.
        --files-ninst=n[,...]   Space or comma separated list of pod counts
                                to test.  Default is {_comma_int(fl.ninst)}.
        --files-dirs-per-pod=n[,...]
                                Space or comma separated list of directories
                                per pod to test.  Default is {_comma_int(fl.dirs_per_volume)}.
        --files-per-dir=n[,...]
                                Space or comma separated list of files per
                                directory.  Default is {_comma_int(fl.per_dir)}.
        --files-block_sizes=size[,...]
                                Space or comma separated list of block sizes
                                to test.  Default is {_comma_int(fl.block_sizes)}.
        --files_sizes=size[,...]
                                Space or comma separated list of file sizes
                                to test.  Must be a multiple of block size.
                                Zero is a valid file size; it indicates that
                                files should be created but no data written.
                                Default is {_comma_int(fl.sizes)}.
        --files-direct=bool[,bool]
                                Space or comma separated list of whether to use
                                direct I/O.  Default is {_comma_int(fl.directs)}.
        --files-drop-cache=[0,1]
                                Drop cache, don't merely sync (default {fl.drop_cache})
"""


def _cpusoaker_help_options() -> str:
    return """\
    CPUsoaker options:
        --cpusoaker-starting-replicas=n
                                Start the test with the specified number of
                                replicas, incrementing until failure.
                                Default 5.
        --cpusoaker-replica-increment=n
                                Increment the number of replicas by the
                                specified number until failure or until
                                --cpusoaker-max-replicas is reached.  Default
                                is cpusoaker-starting-replicas.
        --cpusoaker-runtime=seconds
                                Allow the pods to run for the specified time.
                                Default is 0.  Typically set to 60 to collect
                                reliable metrics data.
        --cpusoaker-timeout=seconds
                                Time the job out after specified time.  Default
                                is the global timeout default.
        --cpusoaker-max-replicas=n
                                Maximum number of replicas to scale to.
                                Default is -1, equivalent to no upper limit.
        --cpusoaker_initial_replicas=n[,n...]
                                Run the specified number of replicas before starting
                                the increment loop
"""


def _hammerdb_help_options(default_job_runtime: int) -> str:
    h = HammerdbWorkload()
    return f"""\
    HammerDB test options:
        --hammerdb-drivers=driver[,driver...]
                                Comma-separated list of drivers: pg, mariadb.
                                Default is {_comma_str(h.drivers)}.
        --hammerdb-runtime=seconds
                                Timed run length in seconds. Default is {default_job_runtime}.
        --hammerdb-timeout=seconds
                                Job timeout. Default is the global timeout.
        --hammerdb-replicas=n   Replicas per run. Default is {h.replicas}.
        --hammerdb-rampup=minutes
                                Ramp-up time in minutes. Default is {h.rampup}.
        --hammerdb-virtual-users=n
                                Number of virtual users. Default is {h.virtual_users}.
        --hammerdb-benchmark=name
                                Benchmark: tpcc or tprocc. Default is {h.benchmark}.
        --hammerdb-params=runtime:driver:replicas:rampup:virtual_users:benchmark[,...]
                                Space or comma separated list of test specifications
                                (same fields as --hammerdb-runtime, driver pg|mariadb,
                                --hammerdb-replicas, --hammerdb-rampup,
                                --hammerdb-virtual-users, --hammerdb-benchmark).
                                If set, --hammerdb-drivers and the scalar hammerdb options
                                above are not used to build the run list.
"""


def _clusterbuster_help_options(repo_root: Path) -> str:
    exe = repo_root / "clusterbuster"
    if not exe.is_file() or not os.access(exe, os.X_OK):
        return """\
  ClusterBuster options:
    (The clusterbuster executable was not found next to this installation;
     run from the ClusterBuster repository root to include driver options here.)
"""
    try:
        r = subprocess.run(
            [str(exe), "--help-options"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if r.returncode != 0 and not r.stdout.strip():
            return f"""\
  ClusterBuster options:
    (clusterbuster --help-options exited with status {r.returncode}.)
"""
        return r.stdout
    except OSError as e:
        return f"""\
  ClusterBuster options:
    (Could not run clusterbuster --help-options: {e})
"""


def build_full_help(
    repo_root: Path,
    *,
    profile_dir: Path | None = None,
    default_job_runtime: int = 120,
    job_delay: int = 0,
) -> str:
    """Assemble the full help string (no external shell scripts)."""
    pdir = profile_dir if profile_dir is not None else repo_root / "lib" / "CI" / "profiles"
    profiles = _list_profile_basenames(pdir)
    profile_lines = "\n".join(f"                                - {n}" for n in profiles) if profiles else "                                - "

    workload_docs = _workload_documentation()
    body = f"""Usage: run-perf-ci-suite [options | clusterbuster_options] [workloads]
{workload_docs}
    Size options may be specified by bytes, or K, Ki, M, Mi, G, Gi, T, or Ti.
    Boolean options may be specified as 1, yes, or true, with anything else
        equivalent to false.
    Additional arguments that are not recognized are passed through to
        clusterbuster.

    General options:

        --client-pin-node=node  Pin client pods to the specified node.
                                If not provided, the first worker node
                                (in the order returned by 'oc get nodes')
                                is used.
        --server-pin-node=node  Pin server pods to the specified node.
                                By default, the second worker node is used.
        --sync-pin-node=node    Pin the sync pod to the specified node.
                                By default, the third worker node is used.
        --pin-node[=class]=node
                                Pin pods of the specified class to the
                                specified node.  Class is optional; if
                                specified, it should be either client,
                                server, or pin.
        --no-pin-nodes          Do not pin jobs to nodes.
        --use-pin-node=[1/0]    Pin (or not) jobs to nodes.
        --runtime=seconds       Run the job for the given number of seconds,
                                if applicable (this does not apply to the
                                files test).  May be overridden by
                                workload-specific values.
        --timeout=seconds       Time the job out after the given number of
                                seconds.  May be overridden by
                                workload-specific values.
        --artifactdir=dir       Store all run artifacts in the specified
                                directory.  Individual runs are in
                                subdirectories.
        --reportformat=format   Format of report printed during run.
                                Default none.  Options are as in clusterbuster.
        --analysisformat=format Format of post-run analysis.  Currently 'ci'
                                and 'summary' are supported.
        --runtimeclasses=classes
                                Comma-separated list of runtime classes to test.
                                Default is <empty> (i. e. default) and kata.
        --cleanup               Clean up all pods after last run.
        --job-delay=N           Delay N seconds between jobs (default {job_delay})
        --restart               Restart any failed or incomplete jobs from a
                                prior run.  Default is no.  Can only be used
                                with identical parameters to previous run.
        --profile=profile       Which profile to use (basename of a .yaml/.yml
                                file under {pdir}, or legacy .profile).
                                Default is no profile.  Known profiles:
{profile_lines}
        --uuid=uuid             Specify a uuid for the job run.  Default is to
                                generate one.
        --prometheus-snapshot
                                Take a Prometheus snapshot and save to the
                                artifacts directory
        --unique-prefix         Prefix the pod names in each job with a
                                distinct string to aid in later identification.

    All other options listed below may be of the form
    --option:<workload>:<runtime>=value to specify that the value should apply
    only to a particular workload and runtimeclass.  Either workload or
    runtimeclass may be omitted or be of the form
    :workload
    :workload1,workload2 (list)
    :!workload (negation)

    Workload-specific options:
{_memory_help_options()}
{_fio_help_options()}
{_uperf_help_options(default_job_runtime)}
{_files_help_options()}
{_cpusoaker_help_options()}
{_hammerdb_help_options(default_job_runtime)}

{_clusterbuster_help_options(repo_root)}
"""
    return body
