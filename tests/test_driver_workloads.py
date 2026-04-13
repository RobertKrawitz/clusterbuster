# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""Unit tests for the workload plugin system and all 15 workloads."""

from __future__ import annotations

import copy
import dataclasses

import pytest

from clusterbuster.ci.compat import ParsedOption
from clusterbuster.driver.config import ClusterbusterConfigBuilder

# Import workloads package to trigger registration
import clusterbuster.driver.workloads  # noqa: F401

from clusterbuster.driver.workload_registry import (
    ArglistContext,
    all_workload_names,
    all_workloads,
    get_workload,
    pod_flags,
    resolve_alias,
)


# ---------------------------------------------------------------------------
# Fixture: save/restore singleton workload state per test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_workload_state():
    """Snapshot every registered workload's __dict__ before each test and
    restore it afterward, preventing singleton state leakage between tests."""
    snapshots: dict[str, dict] = {}
    for name in all_workload_names():
        wl = get_workload(name)
        snapshots[name] = copy.deepcopy(wl.__dict__)
    yield
    for name, saved in snapshots.items():
        wl = get_workload(name)
        wl.__dict__.update(saved)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides):
    """Build a minimal ClusterbusterConfig for arglist testing."""
    b = ClusterbusterConfigBuilder()
    b.requested_workload = overrides.pop("requested_workload", "cpusoaker")
    for k, v in overrides.items():
        setattr(b, k, v)
    return b.build()


def _make_ctx(workload_name="cpusoaker", **config_overrides) -> ArglistContext:
    cfg = _make_config(requested_workload=workload_name, **config_overrides)
    return ArglistContext(
        mountdir="/var/lib/clusterbuster/",
        namespace="test-ns",
        instance=0,
        secret_count=0,
        replicas=1,
        containers_per_pod=1,
        container_index=0,
        config=cfg,
    )


def _parsed(noptname1: str, optvalue: str, *, noptname: str = "") -> ParsedOption:
    """Build a ParsedOption.  *noptname* defaults to *noptname1* when omitted."""
    return ParsedOption(
        noptname1=noptname1,
        noptname=noptname or noptname1,
        optvalue=optvalue,
    )


# =========================================================================
# Registry tests
# =========================================================================

class TestRegistry:
    def test_all_15_registered(self):
        assert len(all_workloads()) == 15

    def test_all_canonical_names(self):
        expected = {
            "cpusoaker", "sleep", "failure", "waitforever", "pausepod",
            "synctest", "logger", "memory", "hammerdb", "sysbench",
            "files", "byo", "fio", "server", "uperf",
        }
        assert set(all_workload_names()) == expected

    def test_alias_cpu(self):
        assert resolve_alias("cpu") == "cpusoaker"

    def test_alias_cpusoak(self):
        assert resolve_alias("cpusoak") == "cpusoaker"

    def test_alias_clusterbuster(self):
        assert resolve_alias("clusterbuster") == "sleep"

    def test_alias_log(self):
        assert resolve_alias("log") == "logger"

    def test_alias_simple_log(self):
        assert resolve_alias("simple-log") == "logger"

    def test_alias_logging(self):
        assert resolve_alias("logging") == "logger"

    def test_alias_hammer(self):
        assert resolve_alias("hammer") == "hammerdb"

    def test_alias_file(self):
        assert resolve_alias("file") == "files"

    def test_alias_byowl(self):
        assert resolve_alias("byowl") == "byo"

    def test_alias_bringyourown(self):
        assert resolve_alias("bringyourown") == "byo"

    def test_alias_pause(self):
        assert resolve_alias("pause") == "pausepod"

    def test_alias_simple_pausepod(self):
        assert resolve_alias("simple-pausepod") == "pausepod"

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown workload"):
            resolve_alias("nonexistent")

    def test_get_workload_by_alias(self):
        wl = get_workload("cpu")
        assert wl.name == "cpusoaker"

    def test_case_insensitive(self):
        assert resolve_alias("CPU") == "cpusoaker"
        assert resolve_alias("HammerDB") == "hammerdb"


# =========================================================================
# Tier 1: Trivial workloads
# =========================================================================

class TestCpusoaker:
    def test_arglist(self):
        ctx = _make_ctx("cpusoaker", workload_run_time=30)
        wl = get_workload("cpusoaker")
        args = wl.arglist(ctx)
        assert args[0] == "python3"
        assert "cpusoaker.py" in args[1]
        assert "--processes" in args
        assert "--runtime" in args
        assert args[args.index("--runtime") + 1] == "30"

    def test_configmaps(self):
        wl = get_workload("cpusoaker")
        assert wl.list_configmaps() == ["cpusoaker.py"]


class TestSleep:
    def test_arglist_default_runtime(self):
        ctx = _make_ctx("sleep", workload_run_time=0)
        wl = get_workload("sleep")
        args = wl.arglist(ctx)
        assert args[args.index("--runtime") + 1] == "0"

    def test_arglist_explicit_runtime(self):
        ctx = _make_ctx("sleep", workload_run_time=42)
        wl = get_workload("sleep")
        args = wl.arglist(ctx)
        assert args[args.index("--runtime") + 1] == "42"

    def test_supports_reporting(self):
        assert get_workload("sleep").supports_reporting() is False

    def test_reporting_class(self):
        assert get_workload("sleep").workload_reporting_class() == "generic"

    def test_logs_required(self):
        assert get_workload("sleep").calculate_logs_required(1, 1, 1, 1, 1) == 0


class TestFailure:
    def test_arglist(self):
        ctx = _make_ctx("failure", workload_run_time=5)
        wl = get_workload("failure")
        args = wl.arglist(ctx)
        assert "failure.py" in args[1]
        assert "--runtime" in args

    def test_reporting_class(self):
        assert get_workload("failure").workload_reporting_class() == "generic"


