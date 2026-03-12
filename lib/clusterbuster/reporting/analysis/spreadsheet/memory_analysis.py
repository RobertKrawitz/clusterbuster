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

from .analyze_spreadsheet_generic import SpreadsheetAnalysis


class memory_analysis(SpreadsheetAnalysis):
    """
    Analyze memory workload data for spreadsheet reports.
    """

    def __init__(self, workload: str, data: dict, metadata: dict):
        dimensions = ['By Replicas', 'By Processes', 'By Memory Size', 'By Scan Pattern']
        variables = [
            {
             'var': 'pages_scanned_sec',
             'name': 'Pages scanned',
             'unit': ' (/sec)',
             'base': 0,
             },
            {
             'var': 'total_pages',
             'name': 'Total pages',
             'base': 0,
             },
            {
             'var': 'job_runtime',
             'name': 'Job runtime',
             'unit': ' (sec)',
             'base': 0,
             },
            ]
        super().__init__(workload, data, metadata, dimensions, variables)
