# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""Unit tests for clusterbuster.driver.cli — option parsing."""

from __future__ import annotations

import contextlib
import io
import re

import pytest
import yaml

from clusterbuster.driver.cli import (
    parse_argv,
    process_option,
    process_job_file,
    _bool,
    _set_workload_bytes,
    _set_runtime,
    _process_pin_node,
    _process_interface,
    _process_runtimeclass,
    _artifact_dirname,
    _parse_affinity,
    _parse_antiaffinity,
    _display_name,
    _build_help_text,
    _OPTION_ALIASES,
    _OPTION_DISPATCH,
    _OPTION_DISPLAY_NAMES,
)
from clusterbuster.driver.config import ClusterbusterConfigBuilder


class TestBoolHelper:
    def test_true_values(self):
        for v in ("1", "y", "yes", "true", "True", "YES", "", "t"):
            assert _bool(v) is True, f"_bool({v!r}) should be True"

    def test_false_values(self):
        for v in ("0", "n", "no", "false", "False", "NO", "x", "abc"):
            assert _bool(v) is False, f"_bool({v!r}) should be False"


class TestProcessOption:
    def test_simple_boolean(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "verbose")
        assert b.verbose is True

    def test_boolean_with_value(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "verbose=0")
        assert b.verbose is False

    def test_no_prefix_negation(self):
        b = ClusterbusterConfigBuilder()
        b.verbose = True
        process_option(b, "no_verbose")
        assert b.verbose is False

    def test_string_value(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "workload=fio")
        assert b.requested_workload == "fio"

    def test_integer_value(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "namespaces=5")
        assert b.namespaces == 5

    def test_quiet_inverts_verbose(self):
        b = ClusterbusterConfigBuilder()
        b.verbose = True
        process_option(b, "quiet")
        assert b.verbose is False

    def test_doit_false(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "doit=0")
        assert b.doit is False

    def test_deployment_type(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "deploymenttype=vm")
        assert b.deployment_type == "vm"

    def test_cleanup_coupling(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "cleanupalways=1")
        assert b.cleanup_always is True
        assert b.cleanup is True

    def test_cleanup_false_clears_always(self):
        b = ClusterbusterConfigBuilder()
        b.cleanup_always = True
        process_option(b, "cleanup=0")
        assert b.cleanup is False
        assert b.cleanup_always is False

    def test_sync_toggle(self):
        b = ClusterbusterConfigBuilder()
        assert b.sync_start is True
        process_option(b, "sync")
        assert b.sync_start is False
        process_option(b, "sync")
        assert b.sync_start is True

    def test_affinity_values(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "affinity=1")
        assert b.affinity == 1
        process_option(b, "affinity=anti")
        assert b.affinity == 2
        process_option(b, "affinity=0")
        assert b.affinity == 0

    def test_antiaffinity(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "antiaffinity")
        assert b.affinity == 2

    def test_vm_options(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "vmcores=4")
        process_option(b, "vmmemory=4Gi")
        process_option(b, "vmimage=my:vm")
        assert b.vm_cores == 4
        assert b.vm_memory == "4Gi"
        assert b.vm_image == "my:vm"

    def test_append_options(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "volume=data:emptydir:/mnt/data")
        process_option(b, "volume=disk:pvc:/mnt/disk")
        assert len(b.volumes) == 2

    def test_label_append(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "label=app=test")
        assert "app=test" in b.pod_labels

    def test_pod_prefix_trailing_dash(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "podprefix=myprefix")
        assert b.pod_prefix == "myprefix-"

    def test_pod_prefix_empty(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "podprefix=")
        assert b.pod_prefix == ""

    def test_unknown_option(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "nonexistentoption=foo")
        assert "nonexistentoption=foo" in b.unknown_opts

    # -- Verbosity / run control -----------------------------------------------

    def test_preserve_tmpdir(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "preservetmpdir=1")
        assert b.preserve_tmpdir is True

    def test_forceabort_no_op(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "forceabort")

    # -- Reporting / metrics ---------------------------------------------------

    def test_artifactdir(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "artifactdir=/tmp/mydir")
        assert b.artifactdir == "/tmp/mydir"

    def test_metrics(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "metrics=default")
        assert "default" not in b.metrics_file or "metrics" in b.metrics_file

    def test_metricsfile_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "metricsfile=0")
        assert b.metrics_file == ""

    def test_metricsepoch(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "metricsepoch=5")
        assert b.metrics_epoch == 5

    def test_metricsinterval(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "metricsinterval=60")
        assert b.metrics_interval == 60

    def test_reportformat(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "reportformat=json")
        assert b.report_format == "json"

    def test_rawreport(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "rawreport")
        assert b.report_format == "raw"

    def test_verbosereport(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "verbosereport")
        assert b.report_format == "verbose"

    def test_reportobjectcreation(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "reportobjectcreation=0")
        assert b.report_object_creation is False

    def test_prometheussnapshot(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "prometheussnapshot=1")
        assert b.take_prometheus_snapshot is True

    def test_predelay(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "predelay=5")
        assert b.predelay == 5

    def test_postdelay(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "postdelay=10")
        assert b.postdelay == 10

    def test_stepinterval(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "stepinterval=3")
        assert b.workload_step_interval == 3

    def test_timeout(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "timeout=120")
        assert b.timeout == 120

    def test_failurestatus(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "failurestatus=Error")
        assert b.failure_status == "Error"

    def test_parallellogretrieval(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "parallellogretrieval=25")
        assert b.parallel_log_retrieval == 25

    def test_parallellog_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "parallellog=30")
        assert b.parallel_log_retrieval == 30

    def test_retrievesuccessfullogs(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "retrievesuccessfullogs=1")
        assert b.retrieve_successful_logs is True

    def test_retrievesuc_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "retrievesuc=1")
        assert b.retrieve_successful_logs is True

    def test_logsuc_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "logsuc=1")
        assert b.retrieve_successful_logs is True

    def test_logsuccessful_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "logsuccessful=1")
        assert b.retrieve_successful_logs is True

    def test_compressreport(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "compressreport=1")
        assert b.compress_report is True

    def test_compress_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "compress=1")
        assert b.compress_report is True

    # -- Job / identity --------------------------------------------------------

    def test_jobname(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "jobname=myjob")
        assert b.job_name == "myjob"

    def test_basename(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "basename=mybase")
        assert b.basename == "mybase"

    def test_arch(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "arch=aarch64")
        assert b.arch == "aarch64"

    def test_watchdogtimeout(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "watchdogtimeout=30")
        assert b.sync_watchdog_timeout == 30

    # -- Object / image / deployment -------------------------------------------

    def test_workdir(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "workdir=/mnt/data")
        assert b.common_workdir == "/mnt/data"

    def test_configmapfile(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "configmapfile=/path/to/file")
        assert "/path/to/file" in b.configmap_files

    def test_containerimage(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "containerimage=my:image")
        assert b.container_image == "my:image"

    def test_clusterbusterbaseimage(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "clusterbusterbaseimage=quay.io/test:latest")
        assert b.clusterbuster_base_image == "quay.io/test:latest"

    def test_syncpodimage(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "syncpodimage=quay.io/sync:v1")
        assert b.sync_pod_image_override == "quay.io/sync:v1"

    def test_containers(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "containers=3")
        assert b.containers_per_pod == 3

    def test_containersperpod_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "containersperpod=4")
        assert b.containers_per_pod == 4

    def test_deployments(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "deployments=5")
        assert b.deps_per_namespace == 5

    def test_depspernamespace_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "depspernamespace=6")
        assert b.deps_per_namespace == 6

    def test_depspername_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "depspername=7")
        assert b.deps_per_namespace == 7

    def test_exitatend(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "exitatend=0")
        assert b.exit_at_end is False

    def test_imagepullpolicy(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "imagepullpolicy=Always")
        assert b.image_pull_policy == "Always"

    def test_nodeselector(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "nodeselector=node-role.kubernetes.io/infra")
        assert b.node_selector == "node-role.kubernetes.io/infra"

    def test_processes(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "processes=4")
        assert b.processes_per_pod == 4

    def test_processesperpod_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "processesperpod=8")
        assert b.processes_per_pod == 8

    def test_limit(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "limit=cpu=500m")
        assert "cpu=500m" in b.resource_limits

    def test_limits_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "limits=memory=1Gi")
        assert "memory=1Gi" in b.resource_limits

    def test_request(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "request=cpu=100m")
        assert "cpu=100m" in b.resource_requests

    def test_requests_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "requests=memory=256Mi")
        assert "memory=256Mi" in b.resource_requests

    def test_kata(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "kata")
        assert b.runtime_class == "kata"

    def test_podannotation(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "podannotation=key=val")
        assert "key=val" in b.pod_annotations

    def test_labels_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "labels=env=test")
        assert "env=test" in b.pod_labels

    def test_uuid(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "uuid=abc-123")
        assert b.uuid == "abc-123"

    def test_secrets(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "secrets=3")
        assert b.secrets == 3

    def test_targetdatarate(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "targetdatarate=10M")
        assert b.target_data_rate == 10000000

    def test_tolerate(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "tolerate=key=val:NoSchedule")
        assert "key=val:NoSchedule" in b.tolerations

    def test_toleration_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "toleration=key2=val2:NoExecute")
        assert "key2=val2:NoExecute" in b.tolerations

    # -- Caching / services / probes / privilege / scheduler / affinity --------

    def test_dropcache(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "dropcache=1")
        assert b.drop_node_cache is True

    def test_dropallcache(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "dropallcache=1")
        assert b.drop_all_node_cache is True

    def test_headlessservices(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "headlessservices=0")
        assert b.headless_services is False

    def test_virtiofsdwriteback(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "virtiofsdwriteback=1")
        assert b.virtiofsd_writeback is True

    def test_virtiofsddirect(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "virtiofsddirect=0")
        assert b.virtiofsd_direct is False

    def test_virtiofsdthreadpoolsize(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "virtiofsdthreadpoolsize=8")
        assert b.virtiofsd_threadpoolsize == 8

    def test_virtiofsdthread_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "virtiofsdthread=4")
        assert b.virtiofsd_threadpoolsize == 4

    def test_livenessprobeinterval(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "livenessprobeinterval=30")
        assert b.liveness_probe_frequency == 30

    def test_livenessprobeint_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "livenessprobeint=20")
        assert b.liveness_probe_frequency == 20

    def test_livenessprobesleeptime(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "livenessprobesleeptime=10")
        assert b.liveness_probe_sleep_time == 10

    def test_livenessprobesleep_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "livenessprobesleep=5")
        assert b.liveness_probe_sleep_time == 5

    def test_privileged(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "privileged=1")
        assert b.create_pods_privileged is True

    def test_syncinfirstnamespace(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "syncinfirstnamespace=1")
        assert b.sync_in_first_namespace is True

    def test_syncinfirst_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "syncinfirst=1")
        assert b.sync_in_first_namespace is True

    def test_scheduler(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "scheduler=custom-scheduler")
        assert b.scheduler == "custom-scheduler"

    def test_syncaffinity(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "syncaffinity=1")
        assert b.sync_affinity == 1

    def test_syncantiaffinity(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "syncantiaffinity=1")
        assert b.sync_affinity == 2

    # -- VM options ------------------------------------------------------------

    def test_vmsockets(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "vmsockets=2")
        assert b.vm_sockets == 2

    def test_vmthreads(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "vmthreads=4")
        assert b.vm_threads == 4

    def test_vmgraceperiod(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "vmgraceperiod=60")
        assert b.vm_grace_period == 60

    def test_vmmigrate(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "vmmigrate=0")
        assert b.vm_evict_migrate is False

    def test_vmrunascontainer(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "vmrunascontainer=1")
        assert b.vm_run_as_container is True

    def test_vmuser(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "vmuser=testuser")
        assert b.vm_user == "testuser"

    def test_vmpassword(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "vmpassword=secret")
        assert b.vm_password == "secret"

    def test_vmrunasroot(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "vmrunasroot=1")
        assert b.vm_run_as_root is True

    def test_vmsshkey(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "vmsshkey=/path/to/key")
        assert b.vm_ssh_keyfile == "/path/to/key"

    def test_vmsshkeyfile_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "vmsshkeyfile=/path/to/key2")
        assert b.vm_ssh_keyfile == "/path/to/key2"

    def test_vmstart(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "vmstart=0")
        assert b.vm_start_running is False

    def test_vmstartrunning_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "vmstartrunning=0")
        assert b.vm_start_running is False

    def test_vmrunstrategy(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "vmrunstrategy=Manual")
        assert b.vm_run_strategy == "Manual"

    def test_vmblockmultiqueue(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "vmblockmultiqueue=4")
        assert b.vm_block_multiqueue == 4

    def test_vmblockmultiq_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "vmblockmultiq=2")
        assert b.vm_block_multiqueue == 2

    # -- Object creation tuning ------------------------------------------------

    def test_objectspercall(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "objectspercall=10")
        assert b.objs_per_call == 10

    def test_objspercall_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "objspercall=20")
        assert b.objs_per_call == 20

    def test_sleep_option(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "sleep=0.5")
        assert b.sleeptime == 0.5

    def test_firstdeployment(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "firstdeployment=3")
        assert b.first_deployment == 3

    def test_parallelconfigmaps(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "parallelconfigmaps=6")
        assert b.parallel_configmaps == 6

    def test_parallelnamespaces(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "parallelnamespaces=3")
        assert b.parallel_namespaces == 3

    def test_paralleldeployments(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "paralleldeployments=5")
        assert b.parallel_deployments == 5

    def test_objectspercallconfigmaps(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "objectspercallconfigmaps=15")
        assert b.objs_per_call_configmaps == 15

    def test_objspercallconfigmaps_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "objspercallconfigmaps=12")
        assert b.objs_per_call_configmaps == 12

    def test_objectspercallsecrets(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "objectspercallsecrets=8")
        assert b.objs_per_call_secrets == 8

    def test_objspercallsecrets_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "objspercallsecrets=7")
        assert b.objs_per_call_secrets == 7

    def test_objectspercallnamespaces(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "objectspercallnamespaces=4")
        assert b.objs_per_call_namespaces == 4

    def test_objspercallnamespaces_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "objspercallnamespaces=3")
        assert b.objs_per_call_namespaces == 3

    def test_objectspercalldeployments(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "objectspercalldeployments=9")
        assert b.objs_per_call_deployments == 9

    def test_objspercalldeployments_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "objspercalldeployments=11")
        assert b.objs_per_call_deployments == 11

    def test_sleepbetweenconfigmaps(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "sleepbetweenconfigmaps=0.1")
        assert b.sleep_between_configmaps == 0.1

    def test_sleepbetweensecrets(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "sleepbetweensecrets=0.2")
        assert b.sleep_between_secrets == 0.2

    def test_sleepbetweennamespaces(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "sleepbetweennamespaces=0.3")
        assert b.sleep_between_namespaces == 0.3

    def test_sleepbetweendeployments(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "sleepbetweendeployments=0.5")
        assert b.sleep_between_deployments == 0.5

    def test_waitsecrets(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "waitsecrets=0")
        assert b.wait_for_secrets is False

    def test_scalens(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "scalens=1")
        assert b.scale_ns is True

    def test_scaledeployments(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "scaledeployments=0")
        assert b.scale_deployments is False

    def test_precleanup(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "precleanup=0")
        assert b.precleanup is False

    def test_removenamespaces(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "removenamespaces=1")
        assert b.remove_namespaces == 1

    def test_removenamespaces_false(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "removenamespaces=0")
        assert b.remove_namespaces == 0

    def test_removenamespace_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "removenamespace=1")
        assert b.remove_namespaces == 1

    def test_baseoffset(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "baseoffset=10")
        assert b.baseoffset == 10

    # -- Sync / misc -----------------------------------------------------------

    def test_syncstart(self):
        b = ClusterbusterConfigBuilder()
        b.sync_start = True
        process_option(b, "syncstart")
        assert b.sync_start is False

    def test_waitforever(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "waitforever=1")
        assert b.wait_forever is True

    def test_forcenometrics(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "forcenometrics=1")
        assert b.metrics_support == 0

    def test_forcenometrics_false(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "forcenometrics=0")
        assert b.metrics_support == -1

    def test_podstarttimeout(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "podstarttimeout=300")
        assert b.pod_start_timeout == 300

    def test_podstarttime_alias(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "podstarttime=120")
        assert b.pod_start_timeout == 120

    def test_externalsync(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "externalsync=myhost:8080")
        assert b.sync_host == "myhost"
        assert b.sync_port == 8080

    def test_externalsync_invalid(self):
        b = ClusterbusterConfigBuilder()
        with pytest.raises(SystemExit, match="Invalid externalsync"):
            process_option(b, "externalsync=badvalue")

    def test_externalsync_invalid_port(self):
        b = ClusterbusterConfigBuilder()
        with pytest.raises(SystemExit, match="Invalid externalsync port"):
            process_option(b, "externalsync=host:99999")

    def test_injecterror(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "injecterror=sync_fail=1")
        assert b.injected_errors["sync_fail"] == "1"

    def test_injecterror_bare(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "injecterror=crash")
        assert b.injected_errors["crash"] == "1"

    def test_debug(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "debug=manifests=verbose")
        assert b.debug_conditions["manifests"] == "verbose"

    def test_debug_bare(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "debug=sync")
        assert b.debug_conditions["sync"] == "1"

    def test_report_format_bare(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "report")
        assert b.report_format == "summary"

    def test_report_format_json(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "jsonreport")
        assert b.report_format == "json"

    def test_create_namespaces_only(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "createnamespacesonly")
        assert b.create_namespaces_only is True

    def test_parallel_settings(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "parallel=8")
        process_option(b, "parallelsecrets=4")
        assert b.parallel == 8
        assert b.parallel_secrets == 4

    def test_force_cleanup(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "forcecleanupiknowthisisdangerous")
        assert b.force_cleanup_timeout == "600"
        process_option(b, "forcecleanupiknowthisisdangerous=120")
        assert b.force_cleanup_timeout == "120"

    def test_processed_options_recorded(self):
        b = ClusterbusterConfigBuilder()
        process_option(b, "verbose=1")
        process_option(b, "workload=fio")
        assert "--verbose=1" in b.processed_options
        assert "--workload=fio" in b.processed_options


