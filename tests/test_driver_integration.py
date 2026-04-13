# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""Integration tests for Phase 3D: launcher, examples, dry-run, entry points."""

from __future__ import annotations

import copy
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from clusterbuster.driver.cli import (
    process_job_file,
    _print_dry_run,
)
from clusterbuster.driver.config import ClusterbusterConfigBuilder


_REPO = Path(__file__).resolve().parent.parent
_EXAMPLES = _REPO / "examples"


class TestLegacyExamplesParse:
    """Verify all 22 original option-format files parse without error."""

    _LEGACY_FILES = [
        "cpusoaker", "files", "fio", "hammerdb", "log", "memory",
        "pausepod", "server", "server-1", "sleep", "synctest",
        "sysbench-cpu", "sysbench-fileio", "sysbench-memory",
        "sysbench-mutex", "sysbench-threads", "uperf", "vmtest",
        "500-pods-per-node-30-nodes",
        "500-pods-per-node-30-nodes-1000-namespaces",
        "500-pods-per-node-30-nodes-1000-namespaces-150K-secrets",
        "verify-workloads-pod-vm",
    ]

    @pytest.mark.parametrize("name", _LEGACY_FILES)
    def test_legacy_example_parses(self, name):
        path = _EXAMPLES / name
        if not path.exists():
            pytest.skip(f"{name} not present")
        b = ClusterbusterConfigBuilder()
        process_job_file(b, str(path))
        assert b.requested_workload, f"{name} should set a workload"


class TestYamlExamplesParse:
    """Verify all YAML example files parse, have options: key, and build valid config."""

    _YAML_FILES = [
        "cpusoaker.yaml", "files.yaml", "fio.yaml", "hammerdb.yaml",
        "log.yaml", "memory.yaml", "pausepod.yaml", "server.yaml",
        "server-1.yaml", "sleep.yaml", "synctest.yaml",
        "sysbench-cpu.yaml", "sysbench-fileio.yaml",
        "sysbench-memory.yaml", "sysbench-mutex.yaml",
        "sysbench-threads.yaml", "uperf.yaml", "vmtest.yaml",
        "500-pods-per-node-30-nodes.yaml",
        "500-pods-per-node-30-nodes-1000-namespaces.yaml",
        "500-pods-per-node-30-nodes-1000-namespaces-150K-secrets.yaml",
    ]

    @pytest.mark.parametrize("name", _YAML_FILES)
    def test_yaml_example_parses(self, name):
        path = _EXAMPLES / name
        assert path.exists(), f"{name} should exist in examples/"
        b = ClusterbusterConfigBuilder()
        process_job_file(b, str(path))
        assert b.requested_workload, f"{name} should set a workload"

    @pytest.mark.parametrize("name", _YAML_FILES)
    def test_yaml_example_has_options_key(self, name):
        """Each YAML example must have an 'options' mapping."""
        path = _EXAMPLES / name
        assert path.exists(), f"{name} should exist"
        with open(path) as f:
            doc = yaml.safe_load(f)
        assert isinstance(doc, dict), f"{name} top-level should be a dict"
        assert "options" in doc, f"{name} should have an 'options' key"
        assert isinstance(doc["options"], dict), f"{name} options should be a dict"

    @pytest.mark.parametrize("name", _YAML_FILES)
    def test_yaml_example_has_recognized_workload(self, name):
        """Each YAML example's workload should be in the registry."""
        path = _EXAMPLES / name
        assert path.exists()
        b = ClusterbusterConfigBuilder()
        process_job_file(b, str(path))
        from clusterbuster.driver.workload_registry import resolve_alias
        canonical = resolve_alias(b.requested_workload)
        assert canonical, f"{name} workload {b.requested_workload!r} should resolve"

    @pytest.mark.parametrize("name", _YAML_FILES)
    def test_yaml_example_builds_valid_config(self, name, _isolate_workloads):
        """Each YAML example should produce a valid ClusterbusterConfig."""
        path = _EXAMPLES / name
        assert path.exists()
        b = ClusterbusterConfigBuilder()
        process_job_file(b, str(path))
        from clusterbuster.driver import _build_with_registry
        config = _build_with_registry(b)
        assert config.resolved_workload, (
            f"{name} config should have a resolved workload"
        )


