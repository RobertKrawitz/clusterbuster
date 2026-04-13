# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""Unit tests for clusterbuster.driver.manifests — ManifestBuilder."""

from __future__ import annotations

from clusterbuster.driver.config import ClusterbusterConfigBuilder
from clusterbuster.driver.manifests import ManifestBuilder


def _config(**overrides):
    b = ClusterbusterConfigBuilder()
    b.requested_workload = "cpusoaker"
    for k, v in overrides.items():
        setattr(b, k, v)
    return b.build()


class TestStandardLabels:
    def test_basic_labels(self):
        cfg = _config()
        mb = ManifestBuilder(cfg)
        labels = mb.standard_labels("worker", workload="cpusoaker", namespace="ns1")
        bn = cfg.basename
        assert labels[f"{bn}-uuid"] == cfg.uuid
        assert labels[f"{bn}-id"] == cfg.uuid
        assert labels[bn] == "true"
        assert labels[f"{bn}base"] == "true"
        assert labels[f"{bn}-worker"] == cfg.uuid
        assert labels[f"{bn}-objtype"] == "worker"
        assert labels[f"{bn}-workload"] == "true"
        assert labels[f"{bn}-cpusoaker"] == cfg.uuid

    def test_xuuid_label(self):
        cfg = _config(uuid="test-uuid-123")
        mb = ManifestBuilder(cfg)
        labels = mb.standard_labels("worker")
        assert labels[f"{cfg.basename}-xuuid"] == cfg.xuuid
        assert cfg.xuuid != "test-uuid-123"  # xuuid is always fresh

    def test_per_type_selectors(self):
        cfg = _config(uuid="u1")
        mb = ManifestBuilder(cfg)
        labels = mb.standard_labels("worker")
        bn = cfg.basename
        assert labels[f"{bn}-worker"] == "u1"
        assert labels[f"{bn}-x-worker"] == cfg.xuuid

    def test_monitor_label_worker(self):
        cfg = _config()
        mb = ManifestBuilder(cfg)
        labels = mb.standard_labels("worker")
        assert labels[f"{cfg.basename}-monitor"] == cfg.xuuid

    def test_monitor_label_sync(self):
        cfg = _config()
        mb = ManifestBuilder(cfg)
        labels = mb.standard_labels("sync")
        assert labels[f"{cfg.basename}-monitor"] == cfg.xuuid

    def test_no_monitor_for_namespace(self):
        cfg = _config()
        mb = ManifestBuilder(cfg)
        labels = mb.standard_labels("namespace")
        assert f"{cfg.basename}-monitor" not in labels

    def test_cross_basename_labels(self):
        cfg = _config(basename="mybase")
        mb = ManifestBuilder(cfg)
        labels = mb.standard_labels("worker")
        assert labels["clusterbusterbase"] == "true"
        assert labels["clusterbuster-objtype"] == "worker"
        assert labels["clusterbuster-workload"] == "true"

    def test_default_basename_keys_match_cross_basename(self):
        cfg = _config()
        mb = ManifestBuilder(cfg)
        labels = mb.standard_labels("worker")
        assert cfg.basename == "clusterbuster"
        # When basename is "clusterbuster", {bn}base == clusterbusterbase and
        # {bn}-objtype == clusterbuster-objtype — same keys that cross-basename
        # would produce, so the cross-basename block simply does not fire.
        assert labels["clusterbusterbase"] == "true"
        assert labels["clusterbuster-objtype"] == "worker"

    def test_no_worker_label_flag(self):
        cfg = _config()
        mb = ManifestBuilder(cfg)
        labels = mb.standard_labels("namespace", worker_label=False)
        assert f"{cfg.basename}-workload" not in labels

    def test_workload_name_app_labels(self):
        cfg = _config()
        mb = ManifestBuilder(cfg)
        labels = mb.standard_labels(
            "workload", workload="fio", namespace="ns1",
            instance="i0", replica="r0",
        )
        assert labels["name"] == "ns1-fio-i0"
        assert labels["app"] == "ns1-fio"
        assert labels["instance"] == "ns1-fio-i0"
        assert labels["fio"] == "true"

    def test_workload_labels_no_namespace(self):
        cfg = _config()
        mb = ManifestBuilder(cfg)
        labels = mb.standard_labels(
            "workload", workload="fio", instance="i0",
        )
        assert labels["name"] == "fio-i0"
        assert labels["app"] == "fio"
        assert not labels["name"].startswith("-")

    def test_user_labels_bare_key(self):
        cfg = _config(pod_labels=["mykey"])
        mb = ManifestBuilder(cfg)
        labels = mb.standard_labels("worker")
        assert labels["mykey"] == "true"

    def test_user_labels_key_value(self):
        cfg = _config(pod_labels=["app=test"])
        mb = ManifestBuilder(cfg)
        labels = mb.standard_labels("worker")
        assert labels["app"] == "test"


