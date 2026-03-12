.PHONY: %.image %.containerdisk guestfs-image FORCE

# Force libguestfs to use direct (qemu) backend so virt-customize does not use libvirt.
# Otherwise RHEL/Fedora SELinux can fail with: "label overrides require relabeling at the domain level".
export LIBGUESTFS_BACKEND := direct

# HammerDB container image (x86_64 only in image; arm64 skips RPM). Code written by Cursor (Auto).
all: $(addsuffix .image, base workloads vm hammerdb hammerdb-vm)
ifeq ($(NOBASEDEP),)
workloads.image: base.image
hammerdb.image: base.image
endif

# Image repo information
REPO_BASE=quay.io/rkrawitz
TAG=latest
# Container image architecture names
ARCHES=amd64 arm64
# VM containerdisk qcow2 build: aarch64/UEFI is not supported in-tree until debugged; only amd64 VM disks.
VM_DISK_ARCHES?=amd64

# Mapping between architecture names (differ between container
# images and Linux machine names).
x86_64_ARCH=amd64
aarch64_ARCH=arm64
amd64_ARCH=amd64
arm64_ARCH=arm64
amd64_ARCH_REV=x86_64
arm64_ARCH_REV=aarch64
x86_64_ARCH_REV=x86_64
aarch64_ARCH_REV=aarch64

# Default does not pass through enough CPU flags to be x86_64_v2, which is neeeded
# for CentOS-Stream9
amd64_QEMU_FLAGS=--arch=x86_64 --virt-type=kvm
x86_64_QEMU_FLAGS=$(amd64_QEMU_FLAGS)
arm64_QEMU_FLAGS=--debug --arch=aarch64 --virt-type=qemu --machine virt
aarch64_QEMU_FLAGS=$(arm64_QEMU_FLAGS)


DIST = CENTOS

# Container information (assumes Fedora)
FEDORA_VERSION=38
FEDORA_IMAGE_TYPE=Cloud
FEDORA_URL_FILE_VERSION=38-1.6

FEDORA_URL_BASE:=http://download.fedoraproject.org/pub/fedora/linux/releases
FEDORA_URL_STEM=$(FEDORA_URL_BASE)/$(FEDORA_VERSION)/$(FEDORA_IMAGE_TYPE)/__ARCH__/images
FEDORA_IMAGE_BASE=Fedora-$(FEDORA_IMAGE_TYPE)-Base-$(FEDORA_URL_FILE_VERSION)
FEDORA_CONTAINER_IAMGE=quay.io/fedora/fedora-minimal:latest
FEDORA_OS_VARIANT=fedora37

# Container information (assumes CentOS Stream)
CENTOS_VERSION=9
CENTOS_IMAGE_TYPE=GenericCloud
CENTOS_URL_FILE_VERSION=latest

CENTOS_URL_BASE=https://cloud.centos.org/centos
CENTOS_URL_STEM=$(CENTOS_URL_BASE)/$(CENTOS_VERSION)-stream/__ARCH__/images
CENTOS_IMAGE_BASE=CentOS-Stream-$(CENTOS_IMAGE_TYPE)-$(CENTOS_VERSION)-$(CENTOS_URL_FILE_VERSION)
CENTOS_CONTAINER_IMAGE=quay.io/centos/centos:stream$(CENTOS_VERSION)-minimal
CENTOS_OS_VARIANT=centos-stream$(CENTOS_VERSION)

# Container image (generic)

URL_TEMPLATE=$($(DIST)_URL_STEM)/$($(DIST)_IMAGE_BASE).__ARCH__.qcow2
# This is a bit tricky.  It relies on this only being used within the
# %.containerdisk rule, so $* will be set correctly.
IMAGE=$(subst __ARCH__,$($*_ARCH_REV),$(URL_TEMPLATE))
IMAGE_BASE=$(notdir $(IMAGE))
OS_VARIANT=$($(DIST)_OS_VARIANT)

MANIFEST="build-clusterbuster-$*"

define NL


endef

ifneq ($(CB_REGISTRY_AUTH_FILE),)
export REGISTRY_AUTH_FILE=$(CB_REGISTRY_AUTH_FILE)
endif

# Run virt-customize in a container to avoid host libvirt "label overrides require relabeling" (set to 1).
# Requires: make guestfs-image first.
VIRT_CUSTOMIZE_IN_CONTAINER?=0
GUESTFS_IMAGE?=clusterbuster-guestfs:local

vm_deps = $(foreach arch, $(ARCHES), $(IMAGE))
base_deps := FORCE
workloads_deps := FORCE

vm.image:: $(addsuffix .containerdisk, $(VM_DISK_ARCHES))
	-buildah manifest rm build-clusterbuster-vm 2>/dev/null; true
	buildah manifest create build-clusterbuster-vm
	for arch in $(VM_DISK_ARCHES); do \
		buildah bud --build-arg ARCH=$$arch -t $(REPO_BASE)/clusterbuster-vm:$(TAG) --manifest build-clusterbuster-vm -f Dockerfile.vm --arch $$arch .; \
	done
	buildah manifest push --all build-clusterbuster-vm docker://$(REPO_BASE)/clusterbuster-vm:$(TAG)

