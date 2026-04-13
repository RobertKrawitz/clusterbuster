# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
"""End-to-end tests that run clusterbuster against a live OpenShift cluster.

Kata and CNV tests are automatically skipped when the respective
feature is not installed on the cluster.  All e2e tests are skipped
when no ``oc`` / ``kubectl`` binary is available.

Artifacts (report JSON, logs) are saved under a per-run directory
inside ``tests/e2e-artifacts/``.  The directory is created fresh at the
start of each pytest session.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_ARTIFACTS_ROOT = Path(__file__).resolve().parent / "e2e-artifacts"


def _has_oc() -> bool:
    return bool(shutil.which("oc") or shutil.which("kubectl"))


def _oc_bin() -> str:
    return shutil.which("oc") or shutil.which("kubectl") or ""


def _has_kata() -> bool:
    oc = _oc_bin()
    if not oc:
        return False
    proc = subprocess.run(
        [oc, "get", "runtimeclass", "kata"],
        capture_output=True, timeout=30,
    )
    return proc.returncode == 0


def _has_cnv() -> bool:
    oc = _oc_bin()
    if not oc:
        return False
    proc = subprocess.run(
        [oc, "get", "hyperconverged", "-A", "--no-headers"],
        capture_output=True, text=True, timeout=30,
    )
    return proc.returncode == 0 and bool(proc.stdout.strip())


requires_cluster = pytest.mark.skipif(
    not _has_oc(), reason="No oc/kubectl on PATH; cluster required"
)
requires_kata = pytest.mark.skipif(
    not _has_kata(), reason="Kata runtime class not installed on cluster"
)
requires_cnv = pytest.mark.skipif(
    not _has_cnv(), reason="CNV (HyperConverged) not installed on cluster"
)


# ---------------------------------------------------------------------------
# Artifact directory management
# ---------------------------------------------------------------------------

def _session_artifact_dir() -> Path:
    """Return a timestamped directory for this pytest session's artifacts."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    d = _ARTIFACTS_ROOT / stamp
    d.mkdir(parents=True, exist_ok=True)
    return d


_SESSION_DIR: Path | None = None


def _get_session_dir() -> Path:
    global _SESSION_DIR
    if _SESSION_DIR is None:
        _SESSION_DIR = _session_artifact_dir()
    return _SESSION_DIR


def _artifact_dir_for_test(request: pytest.FixtureRequest) -> Path:
    """Build ``<session>/<ClassName>/<test_name>`` artifact path."""
    cls = request.cls.__name__ if request.cls else "global"
    d = _get_session_dir() / cls / request.node.name
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture()
def artifact_dir(request: pytest.FixtureRequest) -> Path:
    """Per-test artifact directory fixture."""
    return _artifact_dir_for_test(request)


# ---------------------------------------------------------------------------
# Pod workloads
# ---------------------------------------------------------------------------

@requires_cluster
class TestE2EPodWorkloads:
    """Basic pod-type workloads on a live cluster."""

    @staticmethod
    def _run(args: list[str], artifact_dir: Path, timeout: int = 300) -> int:
        from clusterbuster.driver import run_from_argv
        return run_from_argv([*args, f"--artifact-dir={artifact_dir}"])

    def test_cpusoaker_pod(self, artifact_dir):
        rc = self._run([
            "-w", "cpusoaker",
            "--workload-runtime=10",
            "--namespaces=1",
            "--replicas=1",
            "--cleanup-always=1",
        ], artifact_dir)
        assert rc == 0

    def test_sleep_pod(self, artifact_dir):
        rc = self._run([
            "-w", "sleep",
            "--workload-runtime=5",
            "--namespaces=1",
            "--replicas=1",
            "--cleanup-always=1",
        ], artifact_dir)
        assert rc == 0

    def test_memory_pod(self, artifact_dir):
        rc = self._run([
            "-w", "memory",
            "--workload-runtime=10",
            "--memory-size=64M",
            "--namespaces=1",
            "--replicas=1",
            "--cleanup-always=1",
        ], artifact_dir)
        assert rc == 0

    def test_files_pod(self, artifact_dir):
        rc = self._run([
            "-w", "files",
            "--workload-runtime=10",
            "--namespaces=1",
            "--replicas=1",
            "--cleanup-always=1",
        ], artifact_dir)
        assert rc == 0


# ---------------------------------------------------------------------------
# Kata workloads
# ---------------------------------------------------------------------------

