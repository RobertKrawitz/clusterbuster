# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""Unit tests for clusterbuster.driver.config — builder + build() validation."""

from __future__ import annotations

import pytest

from clusterbuster.driver.config import (
    ClusterbusterConfigBuilder,
    ClusterbusterConfig,
    ConfigError,
)


class TestBuilderDefaults:
    def test_default_basename_from_env(self, monkeypatch):
        monkeypatch.setenv("CLUSTERBUSTER_DEFAULT_BASENAME", "mybase")
        b = ClusterbusterConfigBuilder()
        assert b.basename == "mybase"

    def test_default_basename_fallback(self, monkeypatch):
        monkeypatch.delenv("CLUSTERBUSTER_DEFAULT_BASENAME", raising=False)
        b = ClusterbusterConfigBuilder()
        assert b.basename == "clusterbuster"

    def test_default_values(self):
        b = ClusterbusterConfigBuilder()
        assert b.namespaces == 1
        assert b.replicas == 1
        assert b.deployment_type == "pod"
        assert b.sync_start is True
        assert b.doit is True
        assert b.verbose is False
        assert b.cleanup is False
        assert b.cleanup_always is False


class TestBuildValidation:
    def _builder(self, **overrides) -> ClusterbusterConfigBuilder:
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "cpusoaker"
        for k, v in overrides.items():
            setattr(b, k, v)
        return b

    def test_build_success(self):
        cfg = self._builder().build()
        assert isinstance(cfg, ClusterbusterConfig)
        assert cfg.resolved_workload == "cpusoaker"
        assert cfg.deployment_type == "pod"
        assert cfg.uuid  # auto-generated
        assert cfg.xuuid  # auto-generated, independent of uuid
        assert cfg.xuuid != cfg.uuid

    def test_build_requires_workload(self):
        b = ClusterbusterConfigBuilder()
        with pytest.raises(ConfigError, match="Workload must be specified"):
            b.build()

    def test_build_normalizes_deployment_type(self):
        cfg = self._builder(deployment_type="Pod").build()
        assert cfg.deployment_type == "pod"

    def test_build_vm_keeps_exit_at_end_true(self):
        cfg = self._builder(deployment_type="vm").build()
        assert cfg.exit_at_end is True
        assert cfg.runtime_class == "vm"

    def test_build_deployment_forces_exit_at_end_false(self):
        cfg = self._builder(deployment_type="Deployment").build()
        assert cfg.exit_at_end is False

    def test_build_invalid_deployment_type(self):
        with pytest.raises(ConfigError, match="Invalid deployment type"):
            self._builder(deployment_type="invalid").build()

    def test_build_vm_containers_must_be_1(self):
        with pytest.raises(ConfigError, match="containers_per_pod must be 1"):
            self._builder(deployment_type="vm", containers_per_pod=2).build()

    def test_build_parallelism_defaults(self):
        cfg = self._builder(parallel=4).build()
        assert cfg.parallel_secrets == 4
        assert cfg.parallel_configmaps == 4
        assert cfg.parallel_namespaces == 4
        assert cfg.parallel_deployments == 4

    def test_build_parallelism_explicit_override(self):
        cfg = self._builder(parallel=4, parallel_secrets=8).build()
        assert cfg.parallel_secrets == 8
        assert cfg.parallel_configmaps == 4

    def test_build_objs_per_call_defaults(self):
        cfg = self._builder(objs_per_call=10).build()
        assert cfg.objs_per_call_secrets == 10

    def test_build_pod_start_timeout_pod(self):
        cfg = self._builder(deployment_type="pod").build()
        assert cfg.pod_start_timeout == 60

    def test_build_pod_start_timeout_vm(self):
        cfg = self._builder(deployment_type="vm").build()
        assert cfg.pod_start_timeout == 180

    def test_build_pod_start_timeout_explicit(self):
        cfg = self._builder(pod_start_timeout=300).build()
        assert cfg.pod_start_timeout == 300

    def test_build_container_image_defaults(self):
        cfg = self._builder().build()
        assert cfg.container_image == cfg.clusterbuster_base_image

    def test_build_container_image_explicit(self):
        cfg = self._builder(container_image="my:image").build()
        assert cfg.container_image == "my:image"

    def test_build_uuid_explicit(self):
        cfg = self._builder(uuid="my-uuid-123").build()
        assert cfg.uuid == "my-uuid-123"
        assert cfg.xuuid != "my-uuid-123"  # xuuid is always fresh

    def test_build_virtiofsd_args(self):
        cfg = self._builder(
            virtiofsd_writeback=True,
            virtiofsd_direct=True,
            virtiofsd_threadpoolsize=4,
        ).build()
        assert cfg.virtiofsd_args == [
            "-o", "allow_direct_io",
            "-o", "writeback",
            "--thread-pool-size=4",
        ]

    def test_build_deployment_type_alias_rs(self):
        cfg = self._builder(deployment_type="rs").build()
        assert cfg.deployment_type == "replicaset"
        assert cfg.exit_at_end is False

    def test_build_deployment_type_alias_dep(self):
        cfg = self._builder(deployment_type="dep").build()
        assert cfg.deployment_type == "deployment"

    def test_build_deployment_type_alias_deploy(self):
        cfg = self._builder(deployment_type="deploy").build()
        assert cfg.deployment_type == "deployment"

    def test_build_sync_watchdog_port(self):
        cfg = self._builder(sync_watchdog_timeout=30).build()
        assert cfg.sync_watchdog_port == 7780

    def test_build_sync_watchdog_port_disabled(self):
        cfg = self._builder(sync_watchdog_timeout=0).build()
        assert cfg.sync_watchdog_port == 0

    def test_build_command_line(self):
        cfg = self._builder().build(command_line=["clusterbuster", "--workload=fio"])
        assert cfg.command_line == ["clusterbuster", "--workload=fio"]

    def test_build_unknown_opts_error(self):
        b = self._builder()
        b.unknown_opts = ["badopt"]
        with pytest.raises(ConfigError, match="Unknown option"):
            b.build()