class TestWaitforever:
    def test_process_options_forces_processes(self):
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "waitforever"
        b.processes_per_pod = 4
        wl = get_workload("waitforever")
        wl.finalize_extra_cli_args(b)
        assert b.processes_per_pod == 1

    def test_arglist_no_runtime(self):
        ctx = _make_ctx("waitforever")
        wl = get_workload("waitforever")
        args = wl.arglist(ctx)
        assert "--runtime" not in args

    def test_supports_reporting(self):
        assert get_workload("waitforever").supports_reporting() is False

    def test_reporting_class(self):
        assert get_workload("waitforever").workload_reporting_class() == "generic_nodata"

    def test_logs_required(self):
        assert get_workload("waitforever").calculate_logs_required(2, 3, 4, 5, 6) == 0


class TestPausepod:
    def test_arglist_empty(self):
        ctx = _make_ctx("pausepod")
        wl = get_workload("pausepod")
        assert wl.arglist(ctx) == []

    def test_process_options_forces_processes(self):
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "pausepod"
        b.processes_per_pod = 8
        wl = get_workload("pausepod")
        wl.finalize_extra_cli_args(b)
        assert b.processes_per_pod == 1

    def test_supports_reporting(self):
        assert get_workload("pausepod").supports_reporting() is False

    def test_reporting_class(self):
        assert get_workload("pausepod").workload_reporting_class() == "generic_nodata"

    def test_logs_required(self):
        assert get_workload("pausepod").calculate_logs_required(1, 1, 1, 1, 1) == 0


# =========================================================================
# Tier 2: Options + arglist
# =========================================================================

class TestSynctest:
    def test_defaults(self):
        wl = get_workload("synctest")
        opts = wl.report_options()
        assert opts["synctest_count"] == 5
        assert opts["synctest_sleep"] == 0

    def test_process_options(self):
        wl = get_workload("synctest")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "synctest"
        wl.process_options(b, _parsed("synctestcount", "10"))
        wl.process_options(b, _parsed("synctestsleep", "3"))
        assert wl.report_options()["synctest_count"] == 10
        assert wl.report_options()["synctest_sleep"] == 3

    def test_option_synctestclustercount(self):
        wl = get_workload("synctest")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "synctest"
        wl.process_options(b, _parsed("synctestclustercount", "5"))
        assert wl._cluster_count == 5

    def test_arglist(self):
        ctx = _make_ctx("synctest")
        wl = get_workload("synctest")
        args = wl.arglist(ctx)
        assert "synctest.py" in args[1]
        assert "--count" in args
        assert "--cluster-count" in args
        assert "--sleep" in args

    def test_reporting_class(self):
        assert get_workload("synctest").workload_reporting_class() == "generic"

    def test_unknown_option(self):
        wl = get_workload("synctest")
        b = ClusterbusterConfigBuilder()
        assert wl.process_options(b, _parsed("unknownopt", "val")) is False


class TestLogger:
    def test_defaults(self):
        wl = get_workload("logger")
        opts = wl.report_options()
        assert opts["log_bytes_per_line"] == 1
        assert opts["log_lines_per_io"] == 1
        assert opts["log_xfer_count"] == 1
        assert opts["log_delay"] == 0

    def test_process_options(self):
        wl = get_workload("logger")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("logbytesperline", "256"))
        assert wl.report_options()["log_bytes_per_line"] == 256

    def test_option_loglinesperio(self):
        wl = get_workload("logger")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("loglinesperio", "5"))
        assert wl._lines_per_io == 5

    def test_option_logxfercount(self):
        wl = get_workload("logger")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("logxfercount", "10"))
        assert wl._xfer_count == 10

    def test_option_logdelay(self):
        wl = get_workload("logger")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("logdelay", "0.05"))
        assert wl._delay == 0.05

    def test_arglist(self):
        ctx = _make_ctx("logger", workload_run_time=60)
        wl = get_workload("logger")
        args = wl.arglist(ctx)
        assert "logger.py" in args[1]
        assert "--bytes-per-line" in args
        assert "--lines-per-io" in args

    def test_reporting_class(self):
        assert get_workload("logger").workload_reporting_class() == "generic"


class TestMemory:
    def test_defaults(self):
        wl = get_workload("memory")
        opts = wl.report_options()
        assert opts["memory_size"] == [1048576, 1048576]
        assert opts["memory_scan"] == 0
        assert opts["memory_stride"] == 0
        assert opts["memory_subproc"] == 0
        assert opts["memory_start_probability"] == -1

    def test_scan_order_mapping(self):
        wl = get_workload("memory")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "memory"
        wl.process_options(b, _parsed("memoryscan", "random"))
        assert wl._scan == 2
        wl.process_options(b, _parsed("memoryscan", "1"))
        assert wl._scan == 1
        wl.process_options(b, _parsed("memoryscan", "0"))
        assert wl._scan == 0

    def test_option_memorystride(self):
        wl = get_workload("memory")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("memorystride", "4096"))
        assert wl._stride == 4096

    def test_option_memoryiterations(self):
        wl = get_workload("memory")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("memoryiterations", "10"))
        assert wl._iterations == "10"

    def test_option_memoryiterationruntime(self):
        wl = get_workload("memory")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("memoryiterationruntime", "5"))
        assert wl._iteration_runtime == "5"

    def test_option_memoryidle(self):
        wl = get_workload("memory")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("memoryidle", "100"))
        assert wl._idle == "100"

    def test_option_memoryidlefirst(self):
        wl = get_workload("memory")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("memoryidlefirst", "1"))
        assert wl._idle_first == 1

    def test_option_memoryrandomseed(self):
        wl = get_workload("memory")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("memoryrandomseed", "abc"))
        import base64
        assert wl._random_seed == base64.b64encode(b"abc").decode()

    def test_option_memorysync(self):
        wl = get_workload("memory")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("memorysync", "yes"))
        assert wl._sync == 1

    def test_option_memorysubproc(self):
        wl = get_workload("memory")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("memorysubproc", "yes"))
        assert wl._subproc == 1

    def test_option_memorystartprobability(self):
        wl = get_workload("memory")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("memorystartprobability", "0.5"))
        assert wl._start_probability == "0.5"

    def test_container_image_side_effect(self):
        wl = get_workload("memory")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "memory"
        wl.finalize_extra_cli_args(b)
        assert b.container_image == "quay.io/rkrawitz/clusterbuster-workloads:latest"

    def test_arglist(self):
        ctx = _make_ctx("memory", workload_run_time=30)
        wl = get_workload("memory")
        args = wl.arglist(ctx)
        assert "memory.py" in args[1]
        assert "--memory-size" in args
        assert "--scan" in args


