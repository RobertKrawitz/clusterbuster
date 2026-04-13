# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""KubeVirt VM manifest generation, cloud-init, and virtctl interface."""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Any

import yaml

from .config import ClusterbusterConfig, kv_get
from .manifests import ManifestBuilder

_LOG = logging.getLogger(__name__)


class VmManifestBuilder:
    """Builds KubeVirt VirtualMachine manifests from a validated config."""

    def __init__(self, config: ClusterbusterConfig, manifest_builder: ManifestBuilder):
        self._cfg = config
        self._mb = manifest_builder
        self._vm_addr = [192, 168, 129, 1]

    # -- VirtualMachine manifest ---------------------------------------------

    def virtual_machine(
        self,
        name: str,
        namespace: str,
        *,
        workload_args: list[str] | None = None,
        workload_env: dict[str, str] | None = None,
        workload_packages: list[str] | None = None,
        workload_setup_commands: list[str] | None = None,
        workload_sysctls: dict[str, str] | None = None,
        workload_listen_ports: list[int] | None = None,
        class_name: str = "",
        label_namespace: str = "",
        label_instance: str = "",
        label_replica: str = "",
        label_workload: str = "",
    ) -> dict[str, Any]:
        cfg = self._cfg
        labels = self._mb.standard_labels(
            "worker",
            workload=label_workload or cfg.requested_workload,
            namespace=label_namespace or namespace,
            instance=label_instance,
            replica=label_replica,
            class_name=class_name,
            logger=True,
        )
        ann = self._mb.annotations(class_name)

        # Networking
        net_iface = cfg.net_interfaces.get(class_name, cfg.net_interfaces.get("default", ""))
        vm_addr = tuple(self._vm_addr)
        self._increment_vm_addr()

        # Domain spec
        domain = self._domain_spec(workload_listen_ports or [], class_name=class_name)

        # Volumes (secrets, configmaps)
        k8s_volumes = self._mb.volumes(
            namespace=namespace,
            include_workdir=False,
            include_extra=False,
        )
        # Container disk
        k8s_volumes.append({
            "name": "containerdisk",
            "containerDisk": {"image": cfg.vm_image},
        })
        # Cloud-init volume
        userdata = self.cloud_init_userdata(
            workload_args=workload_args or [],
            workload_env=workload_env or {},
            workload_packages=workload_packages or [],
            workload_setup_commands=workload_setup_commands or [],
            workload_sysctls=workload_sysctls or {},
        )
        cloud_init: dict[str, Any] = {
            "name": "cloudinitdisk",
            "cloudInitNoCloud": {"userData": userdata},
        }
        if net_iface:
            cloud_init["cloudInitNoCloud"]["networkData"] = self.cloud_init_networkdata(vm_addr)
        k8s_volumes.append(cloud_init)

        # Extra volumes (emptydisk, pvc)
        emptydisk_id = 0
        for vol_spec in cfg.volumes:
            parts = vol_spec.split(":")
            vname = parts[0]
            vtype = parts[1].lower() if len(parts) > 1 else "emptydir"
            kv_args = parts[3:] if len(parts) > 3 else []
            size = kv_get(kv_args, "size")
            if vtype == "emptydisk":
                if not vname:
                    vname = f"cbemptydisk{emptydisk_id}"
                    emptydisk_id += 1
                k8s_volumes.append({
                    "name": vname,
                    "emptyDisk": {"capacity": size} if size else {},
                })
            elif vtype in ("pvc", "persistentvolumeclaim"):
                claim = kv_get(kv_args, "claimName") or vname
                k8s_volumes.append({
                    "name": vname,
                    "persistentVolumeClaim": {"claimName": claim},
                })

        # VM spec
        vm_spec: dict[str, Any] = {}
        evict = self._eviction_strategy()
        if evict:
            vm_spec["evictionStrategy"] = evict
        vm_spec["domain"] = domain
        vm_spec["volumes"] = k8s_volumes

        if cfg.scheduler:
            vm_spec["schedulerName"] = cfg.scheduler

        # Affinity / node selector
        pin = cfg.pin_nodes.get(class_name, cfg.pin_nodes.get("default", ""))
        if pin:
            vm_spec["nodeSelector"] = {"kubernetes.io/hostname": pin}
        elif cfg.node_selector:
            vm_spec["nodeSelector"] = {cfg.node_selector: ""}

        aff = self._mb.affinity(cfg.affinity)
        if aff:
            vm_spec["affinity"] = aff

        vm_spec["terminationGracePeriodSeconds"] = cfg.vm_grace_period

        # Networks
        vm_spec["networks"] = self._networks(net_iface)

        # Template metadata
        template_labels = dict(labels)
        template_labels["kubevirt-vm"] = name
        template_meta: dict[str, Any] = {"labels": template_labels}
        if ann:
            template_meta["annotations"] = ann
        if net_iface:
            template_meta.setdefault("annotations", {})
            template_meta["annotations"]["k8s.v1.cni.cncf.io/networks"] = net_iface

        # Run strategy
        run_strategy = self._run_strategy()

        manifest: dict[str, Any] = {
            "apiVersion": "kubevirt.io/v1",
            "kind": "VirtualMachine",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": labels,
            },
            "spec": {
                **run_strategy,
                "template": {
                    "metadata": template_meta,
                    "spec": vm_spec,
                },
            },
        }

        return manifest

    # -- Domain spec ---------------------------------------------------------

    def _domain_spec(self, listen_ports: list[int], *, class_name: str = "") -> dict[str, Any]:
        cfg = self._cfg
        cpu: dict[str, Any] = {
            "cores": cfg.vm_cores,
            "threads": cfg.vm_threads,
            "sockets": cfg.vm_sockets,
        }

        disks: list[dict[str, Any]] = [
            {"name": "containerdisk", "disk": {"bus": "virtio"}},
            {"name": "cloudinitdisk", "disk": {"bus": "virtio"}},
        ]

        # Configmap disks (always present, matching bash)
        disks.append({
            "name": "user-configmap",
            "serial": "userconfigmap",
            "disk": {"bus": "sata"},
        })
        disks.append({
            "name": "system-configmap",
            "serial": "systemconfigmap",
            "disk": {"bus": "sata"},
        })

        # Extra volume disks
        emptydisk_id = 0
        for vol_spec in cfg.volumes:
            parts = vol_spec.split(":")
            vname = parts[0]
            vtype = parts[1].lower() if len(parts) > 1 else "emptydir"
            if vtype == "emptydir":
                continue
            kv_args = parts[3:] if len(parts) > 3 else []
            if vtype == "emptydisk" and not vname:
                vname = f"cbemptydisk{emptydisk_id}"
                emptydisk_id += 1
            bus = kv_get(kv_args, "bus") or "virtio"
            disk_spec: dict[str, Any] = {"bus": bus}
            cache_val = kv_get(kv_args, "cache")
            if cache_val:
                disk_spec["cache"] = cache_val
            disk_entry: dict[str, Any] = {"name": vname, "serial": vname, "disk": disk_spec}
            dio = kv_get(kv_args, "dedicatedIOThread")
            if dio and dio.lower() in ("1", "true", "yes"):
                disk_entry["dedicatedIOThread"] = True
            disks.append(disk_entry)

        net_iface = cfg.net_interfaces.get(class_name, cfg.net_interfaces.get("default", ""))
        interfaces = self._interfaces(net_iface)

        devices: dict[str, Any] = {
            "disks": disks,
            "interfaces": interfaces,
        }

        if cfg.vm_block_multiqueue:
            devices["blockMultiQueue"] = True

        res = self._mb.resources()
        if res:
            devices["resources"] = res

        domain: dict[str, Any] = {
            "cpu": cpu,
            "memory": {"guest": cfg.vm_memory},
            "devices": devices,
        }

        if cfg.vm_block_multiqueue:
            domain["ioThreadsPolicy"] = "supplementalPool"
            domain["ioThreads"] = {
                "supplementalPoolThreadCount": cfg.vm_block_multiqueue,
            }

        return domain

    # -- Cloud-init ----------------------------------------------------------

    def cloud_init_userdata(
        self,
        *,
        workload_args: list[str],
        workload_env: dict[str, str],
        workload_packages: list[str],
        workload_setup_commands: list[str],
        workload_sysctls: dict[str, str],
    ) -> str:
        """Build cloud-config YAML string for VM userData.

        Matches the bash ``create_vm_deployment`` cloud-init layout:
        bootcmd gets dirs, mounts, ssh keys, packages, setup, sysctls;
        runcmd gets only the workload command.
        """
        from .manifests import SYSTEM_CONFIGMAP_MOUNT_DIR, USER_CONFIGMAP_MOUNT_DIR

        cfg = self._cfg
        cloud: dict[str, Any] = {}

        def _q(s: str) -> str:
            return f"'{s}'"

        # User setup
        if cfg.vm_user:
            cloud["user"] = cfg.vm_user
            cloud["username"] = cfg.vm_user
        if cfg.vm_password:
            cloud["password"] = cfg.vm_password
        cloud["chpasswd"] = {"expire": False}

        bootcmd: list[str] = []

        # Create configmap mount dirs (always both)
        bootcmd.append(
            f"mkdir -p {_q(SYSTEM_CONFIGMAP_MOUNT_DIR)} {_q(USER_CONFIGMAP_MOUNT_DIR)}"
        )

        # Mount extra volumes (before configmap mounts, matching bash order)
        emptydiskid = 0
        for vol_spec in cfg.volumes:
            parts = vol_spec.split(":")
            vname = parts[0]
            vtype = parts[1].lower() if len(parts) > 1 else "emptydir"
            mount = parts[2] if len(parts) > 2 else f"/mnt/{vname}"
            kv_args = parts[3:] if len(parts) > 3 else []
            if vtype == "emptydir":
                continue
            bootcmd.append(f"mkdir -p {_q(mount)}")
            mountopts = kv_get(kv_args, "mountopts") or ""
            nfssrv = kv_get(kv_args, "nfssrv") or ""
            nfsshare = kv_get(kv_args, "nfsshare") or "/"
            if nfssrv:
                remote = f"{nfssrv}:{nfsshare}"
                mopts = f" {mountopts}" if mountopts else ""
                bootcmd.append(
                    f"mount{mopts} {_q(remote)} {_q(mount)} && chmod 777 {_q(mount)}"
                )
            else:
                bus = kv_get(kv_args, "bus") or "virtio"
                if vtype == "emptydisk":
                    disk_name = f"cbemptydisk{emptydiskid}"
                    emptydiskid += 1
                else:
                    disk_name = vname
                dev_id = f"{bus}-{disk_name}"
                fstype = kv_get(kv_args, "fstype")
                if not fstype and vtype == "emptydisk":
                    fstype = "ext4"
                if fstype:
                    fsopts = kv_get(kv_args, "fsopts") or ""
                    inodes = kv_get(kv_args, "inodes") or ""
                    sinodes = ""
                    if inodes and fstype.startswith("ext"):
                        sinodes = f" -N {_q(inodes)}"
                    force = " -f" if fstype == "xfs" else ""
                    fopts = f" {fsopts}" if fsopts else ""
                    bootcmd.append(
                        f"{_q('mkfs.' + fstype)}{force}{sinodes}{fopts}"
                        f" '/dev/disk/by-id/{dev_id}'"
                    )
                mopts = f" {mountopts}" if mountopts else ""
                bootcmd.append(
                    f"mount{mopts} '/dev/disk/by-id/{dev_id}' {_q(mount)}"
                    f" && chmod 777 {_q(mount)}"
                )

        # Mount configmaps via ATA disk serial ID (read-only)
        bootcmd.append(
            f"mount -o ro /dev/disk/by-id/ata-QEMU_HARDDISK_systemconfigmap"
            f" {_q(SYSTEM_CONFIGMAP_MOUNT_DIR)}"
        )
        bootcmd.append(
            f"mount -o ro /dev/disk/by-id/ata-QEMU_HARDDISK_userconfigmap"
            f" {_q(USER_CONFIGMAP_MOUNT_DIR)}"
        )

        # SSH keys — inject via bootcmd echo/chmod (matches bash _insert_ssh_keys)
        if cfg.vm_ssh_keyfile:
            pubkey_path = cfg.vm_ssh_keyfile
            if not pubkey_path.endswith(".pub"):
                pubkey_path = pubkey_path + ".pub"
            try:
                with open(pubkey_path) as fh:
                    pubkey = fh.read().strip()
                if pubkey:
                    bootcmd.append(
                        f"echo {_q(pubkey)}"
                        f' >> "/root/.ssh/authorized_keys"'
                        f' && chmod 600 "/root/.ssh/authorized_keys"'
                    )
            except OSError:
                _LOG.warning("Cannot read SSH public key %s", pubkey_path)

        # Packages (in bootcmd, matching bash _install_required_packages)
        if not cfg.vm_run_as_container and workload_packages:
            bootcmd.append(f"dnf install -y {' '.join(workload_packages)}")

        # Setup commands (in bootcmd, matching bash _setup_commands)
        bootcmd.extend(workload_setup_commands)
        if cfg.vm_run_as_container:
            bootcmd.append("sudo dnf install -y podman")

        # Sysctls (in bootcmd, matching bash _setup_sysctls)
        if not cfg.vm_run_as_container:
            for k, v in workload_sysctls.items():
                bootcmd.append(f"sysctl -w {_q(k + '=' + v)}")

        cloud["bootcmd"] = bootcmd

        # runcmd — only the workload command
        if workload_args:
            env_parts = [f"{k}={_q(v)}" for k, v in workload_env.items()]
            arg_parts = [_q(a) for a in workload_args]

            if cfg.vm_run_as_container:
                sysctl_flags = " ".join(
                    f"'--sysctl={k}={v}'" for k, v in workload_sysctls.items()
                )
                env_flags = " ".join(
                    f"'-e{k}={v}'" for k, v in workload_env.items()
                )
                vol_flags = (
                    f"'-v{SYSTEM_CONFIGMAP_MOUNT_DIR}:{SYSTEM_CONFIGMAP_MOUNT_DIR}'"
                    f" '-v{USER_CONFIGMAP_MOUNT_DIR}:{USER_CONFIGMAP_MOUNT_DIR}'"
                )
                podman_parts = ["podman", "run", "--rm", "--privileged"]
                if sysctl_flags:
                    podman_parts.append(sysctl_flags)
                podman_parts.extend(["-P", env_flags, vol_flags,
                                     _q(cfg.container_image)])
                run_cmd = " ".join(podman_parts) + " " + " ".join(arg_parts)
            else:
                run_cmd = " ".join(env_parts + arg_parts)
                if cfg.vm_run_as_root:
                    run_cmd = f"sudo -E {run_cmd}"
            cloud["runcmd"] = [run_cmd]

        return "#cloud-config\n" + yaml.dump(cloud, default_flow_style=False)

    def cloud_init_networkdata(self, vm_addr: tuple[int, ...]) -> str:
        """Cloud-init v2 ethernet config for secondary interface."""
        cfg = self._cfg
        raw_iface = cfg.net_interfaces.get("default", "eth1")
        if ":" in raw_iface:
            iface = raw_iface.split(":", 1)[1]
        else:
            iface = raw_iface
        addr_str = ".".join(str(o) for o in vm_addr)
        network = {
            "version": 2,
            "ethernets": {
                iface: {
                    "addresses": [f"{addr_str}/16"],
                    "dhcp4": False,
                    "dhcp6": False,
                }
            },
        }
        return yaml.dump(network, default_flow_style=False)

    # -- VM helpers ----------------------------------------------------------

    def _run_strategy(self) -> dict[str, str]:
        cfg = self._cfg
        if cfg.vm_run_strategy:
            return {"runStrategy": cfg.vm_run_strategy}
        if cfg.vm_start_running:
            return {"runStrategy": "Always"}
        return {"runStrategy": "Halted"}

    def _eviction_strategy(self) -> str | None:
        if self._cfg.vm_evict_migrate:
            return "LiveMigrate"
        return None

    def _interfaces(self, net_iface: str) -> list[dict[str, Any]]:
        interfaces: list[dict[str, Any]] = [
            {"name": "default", "masquerade": {}},
        ]
        if net_iface:
            interfaces.append({"name": "multus", "bridge": {}})
        return interfaces

    def _networks(self, net_iface: str) -> list[dict[str, Any]]:
        networks: list[dict[str, Any]] = [
            {"name": "default", "pod": {}},
        ]
        if net_iface:
            networks.append({
                "name": "multus",
                "multus": {"networkName": net_iface},
            })
        return networks

    def _increment_vm_addr(self) -> None:
        self._vm_addr[3] += 1
        for i in range(3, 0, -1):
            if self._vm_addr[i] > 254:
                self._vm_addr[i] = 1
                self._vm_addr[i - 1] += 1


