# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
"""Python implementation of ``run-perf-ci-suite`` (orchestration + :class:`ClusterbusterCISuite`)."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from clusterbuster.ci.ci_options import parse_ci_option, splitarg
from clusterbuster.ci.compat.options import bool_str, parse_optvalues
from clusterbuster.ci.config import ClusterbusterCISuiteConfig
from clusterbuster.ci.help_text import build_full_help
from clusterbuster.ci.logging_config import configure_clusterbuster_ci_logging
from clusterbuster.ci.profile_yaml import load_yaml_profile, resolve_profile_path
from clusterbuster.ci.registry import default_registry
from clusterbuster.ci.suite import ClusterbusterCISuite

# Fixed name so logging works whether the package is imported as ``clusterbuster.ci``
# (``PYTHONPATH=lib``) or ``lib.clusterbuster.ci`` (repo-root script).
_LOG = logging.getLogger("clusterbuster.ci.run_perf")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def standard_snapshot_date_format() -> str:
    return "%Y_%m_%dT%H_%M_%S%z"


def artifact_dirname(template: str, *, utc_now: datetime | None = None) -> str:
    dt = utc_now or datetime.now(timezone.utc)
    ts = dt.strftime("%Y_%m_%dT%H_%M_%S%z")
    if len(ts) > 5 and ts[-5] in "+-":
        ts = ts[:-2] + ":" + ts[-2:]
    return template.replace("%T", ts).replace("%s", ts)


def parse_time(time_str: str) -> int:
    parts = re.split(r":", time_str.strip())
    d, h, m, s = 0, 0, 0, 0
    if len(parts) == 1:
        s = int(parts[0].lstrip("0") or "0")
    elif len(parts) == 2:
        h = int(parts[0].lstrip("0") or "0")
        m = int(parts[1].lstrip("0") or "0")
    elif len(parts) == 3:
        h = int(parts[0].lstrip("0") or "0")
        m = int(parts[1].lstrip("0") or "0")
        s = int(parts[2].lstrip("0") or "0")
    elif len(parts) == 4:
        d = int(parts[0].lstrip("0") or "0")
        h = int(parts[1].lstrip("0") or "0")
        m = int(parts[2].lstrip("0") or "0")
        s = int(parts[3].lstrip("0") or "0")
    else:
        raise SystemExit(f"Malformed time {time_str!r}")
    return d * 86400 + h * 3600 + m * 60 + s


def _ci_bool(s: str) -> bool:
    return bool_str(s) == "1"


def find_oc() -> str:
    oc = os.environ.get("OC") or os.environ.get("KUBECTL")
    if oc:
        return oc
    for cand in ("oc", "kubectl"):
        p = shutil.which(cand)
        if p:
            return p
    return ""


def get_available_nodes(oc: str, *, dry: bool) -> list[str]:
    if dry:
        return ["node1", "node2", "node3"]
    roles = [
        "node-role.kubernetes.io/clusterbuster=",
        "node-role.kubernetes.io/worker=,node-role.kubernetes.io/master!=,node-role.kubernetes.io/infra!=",
        "node-role.kubernetes.io/worker=",
    ]
    for role in roles:
        proc = subprocess.run(
            [oc, "get", "node", "-l", role, "-o", "jsonpath={.items[*].metadata.name}"],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.split()
    return []


def set_pin_nodes(state: CISuiteState, oc: str, *, dry: bool) -> None:
    if not state.pin_jobs:
        return
    if state.client_pin and state.server_pin and state.sync_pin:
        return
    nodes = get_available_nodes(oc, dry=dry)
    if not nodes:
        raise SystemExit("No nodes found!")
    n = len(nodes)
    if not state.client_pin:
        state.client_pin = nodes[0 % n]
    if not state.server_pin:
        state.server_pin = nodes[1 % n]
    if not state.sync_pin:
        state.sync_pin = nodes[2 % n]


def set_pin_node_value(state: CISuiteState, setting: str) -> None:
    if "=" in setting:
        key, _, val = setting.partition("=")
        key = key.strip().lower()
        if key == "server":
            state.server_pin = val
        elif key == "client":
            state.client_pin = val
        elif key == "sync":
            state.sync_pin = val
    elif setting:
        state.server_pin = setting
        state.client_pin = setting
        state.sync_pin = setting


def filter_runtimeclasses(state: CISuiteState, oc: str) -> None:
    out: list[str] = []
    for rc in state.runtimeclasses:
        if rc == "" or rc == "runc":
            out.append(rc)
            continue
        if rc == "vm":
            proc = subprocess.run(
                [oc, "get", "hyperconverged", "-A", "--no-headers"],
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                out.append(rc)
            continue
        proc = subprocess.run([oc, "get", "runtimeclass", rc], capture_output=True)
        if proc.returncode == 0:
            out.append(rc)
    state.runtimeclasses = out

    if os.environ.get("CB_ALLOW_VM_AARCH64", "0") != "1" and oc:
        proc = subprocess.run(
            [
                oc,
                "get",
                "nodes",
                "-l",
                "node-role.kubernetes.io/worker=",
                "-o",
                "jsonpath={.items[0].status.nodeInfo.architecture}",
            ],
            capture_output=True,
            text=True,
        )
        arch = (proc.stdout or "").strip()
        if not arch:
            proc = subprocess.run(
                [oc, "get", "nodes", "-o", "jsonpath={.items[0].status.nodeInfo.architecture}"],
                capture_output=True,
                text=True,
            )
            arch = (proc.stdout or "").strip()
        if arch in ("aarch64", "arm64"):
            prev_rc = list(state.runtimeclasses)
            state.runtimeclasses = [r for r in state.runtimeclasses if r != "vm"]
            if len(state.runtimeclasses) < len(prev_rc):
                _LOG.warning(
                    "run-perf-ci-suite: skipping runtimeclass vm on aarch64 cluster "
                    "(set CB_ALLOW_VM_AARCH64=1 to force)",
                )


@dataclass
class CISuiteState:
    client_pin: str = ""
    server_pin: str = ""
    sync_pin: str = ""
    pin_jobs: bool = True
    job_runtime: int = 120
    job_timeout: int = -1200
    dry_run_from_profile: bool = False
    artifactdir_template: str = ""
    report_format: str = "none"
    analysis_format: str = ""
    debugonly: int = 0
    run_timeout: int = 0
    force_pull_image: bool = False
    use_python_venv: bool = True
    python_venv: str = ""
    analyze_results: str = ""
    take_prometheus_snapshot: bool = False
    unique_job_prefix: bool = False
    job_delay: int = 0
    uuid: str = ""
    force_cleanup_timeout: str = ""
    restart: bool = False
    prerun: str = ""
    postrun: str = ""
    workloads: list[str] = field(default_factory=list)
    runtimeclasses: list[str] = field(default_factory=lambda: ["", "kata", "vm"])
    extra_args: list[str] = field(default_factory=list)
    debug_args_list: list[str] = field(default_factory=list)
    compress_report: str = ""
    hard_fail_on_error: bool = False


def apply_profile_lines(state: CISuiteState, lines: Sequence[str]) -> None:
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        apply_process_option(state, line)


def process_profile(state: CISuiteState, profile_arg: str, profile_dir: Path) -> None:
    path = Path(profile_arg)
    if not path.is_file():
        path = resolve_profile_path(profile_arg, profile_dir)
    if path.suffix in (".yaml", ".yml"):
        apply_profile_lines(state, load_yaml_profile(path))
    else:
        text = path.read_text(encoding="utf-8")
        raw_lines = text.splitlines()
        i = 0
        while i < len(raw_lines):
            raw = raw_lines[i]
            line = raw.split("#", 1)[0]
            if not line.strip():
                i += 1
                continue
            while line.rstrip().endswith("\\") and i + 1 < len(raw_lines):
                cont = raw_lines[i + 1].split("#", 1)[0]
                line = line.rstrip()[:-1] + cont.lstrip()
                i += 1
            line = line.strip()
            if line:
                apply_process_option(state, line)
            i += 1


def apply_process_option(state: CISuiteState, option: str) -> None:
    p = parse_ci_option(option, "", "")
    if p is None:
        return
    n1 = p.noptname1
    optvalue = splitarg(p.optvalue)
    if n1.startswith("help"):
        print_help()
        raise SystemExit(0)
    if n1.startswith("debugonly"):
        state.dry_run_from_profile = _ci_bool(optvalue)
    elif n1 == "debug":
        state.debug_args_list.append(f"--debug={optvalue}")
    elif n1.startswith("clientpin"):
        state.client_pin = optvalue
    elif n1.startswith("serverpin"):
        state.server_pin = optvalue
    elif n1.startswith("syncpin"):
        state.sync_pin = optvalue
    elif n1.startswith("nopinnode"):
        state.pin_jobs = False
    elif n1.startswith("usepinnode"):
        state.pin_jobs = _ci_bool(optvalue)
    elif n1.startswith("pin"):
        set_pin_node_value(state, optvalue)
    elif n1 in ("jobruntime", "runtime"):
        state.job_runtime = int(optvalue)
    elif n1 in ("jobtimeout", "timeout"):
        state.job_timeout = int(optvalue)
    elif n1.startswith("artifactdir"):
        state.artifactdir_template = artifact_dirname(optvalue or "cb-ci-%T")
    elif n1.startswith("analyze"):
        state.analyze_results = optvalue
    elif n1.startswith("reportformat"):
        state.report_format = optvalue
    elif n1.startswith("analysisformat"):
        state.analysis_format = optvalue
    elif n1.startswith("runtimeclass"):
        state.runtimeclasses = parse_optvalues(optvalue)
    elif n1.startswith("restart"):
        state.restart = _ci_bool(optvalue)
    elif n1.startswith("runtimeout"):
        state.run_timeout = parse_time(optvalue)
    elif n1.startswith("profile") or n1.startswith("runtype"):
        process_profile(state, optvalue, repo_root() / "lib" / "CI" / "profiles")
    elif n1.startswith("forcepull"):
        state.force_pull_image = _ci_bool(optvalue)
    elif n1.startswith("usepythonvenv"):
        state.use_python_venv = _ci_bool(optvalue)
    elif n1.startswith("uuid"):
        state.uuid = optvalue
    elif n1.startswith("forcecleanupiknowthisisdangerous"):
        state.force_cleanup_timeout = optvalue
    elif n1.startswith("prometheussnapshot"):
        state.take_prometheus_snapshot = _ci_bool(optvalue)
    elif n1.startswith("uniqueprefix"):
        v = optvalue.strip().lower()
        state.unique_job_prefix = v in ("1", "y", "yes", "true")
    elif n1.startswith("jobdelay"):
        state.job_delay = int(optvalue)
    elif n1.startswith("compress"):
        state.compress_report = "-z" if _ci_bool(optvalue) else ""
    elif n1.startswith("workloads"):
        state.workloads = parse_optvalues(optvalue)
    elif n1.startswith("hardfail") or n1.startswith("hard_fail"):
        state.hard_fail_on_error = _ci_bool(optvalue)
    else:
        state.extra_args.append(option)


def print_help() -> None:
    root = repo_root()
    text = build_full_help(
        root,
        profile_dir=root / "lib" / "CI" / "profiles",
        default_job_runtime=120,
        job_delay=0,
    )
    print(
        text
        + """