class TestHammerdb:
    def test_defaults(self):
        wl = get_workload("hammerdb")
        opts = wl.report_options()
        assert opts["hammerdb_driver"] == "pg"
        assert opts["hammerdb_database"] == "hammerdb"
        assert opts["hammerdb_benchmark"] == "tpcc"
        assert opts["hammerdb_virtual_users"] == 4
        assert opts["hammerdb_rampup"] == 1

    def test_option_hammerdbdriver(self):
        wl = get_workload("hammerdb")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("hammerdbdriver", "mariadb"))
        assert wl._driver == "mariadb"

    def test_option_hammerdbdatabase(self):
        wl = get_workload("hammerdb")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("hammerdbdatabase", "mydb"))
        assert wl._database == "mydb"

    def test_option_hammerdbbenchmark(self):
        wl = get_workload("hammerdb")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("hammerdbbenchmark", "tpch"))
        assert wl._benchmark == "tpch"

    def test_option_hammerdbvirtualusers(self):
        wl = get_workload("hammerdb")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("hammerdbvirtualusers", "8"))
        assert wl._virtual_users == 8

    def test_option_hammerdbrampup(self):
        wl = get_workload("hammerdb")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("hammerdbrampup", "3"))
        assert wl._rampup == 3

    def test_option_hammerdbworkdir(self):
        wl = get_workload("hammerdb")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("hammerdbworkdir", "/var/hammerdb"))
        assert wl._workdir == "/var/hammerdb"

    def test_container_image_side_effect(self):
        wl = get_workload("hammerdb")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "hammerdb"
        wl.finalize_extra_cli_args(b)
        assert b.container_image == "quay.io/rkrawitz/clusterbuster-hammerdb:latest"
        assert b.vm_image == "quay.io/rkrawitz/clusterbuster-hammerdb-vm:latest"

    def test_vm_resource_adjustments(self):
        wl = get_workload("hammerdb")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "hammerdb"
        b.deployment_type = "vm"
        b.vm_cores = 1
        b.vm_memory = "2Gi"
        b.pod_start_timeout = 60
        wl.finalize_extra_cli_args(b)
        assert b.vm_cores == 2
        assert b.vm_memory == "4Gi"
        assert b.pod_start_timeout == 600

    def test_arglist(self):
        ctx = _make_ctx("hammerdb", workload_run_time=120)
        wl = get_workload("hammerdb")
        args = wl.arglist(ctx)
        assert "hammerdb.py" in args[1]
        assert "--driver" in args
        assert "--database" in args


class TestSysbench:
    def test_defaults(self):
        wl = get_workload("sysbench")
        opts = wl.report_options()
        assert opts["sysbench_workload"] == "fileio"
        assert "seqwr" in opts["sysbench_fileio_tests"]

    def test_namespace_policy_fileio(self):
        wl = get_workload("sysbench")
        assert wl.namespace_policy() == "privileged"

    def test_namespace_policy_cpu(self):
        wl = get_workload("sysbench")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "sysbench"
        wl.process_options(b, _parsed("sysbenchworkload", "cpu"))
        assert wl.namespace_policy() == "restricted"

    def test_requires_drop_cache_fileio(self):
        wl = get_workload("sysbench")
        assert wl.requires_drop_cache() is True

    def test_no_drop_node_cache_side_effect(self):
        """Sysbench must NOT set builder.drop_node_cache."""
        wl = get_workload("sysbench")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "sysbench"
        b.drop_node_cache = False
        wl.process_options(b, _parsed("sysbenchworkload", "fileio"))
        assert b.drop_node_cache is False

    def test_container_image_side_effect(self):
        wl = get_workload("sysbench")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "sysbench"
        wl.finalize_extra_cli_args(b)
        assert b.container_image == "quay.io/rkrawitz/clusterbuster-workloads:latest"

    def test_arglist(self):
        ctx = _make_ctx("sysbench", workload_run_time=30)
        wl = get_workload("sysbench")
        args = wl.arglist(ctx)
        assert "sysbench.py" in args[1]
        assert "--rundir" in args
        assert "--workload" in args

    def test_passthrough_option_stored(self):
        """Passthrough options like --sysbench-threads=4 must be stored."""
        wl = get_workload("sysbench")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "sysbench"
        wl.process_options(
            b,
            _parsed("sysbenchthreads", "4", noptname="sysbench_threads"),
        )
        assert len(wl._passthrough) == 1
        assert wl._passthrough["sysbenchthreads"] == "--threads=4"

    def test_passthrough_in_arglist(self):
        """Passthrough options must appear in the arglist."""
        wl = get_workload("sysbench")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "sysbench"
        wl.process_options(
            b,
            _parsed("sysbenchthreads", "4", noptname="sysbench_threads"),
        )
        ctx = _make_ctx("sysbench", workload_run_time=30)
        args = wl.arglist(ctx)
        assert "--sysbench-option" in args
        idx = args.index("--sysbench-option")
        assert args[idx + 1] == "--threads=4"

    def test_passthrough_multiple(self):
        """Multiple passthrough options are all stored."""
        wl = get_workload("sysbench")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "sysbench"
        wl.process_options(
            b,
            _parsed("sysbenchthreads", "4", noptname="sysbench_threads"),
        )
        wl.process_options(
            b,
            _parsed("sysbenchfiletotalsize", "1G", noptname="sysbench_file_total_size"),
        )
        assert len(wl._passthrough) == 2


# =========================================================================
# Tier 3: Custom deployment or configmaps
# =========================================================================