@requires_cluster
@requires_kata
class TestE2EKata:
    """Kata runtime class workloads on a live cluster.

    Skipped automatically if the kata RuntimeClass is not present.
    """

    @staticmethod
    def _run(args: list[str], artifact_dir: Path) -> int:
        from clusterbuster.driver import run_from_argv
        return run_from_argv([*args, f"--artifact-dir={artifact_dir}"])

    def test_cpusoaker_kata(self, artifact_dir):
        rc = self._run([
            "-w", "cpusoaker",
            "--kata",
            "--workload-runtime=10",
            "--namespaces=1",
            "--replicas=1",
            "--cleanup-always=1",
        ], artifact_dir)
        assert rc == 0

    def test_sleep_kata(self, artifact_dir):
        rc = self._run([
            "-w", "sleep",
            "--kata",
            "--workload-runtime=5",
            "--namespaces=1",
            "--replicas=1",
            "--cleanup-always=1",
        ], artifact_dir)
        assert rc == 0

    def test_memory_kata(self, artifact_dir):
        rc = self._run([
            "-w", "memory",
            "--kata",
            "--workload-runtime=10",
            "--memory-size=64M",
            "--namespaces=1",
            "--replicas=1",
            "--cleanup-always=1",
        ], artifact_dir)
        assert rc == 0

    def test_fio_kata(self, artifact_dir):
        rc = self._run([
            "-w", "fio",
            "--kata",
            "--workload-runtime=15",
            "--fio-block-size=4k",
            "--fio-pattern=read",
            "--namespaces=1",
            "--replicas=1",
            "--volume=:emptydir:/var/opt/clusterbuster",
            "--cleanup-always=1",
        ], artifact_dir)
        assert rc == 0


# ---------------------------------------------------------------------------
# CNV / KubeVirt VM workloads
# ---------------------------------------------------------------------------

@requires_cluster
@requires_cnv
class TestE2ECNV:
    """CNV / KubeVirt VM workloads on a live cluster.

    Skipped automatically if HyperConverged CR is not present.
    """

    @staticmethod
    def _run(args: list[str], artifact_dir: Path) -> int:
        from clusterbuster.driver import run_from_argv
        return run_from_argv([*args, f"--artifact-dir={artifact_dir}"])

    def test_cpusoaker_vm(self, artifact_dir):
        rc = self._run([
            "-w", "cpusoaker",
            "--deployment-type=vm",
            "--workload-runtime=30",
            "--namespaces=1",
            "--replicas=1",
            "--cleanup-always=1",
        ], artifact_dir)
        assert rc == 0

    def test_sleep_vm(self, artifact_dir):
        rc = self._run([
            "-w", "sleep",
            "--deployment-type=vm",
            "--workload-runtime=15",
            "--namespaces=1",
            "--replicas=1",
            "--cleanup-always=1",
        ], artifact_dir)
        assert rc == 0

    def test_memory_vm(self, artifact_dir):
        rc = self._run([
            "-w", "memory",
            "--deployment-type=vm",
            "--workload-runtime=30",
            "--memory-size=64M",
            "--namespaces=1",
            "--replicas=1",
            "--cleanup-always=1",
        ], artifact_dir)
        assert rc == 0

    def test_fio_vm_emptydisk(self, artifact_dir):
        rc = self._run([
            "-w", "fio",
            "--deployment-type=vm",
            "--workload-runtime=30",
            "--fio-block-size=4k",
            "--fio-pattern=read",
            "--volume=scratch:emptydisk:/scratch:size=5Gi",
            "--namespaces=1",
            "--replicas=1",
            "--cleanup-always=1",
        ], artifact_dir)
        assert rc == 0


# ---------------------------------------------------------------------------
# Namespace reuse / cleanup semantics
# ---------------------------------------------------------------------------

def _oc(*args: str) -> subprocess.CompletedProcess[str]:
    """Run an oc/kubectl command and return the result."""
    return subprocess.run(
        [_oc_bin(), *args],
        capture_output=True, text=True, timeout=60,
    )


