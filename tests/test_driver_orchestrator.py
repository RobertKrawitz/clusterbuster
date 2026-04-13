# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""Integration-level tests for Phase 3C orchestration, sync, monitoring,
reporting, artifacts, cleanup, metrics.

Every test calls actual production functions with a MockClusterInterface.
Tests verify exact arguments, YAML content, and return values — not just
``>= 1 call``.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Any, Iterator
from unittest.mock import MagicMock, patch

import pytest
import yaml

from clusterbuster.driver.config import ClusterbusterConfigBuilder
from clusterbuster.driver.orchestrator import (
    RunContext,
    RunFailed,
    _check_inject_error,
    _check_pods_direct,
    _create_sync_services,
    _find_first_deployment,
    _interruptible_run,
    _is_client_server_workload,
    _join_threads,
    _make_signal_handler,
    _set_run_failed,
    _terminate_subprocesses,
    allocate_namespaces,
    create_all_objects,
    do_logging,
    plan_namespace_names,
)
from clusterbuster.driver.manifests import ManifestBuilder


# ---------------------------------------------------------------------------
# MockClusterInterface — enhanced for integration-level testing
# ---------------------------------------------------------------------------

class MockClusterInterface:
    """Configurable mock for ClusterInterface.

    Enhancements over original:
    - ``oc_path`` property (P1-9)
    - ``get_json`` routes by first positional arg
    - ``watch`` yields configurable str|None sequences
    - ``stdin_data`` captured in call tuples
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self._get_json_responses: dict[str, Any] = {}
        self._get_json_default: Any = {"items": []}
        self._run_results: deque[subprocess.CompletedProcess] = deque()
        self._default_result = subprocess.CompletedProcess(
            args=["oc"], returncode=0, stdout="", stderr=""
        )
        self._oc = "/usr/bin/oc"
        self._doit = True
        self._verbose = False
        self._debug: dict[str, str] = {}
        self._block_event: threading.Event | None = None
        self._watch_lines: list[str | None] = []

    @property
    def oc_path(self) -> str:
        return self._oc

    def run(self, *args: str, **kwargs: Any) -> subprocess.CompletedProcess:
        self.calls.append(("run", args, kwargs))
        if self._block_event:
            self._block_event.wait()
        if self._run_results:
            return self._run_results.popleft()
        # Namespace existence checks should return "not found" by default
        # so that create_all_namespaces actually creates them.
        if len(args) >= 2 and args[0] == "get" and args[1] == "namespace":
            return subprocess.CompletedProcess(
                args=["oc"], returncode=1, stdout="", stderr="not found"
            )
        return self._default_result

    def run_fatal(self, *args: str, **kwargs: Any) -> subprocess.CompletedProcess:
        self.calls.append(("run_fatal", args, kwargs))
        return self._default_result

    def exec_(self, *args: str, **kwargs: Any) -> subprocess.CompletedProcess:
        self.calls.append(("exec_", args, kwargs))
        if self._run_results:
            return self._run_results.popleft()
        return self._default_result

    def get_json(self, *args: str) -> Any:
        self.calls.append(("get_json", args, {}))
        resource = args[0] if args else ""
        if resource in self._get_json_responses:
            return self._get_json_responses[resource]
        return self._get_json_default

    @contextmanager
    def watch(self, *args: str, **kwargs: Any) -> Iterator[Iterator[str]]:
        self.calls.append(("watch", args, kwargs))
        yield iter(self._watch_lines)

    def create(self, yaml_docs: str, **kwargs: Any) -> subprocess.CompletedProcess:
        self.calls.append(("create", (yaml_docs,), kwargs))
        return self._default_result

    def apply(self, yaml_docs: str, **kwargs: Any) -> subprocess.CompletedProcess:
        self.calls.append(("apply", (yaml_docs,), kwargs))
        return self._default_result

    def delete(self, *args: str, **kwargs: Any) -> subprocess.CompletedProcess:
        self.calls.append(("delete", args, kwargs))
        if self._run_results:
            return self._run_results.popleft()
        return self._default_result

    def label(self, *args: str) -> subprocess.CompletedProcess:
        self.calls.append(("label", args, {}))
        return self._default_result

    def logs(self, *args: str) -> str:
        self.calls.append(("logs", args, {}))
        return "log output"

    def describe(self, *args: str) -> str:
        self.calls.append(("describe", args, {}))
        return "description output"

    def wait(self, *args: str) -> subprocess.CompletedProcess:
        self.calls.append(("wait", args, {}))
        return self._default_result

    def debug_node(self, node: str, *args: str) -> subprocess.CompletedProcess:
        self.calls.append(("debug_node", (node, *args), {}))
        return self._default_result

    def _calls_of(self, method: str) -> list[tuple[str, tuple, dict]]:
        return [c for c in self.calls if c[0] == method]


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

def _make_config(**overrides: Any) -> Any:
    """Build a ClusterbusterConfig with test defaults."""
    builder = ClusterbusterConfigBuilder(
        requested_workload="sleep",
        basename="test-cb",
        namespaces=2,
        deps_per_namespace=1,
        doit=True,
    )
    for k, v in overrides.items():
        setattr(builder, k, v)
    return builder.build()


def _make_ctx(config: Any = None, **config_overrides: Any) -> RunContext:
    """Build a RunContext with mock cluster."""
    cfg = config or _make_config(**config_overrides)
    mock_cluster = MockClusterInterface()
    mb = ManifestBuilder(cfg)
    return RunContext(
        config=cfg,
        cluster=mock_cluster,
        manifest_builder=mb,
        vm_builder=None,
    )


def _parse_created_yaml(mock: MockClusterInterface) -> list[dict]:
    """Parse all YAML documents from create() and apply() calls."""
    docs = []
    for call in mock._calls_of("create") + mock._calls_of("apply"):
        yaml_str = call[1][0]
        for doc in yaml.safe_load_all(yaml_str):
            if doc:
                docs.append(doc)
    return docs


class _MockWorkload:
    """Minimal workload mock for deployment testing."""

    name = "sleep"

    def supports_reporting(self) -> bool:
        return False

    def create_deployment(self, ctx: Any) -> bool:
        return False

    def arglist(self, ctx: Any) -> list[str]:
        return ["/bin/sleep", "infinity"]

    def list_configmaps(self) -> list[str]:
        return []

    def list_user_configmaps(self) -> list[str]:
        return []

    def generate_metadata(self) -> dict:
        return {}

    def report_options(self) -> dict:
        return {}

    def workload_reporting_class(self) -> str:
        return "sleep"

    def listen_ports(self, config: Any = None) -> list[int]:
        return []

    def requires_drop_cache(self) -> bool:
        return False

    def sysctls(self, config: Any = None) -> dict[str, str]:
        return {}

    def namespace_policy(self) -> str:
        return ""

    def vm_required_packages(self) -> list[str]:
        return []

    def vm_setup_commands(self) -> list[str]:
        return []


class _ReportingWorkload(_MockWorkload):
    def supports_reporting(self) -> bool:
        return True


class _ClientServerWorkload:
    """Mock for a client-server workload (no arglist, has server/client)."""

    name = "server"

    def supports_reporting(self) -> bool:
        return False

    def create_deployment(self, ctx: Any) -> bool:
        return False

    def server_arglist(self, ctx: Any) -> list[str]:
        return ["python3", "/opt/server.py", "--port", "30000"]

    def client_arglist(self, ctx: Any) -> list[str]:
        return ["python3", "/opt/client.py", "--port", "30000"]

    def list_configmaps(self) -> list[str]:
        return []

    def list_user_configmaps(self) -> list[str]:
        return []

    def generate_metadata(self) -> dict:
        return {}

    def report_options(self) -> dict:
        return {}

    def workload_reporting_class(self) -> str:
        return "server"

    def listen_ports(self, config: Any = None) -> list[int]:
        return []

    def requires_drop_cache(self) -> bool:
        return False

    def sysctls(self, config: Any = None) -> dict[str, str]:
        return {}

    def namespace_policy(self) -> str:
        return ""

    def vm_required_packages(self) -> list[str]:
        return []

    def vm_setup_commands(self) -> list[str]:
        return []


# ---------------------------------------------------------------------------
# Namespace allocation
# ---------------------------------------------------------------------------

class TestNamespaceAllocation:
    def test_simple_allocation(self):
        ctx = _make_ctx(namespaces=3)
        allocate_namespaces(ctx)
        assert ctx.namespaces_to_create == ["test-cb-0", "test-cb-1", "test-cb-2"]

    def test_scaling_skips_existing(self):
        ctx = _make_ctx(namespaces=2, scale_ns=True)
        ctx.cluster._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=0,
            stdout="namespace/test-cb-0\n", stderr=""
        ))
        allocate_namespaces(ctx)
        assert ctx.namespaces_to_create == ["test-cb-1", "test-cb-2"]

    def test_plan_namespace_names_basic(self):
        cfg = _make_config(namespaces=3)
        names = plan_namespace_names(cfg)
        assert names == ["test-cb-0", "test-cb-1", "test-cb-2"]

    def test_plan_namespace_names_skips_existing(self):
        cfg = _make_config(namespaces=2)
        names = plan_namespace_names(cfg, existing={"test-cb-0"})
        assert names == ["test-cb-1", "test-cb-2"]

    def test_plan_namespace_names_skips_multiple_existing(self):
        cfg = _make_config(namespaces=2)
        names = plan_namespace_names(cfg, existing={"test-cb-0", "test-cb-1", "test-cb-3"})
        assert names == ["test-cb-2", "test-cb-4"]

    def test_plan_namespace_names_empty_existing(self):
        cfg = _make_config(namespaces=1)
        names = plan_namespace_names(cfg, existing=set())
        assert names == ["test-cb-0"]

    def test_sync_namespace_separate(self):
        ctx = _make_ctx(sync_in_first_namespace=False)
        allocate_namespaces(ctx)
        assert ctx.sync_namespace == "test-cb-sync"
        assert ctx.sync_pod_args == ["-n", "test-cb-sync", "test-cb-sync"]

    def test_sync_namespace_first(self):
        ctx = _make_ctx(sync_in_first_namespace=True)
        allocate_namespaces(ctx)
        assert ctx.sync_namespace == "test-cb-0"

    def test_global_sync_service_exact_dns(self):
        ctx = _make_ctx(sync_in_first_namespace=False)
        allocate_namespaces(ctx)
        assert ctx.global_sync_service == "test-cb-sync-0.test-cb-sync.svc.cluster.local"


# ---------------------------------------------------------------------------
# Object creation
# ---------------------------------------------------------------------------

class TestObjectCreation:
    def test_namespace_creation_produces_correct_yaml(self):
        ctx = _make_ctx(namespaces=1)
        allocate_namespaces(ctx)
        create_all_objects(ctx, "namespaces")
        docs = _parse_created_yaml(ctx.cluster)
        ns_docs = [d for d in docs if d["kind"] == "Namespace"]
        assert len(ns_docs) >= 1
        assert ns_docs[0]["metadata"]["name"] == "test-cb-0"
        assert ns_docs[0]["metadata"]["labels"]["test-cb"] == "true"

    def test_configmap_creation_produces_system_configmap(self):
        ctx = _make_ctx(namespaces=1)
        allocate_namespaces(ctx)
        create_all_objects(ctx, "namespaces")
        create_all_objects(ctx, "configmaps")
        docs = _parse_created_yaml(ctx.cluster)
        cm_docs = [d for d in docs if d["kind"] == "ConfigMap"]
        assert len(cm_docs) >= 1
        sys_cm = [d for d in cm_docs if "system-configmap" in d["metadata"]["name"]]
        assert len(sys_cm) == 1
        assert sys_cm[0]["metadata"]["namespace"] == "test-cb-0"

    def test_user_configmap_created_when_files_configured(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("test content")
            f.flush()
            try:
                ctx = _make_ctx(namespaces=1, configmap_files=[f.name])
                allocate_namespaces(ctx)
                create_all_objects(ctx, "namespaces")
                create_all_objects(ctx, "configmaps")
                docs = _parse_created_yaml(ctx.cluster)
                cm_docs = [d for d in docs if d["kind"] == "ConfigMap"]
                user_cms = [d for d in cm_docs if "user-configmap" in d["metadata"]["name"]]
                assert len(user_cms) == 1
                assert "test content" in str(user_cms[0].get("data", {}).values())
            finally:
                os.unlink(f.name)

    def test_secret_creation_correct_name_and_data(self):
        ctx = _make_ctx(namespaces=1, secrets=1, wait_for_secrets=False)
        allocate_namespaces(ctx)
        create_all_objects(ctx, "namespaces")
        create_all_objects(ctx, "secrets")
        docs = _parse_created_yaml(ctx.cluster)
        secret_docs = [d for d in docs if d["kind"] == "Secret"]
        assert len(secret_docs) == 1
        s = secret_docs[0]
        assert s["metadata"]["name"] == "secret-test-cb-0-0-0"
        assert s["metadata"]["namespace"] == "test-cb-0"
        assert "data" in s
        assert "secret-0" in s["data"]

    def test_secret_range_respects_first_deployment(self):
        ctx = _make_ctx(namespaces=1, secrets=1, first_deployment=2,
                        deps_per_namespace=1, wait_for_secrets=False)
        allocate_namespaces(ctx)
        create_all_objects(ctx, "namespaces")
        create_all_objects(ctx, "secrets")
        docs = _parse_created_yaml(ctx.cluster)
        secret_docs = [d for d in docs if d["kind"] == "Secret"]
        assert len(secret_docs) == 1
        assert secret_docs[0]["metadata"]["name"] == "secret-test-cb-0-2-0"

    def test_wait_for_secrets_matches_created_names(self):
        ctx = _make_ctx(namespaces=1, secrets=1, wait_for_secrets=True)
        allocate_namespaces(ctx)
        create_all_objects(ctx, "namespaces")
        create_all_objects(ctx, "configmaps")
        ctx.cluster._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=0,
            stdout="secret/secret-test-cb-0-0-0\n", stderr=""
        ))
        create_all_objects(ctx, "secrets")
        run_calls = [c for c in ctx.cluster._calls_of("run")
                     if len(c[1]) > 0 and c[1][0] == "get" and "secret" in c[1]]
        assert len(run_calls) >= 1

    @patch("clusterbuster.driver.workload_registry.get_workload")
    def test_deployment_creation_calls_arglist(self, mock_get_wl):
        wl = _MockWorkload()
        mock_get_wl.return_value = wl
        ctx = _make_ctx(namespaces=1, deps_per_namespace=1)
        allocate_namespaces(ctx)
        create_all_objects(ctx, "namespaces")
        create_all_objects(ctx, "configmaps")
        create_all_objects(ctx, "deployments")
        docs = _parse_created_yaml(ctx.cluster)
        pod_docs = [d for d in docs if d["kind"] in ("Pod", "Deployment", "ReplicaSet")
                    and "sync" not in d["metadata"].get("name", "")]
        assert len(pod_docs) >= 1
        pod = pod_docs[0]
        if pod["kind"] == "Pod":
            containers = pod["spec"]["containers"]
        else:
            containers = pod["spec"]["template"]["spec"]["containers"]
        assert len(containers) >= 1
        assert containers[0]["command"] == ["/bin/sleep", "infinity"]
        assert containers[0]["image"]
        assert containers[0]["name"]

    def test_is_client_server_detects_single_role(self):
        wl = _MockWorkload()
        assert _is_client_server_workload(wl) is False

    def test_is_client_server_detects_client_server(self):
        wl = _ClientServerWorkload()
        assert _is_client_server_workload(wl) is True

    @patch("clusterbuster.driver.workload_registry.get_workload")
    def test_client_server_creates_both_roles(self, mock_get_wl):
        wl = _ClientServerWorkload()
        mock_get_wl.return_value = wl
        ctx = _make_ctx(namespaces=1, deps_per_namespace=1)
        allocate_namespaces(ctx)
        create_all_objects(ctx, "namespaces")
        create_all_objects(ctx, "configmaps")
        create_all_objects(ctx, "deployments")
        docs = _parse_created_yaml(ctx.cluster)
        pod_docs = [d for d in docs if d["kind"] in ("Pod", "Deployment", "ReplicaSet")
                    and "sync" not in d["metadata"].get("name", "")]
        assert len(pod_docs) == 2
        names = sorted(d["metadata"]["name"] for d in pod_docs)
        assert any("server" in n for n in names)
        assert any("client" in n for n in names)

    @patch("clusterbuster.driver.workload_registry.get_workload")
    def test_client_server_arglist_content(self, mock_get_wl):
        wl = _ClientServerWorkload()
        mock_get_wl.return_value = wl
        ctx = _make_ctx(namespaces=1, deps_per_namespace=1)
        allocate_namespaces(ctx)
        create_all_objects(ctx, "namespaces")
        create_all_objects(ctx, "configmaps")
        create_all_objects(ctx, "deployments")
        docs = _parse_created_yaml(ctx.cluster)
        pod_docs = [d for d in docs if d["kind"] in ("Pod", "Deployment", "ReplicaSet")]
        commands = {}
        for pod in pod_docs:
            name = pod["metadata"]["name"]
            if pod["kind"] == "Pod":
                cmd = pod["spec"]["containers"][0]["command"]
            else:
                cmd = pod["spec"]["template"]["spec"]["containers"][0]["command"]
            if "server" in name:
                commands["server"] = cmd
            elif "client" in name:
                commands["client"] = cmd
        assert "server.py" in " ".join(commands["server"])
        assert "client.py" in " ".join(commands["client"])

    @patch("clusterbuster.driver.workload_registry.get_workload")
    def test_deployment_creation_raises_on_workload_failure(self, mock_get_wl):
        mock_get_wl.side_effect = ValueError("unknown workload")
        ctx = _make_ctx(namespaces=1, deps_per_namespace=1)
        allocate_namespaces(ctx)
        with pytest.raises(RunFailed, match="Cannot resolve workload"):
            create_all_objects(ctx, "deployments")

    @patch("clusterbuster.driver.workload_registry.get_workload")
    def test_sync_service_created_in_deployment_path(self, mock_get_wl):
        mock_get_wl.return_value = _MockWorkload()
        ctx = _make_ctx(namespaces=2, sync_in_first_namespace=False)
        allocate_namespaces(ctx)
        create_all_objects(ctx, "namespaces")
        create_all_objects(ctx, "configmaps")
        create_all_objects(ctx, "deployments")
        docs = _parse_created_yaml(ctx.cluster)
        svc_docs = [d for d in docs if d["kind"] == "Service"]
        assert len(svc_docs) >= 1
        primary = [s for s in svc_docs if s["spec"].get("type") != "ExternalName"]
        external = [s for s in svc_docs if s["spec"].get("type") == "ExternalName"]
        assert len(primary) >= 1
        assert primary[0]["metadata"]["namespace"] == "test-cb-sync"
        assert len(external) >= 1

    @patch("clusterbuster.driver.workload_registry.get_workload")
    def test_multi_namespace_deployment_creates_per_ns(self, mock_get_wl):
        mock_get_wl.return_value = _MockWorkload()
        ctx = _make_ctx(namespaces=3, deps_per_namespace=2)
        allocate_namespaces(ctx)
        create_all_objects(ctx, "namespaces")
        create_all_objects(ctx, "configmaps")
        create_all_objects(ctx, "deployments")
        docs = _parse_created_yaml(ctx.cluster)
        pod_docs = [d for d in docs if d["kind"] in ("Pod", "Deployment", "ReplicaSet")
                    and "sync" not in d["metadata"].get("name", "")]
        assert len(pod_docs) == 6  # 3 ns * 2 deps


# ---------------------------------------------------------------------------
# Find first deployment (scaling)
# ---------------------------------------------------------------------------

class TestFindFirstDeployment:
    def test_no_scaling(self):
        ctx = _make_ctx(scale_deployments=False, first_deployment=5)
        assert _find_first_deployment(ctx) == 5

    def test_scaling_no_existing(self):
        ctx = _make_ctx(scale_deployments=True)
        ctx.cluster._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=0, stdout="", stderr=""
        ))
        assert _find_first_deployment(ctx) == 0

    def test_scaling_with_existing(self):
        ctx = _make_ctx(scale_deployments=True)
        ctx.cluster._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=0,
            stdout="test-cb-0-3\ntest-cb-0-5\ntest-cb-1-2\n", stderr=""
        ))
        assert _find_first_deployment(ctx) == 6


# ---------------------------------------------------------------------------
# Sync services
# ---------------------------------------------------------------------------

class TestSyncServices:
    def test_creates_primary_and_external(self):
        ctx = _make_ctx(namespaces=2, sync_in_first_namespace=False)
        allocate_namespaces(ctx)
        _create_sync_services(ctx)
        docs = _parse_created_yaml(ctx.cluster)
        svc_docs = [d for d in docs if d["kind"] == "Service"]
        primary = [d for d in svc_docs if d["spec"].get("type") != "ExternalName"]
        external = [d for d in svc_docs if d["spec"].get("type") == "ExternalName"]
        assert len(primary) == 1
        assert primary[0]["metadata"]["namespace"] == "test-cb-sync"
        assert len(external) == 2
        sync_pods = [d for d in docs if d["kind"] == "Pod" and "sync" in d["metadata"].get("name", "")]
        assert len(sync_pods) == 1
        assert sync_pods[0]["metadata"]["namespace"] == "test-cb-sync"

    def test_skip_when_no_sync(self):
        ctx = _make_ctx(sync_start=False)
        allocate_namespaces(ctx)
        _create_sync_services(ctx)
        assert len(ctx.cluster._calls_of("create")) == 0


# ---------------------------------------------------------------------------
# Failure propagation
# ---------------------------------------------------------------------------

class TestFailurePropagation:
    def test_first_writer_wins(self):
        ctx = _make_ctx()
        ctx.sync_pod_args = ["-n", "ns", "pod"]
        _set_run_failed(ctx, "first reason")
        _set_run_failed(ctx, "second reason")
        assert ctx.failure_reason == "first reason"

    def test_notifies_sync_pod_with_exec(self):
        ctx = _make_ctx()
        ctx.sync_pod_args = ["-n", "ns", "pod"]
        _set_run_failed(ctx, "pod crashed")
        assert ctx.run_failed.is_set()
        exec_calls = [c for c in ctx.cluster.calls
                      if c[0] == "run" and len(c[1]) > 0 and c[1][0] == "exec"]
        assert len(exec_calls) == 1
        cmd_args = exec_calls[0][1]
        assert "exec" in cmd_args
        assert "-n" in cmd_args and "ns" in cmd_args

    def test_sync_unreachable_sets_run_aborted(self):
        ctx = _make_ctx()
        ctx.sync_pod_args = ["-n", "ns", "pod"]
        ctx.cluster._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=1, stdout="", stderr="conn refused"
        ))
        _set_run_failed(ctx, "test failure")
        assert ctx.run_aborted is True

    def test_concurrent_first_writer(self):
        ctx = _make_ctx()
        ctx.sync_pod_args = ["-n", "ns", "pod"]
        barrier = threading.Barrier(2)

        def _writer(reason: str) -> None:
            barrier.wait()
            _set_run_failed(ctx, reason)

        t1 = threading.Thread(target=_writer, args=("A",))
        t2 = threading.Thread(target=_writer, args=("B",))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)
        assert ctx.run_failed.is_set()
        assert ctx.failure_reason in ("A", "B")
        exec_calls = [c for c in ctx.cluster.calls
                      if c[0] == "run" and len(c[1]) > 0 and c[1][0] == "exec"]
        assert len(exec_calls) == 1  # only first writer notifies

    def test_run_failed_propagates_to_create_all_objects(self):
        ctx = _make_ctx()
        ctx.run_failed.set()
        ctx.failure_reason = "earlier"
        with pytest.raises(RunFailed, match="earlier"):
            create_all_objects(ctx, "namespaces")


# ---------------------------------------------------------------------------
# Signal handler (P1-23: tests the actual handler via _make_signal_handler)
# ---------------------------------------------------------------------------

class TestSignalHandling:
    def test_handler_sets_events(self):
        ctx = _make_ctx()
        handler = _make_signal_handler(ctx)
        handler(signal.SIGINT, None)
        assert ctx.shutdown_event.is_set()
        assert ctx.run_failed.is_set()
        assert "SIGINT" in ctx.failure_reason

    def test_handler_preserves_prior_failure(self):
        ctx = _make_ctx()
        ctx.sync_pod_args = ["-n", "ns", "pod"]
        _set_run_failed(ctx, "Pod OOM")
        handler = _make_signal_handler(ctx)
        handler(signal.SIGTERM, None)
        assert ctx.failure_reason == "Pod OOM"

    def test_double_signal_calls_sigdfl(self):
        ctx = _make_ctx()
        ctx.shutdown_event.set()
        handler = _make_signal_handler(ctx)
        with patch("clusterbuster.driver.orchestrator.signal.signal") as mock_sig, \
             patch("clusterbuster.driver.orchestrator.os.kill") as mock_kill:
            handler(signal.SIGINT, None)
            mock_sig.assert_called_once_with(signal.SIGINT, signal.SIG_DFL)
            mock_kill.assert_called_once_with(os.getpid(), signal.SIGINT)

    def test_async_signal_sets_events(self):
        ctx = _make_ctx()
        handler = _make_signal_handler(ctx)
        old = signal.getsignal(signal.SIGUSR1)
        try:
            signal.signal(signal.SIGUSR1, handler)
            timer = threading.Timer(0.05, lambda: os.kill(os.getpid(), signal.SIGUSR1))
            timer.start()
            assert ctx.run_failed.wait(timeout=2.0)
            assert ctx.shutdown_event.is_set()
            assert "SIGUSR1" in ctx.failure_reason
        finally:
            signal.signal(signal.SIGUSR1, old)


# ---------------------------------------------------------------------------
# Monitor pods — state machine (P1-4 fix verified: only "failed" in _FAILURE_PHASES)
# ---------------------------------------------------------------------------

class TestMonitorPods:
    def _run(self, lines, *, cfg=None, on_failure=None, on_complete=None,
             shutdown_check=None):
        from clusterbuster.driver.monitoring import monitor_pods
        _cfg = cfg or _make_config()
        failures = []
        completes = []

        with patch("clusterbuster.driver.monitoring.watch_pods_with_heartbeat") as mock_w:
            mock_w.return_value = iter(lines)
            result = monitor_pods(
                _cfg, "/usr/bin/oc",
                on_failure=on_failure or (lambda r: failures.append(r)),
                on_complete=on_complete or (lambda: completes.append(True)),
                shutdown_check=shutdown_check or (lambda: False),
            )
        return result, failures, completes

    def test_pods_pending_to_running_no_failure(self):
        lines = [
            "ns1 pod-0 Pending", None,
            "ns1 pod-0 Running",
            "ns1 pod-1 Pending", None,
            "ns1 pod-1 Running",
        ]
        result, failures, _ = self._run(lines)
        assert result is None
        assert len(failures) == 0

    def test_pod_failure_detected(self):
        lines = [
            "ns1 pod-0 Running",
            "ns1 pod-1 Failed",
        ]
        result, failures, _ = self._run(lines)
        assert result is not None
        assert len(failures) == 1
        assert "ns1/pod-1" in failures[0]
        assert "Failed" in failures[0]

    def test_pod_start_timeout(self):
        cfg = _make_config(pod_start_timeout=1)
        lines = ["ns1 pod-0 Pending", None, None]
        call_count = [0]
        base = time.time()

        def _advancing_time():
            call_count[0] += 1
            return base + call_count[0] * 2.0

        with patch("clusterbuster.driver.monitoring.time") as mock_t:
            mock_t.time = _advancing_time
            mock_t.sleep = lambda x: None
            result, failures, _ = self._run(lines, cfg=cfg)
        assert len(failures) == 1
        assert "timeout" in failures[0].lower() or "stuck" in failures[0].lower()

    def test_duplicate_phase_filtered(self):
        lines = [
            "ns1 pod-0 Running",
            "ns1 pod-0 Running",
            "ns1 pod-0 Running",
        ]
        result, failures, _ = self._run(lines)
        assert len(failures) == 0

    def test_non_reporting_completion(self):
        cfg = _make_config(workload_run_time=0)
        lines = [
            "ns1 pod-0 Pending", None,
            "ns1 pod-0 Running", None,
        ]
        with patch("clusterbuster.driver.workload_registry.get_workload") as mock_get:
            mock_get.return_value = _MockWorkload()
            result, _, completes = self._run(lines, cfg=cfg)
        assert len(completes) == 1

    def test_shutdown_exits_early(self):
        lines = [
            "ns1 pod-0 Pending", None,
            "ns1 pod-0 Running",
        ]
        result, failures, _ = self._run(
            lines, shutdown_check=lambda: True,
        )
        assert result is None
        assert len(failures) == 0

    def test_heartbeat_resets_progress(self):
        cfg = _make_config(pod_start_timeout=10)
        lines = [
            "ns1 pod-0 Pending",
            None,
            "ns1 pod-1 Pending",
            None,
            "ns1 pod-0 Running",
            None,
        ]
        result, failures, _ = self._run(lines, cfg=cfg)
        assert len(failures) == 0


# ---------------------------------------------------------------------------
# Sync protocol (P1-2 timestamp ordering, P1-6 validation, P1-8 exec_fn)
# ---------------------------------------------------------------------------

class TestSyncProtocol:
    def test_timestamp_ordering_first_before_exec(self):
        """P1-2: first_local captured before exec, not after."""
        from clusterbuster.driver.sync import get_pod_and_local_timestamps
        mock = MockClusterInterface()
        mock._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=0, stdout="1000000000.5\n", stderr=""
        ))
        mock._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=0, stdout="", stderr=""
        ))
        first, remote, second = get_pod_and_local_timestamps(
            mock, ["-n", "ns", "pod"],
        )
        assert isinstance(remote, float)  # remote is from a different clock
        assert first <= second
        assert remote == pytest.approx(1000000000.5)

    def test_atomic_timestamp_write(self):
        from clusterbuster.driver.sync import get_pod_and_local_timestamps
        mock = MockClusterInterface()
        mock._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=0, stdout="1234567890.123\n", stderr=""
        ))
        mock._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=0, stdout="", stderr=""
        ))
        get_pod_and_local_timestamps(mock, ["-n", "ns", "pod"])
        write_call = mock._calls_of("run")[1]
        cmd_str = " ".join(str(a) for a in write_call[1])
        assert ".tmp" in cmd_str
        assert "mv" in cmd_str

    def test_sync_pod_phase_check_on_failure(self):
        from clusterbuster.driver.sync import get_pod_and_local_timestamps
        mock = MockClusterInterface()
        mock._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=1, stdout="", stderr="err"
        ))
        mock._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=0, stdout="Failed", stderr=""
        ))
        with pytest.raises(RuntimeError, match="Failed"):
            get_pod_and_local_timestamps(
                mock, ["-n", "ns", "pod"], pod_start_timeout=60,
            )

    def test_log_helper_successful_retrieval(self):
        from clusterbuster.driver.sync import log_helper
        results_json = json.dumps({"worker_results": {"test": "data"}})
        exec_queue = deque([
            subprocess.CompletedProcess(args=[], returncode=0, stdout=results_json, stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ])

        def _exec(*args, **kw):
            return exec_queue.popleft()

        mock = MockClusterInterface()
        cfg = _make_config()
        on_complete_calls = []
        result = log_helper(
            mock, cfg, ["-n", "ns", "pod"],
            shutdown_check=lambda: False,
            on_complete=lambda: on_complete_calls.append(True),
            exec_fn=_exec,
        )
        assert result is not None
        parsed = json.loads(result)
        assert "worker_results" in parsed
        assert len(on_complete_calls) == 1

    def test_log_helper_rejects_missing_worker_results(self):
        """P1-6: must reject JSON without worker_results key."""
        from clusterbuster.driver.sync import log_helper
        bad_json = json.dumps({"error": "something wrong"})
        call_count = [0]

        def _exec(*args, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                return subprocess.CompletedProcess(args=[], returncode=0, stdout=bad_json, stderr="")
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        mock = MockClusterInterface()
        cfg = _make_config()
        result = log_helper(
            mock, cfg, ["-n", "ns", "pod"],
            shutdown_check=lambda: call_count[0] >= 2,
            exec_fn=_exec,
        )
        assert result is None

    def test_fail_helper_detects_error(self):
        from clusterbuster.driver.sync import fail_helper
        exec_queue = deque([
            subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout="sync pod error message\n___ERROR___", stderr=""
            ),
        ])

        def _exec(*args, **kw):
            return exec_queue.popleft()

        failures = []
        fail_helper(
            MockClusterInterface(), ["-n", "ns", "pod"],
            shutdown_check=lambda: False,
            on_failure=lambda r: failures.append(r),
            exec_fn=_exec,
        )
        assert len(failures) == 1
        assert "sync pod error message" in failures[0]

    def test_fail_helper_detects_done(self):
        from clusterbuster.driver.sync import fail_helper
        exec_queue = deque([
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout="___DONE___", stderr=""
            ),
        ])

        def _exec(*args, **kw):
            return exec_queue.popleft()

        failures = []
        fail_helper(
            MockClusterInterface(), ["-n", "ns", "pod"],
            shutdown_check=lambda: False,
            on_failure=lambda r: failures.append(r),
            exec_fn=_exec,
        )
        assert len(failures) == 0

    def test_notify_sync_pod_error_success(self):
        from clusterbuster.driver.sync import notify_sync_pod_error, SYNC_ERROR_FILE
        mock = MockClusterInterface()
        result = notify_sync_pod_error(mock, ["-n", "ns", "pod"], "test error")
        assert result is True
        run_call = mock._calls_of("run")[0]
        cmd_str = " ".join(str(a) for a in run_call[1])
        assert SYNC_ERROR_FILE in cmd_str
        assert "test error" in cmd_str


# ---------------------------------------------------------------------------
# do_logging — thread coordination (P1-5: non-reporting workloads)
# ---------------------------------------------------------------------------

class TestDoLogging:
    @patch("clusterbuster.driver.orchestrator._monitor_thread_fn")
    @patch("clusterbuster.driver.orchestrator._log_helper_thread_fn")
    @patch("clusterbuster.driver.workload_registry.get_workload")
    def test_non_reporting_starts_monitor_no_log_helper(
        self, mock_get_wl, mock_log_fn, mock_mon_fn,
    ):
        """P1-5: non-reporting workload must start monitor, skip log helper."""
        mock_get_wl.return_value = _MockWorkload()  # supports_reporting=False

        def _monitor(ctx, mod):
            ctx.run_complete.set()
            ctx.any_thread_done.set()

        mock_mon_fn.side_effect = _monitor
        ctx = _make_ctx(namespaces=1)
        allocate_namespaces(ctx)
        result = do_logging(ctx)
        assert result == 0
        mock_mon_fn.assert_called_once()
        mock_log_fn.assert_not_called()

    @patch("clusterbuster.driver.orchestrator._monitor_thread_fn")
    @patch("clusterbuster.driver.orchestrator._log_helper_thread_fn")
    @patch("clusterbuster.driver.workload_registry.get_workload")
    @patch("clusterbuster.driver.sync.get_pod_and_local_timestamps")
    def test_reporting_starts_both_threads(
        self, mock_ts, mock_get_wl, mock_log_fn, mock_mon_fn,
    ):
        mock_get_wl.return_value = _ReportingWorkload()
        mock_ts.return_value = (1.0, 2.0, 3.0)

        def _log(ctx, mod):
            ctx.any_thread_done.set()

        mock_log_fn.side_effect = _log
        ctx = _make_ctx(namespaces=1)
        allocate_namespaces(ctx)
        result = do_logging(ctx)
        assert result == 0
        mock_mon_fn.assert_called_once()
        mock_log_fn.assert_called_once()
        assert ctx.first_local_ts == 1.0
        assert ctx.remote_ts == 2.0
        assert ctx.second_local_ts == 3.0

    @patch("clusterbuster.driver.orchestrator._check_pods_direct")
    @patch("clusterbuster.driver.orchestrator._monitor_thread_fn")
    @patch("clusterbuster.driver.workload_registry.get_workload")
    def test_timeout_sets_run_failed(self, mock_get_wl, mock_mon_fn, mock_check):
        mock_get_wl.return_value = _MockWorkload()

        ctx = _make_ctx(namespaces=1, timeout=1)
        allocate_namespaces(ctx)
        result = do_logging(ctx)
        assert result == 1
        assert ctx.run_failed.is_set()
        assert "timed out" in ctx.failure_reason.lower()

    @patch("clusterbuster.driver.orchestrator._monitor_thread_fn")
    @patch("clusterbuster.driver.workload_registry.get_workload")
    @patch("clusterbuster.driver.sync.get_pod_and_local_timestamps")
    def test_sync_timestamp_failure_returns_1(
        self, mock_ts, mock_get_wl, mock_mon_fn,
    ):
        mock_get_wl.return_value = _ReportingWorkload()
        mock_ts.side_effect = RuntimeError("sync pod unreachable")
        ctx = _make_ctx(namespaces=1)
        allocate_namespaces(ctx)
        result = do_logging(ctx)
        assert result == 1
        assert ctx.run_failed.is_set()


# ---------------------------------------------------------------------------
# Check pods direct (P1-4: table output + regex)
# ---------------------------------------------------------------------------

class TestCheckPodsDirect:
    def test_detects_error_in_status_column(self):
        ctx = _make_ctx()
        ctx.sync_pod_args = ["-n", "ns", "pod"]
        ctx.cluster._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=0,
            stdout="test-cb-0   pod-0   1/1   Error   0   5m\n"
                   "test-cb-0   pod-1   1/1   Running   0   5m\n",
            stderr=""
        ))
        _check_pods_direct(ctx)
        assert ctx.run_failed.is_set()
        assert "Error" in ctx.failure_reason

    def test_detects_evicted(self):
        ctx = _make_ctx()
        ctx.sync_pod_args = ["-n", "ns", "pod"]
        ctx.cluster._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=0,
            stdout="test-cb-0   pod-0   0/1   Evicted   0   5m\n",
            stderr=""
        ))
        _check_pods_direct(ctx)
        assert ctx.run_failed.is_set()

    def test_detects_crashloopbackoff(self):
        ctx = _make_ctx()
        ctx.sync_pod_args = ["-n", "ns", "pod"]
        ctx.cluster._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=0,
            stdout="test-cb-0   pod-0   0/1   CrashLoopBackOff   3   10m\n",
            stderr=""
        ))
        _check_pods_direct(ctx)
        assert ctx.run_failed.is_set()

    def test_ignores_running_pods(self):
        ctx = _make_ctx()
        ctx.cluster._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=0,
            stdout="test-cb-0   pod-0   1/1   Running   0   5m\n",
            stderr=""
        ))
        _check_pods_direct(ctx)
        assert not ctx.run_failed.is_set()

    def test_ignores_container_creating(self):
        ctx = _make_ctx()
        ctx.cluster._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=0,
            stdout="test-cb-0   pod-0   0/1   ContainerCreating   0   5s\n",
            stderr=""
        ))
        _check_pods_direct(ctx)
        assert not ctx.run_failed.is_set()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

class TestReporting:
    def test_report_structure_exact_fields(self):
        from clusterbuster.driver.reporting import assemble_report
        cfg = _make_config()
        report = assemble_report(
            cfg, MockClusterInterface(),
            worker_results={"test": "data"},
            status="Success",
            first_start_ts=1000.0,
            second_start_ts=1001.0,
            prometheus_start_ts=999.0,
            presync_ts=1002.0,
            sync_ts=1003.0,
            postsync_ts=1004.0,
        )
        assert report["Status"] == "Success"
        assert report["Results"] == {"test": "data"}
        meta = report["metadata"]
        assert meta["uuid"] == cfg.uuid
        assert meta["workload"] == cfg.resolved_workload
        assert meta["controller_first_start_timestamp"] == 1000.0
        assert meta["controller_second_start_timestamp"] == 1001.0
        assert meta["prometheus_starting_timestamp"] == 999.0
        assert meta["controller_presync_timestamp"] == 1002.0
        assert meta["sync_timestamp"] == 1003.0
        assert meta["controller_postsync_timestamp"] == 1004.0
        opts = meta["options"]
        assert opts["container_image"] == cfg.container_image
        assert opts["basename"] == "test-cb"
        assert opts["namespaces"] == cfg.namespaces

    def test_emergency_report(self):
        from clusterbuster.driver.reporting import generate_emergency_report
        cfg = _make_config()
        report = generate_emergency_report(cfg, MockClusterInterface(), 1000.0)
        assert report["Status"] == cfg.failure_status
        assert report["Results"] == {}
        assert report["metadata"]["controller_first_start_timestamp"] == 1000.0

    def test_metrics_included_when_provided(self):
        from clusterbuster.driver.reporting import assemble_report
        cfg = _make_config()
        metrics = {"cpu": {"avg": 42.5}}
        report = assemble_report(
            cfg, MockClusterInterface(), metrics=metrics,
        )
        assert report["metrics"] == metrics

    def test_report_options_match_config(self):
        from clusterbuster.driver.reporting import assemble_report
        cfg = _make_config(namespaces=5, deps_per_namespace=3)
        report = assemble_report(cfg, MockClusterInterface())
        opts = report["metadata"]["options"]
        assert opts["namespaces"] == 5
        assert opts["deployments_per_namespace"] == 3

    def test_write_report_to_artifacts(self):
        from clusterbuster.driver.reporting import write_report_to_artifacts
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _make_config(artifactdir=tmpdir, report_format="raw")
            report = {"Status": "Success", "Results": {}}
            write_report_to_artifacts(report, cfg)
            raw_path = os.path.join(tmpdir, "clusterbuster-report.json")
            assert os.path.isfile(raw_path)
            with open(raw_path) as f:
                loaded = json.load(f)
            assert loaded["Status"] == "Success"

    def test_print_report_raw(self, capsys):
        from clusterbuster.driver.reporting import print_report
        cfg = _make_config(report_format="raw")
        report = {"Status": "Success"}
        print_report(report, cfg)
        captured = capsys.readouterr()
        assert "Success" in captured.out


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

class TestArtifacts:
    def test_skip_when_no_artifactdir(self):
        from clusterbuster.driver.artifacts import retrieve_artifacts
        assert retrieve_artifacts(MockClusterInterface(), _make_config(artifactdir="")) is False

    def test_skip_when_already_retrieved(self):
        from clusterbuster.driver.artifacts import retrieve_artifacts
        cfg = _make_config(artifactdir="/tmp/arts")
        assert retrieve_artifacts(MockClusterInterface(), cfg, already_retrieved=True) is False

    def test_collects_describe_and_logs(self):
        from clusterbuster.driver.artifacts import retrieve_artifacts
        mock = MockClusterInterface()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _make_config(artifactdir=tmpdir)
            mock._get_json_responses["pod"] = {
                "items": [{
                    "metadata": {"namespace": "ns1", "name": "pod1"},
                    "status": {"phase": "Failed"},
                    "spec": {"containers": [{"name": "c0"}]},
                }]
            }
            retrieve_artifacts(mock, cfg, run_failed=True)
            describe_calls = [c for c in mock.calls if c[0] == "describe"]
            log_calls = [c for c in mock.calls if c[0] == "logs"]
            assert len(describe_calls) == 1
            assert describe_calls[0][1] == ("pod", "-n", "ns1", "pod1")
            assert len(log_calls) == 1
            assert log_calls[0][1] == ("-n", "ns1", "pod1", "-c", "c0")

    def test_writes_files_to_correct_paths(self):
        from clusterbuster.driver.artifacts import retrieve_artifacts
        mock = MockClusterInterface()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _make_config(artifactdir=tmpdir)
            mock._get_json_responses["pod"] = {
                "items": [{
                    "metadata": {"namespace": "ns1", "name": "pod1"},
                    "status": {"phase": "Failed"},
                    "spec": {"containers": [{"name": "c0"}]},
                }]
            }
            retrieve_artifacts(mock, cfg, run_failed=True)
            assert os.path.isfile(os.path.join(tmpdir, "Describe", "ns1:pod1"))
            assert os.path.isfile(os.path.join(tmpdir, "Logs", "ns1:pod1:c0"))

    def test_skips_successful_pods(self):
        from clusterbuster.driver.artifacts import retrieve_artifacts
        mock = MockClusterInterface()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _make_config(artifactdir=tmpdir, retrieve_successful_logs=False)
            mock._get_json_responses["pod"] = {
                "items": [{
                    "metadata": {"namespace": "ns1", "name": "pod1"},
                    "status": {"phase": "Succeeded"},
                    "spec": {"containers": [{"name": "c0"}]},
                }]
            }
            retrieve_artifacts(mock, cfg, run_failed=False)
            log_calls = [c for c in mock.calls if c[0] == "logs"]
            assert len(log_calls) == 0

    def test_collects_sync_namespace_pods(self):
        """Sync-namespace pods are collected, matching bash behavior."""
        from clusterbuster.driver.artifacts import retrieve_artifacts
        mock = MockClusterInterface()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _make_config(artifactdir=tmpdir, sync_in_first_namespace=False)
            mock._get_json_responses["pod"] = {
                "items": [
                    {
                        "metadata": {"namespace": "sync-ns", "name": "sync-pod"},
                        "status": {"phase": "Running"},
                        "spec": {"containers": [{"name": "c0"}]},
                    },
                    {
                        "metadata": {"namespace": "work-ns", "name": "work-pod"},
                        "status": {"phase": "Running"},
                        "spec": {"containers": [{"name": "c0"}]},
                    },
                ]
            }
            retrieve_artifacts(
                mock, cfg, run_failed=True,
                sync_namespace="sync-ns",
            )
            log_calls = [c for c in mock.calls if c[0] == "logs"]
            ns_logged = {c[1][1] for c in log_calls}
            assert "sync-ns" in ns_logged
            assert "work-ns" in ns_logged


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

class TestCleanup:
    def test_label_scoped_sync_first_then_basename(self):
        from clusterbuster.driver.cleanup import do_cleanup
        mock = MockClusterInterface()
        cfg = _make_config()
        do_cleanup(mock, cfg)
        delete_calls = mock._calls_of("delete")
        assert len(delete_calls) == 2
        first_selector = str(delete_calls[0][1])
        second_selector = str(delete_calls[1][1])
        assert "test-cb-sync" in first_selector
        assert "test-cb" in second_selector

    def test_noop_dryrun(self):
        from clusterbuster.driver.cleanup import do_cleanup
        mock = MockClusterInterface()
        assert do_cleanup(mock, _make_config(doit=False)) is True
        assert len(mock.calls) == 0

    def test_force_path_with_timeout(self):
        from clusterbuster.driver.cleanup import do_cleanup
        mock = MockClusterInterface()
        mock._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=1, stdout="", stderr="fail"
        ))
        mock._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=1, stdout="", stderr="fail"
        ))
        cfg = _make_config(force_cleanup_timeout="30s")
        do_cleanup(mock, cfg)
        delete_calls = mock._calls_of("delete")
        # sync + basename (both fail), then force escalation:
        # oc delete all (clear resources), then oc delete namespace
        assert len(delete_calls) == 4
        force_all = delete_calls[2]
        assert force_all[2].get("force") is True
        assert force_all[2].get("timeout") == "30s"
        assert force_all[1][0] == "all"
        force_ns = delete_calls[3]
        assert force_ns[2].get("force") is True
        assert force_ns[1][0] == "namespace"

    def test_force_cleanup_vm(self):
        """VM deployments get an extra sync-label all-delete before the
        basename all-delete, matching bash line 4676."""
        from clusterbuster.driver.cleanup import do_cleanup
        mock = MockClusterInterface()
        mock._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=1, stdout="", stderr="fail"
        ))
        mock._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=1, stdout="", stderr="fail"
        ))
        cfg = _make_config(deployment_type="vm", force_cleanup_timeout="30s")
        do_cleanup(mock, cfg)
        delete_calls = mock._calls_of("delete")
        # sync + basename (both fail), then force escalation:
        # vm sync all-delete, basename all-delete, namespace delete
        assert len(delete_calls) == 5
        vm_sync = delete_calls[2]
        assert vm_sync[1][0] == "all"
        assert f"-l{cfg.basename}-sync" in vm_sync[1]
        assert vm_sync[2].get("force") is True
        force_all = delete_calls[3]
        assert force_all[1][0] == "all"
        assert f"-l{cfg.basename}" in force_all[1]
        force_ns = delete_calls[4]
        assert force_ns[1][0] == "namespace"
        assert force_ns[2].get("force") is True

    def test_drop_host_caches_pinned(self):
        from clusterbuster.driver.cleanup import drop_host_caches
        mock = MockClusterInterface()
        cfg = _make_config(
            drop_node_cache=True,
            pin_nodes={"dep0": "w1", "dep1": "w2"},
        )
        drop_host_caches(mock, cfg)
        debug_calls = mock._calls_of("debug_node")
        assert {c[1][0] for c in debug_calls} == {"w1", "w2"}

    def test_drop_host_caches_all_workers(self):
        from clusterbuster.driver.cleanup import drop_host_caches
        mock = MockClusterInterface()
        mock._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=0,
            stdout="node/w1\nnode/w2\nnode/w3\n", stderr=""
        ))
        cfg = _make_config(drop_node_cache=True, drop_all_node_cache=True)
        drop_host_caches(mock, cfg)
        assert len(mock._calls_of("debug_node")) == 3


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class TestMetrics:
    def test_supports_metrics_true(self):
        from clusterbuster.driver.metrics import supports_metrics
        assert supports_metrics(MockClusterInterface()) is True

    def test_supports_metrics_false(self):
        from clusterbuster.driver.metrics import supports_metrics
        mock = MockClusterInterface()
        mock._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=1, stdout="", stderr="not found"
        ))
        assert supports_metrics(mock) is False

    def test_start_timestamps_with_prometheus(self):
        from clusterbuster.driver.metrics import set_start_timestamps
        mock = MockClusterInterface()
        mock._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=0, stdout="", stderr=""
        ))
        mock._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=0, stdout="9876543.21\n", stderr=""
        ))
        first, prom, second = set_start_timestamps(mock)
        assert prom == pytest.approx(9876543.21)
        assert first <= second

    def test_start_timestamps_without_prometheus(self):
        from clusterbuster.driver.metrics import set_start_timestamps
        mock = MockClusterInterface()
        mock._run_results.append(subprocess.CompletedProcess(
            args=["oc"], returncode=1, stdout="", stderr=""
        ))
        first, prom, second = set_start_timestamps(mock)
        assert first == prom == second


# ---------------------------------------------------------------------------
# Interruptible run (P1-7)
# ---------------------------------------------------------------------------

class TestInterruptibleRun:
    @patch("clusterbuster.driver.orchestrator.subprocess.Popen")
    def test_registers_and_deregisters_subprocess(self, mock_popen_cls):
        proc_mock = MagicMock()
        proc_mock.communicate.return_value = ("output", "")
        proc_mock.returncode = 0
        proc_mock.args = ["oc", "get", "pod"]
        mock_popen_cls.return_value = proc_mock

        ctx = _make_ctx()
        result = _interruptible_run(ctx, "get", "pod")
        assert result.returncode == 0
        assert result.stdout == "output"
        assert len(ctx._active_subprocesses) == 0

    @patch("clusterbuster.driver.orchestrator.subprocess.Popen")
    def test_passes_stdin_data(self, mock_popen_cls):
        proc_mock = MagicMock()
        proc_mock.communicate.return_value = ("", "")
        proc_mock.returncode = 0
        proc_mock.args = ["oc", "exec"]
        mock_popen_cls.return_value = proc_mock

        ctx = _make_ctx()
        _interruptible_run(ctx, "exec", "-n", "ns", "pod", stdin_data="hello")
        proc_mock.communicate.assert_called_once_with(input="hello")

    def test_terminate_subprocesses_kills_registered(self):
        ctx = _make_ctx()
        proc = MagicMock()
        with ctx._subprocess_lock:
            ctx._active_subprocesses.append(proc)
        _terminate_subprocesses(ctx)
        proc.kill.assert_called_once()


# ---------------------------------------------------------------------------
# Thread coordination
# ---------------------------------------------------------------------------

class TestThreadCoordination:
    def test_join_threads_uses_lock(self):
        ctx = _make_ctx()
        done = threading.Event()

        def _fn():
            done.wait()

        t = threading.Thread(target=_fn, daemon=True, name="test")
        with ctx._threads_lock:
            ctx.threads.append(t)
        t.start()

        import clusterbuster.driver.orchestrator as orch
        orig = orch._JOIN_TIMEOUT
        orch._JOIN_TIMEOUT = 0.1
        try:
            _join_threads(ctx)
        finally:
            orch._JOIN_TIMEOUT = orig
            done.set()
            t.join(timeout=2)

    def test_any_thread_done_coordination(self):
        ctx = _make_ctx()

        def _thread_fn():
            time.sleep(0.05)
            ctx.any_thread_done.set()

        t = threading.Thread(target=_thread_fn, daemon=True)
        t.start()
        assert ctx.any_thread_done.wait(timeout=2.0)
        t.join(timeout=2)


# ---------------------------------------------------------------------------
# RunFailed
# ---------------------------------------------------------------------------

class TestRunFailed:
    def test_exception_message(self):
        with pytest.raises(RunFailed, match="specific error"):
            raise RunFailed("specific error")

    def test_aborts_create_all_objects(self):
        ctx = _make_ctx()
        ctx.run_failed.set()
        ctx.failure_reason = "prior"
        with pytest.raises(RunFailed, match="prior"):
            create_all_objects(ctx, "namespaces")


# ---------------------------------------------------------------------------
# pod_flags runtime fields
# ---------------------------------------------------------------------------

class TestPodFlags:
    def test_runtime_values_included(self):
        from clusterbuster.driver.workload_registry import pod_flags, ArglistContext
        cfg = _make_config()
        ctx = ArglistContext(
            mountdir="/mnt", namespace="ns1", instance=0,
            secret_count=0, replicas=1, containers_per_pod=1,
            container_index=0, config=cfg,
            sync_host="sync.ns.svc.cluster.local",
            basetime=1234567890.0, crtime=1234567891.0,
            drop_cache_host="dc.ns.svc.cluster.local",
        )
        flags = pod_flags(ctx)
        flag_str = " ".join(flags)
        assert "--cb-sync-host=sync.ns.svc.cluster.local" in flag_str
        assert "--cb-basetime=1234567890.0" in flag_str
        assert "--cb-crtime=1234567891.0" in flag_str
        assert "--cb-drop-cache-host=dc.ns.svc.cluster.local" in flag_str

    def test_default_runtime_values(self):
        from clusterbuster.driver.workload_registry import pod_flags, ArglistContext
        cfg = _make_config()
        ctx = ArglistContext(
            mountdir="/mnt", namespace="ns1", instance=0,
            secret_count=0, replicas=1, containers_per_pod=1,
            container_index=0, config=cfg,
        )
        flags = pod_flags(ctx)
        flag_str = " ".join(flags)
        assert "--cb-basetime=0" in flag_str
        assert "--cb-sync-host=" in flag_str


# ---------------------------------------------------------------------------
# Error injection
# ---------------------------------------------------------------------------

class TestErrorInjection:
    def test_inject_error_raises_when_key_present(self):
        ctx = _make_ctx(injected_errors={"create_deployment": "1"})
        with pytest.raises(RunFailed, match="Injected error at 'create_deployment'"):
            _check_inject_error(ctx, "create_deployment")

    def test_inject_error_silent_when_key_absent(self):
        ctx = _make_ctx(injected_errors={"create_deployment": "1"})
        _check_inject_error(ctx, "sync")

    def test_inject_error_silent_when_no_errors(self):
        ctx = _make_ctx()
        _check_inject_error(ctx, "create_deployment")

    def test_unrecognized_key_ignored(self):
        ctx = _make_ctx(injected_errors={"nonexistent_point": "1"})
        _check_inject_error(ctx, "create_deployment")
        _check_inject_error(ctx, "cleanup")
        _check_inject_error(ctx, "sync")
        _check_inject_error(ctx, "monitor")

    @patch("clusterbuster.driver.workload_registry.get_workload")
    def test_inject_create_deployment_blocks_deployments(self, mock_get_wl):
        mock_get_wl.return_value = _MockWorkload()
        ctx = _make_ctx(injected_errors={"create_deployment": "1"})
        allocate_namespaces(ctx)
        with pytest.raises(RunFailed, match="create_deployment"):
            create_all_objects(ctx, "deployments")

    def test_inject_monitor_sets_run_failed(self):
        from clusterbuster.driver.orchestrator import _monitor_thread_fn
        ctx = _make_ctx(injected_errors={"monitor": "1"})
        mon_mod = MagicMock()
        _monitor_thread_fn(ctx, mon_mod)
        assert ctx.run_failed.is_set()
        assert "monitor" in ctx.failure_reason.lower()
        mon_mod.monitor_pods.assert_not_called()

    def test_inject_cleanup_raises_in_cleanup_context(self):
        ctx = _make_ctx(injected_errors={"cleanup": "1"})
        with pytest.raises(RunFailed, match="cleanup"):
            _check_inject_error(ctx, "cleanup")

    @patch("clusterbuster.driver.orchestrator._check_pods_direct")
    @patch("clusterbuster.driver.orchestrator._monitor_thread_fn")
    @patch("clusterbuster.driver.workload_registry.get_workload")
    def test_nested_cleanup_failure_preserves_original(
        self, mock_get_wl, mock_mon_fn, mock_check
    ):
        """Workload failure + cleanup failure: original reason preserved, no hang."""
        mock_get_wl.return_value = _MockWorkload()

        cfg = _make_config(
            cleanup_always=True,
            injected_errors={"create_deployment": "1", "cleanup": "1"},
        )
        from clusterbuster.driver.orchestrator import run
        result = run(cfg)
        assert result == 1