class TestFiles:
    def test_defaults(self):
        wl = get_workload("files")
        opts = wl.report_options()
        assert opts["file_size"] == 4096
        assert opts["file_block_size"] == 4096
        assert opts["dirs_per_volume"] == 1
        assert opts["files_per_dir"] == 1
        assert opts["files_direct"] == 0
        assert opts["files_drop_cache"] == 1

    def test_block_size_auto_promotion(self):
        """block_size=0 should auto-promote to file_size."""
        wl = get_workload("files")
        assert wl._block_size == 0
        assert wl._effective_block_size() == 4096

    def test_requires_writable_workdir(self):
        assert get_workload("files").requires_writable_workdir() is True

    def test_requires_drop_cache(self):
        assert get_workload("files").requires_drop_cache() is True

    def test_arglist(self):
        ctx = _make_ctx("files")
        wl = get_workload("files")
        args = wl.arglist(ctx)
        assert "files.py" in args[1]
        assert "--dirs-per-volume" in args
        assert "--files-per-dir" in args
        assert "--dir" in args

    def test_option_dirs_per_volume(self):
        wl = get_workload("files")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("dirspervolume", "5"))
        assert wl._dirs_per_volume == 5

    def test_option_files_per_dir(self):
        wl = get_workload("files")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("filesperdir", "100"))
        assert wl._files_per_dir == 100

    def test_option_file_size(self):
        wl = get_workload("files")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("filesize", "8192"))
        assert wl._file_size == 8192

    def test_option_file_block_size(self):
        wl = get_workload("files")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("fileblocksize", "1024"))
        assert wl._block_size == 1024
        assert wl._effective_block_size() == 1024

    def test_option_files_direct(self):
        wl = get_workload("files")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("filesdirect", "yes"))
        assert wl._direct == 1

    def test_option_files_drop_cache(self):
        wl = get_workload("files")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("filesdropcache", "no"))
        assert wl._drop_cache == 0

    def test_option_files_dir_append(self):
        wl = get_workload("files")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("filesdir", "/mnt/vol1"))
        wl.process_options(b, _parsed("filesdir", "/mnt/vol2"))
        assert wl._dirs == ["/mnt/vol1", "/mnt/vol2"]

    def test_option_files_dir_clear(self):
        """Empty --files-dir= clears the directory list."""
        wl = get_workload("files")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("filesdir", "/mnt/vol1"))
        wl.process_options(b, _parsed("filesdir", ""))
        assert wl._dirs == []


class TestByo:
    def test_arglist_basename_stripping(self):
        """Basename stripping happens in arglist, not process_options."""
        wl = get_workload("byo")
        wl._args = ["/usr/bin/my-tool", "--flag"]
        ctx = _make_ctx("byo")
        args = wl.arglist(ctx)
        dd_idx = args.index("--")
        assert args[dd_idx + 1] == "my-tool"

    def test_reporting_class_with_name(self):
        wl = get_workload("byo")
        wl._byo_name = "custom"
        assert wl.workload_reporting_class() == "custom"

    def test_reporting_class_without_name(self):
        wl = get_workload("byo")
        wl._byo_name = ""
        wl._args = ["/usr/bin/my-tool"]
        assert wl.workload_reporting_class() == "byo_my_tool"

    def test_requires_writable_workdir(self):
        assert get_workload("byo").requires_writable_workdir() is True

    def test_finalize_adds_dd_and_extra_args(self):
        """finalize_extra_cli_args adds -- and extra_args to processed_options."""
        wl = get_workload("byo")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "byo"
        b.extra_args = ["/usr/bin/test-script", "--flag1"]
        b.processed_options = ["--some-opt=val"]
        wl.finalize_extra_cli_args(b)
        assert wl._args == ["/usr/bin/test-script", "--flag1"]
        assert "--" in b.processed_options
        assert "/usr/bin/test-script" in b.processed_options
        assert "--flag1" in b.processed_options

    def test_finalize_raises_without_args(self):
        """finalize_extra_cli_args raises SystemExit when no command given."""
        wl = get_workload("byo")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "byo"
        b.extra_args = []
        with pytest.raises(SystemExit, match="No command specified"):
            wl.finalize_extra_cli_args(b)

    def test_option_byo_file(self):
        wl = get_workload("byo")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("byofile", "/path/to/file"))
        assert "/path/to/file" in wl._extra_files

    def test_option_byo_workload_sets_name(self):
        wl = get_workload("byo")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("byoworkload", "my_wl"))
        assert wl._byo_workload == "my_wl"
        assert wl._byo_name == "my_wl"

    def test_option_byo_name(self):
        wl = get_workload("byo")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("byoname", "custom_name"))
        assert wl._byo_name == "custom_name"

    def test_option_byo_workdir(self):
        wl = get_workload("byo")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("byoworkdir", "/var/tmp/byo"))
        assert wl._workdir == "/var/tmp/byo"

    def test_option_byo_dropcache(self):
        wl = get_workload("byo")
        b = ClusterbusterConfigBuilder()
        wl.process_options(b, _parsed("byodropcache", "yes"))
        assert wl._drop_cache == 1
        wl.process_options(b, _parsed("byodropcache", "no"))
        assert wl._drop_cache == 0