class VirtctlInterface:
    """Subprocess wrapper around ``virtctl`` for VM operations."""

    def __init__(
        self,
        virtctl_path: str | None = None,
        *,
        doit: bool = True,
    ):
        if virtctl_path:
            self._virtctl = virtctl_path
        else:
            self._virtctl = shutil.which("virtctl") or "virtctl"
        self._doit = doit
        self._local_ssh: bool | None = None
        self._use_type_and_name: bool = False
        self._probed = False

    def _probe(self) -> None:
        """Probe virtctl capabilities (--local-ssh, vm/name/ns form)."""
        if self._probed:
            return
        self._probed = True
        try:
            result = subprocess.run(
                [self._virtctl, "ssh", "--help"],
                capture_output=True, text=True,
            )
            self._local_ssh = "--local-ssh" in result.stdout
            self._use_type_and_name = "TYPE/NAME" in result.stdout
        except FileNotFoundError:
            self._local_ssh = False
            self._use_type_and_name = False

    def start(self, vm_name: str, namespace: str) -> None:
        """``virtctl start``."""
        if not self._doit:
            _LOG.debug("(skipped) virtctl start %s -n %s", vm_name, namespace)
            return
        subprocess.run(
            [self._virtctl, "start", vm_name, "-n", namespace],
            check=False,
        )

    def ssh(
        self,
        user: str,
        vm_name: str,
        namespace: str,
        command: str,
    ) -> subprocess.CompletedProcess[str]:
        """``virtctl ssh``."""
        self._probe()
        cmd = [self._virtctl, "ssh"]
        if self._local_ssh:
            cmd.append("--local-ssh")
        cmd.extend(["-n", namespace])

        if self._use_type_and_name:
            cmd.extend(["-l", user, f"vm/{vm_name}"])
        else:
            cmd.append(f"{user}@{vm_name}.{namespace}")

        cmd.extend(["--command", command])

        return subprocess.run(cmd, capture_output=True, text=True)

    def scp(
        self,
        source: str,
        dest: str,
        *,
        namespace: str = "",
        ssh_options: list[str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """``virtctl scp``."""
        self._probe()
        cmd = [self._virtctl, "scp"]
        if self._local_ssh:
            cmd.append("--local-ssh")
        if namespace:
            cmd.extend(["-n", namespace])
        if ssh_options:
            for opt in ssh_options:
                cmd.extend(["--ssh-options", opt])
        cmd.extend([source, dest])

        return subprocess.run(cmd, capture_output=True, text=True)
