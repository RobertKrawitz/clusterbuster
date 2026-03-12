#!/usr/bin/env python3
# Copyright 2022-2026 Robert Krawitz/Red Hat
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use it except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# HammerDB loader: code written by Cursor (Auto).

from .ClusterBusterLoader import ClusterBusterLoadOneReportBase


class hammerdb_loader(ClusterBusterLoadOneReportBase):
    def __init__(self, name: str, report: dict, data: dict, extras=None):
        super().__init__(name, report, data, extras=extras)

    def Load(self):
        if not self._summary.get('total_pods'):
            return
        self._MakeHierarchy(self._data, ['hammerdb', self._count, self._name])
        root = self._data['hammerdb'][self._count][self._name]
        root['pod_start_rate'] = self._summary.get('pod_start_rate')
        root['first_pod_start'] = self._summary.get('first_pod_start_time')
        root['last_pod_start'] = self._summary.get('last_pod_start_time')
        if 'workloads' in self._summary:
            for bench_key, bench in self._summary['workloads'].items():
                root['nopm'] = bench.get('nopm', 0)
                root['tpm'] = bench.get('tpm', 0)
                root['elapsed_time'] = bench.get('elapsed_time')
                root['virtual_users'] = bench.get('virtual_users')
                break
        try:
            root['memory'] = self._metrics['Maximum memory working set'][f'node: {self._client_pin_node}']
            root['memory_per_pod'] = root['memory'] / self._count
        except (TypeError, KeyError, ZeroDivisionError):
            pass
