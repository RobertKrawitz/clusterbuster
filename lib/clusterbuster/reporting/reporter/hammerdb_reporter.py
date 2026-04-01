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
# HammerDB reporter: code written by Cursor (Auto).

from .ClusterBusterReporter import ClusterBusterReporter


class hammerdb_reporter(ClusterBusterReporter):
    @staticmethod
    def __augment_parser_workload(parser):
        """Register workload-specific command-line flags for clusterbuster-report."""
        pass

    def __init__(self, jdata: dict, args):
        super().__init__(jdata, args)
        self._set_header_components(['namespace', 'pod', 'container', 'process_id'])
        opts = self._get_workload_options()
        benchmark = (opts.get('hammerdb_benchmark') or 'tpcc').lower()
        self._benchmark_key = benchmark
        self._workload_prefix = f'workloads.{benchmark}'
        self._add_accumulators([
            f'{self._workload_prefix}.nopm',
            f'{self._workload_prefix}.tpm',
            f'{self._workload_prefix}.elapsed_time',
            f'{self._workload_prefix}.user_cpu_time',
            f'{self._workload_prefix}.sys_cpu_time',
        ])
        self._add_fields_to_copy([f'{self._workload_prefix}.virtual_users'])

    def __update_report(self, dest: dict, source: dict, bench_data: dict):
        prefix = self._benchmark_key
        if prefix not in source:
            return
        data = source[prefix]
        dest['Elapsed Time'] = self._fformat(data.get('elapsed_time', 0), 3)
        dest['CPU Time'] = self._fformat(
            data.get('user_cpu_time', 0) + data.get('sys_cpu_time', 0), 3
        )
        dest['NOPM'] = self._prettyprint(
            data.get('nopm', 0), precision=0, integer=1, base=1000, suffix=' NOPM'
        )
        dest['TPM'] = self._prettyprint(
            data.get('tpm', 0), precision=0, integer=1, base=1000, suffix=' TPM'
        )
        dest['Virtual Users'] = data.get('virtual_users', '')

    def _generate_summary(self, results: dict):
        ClusterBusterReporter._generate_summary(self, results)
        if 'workloads' not in self._summary or self._benchmark_key not in self._summary.get('workloads', {}):
            return
        bench = self._summary['workloads'][self._benchmark_key]
        results['NOPM (total)'] = self._prettyprint(
            bench.get('nopm', 0), precision=0, integer=1, base=1000, suffix=' NOPM'
        )
        results['TPM (total)'] = self._prettyprint(
            bench.get('tpm', 0), precision=0, integer=1, base=1000, suffix=' TPM'
        )
        nopm_avg = self._safe_div(
            bench.get('nopm', 0), self._summary['total_instances'], number_only=True
        )
        results['NOPM (avg per client)'] = self._prettyprint(
            nopm_avg, precision=0, integer=1, base=1000, suffix=' NOPM'
        )

    def _generate_row(self, results: dict, row: dict):
        ClusterBusterReporter._generate_row(self, results, row)
        result = {}
        if 'workloads' in row:
            self.__update_report(result, row['workloads'], row['workloads'])
        self._insert_into(
            results,
            [row['namespace'], row['pod'], row['container'], str(row['process_id'])],
            result,
        )
