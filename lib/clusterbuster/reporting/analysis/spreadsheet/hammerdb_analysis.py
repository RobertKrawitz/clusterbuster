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
# HammerDB spreadsheet analysis: code written by Cursor (Auto).

from ..ClusterBusterAnalysis import ClusterBusterAnalyzeOneBase
from ...prettyprint import prettyprint


class hammerdb_analysis(ClusterBusterAnalyzeOneBase):
    """
    Analyze HammerDB data for spreadsheet output
    """

    @staticmethod
    def __augment_parser_workload(parser):
        """Register workload-specific command-line flags for analyze-clusterbuster-report."""
        pass

    def __init__(self, workload: str, data: dict, metadata: dict):
        super().__init__(workload, data, metadata)

    def get_value(self, data: dict, run: str, col: str, valfunc=None):
        if valfunc is not None:
            return valfunc(data, run, col)
        return self._safe_get(data, [run, col], '')

    def Analyze(self):
        answer = f"Workload: {self._workload}\n\n"
        answer += self._analyze_variables(self._data, 'nopm', 'NOPM', multiplier=1.0, integer=True)
        answer += self._analyze_variables(self._data, 'tpm', 'TPM', multiplier=1.0, integer=True)
        return answer

    def _analyze_variables(self, data: dict, column: str, header: str, multiplier=1.0, integer: bool = True):
        def isnumber(x):
            return isinstance(x, (int, float))

        tab = '\t'
        runs = list(self._metadata['jobs'].keys())
        columns_txt = f'# Pods{tab}{tab.join(runs)}'
        answer = f"\n{header}, N pods\n{columns_txt}\n"
        rows = []
        for pods, data1 in sorted(list(data.items())):
            row = [str(pods)]
            for run in runs:
                run_value = self.get_value(data1, run, column)
                row.append(prettyprint(run_value, base=0, integer=integer, precision=3, multiplier=multiplier))
            rows.append('\t'.join(row))
        answer += '\n'.join(rows) + '\n'

        answer += f"\n{header}, N pods (ratio)\n{columns_txt}\n"
        rows = []
        for pods, data1 in sorted(list(data.items())):
            baseline_value = self.get_value(data1, runs[0], column)
            row = [str(pods), '']
            for run in runs[1:]:
                run_value = self.get_value(data1, run, column)
                run_ratio = (run_value / baseline_value
                             if (isnumber(baseline_value) and baseline_value > 0 and isnumber(run_value))
                             else '')
                row.append(prettyprint(run_ratio, base=0, precision=3))
            rows.append('\t'.join(row))
        answer += '\n'.join(rows) + '\n'
        return answer