class TestHelperFunctions:
    def test_set_workload_bytes(self):
        b = ClusterbusterConfigBuilder()
        _set_workload_bytes(b, "1M,2M")
        assert b.bytes_transfer == 1000000
        assert b.bytes_transfer_max == 2000000

    def test_set_workload_bytes_swap(self):
        b = ClusterbusterConfigBuilder()
        _set_workload_bytes(b, "2M,1M")
        assert b.bytes_transfer == 1000000
        assert b.bytes_transfer_max == 2000000

    def test_set_runtime(self):
        b = ClusterbusterConfigBuilder()
        _set_runtime(b, "10,60")
        assert b.workload_run_time == 10
        assert b.workload_run_time_max == 60

    def test_set_runtime_swap(self):
        b = ClusterbusterConfigBuilder()
        _set_runtime(b, "60,10")
        assert b.workload_run_time == 10
        assert b.workload_run_time_max == 60

    def test_process_pin_node_class(self):
        b = ClusterbusterConfigBuilder()
        _process_pin_node(b, "client=node1")
        assert b.pin_nodes["client"] == "node1"

    def test_process_pin_node_multi_class(self):
        b = ClusterbusterConfigBuilder()
        _process_pin_node(b, "client,server=node2")
        assert b.pin_nodes["client"] == "node2"
        assert b.pin_nodes["server"] == "node2"

    def test_process_pin_node_bare(self):
        b = ClusterbusterConfigBuilder()
        _process_pin_node(b, "node3")
        assert b.pin_nodes["default"] == "node3"

    def test_process_interface(self):
        b = ClusterbusterConfigBuilder()
        _process_interface(b, "client=eth1")
        assert b.net_interfaces["client"] == "eth1"

    def test_process_runtimeclass_vm(self):
        b = ClusterbusterConfigBuilder()
        _process_runtimeclass(b, "vm")
        assert b.deployment_type == "vm"
        assert b.runtime_class == "vm"

    def test_process_runtimeclass_pod(self):
        b = ClusterbusterConfigBuilder()
        b.runtime_class = "kata"
        _process_runtimeclass(b, "pod")
        assert b.runtime_class == ""

    def test_process_runtimeclass_mapping(self):
        b = ClusterbusterConfigBuilder()
        _process_runtimeclass(b, "client=kata-qemu")
        assert b.runtime_classes["client"] == "kata-qemu"

    def test_artifact_dirname_timestamp(self):
        result = _artifact_dirname("/tmp/%T/run")
        assert "%T" not in result
        assert "/tmp/" in result

    def test_artifact_dirname_no_timestamp(self):
        assert _artifact_dirname("/tmp/run") == "/tmp/run"

    def test_parse_affinity(self):
        assert _parse_affinity("1") == 1
        assert _parse_affinity("") == 1
        assert _parse_affinity("2") == 2
        assert _parse_affinity("anti") == 2
        assert _parse_affinity("0") == 0

    def test_parse_antiaffinity(self):
        assert _parse_antiaffinity("1") == 2
        assert _parse_antiaffinity("") == 2
        assert _parse_antiaffinity("0") == 0