class TestAffinity:
    def test_no_affinity(self):
        cfg = _config()
        mb = ManifestBuilder(cfg)
        assert mb.affinity(0) is None

    def test_pod_affinity(self):
        cfg = _config()
        mb = ManifestBuilder(cfg)
        aff = mb.affinity(1)
        assert "podAffinity" in aff
        assert "requiredDuringSchedulingIgnoredDuringExecution" in aff["podAffinity"]

    def test_pod_anti_affinity(self):
        cfg = _config()
        mb = ManifestBuilder(cfg)
        aff = mb.affinity(2)
        assert "podAntiAffinity" in aff


class TestTolerations:
    def test_no_tolerations(self):
        cfg = _config()
        mb = ManifestBuilder(cfg)
        assert mb.tolerations() is None

    def test_tolerations_parsed(self):
        cfg = _config(tolerations=["mykey:Equal:NoSchedule"])
        mb = ManifestBuilder(cfg)
        tols = mb.tolerations()
        assert len(tols) == 1
        assert tols[0]["key"] == "mykey"
        assert tols[0]["operator"] == "Equal"
        assert tols[0]["effect"] == "NoSchedule"

    def test_tolerations_key_only(self):
        cfg = _config(tolerations=["mykey"])
        mb = ManifestBuilder(cfg)
        tols = mb.tolerations()
        assert tols[0]["key"] == "mykey"
        assert tols[0]["operator"] == "Exists"


class TestResources:
    def test_no_resources(self):
        cfg = _config()
        mb = ManifestBuilder(cfg)
        assert mb.resources() is None

    def test_requests_and_limits(self):
        cfg = _config(
            resource_requests=["cpu=100m", "memory=256Mi"],
            resource_limits=["cpu=1"],
        )
        mb = ManifestBuilder(cfg)
        res = mb.resources()
        assert res["requests"]["cpu"] == "100m"
        assert res["requests"]["memory"] == "256Mi"
        assert res["limits"]["cpu"] == "1"


class TestSecurityContext:
    def test_restricted(self):
        cfg = _config()
        mb = ManifestBuilder(cfg)
        sc = mb.security_context()
        assert sc["allowPrivilegeEscalation"] is False

    def test_privileged(self):
        cfg = _config(create_pods_privileged=True)
        mb = ManifestBuilder(cfg)
        sc = mb.security_context()
        assert sc["privileged"] is True


class TestAnnotations:
    def test_colon_space_format(self):
        cfg = _config(
            pod_annotations=["io.kubernetes.cri-o.TrustedSandbox: \"true\""]
        )
        mb = ManifestBuilder(cfg)
        ann = mb.annotations()
        assert "io.kubernetes.cri-o.TrustedSandbox" in ann

    def test_equals_format_compat(self):
        cfg = _config(pod_annotations=["mykey=myval"])
        mb = ManifestBuilder(cfg)
        ann = mb.annotations()
        assert ann["mykey"] == "myval"

    def test_scoped_annotation(self):
        cfg = _config(pod_annotations=[":client:io.foo: bar"])
        mb = ManifestBuilder(cfg)
        ann_client = mb.annotations("client")
        ann_server = mb.annotations("server")
        assert "io.foo" in ann_client
        assert "io.foo" not in ann_server

    def test_kata_annotation_injected(self):
        cfg = _config(runtime_class="kata", virtiofsd_direct=True)
        mb = ManifestBuilder(cfg)
        ann = mb.annotations()
        key = "io.katacontainers.config.hypervisor.virtio_fs_extra_args"
        assert key in ann
        import json
        parsed = json.loads(ann[key])
        assert "-o" in parsed
        assert "allow_direct_io" in parsed

    def test_kata_annotation_compact_json(self):
        """Annotation value uses compact JSON (no spaces after separators)."""
        cfg = _config(runtime_class="kata", virtiofsd_direct=True)
        mb = ManifestBuilder(cfg)
        ann = mb.annotations()
        val = ann["io.katacontainers.config.hypervisor.virtio_fs_extra_args"]
        assert ", " not in val
        assert ": " not in val

    def test_kata_variant_startswith(self):
        """Kata variants like 'kata-qemu' also trigger annotation."""
        cfg = _config(runtime_class="kata-qemu", virtiofsd_direct=True)
        mb = ManifestBuilder(cfg)
        ann = mb.annotations()
        assert "io.katacontainers.config.hypervisor.virtio_fs_extra_args" in ann

    def test_kata_annotation_not_injected_for_runc(self):
        cfg = _config(runtime_class="runc", virtiofsd_direct=True)
        mb = ManifestBuilder(cfg)
        ann = mb.annotations()
        assert "io.katacontainers.config.hypervisor.virtio_fs_extra_args" not in ann

    def test_kata_annotation_not_injected_without_virtiofsd(self):
        cfg = _config(runtime_class="kata", virtiofsd_direct=False, virtiofsd_writeback=False)
        mb = ManifestBuilder(cfg)
        ann = mb.annotations()
        assert "io.katacontainers.config.hypervisor.virtio_fs_extra_args" not in ann

    def test_kata_annotation_with_multiple_args(self):
        """Both direct and writeback produce multiple entries."""
        cfg = _config(
            runtime_class="kata",
            virtiofsd_direct=True,
            virtiofsd_writeback=True,
        )
        mb = ManifestBuilder(cfg)
        ann = mb.annotations()
        import json
        parsed = json.loads(
            ann["io.katacontainers.config.hypervisor.virtio_fs_extra_args"]
        )
        assert parsed == ["-o", "allow_direct_io", "-o", "writeback"]

    def test_kata_annotation_threadpoolsize(self):
        cfg = _config(
            runtime_class="kata",
            virtiofsd_direct=False,
            virtiofsd_writeback=False,
            virtiofsd_threadpoolsize=4,
        )
        mb = ManifestBuilder(cfg)
        ann = mb.annotations()
        import json
        parsed = json.loads(
            ann["io.katacontainers.config.hypervisor.virtio_fs_extra_args"]
        )
        assert "--thread-pool-size=4" in parsed