class TestDryRun:
    """Verify dry-run output content and behavior."""

    def _make_config(self, **overrides):
        b = ClusterbusterConfigBuilder()
        b.requested_workload = overrides.pop("workload", "cpusoaker")
        for k, v in overrides.items():
            setattr(b, k, v)
        from clusterbuster.driver import _build_with_registry
        return _build_with_registry(b)

    def test_dryrun_prints_config_summary(self, capsys):
        config = self._make_config(namespaces=2, replicas=3)
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "Workload: cpusoaker" in out
        assert "Namespaces: 2" in out
        assert "Replicas: 3" in out
        assert config.uuid in out
        assert config.basename in out

    def test_dryrun_prints_namespace_list(self, capsys):
        config = self._make_config(namespaces=3)
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "Namespaces (3):" in out

    # -- Basic manifest structure -------------------------------------------

    def test_dryrun_prints_manifests(self, capsys):
        config = self._make_config()
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "kind: Namespace" in out
        assert "kind: ConfigMap" in out
        assert "kind: Pod" in out

    def test_dryrun_no_cluster_contact(self):
        """Dry-run should not instantiate or call ClusterInterface."""
        config = self._make_config()
        with patch("clusterbuster.driver.orchestrator.run") as mock_run:
            _print_dry_run(config)
            mock_run.assert_not_called()

    def test_dryrun_manifest_no_yaml_anchors(self, capsys):
        config = self._make_config()
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "&id0" not in out
        assert "*id0" not in out

    # -- Container image tests -----------------------------------------------

    def test_dryrun_manifest_workloads_image(self, capsys):
        """fio workload pods must use the workloads image."""
        config = self._make_config(workload="fio")
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "image: quay.io/rkrawitz/clusterbuster-workloads:latest" in out

    def test_dryrun_manifest_pausepod_image(self, capsys):
        config = self._make_config(workload="pausepod")
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "image: gcr.io/google_containers/pause-amd64:3.2" in out

    def test_dryrun_manifest_base_image(self, capsys):
        """cpusoaker (no custom image) must use the base image."""
        config = self._make_config(workload="cpusoaker")
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "image: quay.io/rkrawitz/clusterbuster-base:latest" in out

    def test_dryrun_manifest_user_image_override(self, capsys):
        custom = "registry.example.com/custom:v1"
        config = self._make_config(workload="fio", container_image=custom)
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert f"image: {custom}" in out

    # -- Workload command and env --------------------------------------------

    def test_dryrun_manifest_shows_workload_command(self, capsys):
        config = self._make_config(workload="memory", workload_run_time=30)
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "python3" in out
        assert "/var/lib/clusterbuster/memory.py" in out
        assert "--cb-sync-nonce=" in out
        assert "--runtime" in out

    def test_dryrun_manifest_shows_env_vars(self, capsys):
        config = self._make_config()
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "name: VERBOSE" in out
        assert "name: SYSTEM_PODFILE_DIR" in out
        assert "name: USER_PODFILE_DIR" in out
        assert "name: PYTHONPATH" in out

    def test_dryrun_manifest_has_volume_mounts(self, capsys):
        config = self._make_config()
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "mountPath: /var/lib/clusterbuster" in out
        assert "mountPath: /etc/clusterbuster" in out
        assert "name: system-configmap" in out
        assert "name: user-configmap" in out

    # -- Client-server workloads ---------------------------------------------

    def test_dryrun_client_server_shows_both_pods(self, capsys):
        config = self._make_config(workload="uperf")
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "name: clusterbuster-0-uperf-server-0-1" in out
        assert "name: clusterbuster-0-uperf-client-0-1" in out

    def test_dryrun_client_server_shows_actual_commands(self, capsys):
        config = self._make_config(workload="uperf")
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "uperf-server.py" in out
        assert "uperf-client.py" in out
        assert "--listen-port" in out
        assert "--server" in out
        assert "run-workload.sh" not in out

    def test_dryrun_client_server_correct_image(self, capsys):
        config = self._make_config(workload="uperf")
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "image: quay.io/rkrawitz/clusterbuster-workloads:latest" in out

    # -- ConfigMaps with real data -------------------------------------------

    def test_dryrun_system_configmap_has_workload_files(self, capsys):
        """System configmap must contain actual workload file data."""
        config = self._make_config(workload="cpusoaker")
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "file: cb_util.py" in out
        assert "file: sync.py" in out
        assert "file: cpusoaker.py" in out

    def test_dryrun_user_configmap_present(self, capsys):
        config = self._make_config()
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "name: clusterbuster-user-configmap" in out

    # -- Sync objects --------------------------------------------------------

    def test_dryrun_sync_service(self, capsys):
        config = self._make_config(sync_start=True)
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "kind: Service" in out
        assert "name: clusterbuster-sync-0" in out

    def test_dryrun_sync_pod(self, capsys):
        config = self._make_config(sync_start=True)
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "name: clusterbuster-sync" in out
        assert "sync.py" in out

    def test_dryrun_sync_namespace_separate(self, capsys):
        """With default config, sync namespace is separate."""
        config = self._make_config(sync_start=True)
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "name: clusterbuster-sync\n" in out

    def test_dryrun_sync_external_services(self, capsys):
        """Namespaces other than sync NS get ExternalName services."""
        config = self._make_config(namespaces=2, sync_start=True)
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "type: ExternalName" in out

    def test_dryrun_no_sync(self, capsys):
        """When sync is off, no sync objects appear."""
        config = self._make_config(sync_start=False)
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "kind: Service" not in out
        assert "name: clusterbuster-sync\n" not in out

    # -- Multiple namespaces -------------------------------------------------

    def test_dryrun_multiple_namespaces(self, capsys):
        config = self._make_config(namespaces=3)
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "name: clusterbuster-0\n" in out
        assert "name: clusterbuster-1\n" in out
        assert "name: clusterbuster-2\n" in out
        assert out.count("kind: Namespace") == 4  # 3 workload + 1 sync

    def test_dryrun_configmaps_per_namespace(self, capsys):
        """Each workload namespace gets its own system + user configmap."""
        config = self._make_config(namespaces=2, sync_start=False)
        _print_dry_run(config)
        out = capsys.readouterr().out
        docs = out.split("---")
        sys_cms = [d for d in docs if "kind: ConfigMap" in d
                   and "clusterbuster-system-configmap" in d]
        usr_cms = [d for d in docs if "kind: ConfigMap" in d
                   and "clusterbuster-user-configmap" in d]
        assert len(sys_cms) == 2
        assert len(usr_cms) == 2

    # -- Secrets -------------------------------------------------------------

    def test_dryrun_secrets_shown(self, capsys):
        """--secrets=2 produces Secret manifests with real data."""
        config = self._make_config(secrets=2, sync_start=False)
        _print_dry_run(config)
        out = capsys.readouterr().out
        docs = out.split("---")
        secret_docs = [d for d in docs if "kind: Secret" in d]
        assert len(secret_docs) == 2
        assert "secret-clusterbuster-0-0-0" in out
        assert "secret-clusterbuster-0-0-1" in out
        assert "type: Opaque" in out

    def test_dryrun_secrets_contain_base64_data(self, capsys):
        config = self._make_config(secrets=1, sync_start=False)
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "key1:" in out
        assert "key2:" in out

    def test_dryrun_secrets_per_namespace_and_dep(self, capsys):
        """2 ns x 2 deps x 2 secrets = 8 Secret manifests."""
        config = self._make_config(
            namespaces=2, deps_per_namespace=2, secrets=2, sync_start=False,
        )
        _print_dry_run(config)
        out = capsys.readouterr().out
        docs = out.split("---")
        secret_docs = [d for d in docs if "kind: Secret" in d]
        assert len(secret_docs) == 8

    def test_dryrun_no_secrets_when_zero(self, capsys):
        config = self._make_config(secrets=0, sync_start=False)
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "kind: Secret" not in out

    # -- User configmap files ------------------------------------------------

    def test_dryrun_user_configmap_with_files(self, capsys, tmp_path):
        """User-supplied --configmap files appear in user configmap keys."""
        user_file = tmp_path / "my-config.txt"
        user_file.write_text("hello world\n")
        config = self._make_config(
            configmap_files=[str(user_file)], sync_start=False,
        )
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "my-config.txt" in out
        assert "hello world" not in out

    # -- Multiple deployments per namespace ----------------------------------

    def test_dryrun_multiple_deployments(self, capsys):
        config = self._make_config(deps_per_namespace=3, sync_start=False)
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "name: clusterbuster-0-cpusoaker-0" in out
        assert "name: clusterbuster-0-cpusoaker-1" in out
        assert "name: clusterbuster-0-cpusoaker-2" in out

    # -- Multiple replicas with pod deployment type --------------------------

    def test_dryrun_replicas_pod_type(self, capsys):
        """deployment_type=pod with replicas=3 produces 3 separate Pod manifests."""
        config = self._make_config(
            replicas=3, deployment_type="pod", sync_start=False,
        )
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert out.count("kind: Pod") == 3
        assert "name: clusterbuster-0-cpusoaker-0-1" in out
        assert "name: clusterbuster-0-cpusoaker-0-2" in out
        assert "name: clusterbuster-0-cpusoaker-0-3" in out

    def test_dryrun_single_replica_pod(self, capsys):
        """deployment_type=pod with replicas=1 uses per-replica name."""
        config = self._make_config(
            replicas=1, deployment_type="pod", sync_start=False,
        )
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert out.count("kind: Pod") == 1
        assert "name: clusterbuster-0-cpusoaker-0-1\n" in out

    # -- deployment_type=deployment ------------------------------------------

    def test_dryrun_deployment_type(self, capsys):
        """Per-replica Deployments (each replicas: 1), matching bash."""
        config = self._make_config(
            deployment_type="deployment", replicas=3, sync_start=False,
        )
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "kind: Deployment" in out
        assert "replicas: 1" in out
        assert out.count("kind: Deployment") == 3

    # -- deployment_type=replicaset ------------------------------------------

    def test_dryrun_replicaset_type(self, capsys):
        """Per-replica ReplicaSets (each replicas: 1), matching bash."""
        config = self._make_config(
            deployment_type="replicaset", replicas=2, sync_start=False,
        )
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "kind: ReplicaSet" in out
        assert "replicas: 1" in out
        assert out.count("kind: ReplicaSet") == 2

    # -- deployment_type=vm --------------------------------------------------

    def test_dryrun_vm_type(self, capsys):
        """deployment_type=vm must produce VirtualMachine, not Pod."""
        config = self._make_config(
            deployment_type="vm", sync_start=False,
            vm_image="quay.io/containerdisks/fedora:latest",
        )
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert "kind: VirtualMachine" in out
        assert "apiVersion: kubevirt.io/v1" in out
        assert "kind: Pod" not in out
        assert "kind: Deployment" not in out

    def test_dryrun_vm_multiple_deps(self, capsys):
        config = self._make_config(
            deployment_type="vm", deps_per_namespace=2, sync_start=False,
            vm_image="quay.io/containerdisks/fedora:latest",
        )
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert out.count("kind: VirtualMachine") == 2

    # -- Combinations --------------------------------------------------------

    def test_dryrun_multi_ns_multi_dep_pod(self, capsys):
        """2 namespaces x 2 deps x 2 replicas = 8 pods."""
        config = self._make_config(
            namespaces=2, deps_per_namespace=2, replicas=2,
            deployment_type="pod", sync_start=False,
        )
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert out.count("kind: Pod") == 8

    def test_dryrun_multi_ns_deployment_type(self, capsys):
        """2 namespaces x 2 deps with deployment type = 4 Deployments."""
        config = self._make_config(
            namespaces=2, deps_per_namespace=2,
            deployment_type="deployment", sync_start=False,
        )
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert out.count("kind: Deployment") == 4

    def test_dryrun_client_server_replicas_pod(self, capsys):
        """uperf with 2 replicas pod type = 3 pods (1 server + 2 client)."""
        config = self._make_config(
            workload="uperf", replicas=2, deployment_type="pod",
            sync_start=False,
        )
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert out.count("kind: Pod") == 3
        assert "uperf-server.py" in out
        assert "uperf-client.py" in out

    def test_dryrun_client_server_deployment_type(self, capsys):
        """uperf with deployment type: 1 Pod (server, forced) + 1 Deployment (client)."""
        config = self._make_config(
            workload="uperf", deployment_type="deployment", sync_start=False,
        )
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert out.count("kind: Deployment") == 1
        assert out.count("kind: Pod") == 1
        assert "0-uperf-server-0-1" in out
        assert "0-uperf-client-0" in out

    def test_dryrun_all_objects_counted(self, capsys):
        """Verify total manifest count: 2 ns + sync ns + 4 configmaps
        + sync configmap + sync svc + sync pod + 2 ext svcs
        + 4 pods (2 ns x 2 deps) = 16."""
        config = self._make_config(
            namespaces=2, deps_per_namespace=2, sync_start=True,
        )
        _print_dry_run(config)
        out = capsys.readouterr().out
        assert out.count("kind: Namespace") == 3  # 2 workload + sync
        assert out.count("kind: ConfigMap") == 5  # 2 sys + 2 user + sync sys
        assert out.count("kind: Service") == 3    # 1 sync svc + 2 ext
        assert "kind: Pod" in out                  # sync pod + workload pods


