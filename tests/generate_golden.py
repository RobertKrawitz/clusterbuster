#!/usr/bin/env python3
"""Generate golden files from clusterbuster.sh for parity tests.

Run once from the repo root:
    python tests/generate_golden.py

Produces JSON golden files under tests/golden/ for each workload ×
deployment-type combination that clusterbuster.sh supports.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN_DIR = os.path.join(REPO_ROOT, "tests", "golden")
BASH_CMD = os.path.join(REPO_ROOT, "clusterbuster.sh")
FIXED_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

WORKLOADS = [
    "byo", "cpusoaker", "failure", "files", "fio", "hammerdb",
    "logger", "memory", "pausepod", "server", "sleep", "synctest",
    "sysbench", "uperf", "waitforever",
]

DEPLOYMENT_TYPES = ["pod", "vm", "deployment", "replicaset"]

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


def normalize_uuid(val: str, fixed: str = FIXED_UUID) -> str:
    """Replace fixed UUID with 'UUID' and any other UUID with 'XUUID'."""
    val = val.replace(fixed, "UUID")
    return _UUID_RE.sub("XUUID", val)


def extract_yaml_documents(raw: str) -> list[str]:
    """Extract YAML document strings from mixed bash output."""
    docs: list[str] = []
    current_lines: list[str] = []
    in_doc = False

    for line in raw.splitlines():
        if line.strip() == "---":
            if in_doc and current_lines:
                docs.append("\n".join(current_lines))
                current_lines = []
            in_doc = True
            continue

        if not in_doc:
            continue

        # Skip bash noise lines (timestamps, kubectl commands)
        if re.match(r"^\d{4}-\d{2}-\d{2}T", line):
            continue

        current_lines.append(line)

    if in_doc and current_lines:
        docs.append("\n".join(current_lines))

    return docs


def parse_yaml_doc(text: str) -> dict | None:
    """Minimal YAML parsing for flat k8s manifests — extract key fields."""
    try:
        doc = yaml.safe_load(text)
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None
    return doc


def extract_object_info(doc: dict) -> dict:
    """Extract the structural info we compare in parity tests."""
    meta = doc.get("metadata", {})
    kind = doc.get("kind", "")
    info: dict = {
        "kind": kind,
        "name": normalize_uuid(str(meta.get("name", ""))),
        "namespace": normalize_uuid(str(meta.get("namespace", ""))),
    }

    # Labels: normalize UUID values
    labels = meta.get("labels", {})
    if labels:
        info["labels"] = {
            k: normalize_uuid(str(v)) for k, v in sorted(labels.items())
        }

    # For Services: extract ports and selector
    spec = doc.get("spec", {})
    if kind == "Service":
        ports = spec.get("ports", [])
        if ports:
            info["ports"] = [
                {
                    "name": normalize_uuid(p.get("name", "")),
                    "port": p.get("port"),
                    "protocol": p.get("protocol", "TCP"),
                }
                for p in ports
            ]
        selector = spec.get("selector", {})
        if selector:
            info["selector"] = {
                k: normalize_uuid(str(v)) for k, v in sorted(selector.items())
            }
        svc_type = spec.get("type", "")
        if svc_type:
            info["service_type"] = svc_type
        if spec.get("clusterIP") == "None":
            info["headless"] = True

    if kind == "Pod":
        restart = spec.get("restartPolicy")
        if restart:
            info["restart_policy"] = restart
        containers = spec.get("containers", [])
        if containers:
            sc = containers[0].get("securityContext", {})
            if "runAsNonRoot" in sc:
                info["run_as_non_root"] = sc["runAsNonRoot"]
            if sc.get("sysctls"):
                info["container_sysctls"] = [
                    s["name"] for s in sc["sysctls"]
                ]
        if spec.get("affinity", {}).get("podAffinity"):
            info["has_pod_affinity"] = True

    # For Deployment/ReplicaSet: extract template labels + pod spec fields
    if kind in ("Deployment", "ReplicaSet"):
        tmpl = spec.get("template", {}).get("metadata", {})
        tmpl_labels = tmpl.get("labels", {})
        if tmpl_labels:
            info["template_labels"] = {
                k: normalize_uuid(str(v))
                for k, v in sorted(tmpl_labels.items())
            }
        sel = spec.get("selector", {}).get("matchLabels", {})
        if sel:
            info["match_labels"] = {
                k: normalize_uuid(str(v)) for k, v in sorted(sel.items())
            }
        tmpl_spec = spec.get("template", {}).get("spec", {})
        restart = tmpl_spec.get("restartPolicy")
        if restart:
            info["restart_policy"] = restart
        containers = tmpl_spec.get("containers", [])
        if containers:
            sc = containers[0].get("securityContext", {})
            if "runAsNonRoot" in sc:
                info["run_as_non_root"] = sc["runAsNonRoot"]
            if sc.get("sysctls"):
                info["container_sysctls"] = [
                    s["name"] for s in sc["sysctls"]
                ]

    # For VirtualMachine: extract template labels + domain structure
    if kind == "VirtualMachine":
        tmpl = spec.get("template", {}).get("metadata", {})
        tmpl_labels = tmpl.get("labels", {})
        if tmpl_labels:
            info["template_labels"] = {
                k: normalize_uuid(str(v))
                for k, v in sorted(tmpl_labels.items())
            }

        vm_spec = spec.get("template", {}).get("spec", {})
        domain = vm_spec.get("domain", {})
        devices = domain.get("devices", {})

        disks = devices.get("disks", [])
        serials = sorted(d["serial"] for d in disks if d.get("serial"))
        if serials:
            info["vm_disk_serials"] = serials

        volumes = vm_spec.get("volumes", [])
        vol_types = sorted(
            next((k for k in v if k != "name"), "unknown")
            for v in volumes
        )
        if vol_types:
            info["vm_volume_types"] = vol_types

        ci_vols = [v for v in volumes if "cloudInitNoCloud" in v]
        if ci_vols:
            userdata = ci_vols[0]["cloudInitNoCloud"].get("userData", "")
            try:
                ci_doc = yaml.safe_load(userdata)
                if isinstance(ci_doc, dict):
                    bootcmds = ci_doc.get("bootcmd", [])
                    structural = []
                    for cmd in bootcmds:
                        if not isinstance(cmd, str):
                            continue
                        verb = cmd.strip().split()[0].strip("'\"")
                        if verb in ("mkdir", "mount") or verb.startswith("mkfs"):
                            structural.append(verb)
                    if structural:
                        info["vm_bootcmd_structural"] = structural
            except Exception:
                pass

    return info


def run_bash(workload: str, dep_type: str) -> str | None:
    """Run clusterbuster.sh and return raw output, or None on failure."""
    args = [
        "bash", BASH_CMD,
        f"--uuid={FIXED_UUID}",
        f"--workload={workload}",
        f"--deployment-type={dep_type}",
        "-n",
    ]
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=60,
            cwd=REPO_ROOT,
        )
        if result.returncode != 0:
            return None
        return result.stdout + result.stderr
    except (subprocess.TimeoutExpired, OSError):
        return None


def generate_golden(workload: str, dep_type: str) -> list[dict] | None:
    """Generate golden data for one workload × deployment-type."""
    raw = run_bash(workload, dep_type)
    if raw is None:
        return None

    docs_text = extract_yaml_documents(raw)
    objects: list[dict] = []
    for text in docs_text:
        doc = parse_yaml_doc(text)
        if doc is None:
            continue
        info = extract_object_info(doc)
        if info.get("kind"):
            objects.append(info)

    return objects if objects else None


def main() -> None:
    os.makedirs(GOLDEN_DIR, exist_ok=True)
    total = 0
    skipped = 0

    for wl in WORKLOADS:
        for dt in DEPLOYMENT_TYPES:
            key = f"{wl}_{dt}"
            print(f"Generating {key}...", end=" ", flush=True)
            objects = generate_golden(wl, dt)
            if objects is None:
                print("SKIP (bash failed)")
                skipped += 1
                continue

            path = os.path.join(GOLDEN_DIR, f"{key}.json")
            with open(path, "w") as f:
                json.dump(objects, f, indent=2, sort_keys=True)
                f.write("\n")

            print(f"OK ({len(objects)} objects)")
            total += 1

    print(f"\nGenerated {total} golden files, skipped {skipped}")


if __name__ == "__main__":
    sys.exit(main() or 0)