class TestFio:
    def test_defaults(self):
        wl = get_workload("fio")
        assert wl._patterns == ["read"]
        assert wl._blocksizes == [4096]
        assert wl._ioengines == ["libaio"]
        assert wl._iodepths == [1]
        assert wl._numjobs == [1]
        assert wl._directs == [0]
        assert wl._fdatasyncs == [1]
        assert wl._ramp_time == 5
        assert wl._drop_cache == 1

    def test_numjobs_is_array(self):
        wl = get_workload("fio")
        assert isinstance(wl._numjobs, list)

    def test_arglist_rundir_not_workdir(self):
        wl = get_workload("fio")
        wl._workdir = "/tmp/fio"
        ctx = _make_ctx("fio", workload_run_time=60)
        args = wl.arglist(ctx)
        assert "--rundir" in args
        assert "--workdir" not in args

    def test_arglist_no_filesize(self):
        wl = get_workload("fio")
        wl._workdir = "/tmp/fio"
        ctx = _make_ctx("fio", workload_run_time=60)
        args = wl.arglist(ctx)
        assert "--filesize" not in args

    def test_arglist_space_separated(self):
        wl = get_workload("fio")
        wl._workdir = "/tmp/fio"
        wl._blocksizes = [4096, 8192]
        ctx = _make_ctx("fio", workload_run_time=60)
        args = wl.arglist(ctx)
        bs_idx = args.index("--blocksizes")
        assert args[bs_idx + 1] == "4096 8192"

    def test_arglist_fio_options_omitted_when_empty(self):
        wl = get_workload("fio")
        wl._workdir = "/tmp/fio"
        ctx = _make_ctx("fio", workload_run_time=60)
        args = wl.arglist(ctx)
        assert "--fio-options" not in args

    def test_arglist_fio_options_present_when_set(self):
        wl = get_workload("fio")
        wl._workdir = "/tmp/fio"
        wl._fio_options = ["--rw=randread"]
        ctx = _make_ctx("fio", workload_run_time=60)
        args = wl.arglist(ctx)
        assert args.count("--fio-options") == 1

    def test_generate_metadata_key_format(self):
        wl = get_workload("fio")
        meta = wl.generate_metadata()
        keys = list(meta["jobs"].keys())
        assert keys[0] == "0001-read-4096-1-1-1-0-libaio"

    def test_container_image_side_effect(self):
        wl = get_workload("fio")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "fio"
        b.workload_run_time = 60
        wl.finalize_extra_cli_args(b)
        assert b.container_image == "quay.io/rkrawitz/clusterbuster-workloads:latest"

    def test_forces_runtime_if_zero(self):
        wl = get_workload("fio")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "fio"
        b.workload_run_time = 0
        wl.finalize_extra_cli_args(b)
        assert b.workload_run_time == 60

    def test_requires_writable_workdir(self):
        assert get_workload("fio").requires_writable_workdir() is True

    def test_array_option_pattern(self):
        """Multi-value fio options parse into lists."""
        wl = get_workload("fio")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "fio"
        b.workload_run_time = 60
        wl.process_options(b, _parsed("fiopattern", "read,write"))
        assert wl._patterns == ["read", "write"]

    def test_array_option_blocksize(self):
        wl = get_workload("fio")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "fio"
        b.workload_run_time = 60
        wl.process_options(b, _parsed("fioblocksize", "4096,8192"))
        assert wl._blocksizes == [4096, 8192]

    def test_array_option_iodepth(self):
        wl = get_workload("fio")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "fio"
        b.workload_run_time = 60
        wl.process_options(b, _parsed("fioiodepth", "1,4,16"))
        assert wl._iodepths == [1, 4, 16]

    def test_array_option_numjobs(self):
        wl = get_workload("fio")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "fio"
        b.workload_run_time = 60
        wl.process_options(b, _parsed("fionumjobs", "1,2,4"))
        assert wl._numjobs == [1, 2, 4]

    def test_array_option_ioengine(self):
        wl = get_workload("fio")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "fio"
        b.workload_run_time = 60
        wl.process_options(b, _parsed("fioioengine", "libaio,io_uring"))
        assert wl._ioengines == ["libaio", "io_uring"]

    def test_metadata_multi_array(self):
        """Metadata key generation with multiple array values."""
        wl = get_workload("fio")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "fio"
        b.workload_run_time = 60
        wl.process_options(b, _parsed("fiopattern", "read,write"))
        wl.process_options(b, _parsed("fioblocksize", "4096,8192"))
        meta = wl.generate_metadata()
        assert len(meta["jobs"]) == 4

    def test_option_fioop(self):
        wl = get_workload("fio")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "fio"
        wl.process_options(b, _parsed("fioop", "--rw=randread"))
        assert "--rw=randread" in wl._fio_options

    def test_option_fiojobfile(self):
        wl = get_workload("fio")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "fio"
        wl.process_options(b, _parsed("fiojobfile", "/tmp/job.fio"))
        assert wl._job_file == "/tmp/job.fio"

    def test_option_fioramptime(self):
        wl = get_workload("fio")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "fio"
        wl.process_options(b, _parsed("fioramptime", "10"))
        assert wl._ramp_time == 10

    def test_option_fiofdatasync(self):
        wl = get_workload("fio")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "fio"
        wl.process_options(b, _parsed("fiofdatasync", "0,1"))
        assert wl._fdatasyncs == [0, 1]

    def test_option_fiofilesize(self):
        wl = get_workload("fio")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "fio"
        wl.process_options(b, _parsed("fiofilesize", "1Gi"))
        assert wl._filesize == 1073741824

    def test_option_fioworkdir(self):
        wl = get_workload("fio")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "fio"
        wl.process_options(b, _parsed("fioworkdir", "/mnt/fio"))
        assert wl._workdir == "/mnt/fio"

    def test_option_fiodropcache(self):
        wl = get_workload("fio")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "fio"
        wl.process_options(b, _parsed("fiodropcache", "no"))
        assert wl._drop_cache == 0

    def test_option_fiodirect(self):
        wl = get_workload("fio")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "fio"
        wl.process_options(b, _parsed("fiodirect", "1,0"))
        assert wl._directs == [1, 0]


# =========================================================================
# Tier 4: Client/server
# =========================================================================