class TestDryRunFullPipeline:
    """Full-pipeline dry-run tests exercising workload-specific options,
    volumes, and combinations through ``run_from_argv``.

    Each test captures the complete ``-n`` output so it can be saved
    for manual verification via ``--save-dryrun-output=DIR``.
    """

    _DRYRUN_DIR: Path | None = None

    @pytest.fixture(autouse=True)
    def _reset_workloads(self):
        """Re-instantiate every registered workload after each test so
        that ``process_options`` mutations do not leak between tests."""
        yield
        from clusterbuster.driver.workload_registry import _WORKLOADS
        for name, wl in list(_WORKLOADS.items()):
            _WORKLOADS[name] = type(wl)()

    @classmethod
    def _save(cls, name: str, output: str) -> None:
        """Optionally save dry-run output for manual inspection."""
        d = cls._DRYRUN_DIR
        if d is not None:
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{name}.yaml").write_text(output)

    @staticmethod
    def _run(args: list[str], capsys) -> str:
        from clusterbuster.driver import run_from_argv
        rc = run_from_argv(["-n", *args])
        assert rc == 0, f"dry-run failed for {args}"
        return capsys.readouterr().out

    # -- fio workload-specific options ---------------------------------------

    def test_fio_block_size_and_pattern(self, capsys):
        out = self._run([
            "-w", "fio",
            "--fio-block-size=4k",
            "--fio-pattern=randread",
            "--workload-runtime=30",
        ], capsys)
        self._save("fio_block_pattern", out)
        assert "fio.py" in out
        assert "--blocksizes" in out
        assert "--patterns" in out
        assert "randread" in out
        assert "--runtime" in out
        assert "'30'" in out

    def test_fio_iodepth_and_engine(self, capsys):
        out = self._run([
            "-w", "fio",
            "--fio-io-depth=8",
            "--fio-io-engine=libaio",
            "--fio-direct=1",
            "--workload-runtime=60",
        ], capsys)
        self._save("fio_iodepth_engine", out)
        assert "--iodepths" in out
        assert "8" in out
        assert "--ioengines" in out
        assert "libaio" in out
        assert "--directs" in out

    def test_fio_ramp_time(self, capsys):
        out = self._run([
            "-w", "fio",
            "--fio-ramp-time=5",
        ], capsys)
        self._save("fio_ramp", out)
        assert "--ramptime" in out
        assert "'5'" in out

    # -- uperf workload-specific options -------------------------------------

    def test_uperf_msg_size_and_test_type(self, capsys):
        out = self._run([
            "-w", "uperf",
            "--uperf-msg-size=2048",
            "--uperf-test-type=rr",
            "--uperf-protocol=udp",
        ], capsys)
        self._save("uperf_msg_rr_udp", out)
        assert "uperf-server.py" in out
        assert "uperf-client.py" in out
        assert "--test" in out
        assert "rr,udp,2048,1" in out
        assert "--listen-port" in out

    def test_uperf_nthr_and_ramp(self, capsys):
        out = self._run([
            "-w", "uperf",
            "--uperf-msg-size=1024",
            "--uperf-test-type=stream",
            "--uperf-protocol=tcp",
            "--uperf-nthr=4",
            "--uperf-ramp-time=5",
        ], capsys)
        self._save("uperf_nthr_ramp", out)
        assert "--ramp-time" in out
        assert "'5'" in out
        assert "stream,tcp,1024,4" in out

    def test_uperf_multiple_tests_expanded(self, capsys):
        """Multiple msg sizes x test types should produce multiple --test args."""
        out = self._run([
            "-w", "uperf",
            "--uperf-msg-size=512,2048",
            "--uperf-test-type=stream,rr",
            "--uperf-protocol=tcp",
            "--uperf-nthr=1",
        ], capsys)
        self._save("uperf_multi_tests", out)
        assert "stream,tcp,512,1" in out
        assert "stream,tcp,2048,1" in out
        assert "rr,tcp,512,1" in out
        assert "rr,tcp,2048,1" in out

    # -- memory workload-specific options ------------------------------------

    def test_memory_size_and_scan(self, capsys):
        out = self._run([
            "-w", "memory",
            "--memory-size=512M",
            "--memory-scan=reverse",
        ], capsys)
        self._save("memory_size_scan", out)
        assert "memory.py" in out
        assert "--memory-size" in out
        assert "512000000" in out
        assert "--scan" in out

    def test_memory_iterations_and_stride(self, capsys):
        out = self._run([
            "-w", "memory",
            "--memory-iterations=100",
            "--memory-stride=64",
        ], capsys)
        self._save("memory_iter_stride", out)
        assert "--iterations" in out
        assert "--stride" in out
        assert "'64'" in out

    # -- sysbench workload-specific options ----------------------------------

    def test_sysbench_workload_fileio(self, capsys):
        out = self._run([
            "-w", "sysbench",
            "--sysbench-workload=fileio",
        ], capsys)
        self._save("sysbench_fileio", out)
        assert "sysbench.py" in out
        assert "--workload" in out
        assert "fileio" in out

    # -- server workload-specific options ------------------------------------

    def test_server_msg_size(self, capsys):
        out = self._run([
            "-w", "server",
            "--msg-size=4096",
        ], capsys)
        self._save("server_msgsize", out)
        assert "server.py" in out
        assert "client.py" in out
        assert "--msg-size" in out
        assert "'4096'" in out

    # -- PVC volumes ---------------------------------------------------------

    def test_pvc_volume(self, capsys):
        out = self._run([
            "-w", "cpusoaker",
            "--volume=mydata:pvc:/data",
            "--sync=0",
        ], capsys)
        self._save("vol_pvc", out)
        assert "persistentVolumeClaim:" in out
        assert "claimName: mydata" in out
        assert "mountPath: /data" in out
        assert "name: mydata" in out

    def test_emptydir_volume(self, capsys):
        out = self._run([
            "-w", "cpusoaker",
            "--volume=scratch:emptydir:/scratch",
            "--sync=0",
        ], capsys)
        self._save("vol_emptydir", out)
        assert "name: scratch" in out
        assert "mountPath: /scratch" in out
        assert "emptyDir:" in out

    def test_emptydisk_volume(self, capsys):
        out = self._run([
            "-w", "cpusoaker",
            "--volume=fast:emptydisk:/fast:size=10Gi",
            "--sync=0",
        ], capsys)
        self._save("vol_emptydisk", out)
        assert "name: fast" in out
        assert "mountPath: /fast" in out

    def test_multiple_volumes(self, capsys):
        out = self._run([
            "-w", "cpusoaker",
            "--volume=db:pvc:/database",
            "--volume=cache:emptydir:/cache",
            "--sync=0",
        ], capsys)
        self._save("vol_multi", out)
        assert "claimName: db" in out
        assert "mountPath: /database" in out
        assert "mountPath: /cache" in out
        assert "name: cache" in out

    def test_pvc_volume_in_deployment(self, capsys):
        """PVC volumes appear correctly in Deployment manifests."""
        out = self._run([
            "-w", "cpusoaker",
            "--volume=data:pvc:/mnt/data",
            "--deployment-type=deployment",
            "--sync=0",
        ], capsys)
        self._save("vol_pvc_deployment", out)
        assert "kind: Deployment" in out
        assert "persistentVolumeClaim:" in out
        assert "claimName: data" in out

    def test_pvc_volume_in_vm(self, capsys):
        """PVC volumes appear as persistentVolumeClaim in VM manifests."""
        out = self._run([
            "-w", "cpusoaker",
            "--volume=dbvol:pvc:/mnt/db",
            "--deployment-type=vm",
            "--sync=0",
        ], capsys)
        self._save("vol_pvc_vm", out)
        assert "kind: VirtualMachine" in out
        assert "persistentVolumeClaim:" in out
        assert "claimName: dbvol" in out

    # -- Volume options --------------------------------------------------------

    def test_pvc_claimname_override(self, capsys):
        """PVC with claimName= uses the override as the claim name."""
        out = self._run([
            "-w", "cpusoaker",
            "--volume=myvol:pvc:/data:claimName=real-pvc-name",
            "--sync=0",
        ], capsys)
        self._save("vol_pvc_claimname", out)
        assert "claimName: real-pvc-name" in out
        assert "name: myvol" in out
        assert "mountPath: /data" in out

    def test_pvc_readonly(self, capsys):
        """PVC with readOnly=true sets readOnly on the volume."""
        out = self._run([
            "-w", "cpusoaker",
            "--volume=rodata:pvc:/readonly:readOnly=true",
            "--sync=0",
        ], capsys)
        self._save("vol_pvc_readonly", out)
        assert "claimName: rodata" in out
        assert "readOnly: true" in out

    def test_vm_pvc_claimname(self, capsys):
        """VM PVC uses claimName= override."""
        out = self._run([
            "-w", "cpusoaker",
            "--volume=dbvol:pvc:/db:claimName=shared-pvc",
            "--deployment-type=vm",
            "--sync=0",
        ], capsys)
        self._save("vol_vm_pvc_claimname", out)
        assert "kind: VirtualMachine" in out
        assert "claimName: shared-pvc" in out

    def test_vm_disk_bus(self, capsys):
        """VM PVC disk with bus=sata uses sata bus."""
        out = self._run([
            "-w", "cpusoaker",
            "--volume=ssd:pvc:/ssd:bus=sata",
            "--deployment-type=vm",
            "--sync=0",
        ], capsys)
        self._save("vol_vm_disk_bus", out)
        assert "kind: VirtualMachine" in out
        assert "bus: sata" in out

    def test_vm_disk_cache(self, capsys):
        """VM PVC disk with cache=writeback."""
        out = self._run([
            "-w", "cpusoaker",
            "--volume=fast:pvc:/fast:cache=writeback",
            "--deployment-type=vm",
            "--sync=0",
        ], capsys)
        self._save("vol_vm_disk_cache", out)
        assert "cache: writeback" in out

    def test_vm_disk_dedicated_io_thread(self, capsys):
        """VM PVC disk with dedicatedIOThread=true."""
        out = self._run([
            "-w", "cpusoaker",
            "--volume=iodisk:pvc:/io:dedicatedIOThread=true",
            "--deployment-type=vm",
            "--sync=0",
        ], capsys)
        self._save("vol_vm_disk_dio", out)
        assert "dedicatedIOThread: true" in out

    def test_vm_disk_combined_options(self, capsys):
        """VM PVC disk with bus, cache, and dedicatedIOThread combined."""
        out = self._run([
            "-w", "cpusoaker",
            "--volume=bigdisk:pvc:/big:bus=sata:cache=none:dedicatedIOThread=true:claimName=shared",
            "--deployment-type=vm",
            "--sync=0",
        ], capsys)
        self._save("vol_vm_disk_combined", out)
        assert "bus: sata" in out
        assert "cache: none" in out
        assert "dedicatedIOThread: true" in out
        assert "claimName: shared" in out

    def test_vm_emptydisk_fstype(self, capsys):
        """VM emptydisk with fstype=xfs uses xfs -f in cloud-init."""
        out = self._run([
            "-w", "cpusoaker",
            "--volume=scratch:emptydisk:/scratch:size=5Gi:fstype=xfs",
            "--deployment-type=vm",
            "--sync=0",
        ], capsys)
        self._save("vol_vm_emptydisk_xfs", out)
        assert "mkfs.xfs" in out
        assert "-f" in out

    def test_vm_emptydisk_ext4_inodes(self, capsys):
        """VM emptydisk with ext4 + inodes uses -N flag."""
        out = self._run([
            "-w", "cpusoaker",
            "--volume=data:emptydisk:/data:size=10Gi:fstype=ext4:inodes=100000",
            "--deployment-type=vm",
            "--sync=0",
        ], capsys)
        self._save("vol_vm_emptydisk_inodes", out)
        assert "mkfs.ext4" in out
        assert "100000" in out

    def test_vm_pvc_mountopts(self, capsys):
        """VM PVC with mountopts shows mount options in cloud-init."""
        out = self._run([
            "-w", "cpusoaker",
            "--volume=stor:pvc:/storage:mountopts=-o noatime",
            "--deployment-type=vm",
            "--sync=0",
        ], capsys)
        self._save("vol_vm_pvc_mountopts", out)
        assert "noatime" in out
        assert "mount" in out

    def test_vm_pvc_nfs(self, capsys):
        """VM PVC with nfssrv/nfsshare uses NFS mount in cloud-init."""
        out = self._run([
            "-w", "cpusoaker",
            "--volume=nfsvol:pvc:/nfs:nfssrv=192.168.1.100:nfsshare=/exports/data",
            "--deployment-type=vm",
            "--sync=0",
        ], capsys)
        self._save("vol_vm_nfs", out)
        assert "192.168.1.100:/exports/data" in out
        assert "mount" in out

    # -- Volume name template expansion (%N, %i, %r) -------------------------

    def test_pvc_percent_r_pod_multi_replica(self, capsys):
        """Bare-pod with %r in PVC name expands per-replica."""
        out = self._run([
            "-w", "cpusoaker",
            "--volume=data-%r:pvc:/mnt/data:claimName=pvc-%N-%i-%r",
            "--deployment-type=pod",
            "--replicas=3",
            "--sync=0",
        ], capsys)
        self._save("vol_pvc_percent_r", out)
        for r in ("1", "2", "3"):
            assert f"claimName: pvc-clusterbuster-0-0-{r}" in out, (
                f"replica {r} should have expanded %r in claimName"
            )

    def test_pvc_percent_r_volume_mount_name(self, capsys):
        """Bare-pod with %r in volume name expands in volumeMount."""
        out = self._run([
            "-w", "cpusoaker",
            "--volume=vol-%r:pvc:/mnt/data",
            "--deployment-type=pod",
            "--replicas=2",
            "--sync=0",
        ], capsys)
        self._save("vol_pvc_percent_r_mount", out)
        assert "name: vol-1" in out
        assert "name: vol-2" in out

    def test_pvc_percent_N_percent_i(self, capsys):
        """%N and %i expand in PVC claimName."""
        out = self._run([
            "-w", "cpusoaker",
            "--volume=data:pvc:/mnt/data:claimName=pvc-%N-%i",
            "--sync=0",
        ], capsys)
        self._save("vol_pvc_percent_N_i", out)
        assert "claimName: pvc-clusterbuster-0-0" in out

    # -- Combinations: workload args + topology + volumes --------------------

    def test_fio_with_pvc_multi_ns(self, capsys):
        """fio with PVC volume, 2 namespaces, 2 replicas."""
        out = self._run([
            "-w", "fio",
            "--fio-block-size=8k",
            "--fio-pattern=write",
            "--volume=storage:pvc:/mnt/storage",
            "--namespaces=2",
            "--replicas=2",
            "--workload-runtime=60",
        ], capsys)
        self._save("fio_pvc_multi_ns", out)
        assert "fio.py" in out
        assert "write" in out
        assert "persistentVolumeClaim:" in out
        assert "claimName: storage" in out
        ns_count = out.count("kind: Namespace")
        assert ns_count == 3  # 2 workload + sync
        # fio defaults to drop_cache=1, so 2 ns x 1 dep x 2 replicas = 4 dc pods
        assert "privileged: true" in out
        assert out.count("kind: Pod") >= 8  # 4 workload + 4 dc + sync

    def test_uperf_deployment_type_multi_dep(self, capsys):
        """uperf with deployment type and multiple deps."""
        out = self._run([
            "-w", "uperf",
            "--uperf-msg-size=4096",
            "--deployment-type=deployment",
            "--deployments=2",
            "--sync=0",
        ], capsys)
        self._save("uperf_dep_type_multi", out)
        assert out.count("kind: Deployment") == 2  # 2 deps x client only
        assert out.count("kind: Pod") >= 2  # 2 server pods forced to Pod
        assert "uperf-server.py" in out
        assert "uperf-client.py" in out

    def test_memory_replicaset_with_volume(self, capsys):
        """memory workload with replicaset type and emptydir volume."""
        out = self._run([
            "-w", "memory",
            "--memory-size=256M",
            "--deployment-type=replicaset",
            "--replicas=3",
            "--volume=workspace:emptydir:/workspace",
            "--sync=0",
        ], capsys)
        self._save("memory_rs_vol", out)
        assert "kind: ReplicaSet" in out
        assert out.count("kind: ReplicaSet") == 3
        assert "replicas: 1" in out
        assert "memory.py" in out
        assert "256000000" in out
        assert "mountPath: /workspace" in out

    # -- Drop-cache pods -----------------------------------------------------

    def test_fio_drop_cache_pod(self, capsys):
        """fio with --fio-drop-cache=1 produces a privileged drop-cache pod."""
        out = self._run([
            "-w", "fio",
            "--fio-drop-cache=1",
            "--sync=0",
        ], capsys)
        self._save("fio_drop_cache", out)
        assert "drop_cache.py" in out
        assert "privileged: true" in out
        assert "proc-sys-vm" in out
        assert "path: /proc/sys/vm" in out
        docs = out.split("---")
        dc_docs = [d for d in docs if "privileged: true" in d]
        assert len(dc_docs) == 1

    def test_drop_cache_per_replica(self, capsys):
        """Drop-cache pods scale with replicas: 2 replicas = 2 dc pods."""
        out = self._run([
            "-w", "fio",
            "--fio-drop-cache=1",
            "--replicas=2",
            "--sync=0",
        ], capsys)
        self._save("fio_drop_cache_replicas", out)
        docs = out.split("---")
        dc_docs = [d for d in docs if "privileged: true" in d]
        assert len(dc_docs) == 2
        assert "0-fio-0-1-dc" in out
        assert "0-fio-0-2-dc" in out

    def test_drop_cache_multi_ns_multi_dep(self, capsys):
        """2 ns x 2 deps x 1 replica = 4 dc pods."""
        out = self._run([
            "-w", "fio",
            "--fio-drop-cache=1",
            "--namespaces=2",
            "--deployments=2",
            "--sync=0",
        ], capsys)
        self._save("fio_drop_cache_multi", out)
        docs = out.split("---")
        dc_docs = [d for d in docs if "privileged: true" in d]
        assert len(dc_docs) == 4

    def test_no_drop_cache_when_not_needed(self, capsys):
        """cpusoaker does not produce drop-cache pods."""
        out = self._run(["-w", "cpusoaker", "--sync=0"], capsys)
        assert "privileged: true" not in out
        assert "proc-sys-vm" not in out

    def test_sysbench_fileio_drop_cache(self, capsys):
        """sysbench fileio workload requires drop-cache."""
        out = self._run([
            "-w", "sysbench",
            "--sysbench-workload=fileio",
            "--sync=0",
        ], capsys)
        self._save("sysbench_fileio_dc", out)
        assert "privileged: true" in out
        assert "drop_cache.py" in out

    # -- Pin nodes -------------------------------------------------------------

    def test_pin_node_default(self, capsys):
        """--pin-node=worker-0 pins all pods to that node."""
        out = self._run([
            "-w", "cpusoaker",
            "--pin-node=worker-0.example.com",
            "--sync=0",
        ], capsys)
        self._save("pin_node_default", out)
        assert "kubernetes.io/hostname: worker-0.example.com" in out
        assert "nodeSelector:" in out

    def test_pin_node_class(self, capsys):
        """--pin-node=server=node-a,client=node-b pins by class."""
        out = self._run([
            "-w", "uperf",
            "--pin-node=server=node-a.example.com",
            "--pin-node=client=node-b.example.com",
            "--sync=0",
        ], capsys)
        self._save("pin_node_class", out)
        assert "node-a.example.com" in out
        assert "node-b.example.com" in out
        assert "kubernetes.io/hostname" in out

    def test_pin_node_with_sync(self, capsys):
        """Sync pod picks up default pin-node or sync-specific pin."""
        out = self._run([
            "-w", "cpusoaker",
            "--pin-node=sync=sync-node.example.com",
            "--pin-node=worker-0.example.com",
        ], capsys)
        self._save("pin_node_sync", out)
        assert "sync-node.example.com" in out
        assert "worker-0.example.com" in out

    def test_pin_node_vm(self, capsys):
        """VMs respect --pin-node."""
        out = self._run([
            "-w", "cpusoaker",
            "--deployment-type=vm",
            "--pin-node=vm-host.example.com",
            "--sync=0",
        ], capsys)
        self._save("pin_node_vm", out)
        assert "kubernetes.io/hostname: vm-host.example.com" in out
        assert "kind: VirtualMachine" in out

    def test_no_pin_node_shows_worker_selector(self, capsys):
        """Without --pin-node, only node-role selector is present."""
        out = self._run(["-w", "cpusoaker", "--sync=0"], capsys)
        assert "node-role.kubernetes.io/worker" in out
        assert "kubernetes.io/hostname" not in out

    # -- Affinity / anti-affinity ---------------------------------------------

    def test_affinity(self, capsys):
        """--affinity produces podAffinity in pod spec."""
        out = self._run([
            "-w", "cpusoaker",
            "--affinity",
            "--sync=0",
        ], capsys)
        self._save("affinity", out)
        assert "podAffinity:" in out
        assert "requiredDuringSchedulingIgnoredDuringExecution" in out
        assert "topologyKey: kubernetes.io/hostname" in out
        assert "podAntiAffinity" not in out

    def test_anti_affinity(self, capsys):
        """--anti-affinity produces podAntiAffinity."""
        out = self._run([
            "-w", "cpusoaker",
            "--anti-affinity",
            "--sync=0",
        ], capsys)
        self._save("anti_affinity", out)
        assert "podAntiAffinity:" in out
        assert "requiredDuringSchedulingIgnoredDuringExecution" in out
        assert "podAffinity:" not in out

    def test_no_affinity_default(self, capsys):
        """Without --affinity, no affinity block appears."""
        out = self._run(["-w", "cpusoaker", "--sync=0"], capsys)
        assert "podAffinity" not in out
        assert "podAntiAffinity" not in out

    def test_sync_affinity(self, capsys):
        """--sync-affinity adds affinity to the sync pod."""
        out = self._run([
            "-w", "cpusoaker",
            "--sync-affinity",
        ], capsys)
        self._save("sync_affinity", out)
        docs = out.split("---")
        sync_docs = [d for d in docs if "sync-pod" in d.lower() or "sync.py" in d]
        assert any("podAffinity:" in d for d in sync_docs)

    def test_sync_anti_affinity(self, capsys):
        """--sync-anti-affinity adds anti-affinity to the sync pod."""
        out = self._run([
            "-w", "cpusoaker",
            "--sync-anti-affinity",
        ], capsys)
        self._save("sync_anti_affinity", out)
        docs = out.split("---")
        sync_docs = [d for d in docs if "sync-pod" in d.lower() or "sync.py" in d]
        assert any("podAntiAffinity:" in d for d in sync_docs)

    def test_affinity_with_pin_node(self, capsys):
        """Affinity and pin-node coexist in the same pod spec."""
        out = self._run([
            "-w", "cpusoaker",
            "--affinity",
            "--pin-node=worker-1.example.com",
            "--sync=0",
        ], capsys)
        self._save("affinity_pin_node", out)
        assert "podAffinity:" in out
        assert "kubernetes.io/hostname: worker-1.example.com" in out

    def test_affinity_multi_ns(self, capsys):
        """Affinity applies to all namespaces."""
        out = self._run([
            "-w", "cpusoaker",
            "--anti-affinity",
            "--namespaces=2",
            "--sync=0",
        ], capsys)
        self._save("affinity_multi_ns", out)
        docs = out.split("---")
        pod_docs = [d for d in docs if "kind: Pod" in d]
        for doc in pod_docs:
            assert "podAntiAffinity:" in doc

    def test_affinity_deployment_type(self, capsys):
        """Affinity works with Deployment type."""
        out = self._run([
            "-w", "cpusoaker",
            "--affinity",
            "--deployment-type=deployment",
            "--sync=0",
        ], capsys)
        self._save("affinity_deployment", out)
        assert "kind: Deployment" in out
        assert "podAffinity:" in out

    def test_affinity_replicaset_type(self, capsys):
        """Affinity works with ReplicaSet type."""
        out = self._run([
            "-w", "cpusoaker",
            "--anti-affinity",
            "--deployment-type=replicaset",
            "--sync=0",
        ], capsys)
        self._save("affinity_replicaset", out)
        assert "kind: ReplicaSet" in out
        assert "podAntiAffinity:" in out

    def test_pin_node_drop_cache(self, capsys):
        """Drop-cache pods respect --pin-node."""
        out = self._run([
            "-w", "fio",
            "--fio-drop-cache=1",
            "--pin-node=worker-0.example.com",
            "--sync=0",
        ], capsys)
        self._save("pin_node_drop_cache", out)
        docs = out.split("---")
        dc_docs = [d for d in docs if "privileged: true" in d]
        assert len(dc_docs) >= 1
        for d in dc_docs:
            assert "kubernetes.io/hostname: worker-0.example.com" in d