class TestParseArgv:
    def test_short_options(self):
        b = parse_argv(["-n", "-v", "-w", "fio", "-B", "mybase"])
        assert b.doit is False
        assert b.verbose is True
        assert b.requested_workload == "fio"
        assert b.basename == "mybase"

    def test_long_options(self):
        b = parse_argv(["--workload=cpusoaker", "--namespaces=3", "--doit=0"])
        assert b.requested_workload == "cpusoaker"
        assert b.namespaces == 3
        assert b.doit is False

    def test_mixed_options(self):
        b = parse_argv(["-w", "memory", "--replicas=4", "-z"])
        assert b.requested_workload == "memory"
        assert b.replicas == 4
        assert b.compress_report is True

    def test_double_dash_separates_extra_args(self):
        b = parse_argv(["--workload=fio", "--", "extra1", "extra2"])
        assert b.requested_workload == "fio"
        assert b.extra_args == ["extra1", "extra2"]

    def test_positional_args_collected(self):
        b = parse_argv(["--workload=fio", "positional"])
        assert "positional" in b.extra_args


class TestProcessJobFile:
    def test_yaml_job_file(self, tmp_path):
        job = {"options": {"workload": "fio", "namespaces": 3, "verbose": True}}
        p = tmp_path / "job.yaml"
        p.write_text(yaml.dump(job))
        b = ClusterbusterConfigBuilder()
        process_job_file(b, str(p))
        assert b.requested_workload == "fio"
        assert b.namespaces == 3
        assert b.verbose is True

    def test_yaml_job_file_flat(self, tmp_path):
        job = {"workload": "memory", "replicas": 2}
        p = tmp_path / "job.yaml"
        p.write_text(yaml.dump(job))
        b = ClusterbusterConfigBuilder()
        process_job_file(b, str(p))
        assert b.requested_workload == "memory"
        assert b.replicas == 2

    def test_missing_job_file(self):
        b = ClusterbusterConfigBuilder()
        with pytest.raises(SystemExit, match="cannot be read"):
            process_job_file(b, "/nonexistent/path.yaml")

    def test_yaml_dict_value_rejected(self, tmp_path):
        job = {"options": {"workload": "fio", "badopt": {"nested": "value"}}}
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(job))
        b = ClusterbusterConfigBuilder()
        with pytest.raises(SystemExit, match="unsupported type"):
            process_job_file(b, str(p))

    def test_yaml_list_value_rejected(self, tmp_path):
        job = {"options": {"workload": "fio", "badopt": [1, 2, 3]}}
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(job))
        b = ClusterbusterConfigBuilder()
        with pytest.raises(SystemExit, match="unsupported type"):
            process_job_file(b, str(p))

    def test_yaml_boolean_values(self, tmp_path):
        job = {"options": {
            "cleanup": True,
            "report-object-creation": False,
            "antiaffinity": True,
            "precleanup": True,
            "workload": "fio",
        }}
        p = tmp_path / "bools.yaml"
        p.write_text(yaml.dump(job))
        b = ClusterbusterConfigBuilder()
        process_job_file(b, str(p))
        assert b.cleanup is True
        assert b.report_object_creation is False
        assert b.affinity == 2
        assert b.precleanup is True

    def test_yaml_boolean_false_cleanup(self, tmp_path):
        job = {"options": {"cleanup": False}}
        p = tmp_path / "no_cleanup.yaml"
        p.write_text(yaml.dump(job))
        b = ClusterbusterConfigBuilder()
        process_job_file(b, str(p))
        assert b.cleanup is False

    def test_legacy_fallback(self, tmp_path):
        content = """\
# Legacy format test
workload=fio
namespaces=3
precleanup
cleanup
no-report-object-creation
exit_at_end
replicas=4
"""
        p = tmp_path / "legacy"
        p.write_text(content)
        b = ClusterbusterConfigBuilder()
        process_job_file(b, str(p))
        assert b.requested_workload == "fio"
        assert b.namespaces == 3
        assert b.precleanup is True
        assert b.cleanup is True
        assert b.report_object_creation is False
        assert b.exit_at_end is True
        assert b.replicas == 4

    def test_legacy_backslash_continuation(self, tmp_path):
        content = """\
workload=\\
fio
namespaces=3
"""
        p = tmp_path / "legacy_cont"
        p.write_text(content)
        b = ClusterbusterConfigBuilder()
        process_job_file(b, str(p))
        assert b.requested_workload == "fio"
        assert b.namespaces == 3

    def test_yaml_null_bare_flags(self, tmp_path):
        """P2-2: YAML null values treated as bare flags."""
        content = "options:\n  cleanup:\n  precleanup:\n  workload: fio\n"
        p = tmp_path / "nullflags.yaml"
        p.write_text(content)
        b = ClusterbusterConfigBuilder()
        process_job_file(b, str(p))
        assert b.cleanup is True
        assert b.precleanup is True
        assert b.requested_workload == "fio"

    def test_yaml_list_rejected(self, tmp_path):
        """P2-1: Valid YAML list should not fall through to legacy."""
        p = tmp_path / "list.yaml"
        p.write_text("- item1\n- item2\n")
        b = ClusterbusterConfigBuilder()
        with pytest.raises(SystemExit, match="expected a YAML mapping"):
            process_job_file(b, str(p))

    def test_yaml_scalar_falls_through_to_legacy(self, tmp_path):
        """A YAML scalar is indistinguishable from a legacy file; allow fallthrough."""
        p = tmp_path / "scalar_legacy"
        p.write_text("workload=cpusoaker\nnamespaces=2\n")
        b = ClusterbusterConfigBuilder()
        process_job_file(b, str(p))
        assert b.requested_workload == "cpusoaker"
        assert b.namespaces == 2


