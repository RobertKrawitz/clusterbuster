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
# HammerDB CI analysis: code written by Cursor (Auto).

from .analyze_ci_generic import CIAnalysis


class hammerdb_analysis(CIAnalysis):
    """
    Analyze HammerDB data for CI
    """

    def __init__(self, workload: str, data: dict, metadata: dict):
        super().__init__(workload, data, metadata,
                         ['pods', 'runtime'])
