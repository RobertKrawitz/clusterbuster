# ClusterBuster release CI profile
#
# Large workers (~256GiB / 64 CPU); up to ~24 hours.

force-pull=1

# instances : directories : files : blocksize : filesize : O_DIRECT
files-params=1:256:256:4096:0:0
files-params=1:256:256:4096:0:1
files-params=1:256:256:4096:4096:0
files-params=1:256:256:4096:4096:1
files-params=1:256:256:4096:262144:0
files-params=1:256:256:4096:262144:1
files-params=1:256:256:65536:262144:0
files-params=1:256:256:65536:262144:1
files-params=4:256:256:4096:0:0
files-params=4:256:256:4096:0:1
files-params=4:256:256:4096:4096:0
files-params=4:256:256:4096:4096:1
files-params=4:256:256:4096:262144:0
files-params=4:256:256:4096:262144:1
files-params=4:256:256:65536:262144:0
files-params=4:256:256:65536:262144:1
files-timeout=7200

fio-fdatasync=0
fio-timeout=5400
fio-absolute-filesize=128Gi
fio-memsize=4096

uperf-timeout=300

cpusoaker-timeout=1200
cpusoaker-initial-replicas=1,2,3,4
pod-start-timeout:cpusoaker:!vm=180
pod-start-timeout:cpusoaker:vm=600

# runtime:replicas:processes:alloc:scan — soak-style runtimes; vm-memory for multi-GiB guest allocs.
memory-timeout=14400
vm-cores:memory=8
vm-memory:memory=48Gi
memory-params=600:32:2:512Mi:1
memory-params=900:16:4:1Gi:random
memory-params=1200:8:4:4Gi:1

# HammerDB recommends >=15-20 min steady-state; 1800s = 30 min workload runtime.
hammerdb-timeout=3600
timeout:hammerdb:vm=7200
# runtime:driver:replicas:rampup:virtual_users:benchmark
hammerdb-params=1800:pg:2:1:4:tpcc
hammerdb-params=1800:mariadb:2:1:4:tpcc
limit:hammerdb=memory=7Gi
limit:hammerdb=cpu=4
vm-cores:hammerdb=5
vm-memory:hammerdb=8Gi

job_runtime=60
artifactdir=
virtiofsd-direct=1
restart=0
use-python-venv=1
cleanup=1
deployment-type=replicaset

volume:files,fio:!vm=:emptydir:/var/opt/clusterbuster
volume:files:vm=:emptydisk:/var/opt/clusterbuster:size=auto:inodes=auto
volume:fio:vm=:emptydisk:/var/opt/clusterbuster:size=auto