class TestServer:
    def test_defaults(self):
        wl = get_workload("server")
        opts = wl.report_options()
        assert opts["msg_size"] == 32768

    def test_finalize_forces_processes(self):
        wl = get_workload("server")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "server"
        b.processes_per_pod = 4
        wl.finalize_extra_cli_args(b)
        assert b.processes_per_pod == 1

    def test_default_bytes_transfer_value(self):
        """_DEFAULT_BYTES_TRANSFER must be 1000000000 (bash default_bytes_transfer)."""
        from clusterbuster.driver.workloads.server import _DEFAULT_BYTES_TRANSFER
        assert _DEFAULT_BYTES_TRANSFER == 1000000000

    def test_server_arglist(self):
        ctx = _make_ctx("server")
        wl = get_workload("server")
        args = wl.server_arglist(ctx)
        assert "server.py" in args[1]
        assert "--listen-port" in args
        assert "--msg-size" in args
        assert "--server-expected-clients" in args

    def test_client_arglist(self):
        ctx = _make_ctx("server")
        wl = get_workload("server")
        args = wl.client_arglist(ctx)
        assert "client.py" in args[1]
        assert "--server" in args
        assert "--port" in args

    def test_configmaps(self):
        wl = get_workload("server")
        assert set(wl.list_configmaps()) == {"client.py", "server.py"}


class TestUperf:
    def test_defaults(self):
        wl = get_workload("uperf")
        opts = wl.report_options()
        assert opts["msg_size"] == [1024]
        assert opts["test_types"] == ["stream"]
        assert opts["protocols"] == ["tcp"]
        assert opts["nthrs"] == [1]
        assert opts["ramp_time"] == 3

    def test_finalize_forces_processes(self):
        wl = get_workload("uperf")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "uperf"
        b.processes_per_pod = 4
        wl.finalize_extra_cli_args(b)
        assert b.processes_per_pod == 1

    def test_container_image_in_finalize(self):
        wl = get_workload("uperf")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "uperf"
        wl.finalize_extra_cli_args(b)
        assert b.container_image == "quay.io/rkrawitz/clusterbuster-workloads:latest"

    def test_tests_cartesian_product(self):
        wl = get_workload("uperf")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "uperf"
        wl.process_options(b, _parsed("uperfmsgsize", "1024,2048"))
        wl.process_options(b, _parsed("uperftesttype", "stream,rr"))
        assert len(wl._tests) == 4
        assert "stream,tcp,1024,1" in wl._tests
        assert "rr,tcp,2048,1" in wl._tests

    def test_option_uperf_ramp_time(self):
        wl = get_workload("uperf")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "uperf"
        wl.process_options(b, _parsed("uperframptime", "10"))
        assert wl._ramp_time == 10

    def test_option_uperf_protocol(self):
        wl = get_workload("uperf")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "uperf"
        wl.process_options(b, _parsed("uperfprotocol", "udp"))
        assert wl._protocols == ["udp"]

    def test_option_uperf_nthr(self):
        wl = get_workload("uperf")
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "uperf"
        wl.process_options(b, _parsed("uperfnthr", "2,4"))
        assert wl._nthrs == [2, 4]

    def test_server_arglist(self):
        ctx = _make_ctx("uperf")
        wl = get_workload("uperf")
        args = wl.server_arglist(ctx)
        assert "uperf-server.py" in args[1]
        assert "--listen-port" in args

    def test_client_arglist(self):
        wl = get_workload("uperf")
        wl._tests = ["stream,tcp,1024,1"]
        ctx = _make_ctx("uperf", workload_run_time=30)
        args = wl.client_arglist(ctx)
        assert "uperf-client.py" in args[1]
        assert "--test" in args
        assert "stream,tcp,1024,1" in args

    def test_configmaps(self):
        wl = get_workload("uperf")
        expected = {"uperf-client.py", "uperf-server.py", "uperf-mini.xml", "uperf-rr.xml", "uperf-stream.xml"}
        assert set(wl.list_configmaps()) == expected

    def test_sysctls_no_server_interface(self):
        """Without a server interface, sysctls emits port range."""
        wl = get_workload("uperf")
        sc = wl.sysctls()
        assert "net.ipv4.ip_local_port_range" in sc

    def test_sysctls_with_server_interface(self):
        """With a server interface configured, sysctls returns empty."""
        wl = get_workload("uperf")
        cfg = _make_config(requested_workload="uperf")
        cfg_with_net = dataclasses.replace(cfg, net_interfaces={"server": "eth1"})
        sc = wl.sysctls(config=cfg_with_net)
        assert sc == {}

    def test_generate_metadata_key_format(self):
        wl = get_workload("uperf")
        meta = wl.generate_metadata()
        keys = list(meta["jobs"].keys())
        assert keys[0].startswith("0001-")
        assert "stream" in keys[0]


# =========================================================================
# Pod flags utility
# =========================================================================

class TestPodFlags:
    def test_pod_flags_count(self):
        ctx = _make_ctx("cpusoaker")
        flags = pod_flags(ctx)
        assert len(flags) >= 13

    def test_pod_flags_prefix(self):
        ctx = _make_ctx("cpusoaker")
        flags = pod_flags(ctx)
        for f in flags:
            assert f.startswith("--cb-")

    def test_pod_flags_sync_nonce(self):
        ctx = _make_ctx("cpusoaker")
        flags = pod_flags(ctx)
        nonce_flags = [f for f in flags if f.startswith("--cb-sync-nonce=")]
        assert len(nonce_flags) == 1


# =========================================================================
# Config integration
# =========================================================================

class TestConfigIntegration:
    def test_alias_resolution_in_build(self):
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "cpu"
        cfg = b.build()
        assert cfg.resolved_workload == "cpusoaker"

    def test_direct_name_resolution(self):
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "fio"
        cfg = b.build()
        assert cfg.resolved_workload == "fio"

    def test_workload_options_consumed(self):
        """Workload-specific options should not cause 'unknown option' errors."""
        from clusterbuster.driver.workload_registry import (
            make_process_options_callback,
        )
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "synctest"
        b.unknown_opts = ["synctest-count=10"]
        callback = make_process_options_callback(b)
        remaining = callback(b.unknown_opts)
        assert remaining == []


# =========================================================================
# Callback factories
# =========================================================================

