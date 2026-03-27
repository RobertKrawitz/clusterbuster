# `clusterbuster-hammerdb-vm` disk image

Built **the same way** as `clusterbuster-vm` (`Makefile` target `%.containerdisk`):

1. Copy upstream CentOS Stream GenericCloud qcow2 (x86_64 only for HammerDB).
2. `virt-customize -a … --firstboot firstboot.hammerdb.vm`
3. `virt-install --import … --network default --boot=hd` — guest boots once, firstboot runs `dnf`/`rpm` on a **live SELinux-enforcing** system (correct contexts, same as mainline).
4. Guest runs `poweroff`; destroy/undefine the transient domain.

**Contents (firstboot only, no runtime `dnf` in the workload):** PostgreSQL (`postgresql-server`, `postgresql`, optional `postgresql-contrib`), MariaDB (`mariadb-server`, `mariadb`), HammerDB RPM on x86_64, plus symlinks from `/usr/pgsql-*/bin/*` into `/usr/local/bin` so `initdb` and related tools are on `PATH` for non-login processes.

Then `Dockerfile.hammerdb-vm` packs the qcow2 into the containerdisk image for KubeVirt.

**Requirements:** `wget`, `virt-customize`, `virt-install`, libvirt **default** network (same as mainline VM build), and enough time for dnf + HammerDB RPM inside the guest.

```bash
cd lib/container-image
make base-image-hammerdb-vm-amd64x.qcow2   # long-running
make hammerdb-vm.image                      # buildah + push
```

**Why not arm64:** HammerDB does not publish an aarch64 RPM for this flow; the workload remains amd64-only for VMs.

**Troubleshooting (label overrides / libvirt):** If `virt-customize` fails with *"label overrides require relabeling to be enabled at the domain level"* (host libvirt/SELinux), even with `LIBGUESTFS_BACKEND=direct` or `setenforce 0`, run **virt-customize inside a container** so the host’s libvirt is not used:

```bash
make guestfs-image
make base-image-hammerdb-vm-amd64x.qcow2 VIRT_CUSTOMIZE_IN_CONTAINER=1
make hammerdb-vm.image
```

For the mainline VM disk (e.g. `make amd64.containerdisk`), use the same flag:

```bash
make guestfs-image
make amd64.containerdisk VIRT_CUSTOMIZE_IN_CONTAINER=1
```

Requires Podman and `/dev/kvm` available to the container.