class TestDryRunKata:
    """Verify kata runtime class dry-run output."""

    @pytest.fixture(autouse=True)
    def _reset_workloads(self):
        """Re-instantiate every registered workload after each test so
        that ``process_options`` mutations do not leak between tests."""
        yield
        from clusterbuster.driver.workload_registry import _WORKLOADS
        for name, wl in list(_WORKLOADS.items()):
            _WORKLOADS[name] = type(wl)()

    @staticmethod
    def _run(args: list[str], capsys) -> str:
        from clusterbuster.driver import run_from_argv
        rc = run_from_argv(["-n", *args])
        assert rc == 0, f"dry-run failed for {args}"
        return capsys.readouterr().out

    def test_kata_runtime_class_in_pod(self, capsys):
        """--kata sets runtimeClassName on pod spec."""
        out = self._run([
            "-w", "cpusoaker",
            "--kata",
            "--sync=0",
        ], capsys)
        assert "runtimeClassName: kata" in out

    def test_kata_virtiofsd_annotation(self, capsys):
        """--kata injects the virtiofsd annotation with compact JSON."""
        out = self._run([
            "-w", "cpusoaker",
            "--kata",
            "--sync=0",
        ], capsys)
        assert "io.katacontainers.config.hypervisor.virtio_fs_extra_args" in out
        assert '"-o"' in out
        assert '"allow_direct_io"' in out

    def test_kata_virtiofsd_compact_json(self, capsys):
        """Annotation value has no spaces after separators."""
        out = self._run([
            "-w", "cpusoaker",
            "--kata",
            "--sync=0",
        ], capsys)
        for line in out.splitlines():
            if "virtio_fs_extra_args" in line:
                val = line.split(":", 1)[1].strip()
                assert ", " not in val
                break

    def test_kata_with_writeback(self, capsys):
        """--kata --virtiofsd-writeback includes writeback in annotation."""
        out = self._run([
            "-w", "cpusoaker",
            "--kata",
            "--virtiofsd-writeback=1",
            "--sync=0",
        ], capsys)
        assert '"writeback"' in out

    def test_kata_with_threadpool(self, capsys):
        out = self._run([
            "-w", "cpusoaker",
            "--kata",
            "--virtiofsd-threadpoolsize=4",
            "--sync=0",
        ], capsys)
        assert '"--thread-pool-size=4"' in out

    def test_kata_with_fio(self, capsys):
        """FIO under kata gets both workload args and kata annotations."""
        out = self._run([
            "-w", "fio",
            "--kata",
            "--fio-block-size=4k",
            "--sync=0",
        ], capsys)
        assert "runtimeClassName: kata" in out
        assert "fio.py" in out
        assert "io.katacontainers.config.hypervisor.virtio_fs_extra_args" in out

    def test_kata_with_uperf(self, capsys):
        """uperf (client-server) under kata: both pods get kata annotation."""
        out = self._run([
            "-w", "uperf",
            "--kata",
            "--sync=0",
        ], capsys)
        assert "runtimeClassName: kata" in out
        assert "uperf-server.py" in out
        assert "uperf-client.py" in out