# HammerDB VM disk: same mechanism as %.containerdisk — virt-customize --firstboot + virt-install.
# (Offline virt-customize-only install was removed: SELinux labels and live-system behavior diverged.)
# amd64 only (HammerDB has no arm64 RPM). Uses distinct virt name so it does not clash with cnv-containerdisk.
IMAGE_HAMMERDB_AMD64=$(subst __ARCH__,x86_64,$(URL_TEMPLATE))
IMAGE_BASE_HAMMERDB_AMD64=$(notdir $(IMAGE_HAMMERDB_AMD64))
HAMMERDB_VM_VIRT_NAME=cnv-hammerdb-vm

base-image-hammerdb-vm-amd64x.qcow2: $(IMAGE_BASE_HAMMERDB_AMD64)
	$(MAKE) $(IMAGE_BASE_HAMMERDB_AMD64) REMOTE=$(IMAGE_HAMMERDB_AMD64)
	-virsh destroy $(HAMMERDB_VM_VIRT_NAME) 2>/dev/null || true
	-virsh undefine $(HAMMERDB_VM_VIRT_NAME) --nvram 2>/dev/null || true
	rm -f base-image-hammerdb-vm-amd64x.qcow2
	cp -p $(IMAGE_BASE_HAMMERDB_AMD64) base-image-hammerdb-vm-amd64x.qcow2
	@if [ "$(VIRT_CUSTOMIZE_IN_CONTAINER)" = "1" ]; then \
		KVM_OPT=; [ -r /dev/kvm ] && KVM_OPT="--device /dev/kvm"; \
		podman run --rm -v $(CURDIR):/work -w /work $$KVM_OPT $(GUESTFS_IMAGE) -a /work/base-image-hammerdb-vm-amd64x.qcow2 --firstboot /work/firstboot.hammerdb.vm; \
		chown $$(id -u):$$(id -g) base-image-hammerdb-vm-amd64x.qcow2 2>/dev/null || true; \
	else \
		LIBGUESTFS_BACKEND=direct virt-customize -a base-image-hammerdb-vm-amd64x.qcow2 --firstboot firstboot.hammerdb.vm; \
	fi
	virt-install --disk base-image-hammerdb-vm-amd64x.qcow2 --import --name=$(HAMMERDB_VM_VIRT_NAME) --graphics=none --network default --os-variant=$(OS_VARIANT) $(amd64_QEMU_FLAGS) --boot=hd
	-virsh destroy $(HAMMERDB_VM_VIRT_NAME) 2>/dev/null || true
	-virsh undefine $(HAMMERDB_VM_VIRT_NAME) --nvram 2>/dev/null || true

hammerdb-vm.image: base-image-hammerdb-vm-amd64x.qcow2
	buildah bud --build-arg ARCH=amd64 -t $(REPO_BASE)/clusterbuster-hammerdb-vm:$(TAG) -f Dockerfile.hammerdb-vm --arch amd64 .
	buildah push $(REPO_BASE)/clusterbuster-hammerdb-vm:$(TAG) docker://$(REPO_BASE)/clusterbuster-hammerdb-vm:$(TAG)

%.image:
	-buildah manifest rm $(MANIFEST)
	buildah manifest create $(MANIFEST)
	$(foreach arch, $(ARCHES), buildah  --build-arg ARCH=$(arch) --build-arg CONTAINER_IMAGE=$($(DIST)_CONTAINER_IMAGE) bud -t "$(REPO_BASE)/clusterbuster-$*:$(TAG)" --manifest $(MANIFEST) -f "Dockerfile.$*" --arch $(arch)$(NL))
	buildah manifest push --all $(MANIFEST) docker://$(REPO_BASE)/clusterbuster-$*:$(TAG)

%.qcow2:
	wget $(REMOTE)

# Workloads VM: mainline procedure. firstboot.vm installs packages; VM is booted then destroyed.
# (Reverted from wait-for-shutdown; use this to match mainline VM build.)
%.containerdisk:
ifeq ($(IMAGE),)
	-echo "Unknown architecture $*"
	false
endif
	$(MAKE) $(IMAGE_BASE) REMOTE=$(IMAGE)
	-virsh destroy cnv-containerdisk 2>/dev/null || true
	-virsh undefine cnv-containerdisk --nvram 2>/dev/null || true
	-rm -f base-image-$*x.qcow2
	cp -p $(IMAGE_BASE) base-image-$*x.qcow2
	@if [ "$(VIRT_CUSTOMIZE_IN_CONTAINER)" = "1" ]; then \
		KVM_OPT=; [ -r /dev/kvm ] && KVM_OPT="--device /dev/kvm"; \
		podman run --rm -v $(CURDIR):/work -w /work $$KVM_OPT $(GUESTFS_IMAGE) -a /work/base-image-$*x.qcow2 --firstboot /work/firstboot.vm; \
		chown $$(id -u):$$(id -g) base-image-$*x.qcow2 2>/dev/null || true; \
	else \
		LIBGUESTFS_BACKEND=direct virt-customize -a base-image-$*x.qcow2 --firstboot firstboot.vm; \
	fi
	virt-install --disk base-image-$*x.qcow2 --import --name=cnv-containerdisk --graphics=none --network default --os-variant=$(OS_VARIANT) $($*_QEMU_FLAGS) --boot=hd
	-virsh destroy cnv-containerdisk 2>/dev/null || true
	-virsh undefine cnv-containerdisk --nvram 2>/dev/null || true

FORCE: ;

# Build image for running virt-customize in container (avoids host libvirt/SELinux "label overrides" error).
guestfs-image:
	podman build -t $(GUESTFS_IMAGE) -f Dockerfile.guestfs .

clean:
	rm -f *.qcow2