class TestCallbackFactories:
    def test_make_process_options_callback_none_for_unknown(self):
        from clusterbuster.driver.workload_registry import make_process_options_callback
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "nonexistent_workload"
        assert make_process_options_callback(b) is None

    def test_make_finalize_callback_none_for_unknown(self):
        from clusterbuster.driver.workload_registry import make_finalize_callback
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "nonexistent_workload"
        assert make_finalize_callback(b) is None

    def test_make_finalize_callback_calls_workload(self):
        from clusterbuster.driver.workload_registry import make_finalize_callback
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "byo"
        b.extra_args = ["/usr/bin/my-script", "--arg1"]
        b.processed_options = []
        cb = make_finalize_callback(b)
        assert cb is not None
        cb(b)
        wl = get_workload("byo")
        assert wl._args == ["/usr/bin/my-script", "--arg1"]
        assert "--" in b.processed_options
        assert "/usr/bin/my-script" in b.processed_options


# =========================================================================
# Compat module tests (P1-10)
# =========================================================================

class TestParseOption:
    def test_simple_key_value(self):
        from clusterbuster.ci.compat import parse_option
        p = parse_option("fio-pattern=read")
        assert p.noptname1 == "fiopattern"
        assert p.noptname == "fio_pattern"
        assert p.optvalue == "read"

    def test_bare_option_defaults_to_1(self):
        from clusterbuster.ci.compat import parse_option
        p = parse_option("verbose")
        assert p.noptname1 == "verbose"
        assert p.optvalue == "1"

    def test_negated_no_prefix(self):
        from clusterbuster.ci.compat import parse_option
        p = parse_option("no-verbose")
        assert p.noptname1 == "verbose"
        assert p.optvalue == "0"

    def test_negated_dont_prefix(self):
        from clusterbuster.ci.compat import parse_option
        p = parse_option("dont-verbose")
        assert p.noptname1 == "verbose"
        assert p.optvalue == "0"

    def test_case_insensitive(self):
        from clusterbuster.ci.compat import parse_option
        p = parse_option("FIO-Pattern=read")
        assert p.noptname1 == "fiopattern"
        assert p.noptname == "fio_pattern"

    def test_empty_value(self):
        from clusterbuster.ci.compat import parse_option
        p = parse_option("files-dir=")
        assert p.noptname1 == "filesdir"
        assert p.optvalue == ""

    def test_value_with_equals(self):
        from clusterbuster.ci.compat import parse_option
        p = parse_option("sysbench-threads=4")
        assert p.noptname1 == "sysbenchthreads"
        assert p.noptname == "sysbench_threads"
        assert p.optvalue == "4"


class TestBoolStr:
    def test_true_values(self):
        from clusterbuster.ci.compat import bool_str
        for v in ("1", "y", "yes", "true", "t", "Y", "YES", "True", "T"):
            assert bool_str(v) == "1", f"Expected '1' for {v!r}"

    def test_false_values(self):
        from clusterbuster.ci.compat import bool_str
        for v in ("0", "n", "no", "false", "f"):
            assert bool_str(v) == "0", f"Expected '0' for {v!r}"

    def test_empty_is_true(self):
        from clusterbuster.ci.compat import bool_str
        assert bool_str("") == "1"

    def test_custom_yes_no(self):
        from clusterbuster.ci.compat import bool_str
        assert bool_str("yes", yes="on", no="off") == "on"
        assert bool_str("no", yes="on", no="off") == "off"


class TestParseSize:
    def test_plain_number(self):
        from clusterbuster.ci.compat import parse_size
        assert parse_size("1024") == "1024"

    def test_kilobytes(self):
        from clusterbuster.ci.compat import parse_size
        assert parse_size("1K") == "1000"

    def test_kibibytes(self):
        from clusterbuster.ci.compat import parse_size
        assert parse_size("1Ki") == "1024"

    def test_megabytes(self):
        from clusterbuster.ci.compat import parse_size
        assert parse_size("1M") == "1000000"

    def test_mebibytes(self):
        from clusterbuster.ci.compat import parse_size
        assert parse_size("1Mi") == "1048576"

    def test_gibibytes(self):
        from clusterbuster.ci.compat import parse_size
        assert parse_size("4Gi") == str(4 * 1073741824)

    def test_invalid_raises(self):
        from clusterbuster.ci.compat import parse_size
        with pytest.raises(ValueError):
            parse_size("abc")

    def test_multiple(self):
        from clusterbuster.ci.compat import parse_size
        result = parse_size("1K", "2K")
        assert result == "1000\n2000"

    def test_delimiter(self):
        from clusterbuster.ci.compat import parse_size
        result = parse_size("1K", "2K", delimiter=" ")
        assert result == "1000 2000"


class TestParseSizeList:
    def test_comma_separated(self):
        from clusterbuster.ci.compat import parse_size_list
        result = parse_size_list("4096,8192")
        assert result == [4096, 8192]

    def test_space_separated(self):
        from clusterbuster.ci.compat import parse_size_list
        result = parse_size_list("4096 8192")
        assert result == [4096, 8192]

    def test_with_units(self):
        from clusterbuster.ci.compat import parse_size_list
        result = parse_size_list("1K,1Ki")
        assert result == [1000, 1024]


# =========================================================================
# Fio template expansion tests (P1-12)
# =========================================================================