class TestMainEntryPoints:
    """Verify main(), run_from_argv(), run_clusterbuster()."""

    def test_main_dryrun(self, capsys):
        """main() with -n prints dry-run and exits 0."""
        from clusterbuster.driver import main
        with patch.object(sys, "argv", ["clusterbuster", "-n", "-w", "cpusoaker"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "Workload: cpusoaker" in out

    def test_main_calls_orchestrator(self):
        """main() without -n calls orchestrator.run()."""
        from clusterbuster.driver import main
        with patch.object(sys, "argv", ["clusterbuster", "-w", "cpusoaker"]):
            with patch("clusterbuster.driver.orchestrator.run", return_value=0) as mock_run:
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 0
                mock_run.assert_called_once()

    def test_run_from_argv_delegates(self):
        from clusterbuster.driver import run_from_argv
        with patch("clusterbuster.driver.orchestrator.run", return_value=0) as mock_run:
            rc = run_from_argv(["-w", "cpusoaker"])
            assert rc == 0
            mock_run.assert_called_once()

    def test_run_from_argv_dryrun(self, capsys):
        from clusterbuster.driver import run_from_argv
        rc = run_from_argv(["-n", "-w", "cpusoaker"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Workload: cpusoaker" in out

    def test_run_clusterbuster_with_config(self, capsys):
        from clusterbuster.driver import run_clusterbuster
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "cpusoaker"
        b.doit = False
        from clusterbuster.driver import _build_with_registry
        config = _build_with_registry(b)
        rc = run_clusterbuster(config=config)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Workload: cpusoaker" in out


class TestLauncher:

    def test_launcher_exists_and_executable(self):
        launcher = _REPO / "clusterbuster"
        assert launcher.exists(), "Python launcher should exist"
        assert launcher.stat().st_mode & stat.S_IXUSR, "Launcher should be executable"

    def test_launcher_is_python(self):
        launcher = _REPO / "clusterbuster"
        first_line = launcher.read_text().splitlines()[0]
        assert "python" in first_line, "Launcher shebang should reference python"

    def test_bash_script_renamed(self):
        bash = _REPO / "clusterbuster.sh"
        assert bash.exists(), "Bash script should be renamed to clusterbuster.sh"


# ---------------------------------------------------------------------------
# Fixture: isolate workload singleton state for build() tests (O-3)
# ---------------------------------------------------------------------------

@pytest.fixture
def _isolate_workloads():
    """Save and restore all workload singleton __dict__ around a test.

    Workload plugins are module-level singletons.  ``builder.build()``
    via ``process_options`` mutates their instance attributes.  This
    fixture deep-copies every registered workload's state before the test
    and restores it afterwards, preventing cross-test pollution.
    """
    from clusterbuster.driver.workload_registry import all_workloads

    saved: list[tuple[object, dict]] = []
    for wl in all_workloads():
        saved.append((wl, copy.deepcopy(wl.__dict__)))

    yield

    for wl, state in saved:
        wl.__dict__.clear()
        wl.__dict__.update(state)