Environment: OC or KUBECTL must point to oc/kubectl if not on PATH.
Long options may be written as --name=value.
Use ``run-perf-ci-suite profile-yaml PATH.yaml`` to print option lines from a YAML profile.
""",
        end="",
    )


def parse_argv(argv: Sequence[str], state: CISuiteState) -> list[str]:
    i = 0
    workloads: list[str] = []
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            print_help()
            raise SystemExit(0)
        elif a == "-n":
            state.debugonly += 1
        elif a == "-z":
            state.compress_report = "-z"
        elif a.startswith("--"):
            apply_process_option(state, a[2:])
        elif a.startswith("-") and "=" in a:
            apply_process_option(state, a[1:])
        elif a.startswith("-"):
            _LOG.warning("Unknown short option %r", a)
            raise SystemExit(2)
        else:
            workloads.append(a)
        i += 1
    return workloads


def write_ci_results_json(
    artifactdir: Path,
    *,
    jobs: list[str],
    failures: list[str],
    job_runtimes: dict[str, str],
    status: str,
    start_ts: int,
    end_ts: int,
) -> None:
    payload = {
        "result": status,
        "job_start": datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "job_end": datetime.fromtimestamp(end_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "job_runtime": end_ts - start_ts,
        "ran": jobs,
        "failed": failures,
    }
    body = json.dumps(payload, indent=2) + "\n"
    tmp = artifactdir / "clusterbuster-ci-results.json.tmp"
    out = artifactdir / "clusterbuster-ci-results.json"
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(out)


def to_hms(start: int, end: int) -> str:
    interval = end - start
    h = interval // 3600
    m = (interval % 3600) // 60
    s = interval % 60
    if h > 0:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


def run_suite_with_orchestration(state: CISuiteState, workload_args: list[str]) -> int:
    dry = state.debugonly > 0 or state.dry_run_from_profile
    oc = find_oc()
    if not oc and not dry:
        _LOG.warning("Cannot find oc or kubectl")
        return 1
    os.environ.setdefault("OC", oc)

    wl = workload_args if workload_args else list(default_registry().keys())
    for w in wl:
        if w not in default_registry():
            _LOG.warning("Unsupported workload %s", w)
            return 1

    if not state.uuid:
        state.uuid = __import__("uuid").uuid4().hex

    art: Path | None = None
    starting_ts = int(time.time())
    set_pin_nodes(state, oc, dry=dry)
    if not dry:
        filter_runtimeclasses(state, oc)
        if not state.artifactdir_template:
            state.artifactdir_template = artifact_dirname("cb-ci-%T", utc_now=datetime.now(timezone.utc))
        elif "%s" in state.artifactdir_template or "%T" in state.artifactdir_template:
            state.artifactdir_template = artifact_dirname(
                state.artifactdir_template, utc_now=datetime.fromtimestamp(starting_ts, tz=timezone.utc)
            )
        art = Path(state.artifactdir_template)
        if not state.restart and art.exists():
            shutil.rmtree(art)
        art.mkdir(parents=True, exist_ok=True)

    debug_args = " ".join(state.debug_args_list)
    compress = bool(state.compress_report)

    cfg = ClusterbusterCISuiteConfig(
        workloads=tuple(wl),
        runtimeclasses=tuple(state.runtimeclasses),
        default_job_runtime=state.job_runtime,
        job_timeout=state.job_timeout,
        dontdoit=dry,
        artifactdir=art,
        uuid=state.uuid,
        extra_args=list(state.extra_args),
        job_delay=state.job_delay,
        unique_prefix=state.unique_job_prefix,
        compress=compress,
        client_pin=state.client_pin,
        server_pin=state.server_pin,
        sync_pin=state.sync_pin,
        report_format=state.report_format or "none",
        debug_args=debug_args,
        force_cleanup_timeout=state.force_cleanup_timeout or "",
        restart=state.restart,
        hard_fail_on_error=state.hard_fail_on_error,
        log=_LOG,
    )

    def _partial(suite: ClusterbusterCISuite) -> None:
        if art is None:
            return
        st = "FAILING" if suite.failures else "PASSING"
        write_ci_results_json(
            art,
            jobs=list(suite.jobs),
            failures=list(suite.failures),
            job_runtimes=dict(suite.job_runtimes),
            status=st,
            start_ts=starting_ts,
            end_ts=int(time.time()),
        )

    cfg.partial_results_hook = _partial

    suite = ClusterbusterCISuite(cfg)
    rc = suite.run()
    end_ts = int(time.time())

    if art is not None:
        status = "PASS" if rc == 0 and not suite.failures else "FAIL"
        write_ci_results_json(
            art,
            jobs=list(suite.jobs),
            failures=list(suite.failures),
            job_runtimes=dict(suite.job_runtimes),
            status=status,
            start_ts=starting_ts,
            end_ts=end_ts,
        )
        _LOG.info(
            "Run took %s (%s)",
            to_hms(starting_ts, end_ts),
            "Passed" if rc == 0 and not suite.failures else "Failed",
        )
        _LOG.info("EXIT=%s", rc)

    return rc


def main(argv: list[str] | None = None) -> int:
    configure_clusterbuster_ci_logging()
    argv = list(argv if argv is not None else sys.argv[1:])
    if argv and argv[0] == "profile-yaml":
        from clusterbuster.ci.profile_yaml import main as profile_yaml_main

        return profile_yaml_main(argv[1:])
    state = CISuiteState()
    try:
        wl = parse_argv(argv, state)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 1
    if state.analyze_results and not state.artifactdir_template:
        _LOG.warning("--analyze-results may only be used with --artifactdir set")
        return 1
    return run_suite_with_orchestration(state, wl)


def run_perf_ci_suite(argv: list[str] | None = None) -> int:
    """Programmatic entry for the full CI orchestration (same behavior as the ``run-perf-ci-suite`` CLI)."""
    return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
