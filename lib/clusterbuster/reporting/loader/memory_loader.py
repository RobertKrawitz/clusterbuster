#!/usr/bin/env python3
# Copyright 2023-2026 Robert Krawitz/Red Hat
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from .ClusterBusterLoader import ClusterBusterLoadOneReportBase


class memory_loader(ClusterBusterLoadOneReportBase):
    def __init__(self, name: str, report: dict, data: dict, extras=None, allow_mismatch=False):
        super().__init__(name, report, data, extras=extras, allow_mismatch=allow_mismatch)

    def Load(self):
        if not self._summary.get('total_instances'):
            return
        opts = self._metadata.get('options', {})
        wo = opts.get('workload_options') or opts.get('workloadOptions') or {}
        replicas = opts.get('replicas', 1)
        namespaces = opts.get('namespaces', 1)
        deps = opts.get('deployments_per_namespace', 1)
        containers = opts.get('containers_per_pod', 1)
        denom = namespaces * deps * replicas * containers
        if denom <= 0:
            denom = 1
        processes = self._summary['total_instances'] // denom
        if processes < 1:
            processes = 1
        ms = wo.get('memory_size')
        if isinstance(ms, list) and ms:
            mem_key = ms[0]
        else:
            mem_key = ms if ms is not None else 0
        sc = wo.get('memory_scan', 0)
        scan_map = {0: 'None', 1: 'Sequential', 2: 'Random'}
        scan_label = scan_map.get(sc, str(sc))
        self._MakeHierarchy(self._data, ['memory', replicas, processes, mem_key, scan_label, self._name])
        root = self._data['memory'][replicas][processes][mem_key][scan_label][self._name]
        root['pages_scanned_sec'] = self._summary.get('pages_scanned_sec', 0)
        root['total_pages'] = self._summary.get('total_pages', 0)
        root['job_runtime'] = self._summary.get('job_runtime', 0)
        root['scan_pattern'] = self._summary.get('scan_pattern', scan_label)