class TestResourceValidation:
    def _builder(self, **overrides) -> ClusterbusterConfigBuilder:
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "cpusoaker"
        for k, v in overrides.items():
            setattr(b, k, v)
        return b

    def test_valid_resources(self):
        cfg = self._builder(
            resource_requests=["cpu=100m", "memory=256Mi"],
            resource_limits=["cpu=1", "memory=1Gi"],
        ).build()
        assert cfg.resource_requests == ["cpu=100m", "memory=256Mi"]

    def test_invalid_resource_request(self):
        with pytest.raises(ConfigError, match="Invalid resource request"):
            self._builder(resource_requests=["cpu100m"]).build()

    def test_invalid_resource_limit(self):
        with pytest.raises(ConfigError, match="Invalid resource limit"):
            self._builder(resource_limits=["badlimit"]).build()


class TestVolumeValidation:
    def _builder(self, **overrides) -> ClusterbusterConfigBuilder:
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "cpusoaker"
        for k, v in overrides.items():
            setattr(b, k, v)
        return b

    def test_valid_volumes(self):
        cfg = self._builder(
            volumes=["data:emptydir:/mnt/data", "disk1:pvc:/mnt/disk1"]
        ).build()
        assert len(cfg.volumes) == 2

    def test_invalid_volume_type(self):
        with pytest.raises(ConfigError, match="Invalid volume type"):
            self._builder(volumes=["bad:nfs:/mnt/bad"]).build()

    def test_emptydisk_needs_size(self):
        with pytest.raises(ConfigError, match="positive size"):
            self._builder(volumes=["disk:emptydisk:/mnt/disk"]).build()

    def test_emptydisk_with_size(self):
        cfg = self._builder(
            volumes=["disk:emptydisk:/mnt/disk:size=1Gi"]
        ).build()
        assert "disk:emptydisk:/mnt/disk:size=1Gi" in cfg.volumes

    def test_duplicate_name_removed(self):
        cfg = self._builder(
            volumes=["data:emptydir:/mnt/data", "data:pvc:/mnt/data"]
        ).build()
        assert len(cfg.volumes) == 1

    def test_too_few_parts(self):
        with pytest.raises(ConfigError, match="at least name:type:mountpoint"):
            self._builder(volumes=["onlyname"]).build()

    def test_two_parts_rejected(self):
        with pytest.raises(ConfigError, match="at least name:type:mountpoint"):
            self._builder(volumes=["name:emptydir"]).build()


class TestAarch64VmGuard:
    def _builder(self, **overrides) -> ClusterbusterConfigBuilder:
        b = ClusterbusterConfigBuilder()
        b.requested_workload = "cpusoaker"
        for k, v in overrides.items():
            setattr(b, k, v)
        return b

    def test_vm_on_aarch64_blocked(self, monkeypatch):
        monkeypatch.delenv("CB_ALLOW_VM_AARCH64", raising=False)
        with pytest.raises(ConfigError, match="aarch64"):
            self._builder(deployment_type="vm", arch="aarch64").build()

    def test_vm_on_aarch64_allowed(self, monkeypatch):
        monkeypatch.setenv("CB_ALLOW_VM_AARCH64", "1")
        cfg = self._builder(deployment_type="vm", arch="aarch64").build()
        assert cfg.deployment_type == "vm"
