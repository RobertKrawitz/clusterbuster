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
# HammerDB summary analysis: code written by Cursor (Auto).

from ..ClusterBusterAnalysis import ClusterBusterAnalyzeOneBase


class hammerdb_analysis(ClusterBusterAnalyzeOneBase):
    """
    Analyze HammerDB data (NOPM, TPM)
    """

    @staticmethod
    def __augment_parser_workload(parser):
        """Register workload-specific command-line flags for analyze-clusterbuster-report."""
        pass

    def __init__(self, workload: str, data: dict, metadata: dict):
        super().__init__(workload, data, metadata)
        self._baseline = self._metadata.get('baseline')

    def Analyze(self):
        answer = {'workload': self._workload}
        max_pods = {}
        nopm = {}
        tpm = {}
        for pods, data1 in self._data.items():
            for runtime, data2 in data1.items():
                if runtime not in max_pods:
                    max_pods[runtime] = 0
                    nopm[runtime] = {}
                    tpm[runtime] = {}
                if pods > max_pods[runtime]:
                    max_pods[runtime] = pods
                nopm[runtime][pods] = data2.get('nopm', 0)
                tpm[runtime][pods] = data2.get('tpm', 0)
        min_max_pods = min(max_pods.values()) if max_pods else None
        for runtime in max_pods:
            answer[runtime] = {}
            answer[runtime]['Max Pods'] = max_pods[runtime]
            if min_max_pods is not None:
                answer[runtime]['NOPM'] = nopm[runtime].get(min_max_pods, 0)
                answer[runtime]['TPM'] = tpm[runtime].get(min_max_pods, 0)
            if self._baseline and self._baseline in answer and runtime != self._baseline:
                try:
                    base_nopm = nopm[self._baseline].get(min_max_pods) or 1
                    base_tpm = tpm[self._baseline].get(min_max_pods) or 1
                    answer[runtime]['Ratio NOPM'] = nopm[runtime].get(min_max_pods, 0) / base_nopm
                    answer[runtime]['Ratio TPM'] = tpm[runtime].get(min_max_pods, 0) / base_tpm
                except (TypeError, ZeroDivisionError):
                    pass
        return answer