class TestNamespaceManifest:
    def test_namespace_structure(self):
        cfg = _config()
        mb = ManifestBuilder(cfg)
        ns = mb.namespace("test-ns")
        assert ns["kind"] == "Namespace"
        assert ns["metadata"]["name"] == "test-ns"
        assert "pod-security.kubernetes.io/enforce" in ns["metadata"]["labels"]

    def test_namespace_policy(self):
        cfg = _config()
        mb = ManifestBuilder(cfg)
        ns = mb.namespace("test-ns", policy="privileged")
        assert ns["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "privileged"


class TestSecretManifest:
    def test_secret_structure(self):
        cfg = _config()
        mb = ManifestBuilder(cfg)
        s = mb.secret("my-secret", "my-ns", {"key": "dmFsdWU="})
        assert s["kind"] == "Secret"
        assert s["type"] == "Opaque"
        assert s["data"]["key"] == "dmFsdWU="


class TestServiceManifest:
    def test_headless_service(self):
        cfg = _config(headless_services=True)
        mb = ManifestBuilder(cfg)
        ports = [{"port": 80, "protocol": "TCP"}]
        svc = mb.service("my-svc", "my-ns", ports)
        assert svc["spec"]["clusterIP"] == "None"

    def test_cluster_ip_service(self):
        cfg = _config(headless_services=False)
        mb = ManifestBuilder(cfg)
        ports = [{"port": 80, "protocol": "TCP"}]
        svc = mb.service("my-svc", "my-ns", ports, headless=False)
        assert "clusterIP" not in svc["spec"]


class TestPodManifest:
    def test_pod_structure(self):
        cfg = _config()
        mb = ManifestBuilder(cfg)
        containers = [mb.container("worker", image="my:image", command=["sleep", "10"])]
        pod = mb.pod("my-pod", "my-ns", containers)
        assert pod["kind"] == "Pod"
        assert pod["metadata"]["name"] == "my-pod"
        assert pod["spec"]["containers"][0]["name"] == "worker"
        assert pod["spec"]["terminationGracePeriodSeconds"] == 1

    def test_pod_with_scheduler(self):
        cfg = _config(scheduler="my-scheduler")
        mb = ManifestBuilder(cfg)
        containers = [mb.container("worker")]
        pod = mb.pod("p", "ns", containers)
        assert pod["spec"]["schedulerName"] == "my-scheduler"

    def test_pod_with_node_selector(self):
        cfg = _config()
        mb = ManifestBuilder(cfg)
        containers = [mb.container("worker")]
        pod = mb.pod("p", "ns", containers)
        assert cfg.node_selector in pod["spec"]["nodeSelector"]


class TestDeploymentManifest:
    def test_deployment_structure(self):
        cfg = _config()
        mb = ManifestBuilder(cfg)
        containers = [mb.container("worker")]
        dep = mb.deployment("my-dep", "my-ns", 3, containers)
        assert dep["kind"] == "Deployment"
        assert dep["spec"]["replicas"] == 3
        assert "template" in dep["spec"]

    def test_replicaset_structure(self):
        cfg = _config()
        mb = ManifestBuilder(cfg)
        containers = [mb.container("worker")]
        rs = mb.replicaset("my-rs", "my-ns", 2, containers)
        assert rs["kind"] == "ReplicaSet"
        assert rs["spec"]["replicas"] == 2


class TestLivenessProbe:
    def test_no_probe_by_default(self):
        cfg = _config()
        mb = ManifestBuilder(cfg)
        assert mb.liveness_probe() is None

    def test_probe_configured(self):
        cfg = _config(liveness_probe_frequency=10, liveness_probe_sleep_time=5)
        mb = ManifestBuilder(cfg)
        probe = mb.liveness_probe()
        assert probe is not None
        assert probe["periodSeconds"] == 10
        assert probe["initialDelaySeconds"] == 10
        assert probe["exec"]["command"] == ["/bin/sleep", "5"]