@requires_cluster
class TestE2ENamespaceReuse:
    """Verify --removenamespaces=0 keeps namespaces while cleaning objects.

    This tests the workflow where a user wants to reuse namespaces
    (e.g. because they contain PVCs) across multiple runs.
    """

    BASENAME = "clusterbuster"

    @staticmethod
    def _run(args: list[str], artifact_dir: Path) -> int:
        from clusterbuster.driver import run_from_argv
        return run_from_argv([*args, f"--artifact-dir={artifact_dir}"])

    @staticmethod
    def _ns_exists(ns: str) -> bool:
        return _oc("get", "namespace", ns).returncode == 0

    @staticmethod
    def _ns_has_resource(ns: str, resource: str, label: str) -> bool:
        result = _oc(
            "get", resource, "-n", ns, f"-l{label}",
            "-o", "jsonpath={.items[*].metadata.name}",
        )
        return result.returncode == 0 and bool(result.stdout.strip())

    def _cleanup_all(self):
        """Force-clean everything for a fresh start."""
        _oc("delete", "namespace", f"-l{self.BASENAME}",
            "--ignore-not-found=true")
        _oc("delete", "namespace", f"{self.BASENAME}-sync",
            "--ignore-not-found=true")
        # Wait for namespaces to be fully gone
        for _ in range(60):
            r = _oc("get", "namespace", f"-l{self.BASENAME}",
                    "-o", "jsonpath={.items[*].metadata.name}")
            if r.returncode == 0 and not r.stdout.strip():
                break
            import time
            time.sleep(2)

    def test_namespace_reuse_across_runs(self, artifact_dir):
        """Two consecutive runs reuse the same namespaces."""
        self._cleanup_all()

        rc = self._run([
            "-w", "cpusoaker",
            "--workload-runtime=5",
            "--namespaces=1",
            "--replicas=1",
            "--cleanup=0",
            "--removenamespaces=0",
        ], artifact_dir / "run1")
        assert rc == 0
        assert self._ns_exists(f"{self.BASENAME}-0"), \
            "Namespace should exist after run 1"

        rc = self._run([
            "-w", "sleep",
            "--workload-runtime=5",
            "--namespaces=1",
            "--replicas=1",
            "--cleanup-always=1",
            "--removenamespaces=0",
        ], artifact_dir / "run2")
        assert rc == 0
        assert self._ns_exists(f"{self.BASENAME}-0"), \
            "Namespace should still exist after run 2 (removenamespaces=0)"

    def test_cleanup_removes_all_objects_but_keeps_ns(self, artifact_dir):
        """--removenamespaces=0 cleanup uses ``oc delete all`` semantics:
        workload objects (pods, services) are removed but non-``all``
        types (configmaps, secrets) and the namespace itself are kept.
        """
        self._cleanup_all()

        rc = self._run([
            "-w", "cpusoaker",
            "--workload-runtime=5",
            "--namespaces=1",
            "--replicas=1",
            "--cleanup=0",
            "--precleanup=0",
        ], artifact_dir / "run1")
        assert rc == 0

        ns = f"{self.BASENAME}-0"
        assert self._ns_exists(ns)
        assert self._ns_has_resource(ns, "pod", self.BASENAME), \
            "Pods should exist before cleanup"
        assert self._ns_has_resource(ns, "configmap", self.BASENAME), \
            "ConfigMaps should exist before cleanup"
        assert self._ns_has_resource(ns, "service", self.BASENAME), \
            "Services should exist before cleanup"

        rc = self._run([
            "-w", "cpusoaker",
            "--workload-runtime=5",
            "--namespaces=1",
            "--replicas=1",
            "--cleanup-always=1",
            "--removenamespaces=0",
        ], artifact_dir / "run2")
        assert rc == 0

        assert self._ns_exists(ns), \
            "Namespace must survive removenamespaces=0 cleanup"
        assert not self._ns_has_resource(ns, "pod", self.BASENAME), \
            "Pods (covered by 'all') should be gone after cleanup"
        assert not self._ns_has_resource(ns, "service", self.BASENAME), \
            "Services (covered by 'all') should be gone after cleanup"
        assert self._ns_has_resource(ns, "configmap", self.BASENAME), \
            "ConfigMaps (not in 'all') should survive removenamespaces=0 cleanup"

    def test_precleanup_with_removenamespaces_zero(self, artifact_dir):
        """Precleanup with explicit --removenamespaces=0 preserves
        namespaces while cleaning objects inside.
        """
        self._cleanup_all()

        rc = self._run([
            "-w", "cpusoaker",
            "--workload-runtime=5",
            "--namespaces=2",
            "--replicas=1",
            "--cleanup=0",
            "--precleanup=0",
        ], artifact_dir / "run1")
        assert rc == 0
        assert self._ns_exists(f"{self.BASENAME}-0")
        assert self._ns_exists(f"{self.BASENAME}-1")

        rc = self._run([
            "-w", "sleep",
            "--workload-runtime=5",
            "--namespaces=2",
            "--replicas=1",
            "--cleanup-always=1",
            "--removenamespaces=0",
        ], artifact_dir / "run2")
        assert rc == 0
        assert self._ns_exists(f"{self.BASENAME}-0"), \
            "Namespace 0 should survive"
        assert self._ns_exists(f"{self.BASENAME}-1"), \
            "Namespace 1 should survive"

        self._cleanup_all()

    def test_autodetect_removes_namespaces_minus_one(self, artifact_dir):
        """Auto-detect path: remove_namespaces=-1 (default) detects
        pre-existing labeled namespaces and switches to 0 internally,
        preserving namespaces and their PVCs.
        """
        self._cleanup_all()

        rc = self._run([
            "-w", "cpusoaker",
            "--workload-runtime=5",
            "--namespaces=2",
            "--replicas=1",
            "--cleanup=0",
            "--precleanup=0",
        ], artifact_dir / "run1")
        assert rc == 0
        assert self._ns_exists(f"{self.BASENAME}-0")
        assert self._ns_exists(f"{self.BASENAME}-1")

        rc = self._run([
            "-w", "sleep",
            "--workload-runtime=5",
            "--namespaces=2",
            "--replicas=1",
            "--cleanup-always=1",
        ], artifact_dir / "run2")
        assert rc == 0
        assert self._ns_exists(f"{self.BASENAME}-0"), \
            "Auto-detect should preserve namespace 0"
        assert self._ns_exists(f"{self.BASENAME}-1"), \
            "Auto-detect should preserve namespace 1"

        self._cleanup_all()