class TestHelpText:
    def test_help_exits_zero(self):
        with pytest.raises(SystemExit) as exc_info:
            parse_argv(["-h"])
        assert exc_info.value.code == 0

    def test_extended_help_exits_zero(self):
        with pytest.raises(SystemExit) as exc_info:
            parse_argv(["-H"])
        assert exc_info.value.code == 0

    def test_help_options_alias_exits_zero(self):
        with pytest.raises(SystemExit) as exc_info:
            parse_argv(["--help-options"])
        assert exc_info.value.code == 0

    def test_display_names_have_hyphens(self):
        for key, display in _OPTION_DISPLAY_NAMES.items():
            assert "-" in display, f"Display name for {key!r} should have hyphens"

    def test_aliases_not_in_display_names(self):
        for alias in _OPTION_ALIASES:
            assert alias not in _OPTION_DISPLAY_NAMES, (
                f"Alias {alias!r} should not have a display name"
            )

    def test_all_dispatch_keys_covered(self):
        for key in _OPTION_DISPATCH:
            if key not in _OPTION_ALIASES:
                name = _display_name(key)
                assert name, f"Option {key!r} should produce a display name"

    def test_help_output_contains_display_names(self, capsys):
        """P1-2: Help output contains human-readable option names."""
        output = _build_help_text()
        for key in _OPTION_DISPATCH:
            if key in _OPTION_ALIASES:
                continue
            display = _display_name(key)
            assert f"--{display}" in output, (
                f"Help output should contain --{display}"
            )

    def test_help_options_grep_extractable(self):
        """P1-2: Each option line must match the grep pattern."""
        output = _build_help_text()
        opt_pattern = re.compile(r"^\s+--[-_a-z]+")
        for line in output.splitlines():
            if line.strip().startswith("--"):
                assert opt_pattern.match(line), (
                    f"Line not grep-extractable: {line!r}"
                )

    def test_help_includes_workload_options(self):
        """P1-2: Help output should include workload-specific options."""
        output = _build_help_text()
        assert "--fio-patterns" in output, "Should include --fio-patterns"
        assert "--memory-size" in output, "Should include --memory-size"
        assert "--uperf-msg-size" in output, "Should include --uperf-msg-size"
        assert "--synctest-count" in output, "Should include --synctest-count"

    def test_help_and_help_options_produce_same_output(self):
        """P1-2: -h and --help-options produce the same output via CLI dispatch."""
        outputs = []
        for flag in ["-h", "--help-options"]:
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                with pytest.raises(SystemExit) as exc:
                    parse_argv([flag])
                assert exc.value.code == 0
            outputs.append(buf.getvalue())
        assert outputs[0] == outputs[1]