class TestFioExpandString:
    def test_simple_var(self):
        from clusterbuster.driver.workloads.fio import _expand_string
        result = _expand_string("val=%{myvar}", {"myvar": "42"})
        assert result == "val=42"

    def test_default_value(self):
        from clusterbuster.driver.workloads.fio import _expand_string
        result = _expand_string("val=%{myvar:-default_val}")
        assert result == "val=default_val"

    def test_override_beats_default(self):
        from clusterbuster.driver.workloads.fio import _expand_string
        result = _expand_string("val=%{myvar:-default_val}", {"myvar": "42"})
        assert result == "val=42"

    def test_multiple_vars(self):
        from clusterbuster.driver.workloads.fio import _expand_string
        result = _expand_string(
            "%{a}-%{b}",
            {"a": "hello", "b": "world"},
        )
        assert result == "hello-world"

    def test_no_match_passthrough(self):
        from clusterbuster.driver.workloads.fio import _expand_string
        result = _expand_string("no_vars_here")
        assert result == "no_vars_here"

    def test_unknown_var_without_default(self):
        from clusterbuster.driver.workloads.fio import _expand_string
        result = _expand_string("val=%{UNSET_XYZZY_12345}")
        assert "UNKNOWN" in result or result == "val="

    def test_empty_override(self):
        from clusterbuster.driver.workloads.fio import _expand_string
        result = _expand_string("val=%{myvar:-fallback}", {"myvar": ""})
        assert result == "val="

    def test_nested_expansion(self):
        """Multiple expansions in the same string."""
        from clusterbuster.driver.workloads.fio import _expand_string
        result = _expand_string(
            "ramp=%{ramp} drop=%{drop:-1}",
            {"ramp": "5"},
        )
        assert result == "ramp=5 drop=1"


# ---------------------------------------------------------------------------
# Container image override semantics
# ---------------------------------------------------------------------------

def _build_config_with_registry(**overrides):
    """Build config through the full pipeline including workload finalize hooks."""
    from clusterbuster.driver import _build_with_registry
    b = ClusterbusterConfigBuilder()
    b.requested_workload = overrides.pop("requested_workload", "cpusoaker")
    for k, v in overrides.items():
        setattr(b, k, v)
    return _build_with_registry(b)


class TestContainerImageOverride:
    """Verify container image selection and --container_image override semantics.

    Rules:
    - Workloads that need a special image (fio, uperf, sysbench, memory,
      hammerdb) set it unconditionally in finalize_extra_cli_args().
    - User --container_image overrides the workload default.
    - pausepod always uses its own image regardless of --container_image.
    - The sync pod always uses clusterbuster_base_image (or
      --sync_pod_image), never --container_image.
    """

    @pytest.mark.parametrize("workload,expected_image", [
        ("fio", "quay.io/rkrawitz/clusterbuster-workloads:latest"),
        ("uperf", "quay.io/rkrawitz/clusterbuster-workloads:latest"),
        ("sysbench", "quay.io/rkrawitz/clusterbuster-workloads:latest"),
        ("memory", "quay.io/rkrawitz/clusterbuster-workloads:latest"),
        ("hammerdb", "quay.io/rkrawitz/clusterbuster-hammerdb:latest"),
    ])
    def test_workload_default_image_no_workload_options(self, workload, expected_image):
        """Workload image set even when zero workload-specific options are parsed."""
        cfg = _build_config_with_registry(requested_workload=workload)
        assert cfg.container_image == expected_image

    def test_base_image_for_generic_workload(self):
        """Workloads without a custom image use clusterbuster_base_image."""
        cfg = _build_config_with_registry(requested_workload="cpusoaker")
        assert cfg.container_image == "quay.io/rkrawitz/clusterbuster-base:latest"

    @pytest.mark.parametrize("workload", [
        "fio", "uperf", "sysbench", "memory", "hammerdb",
    ])
    def test_user_override_wins_over_workload_default(self, workload):
        """--container_image overrides the workload's default image."""
        user_image = "registry.example.com/custom-image:v1"
        cfg = _build_config_with_registry(
            requested_workload=workload,
            container_image=user_image,
        )
        assert cfg.container_image == user_image

    def test_pausepod_default_image(self):
        """pausepod defaults to pause image when no --container_image set."""
        cfg = _build_config_with_registry(requested_workload="pausepod")
        assert cfg.container_image == "gcr.io/google_containers/pause-amd64:3.2"

    def test_pausepod_user_override(self):
        """--container_image overrides pausepod's default image."""
        user_image = "registry.example.com/custom-image:v1"
        cfg = _build_config_with_registry(
            requested_workload="pausepod",
            container_image=user_image,
        )
        assert cfg.container_image == user_image

    def test_sync_pod_ignores_user_container_image(self):
        """Sync pod uses clusterbuster_base_image, not --container_image."""
        from clusterbuster.driver.manifests import ManifestBuilder

        user_image = "registry.example.com/custom-image:v1"
        cfg = _build_config_with_registry(
            requested_workload="fio",
            container_image=user_image,
        )
        assert cfg.container_image == user_image

        mb = ManifestBuilder(cfg)
        sync_manifest = mb.sync_pod(
            "test-sync-ns", expected_clients=4, initial_expected_clients=4,
        )
        sync_image = sync_manifest["spec"]["containers"][0]["image"]
        assert sync_image == "quay.io/rkrawitz/clusterbuster-base:latest"
        assert sync_image != user_image

    def test_sync_pod_uses_sync_pod_image_override(self):
        """--sync_pod_image overrides the sync pod image."""
        from clusterbuster.driver.manifests import ManifestBuilder

        sync_override = "registry.example.com/custom-sync:v2"
        cfg = _build_config_with_registry(
            requested_workload="cpusoaker",
            sync_pod_image_override=sync_override,
        )
        mb = ManifestBuilder(cfg)
        sync_manifest = mb.sync_pod(
            "test-sync-ns", expected_clients=1, initial_expected_clients=1,
        )
        sync_image = sync_manifest["spec"]["containers"][0]["image"]
        assert sync_image == sync_override

    def test_sync_pod_image_independent_of_container_image(self):
        """--container_image and --sync_pod_image are fully independent."""
        from clusterbuster.driver.manifests import ManifestBuilder

        user_image = "registry.example.com/workload:v1"
        sync_override = "registry.example.com/sync:v2"
        cfg = _build_config_with_registry(
            requested_workload="fio",
            container_image=user_image,
            sync_pod_image_override=sync_override,
        )
        assert cfg.container_image == user_image

        mb = ManifestBuilder(cfg)
        workload_container = mb.container("worker")
        assert workload_container["image"] == user_image

        sync_manifest = mb.sync_pod(
            "test-sync-ns", expected_clients=1, initial_expected_clients=1,
        )
        assert sync_manifest["spec"]["containers"][0]["image"] == sync_override
