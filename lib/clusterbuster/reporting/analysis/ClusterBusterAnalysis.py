#!/usr/bin/env python3
# Copyright 2022-2023 Robert Krawitz/Red Hat
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

# ClusterBusterAnalysis CLI orchestration (augment_parser, run_analysis): code written by Cursor (Auto).

import sys
import os
import glob
import pathlib
import importlib
import inspect
import argparse
import json
from ..reporting_exceptions import ClusterBusterReportingException


class ClusterBusterAnalysisException(ClusterBusterReportingException):
    def __init__(self, *args):
        super().__init__(args)


class _ClusterBusterAnalysisIncompatibleReportTypes(ClusterBusterAnalysisException):
    def __init__(self, workload, report_type, you):
        super().__init__("Incompatible report types for %s: expect %s, found %s" %
                         (workload, report_type, you.__class__.__name__))


class _ClusterBusterAnalysisBadReportType(ClusterBusterAnalysisException):
    def __init__(self, report_type):
        super().__init__("Unexpected report type %s, expect either str or dict" %
                         (report_type.__name__))


class _ClusterBusterAnalysisImportFailed(ClusterBusterAnalysisException):
    def __init__(self, report_type, exc):
        super().__init__(f"Failed to import module {report_type}: {exc}")


class _ClusterBusterAnalysisListFormats(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        print('\n'.join(sorted(ClusterBusterAnalysis.list_analysis_formats())))
        sys.exit(0)


class ClusterBusterAnalyzeOneBase:
    """
    Base class for all workload and report type analysis classes.
    This class should not be instantiated directly.
    """
    def __init__(self, workload: str, data: dict, metadata: dict):
        self._workload = workload
        self._data = data
        self._metadata = metadata

    def _safe_get(self, obj, keys: list, default=None):
        try:
            while keys:
                key = keys[0]
                obj = obj[key]
                keys = keys[1:]
            return obj
        except KeyError:
            return default

    def Analyze(self):
        pass


class _ClusterBusterAnalysisBase:
    def __init__(self):
        pass

    def job_status_vars(self):
        return ['result', 'job_start', 'job_end', 'job_runtime']

    def job_metadata_vars(self):
        return ['uuid', 'run_host', 'openshift_version', 'kata_containers_version', 'kata_version', 'cnv_version']


class ClusterBusterPostprocessBase(_ClusterBusterAnalysisBase):
    """
    Base class for postprocessors.
    This class should not be instantiated directly.
    """
    def __init__(self, report, status, metadata, extras=None, allow_mismatch=False):
        self._report = report
        self._status = status
        self._metadata = metadata
        self._extra_args = extras
        self._allow_mismatch = allow_mismatch


class ClusterBusterAnalysis(_ClusterBusterAnalysisBase):
    """
    Analyze ClusterBuster reports
    """
    def __init__(self, data: dict, report_type=None, extras=None, allow_mismatch=False):
        super().__init__()
        self._data = data
        self._extras = extras
        self._allow_mismatch = allow_mismatch
        if report_type is None:
            report_type = 'ci'
        self._report_type = report_type

    @staticmethod
    def list_analysis_formats():
        return ['ci', 'spreadsheet', 'summary', 'raw']

    @staticmethod
    def augment_parser(parser=None):
        """
        Add arguments to any top-level parser used for analyze-clusterbuster-report.

        Core options are registered first. Then, for each analysis report type directory
        that exists under this package, optional hooks are invoked:

        - AnalyzePostprocess.__augment_parser_report_type(parser) in
          <report_type>/analyze_postprocess.py (name-mangled:
          _AnalyzePostprocess__augment_parser_report_type).

        - For each *_analysis.py in <report_type>/, the class named like the module stem
          may define __augment_parser_workload(parser) (name-mangled:
          _<stem>__augment_parser_workload), same convention as workload reporters.
        """
        if parser is None:
            parser = argparse.ArgumentParser(description='Analyze ClusterBuster report')

        analysis_formats = ClusterBusterAnalysis.list_analysis_formats()
        parser.add_argument('--list_formats', '--list-formats', action=_ClusterBusterAnalysisListFormats,
                            nargs=0, help='List available analysis formats')
        parser.add_argument('-o', '--outfile', default=None, type=str, metavar='file',
                            help='Output filename')
        parser.add_argument('--std_report', '--std-report', '--std', action='store_true',
                            help='Compare results for standard runtime classes')
        parser.add_argument('--kata', action='store_true',
                            help='Compare results for standard runtime classes')
        parser.add_argument('-r', '--report-type', '--report_type', default=None, type=str, metavar='format',
                            choices=analysis_formats,
                            help=f'Analysis format: one of {", ".join(analysis_formats)}')
        parser.add_argument('-w', '--workload', type=str, help='Workloads to process', action='append')
        parser.add_argument('--allow-mismatch', action='store_true',
                            help='Allow metadata mismatches when loading or post-processing')
        parser.add_argument('files', metavar='file', type=str, nargs='*', help='Files or directories to process')

        analysis_dir = os.path.dirname(os.path.realpath(__file__))
        for report_type in analysis_formats:
            rt_path = os.path.join(analysis_dir, report_type)
            if not os.path.isdir(rt_path):
                continue
            try:
                imported_post = importlib.import_module(f'..{report_type}.analyze_postprocess', __name__)
                for i in inspect.getmembers(imported_post):
                    if i[0] == 'AnalyzePostprocess':
                        ap_class = i[1]
                        mangled = '_AnalyzePostprocess__augment_parser_report_type'
                        if mangled in ap_class.__dict__:
                            ap_class.__dict__[mangled](parser)
                        break
            except KeyboardInterrupt:
                sys.exit(0)
            except Exception:
                pass
            for script in glob.glob(os.path.join(rt_path, '*_analysis.py')):
                if not os.path.isfile(script):
                    continue
                try:
                    base = pathlib.PurePath(os.path.basename(script)).stem
                    stem = f'..{report_type}.{base}'
                    imported_lib = importlib.import_module(stem, __name__)
                    for j in inspect.getmembers(imported_lib):
                        if j[0] == base:
                            j[1].__dict__[f'_{base}__augment_parser_workload'](parser)
                except KeyboardInterrupt:
                    sys.exit(0)
                except Exception:
                    pass
        return parser

    @staticmethod
    def parse_args(commandlineargs=sys.argv[1:]):
        return ClusterBusterAnalysis.augment_parser().parse_args(commandlineargs)

    @staticmethod
    def _dir_args_from_args(args):
        if args.std_report or args.kata:
            dir_args = []
            for arg in args.files:
                dir_args.append(f'{arg}:job_pattern=-runc-:name_suffix=runc')
                dir_args.append(f'{arg}:job_pattern=-kata-:name_suffix=kata')
                if not args.kata:
                    dir_args.append(f'{arg}:job_pattern=-vm-:name_suffix=vm')
        else:
            dir_args = list(args.files)
        if args.workload:
            dir_args = [f'{f}:job_pattern=^({"|".join(args.workload)})-' for f in dir_args]
        return dir_args

    @staticmethod
    def _write_report(f, data, args):
        try:
            report = ClusterBusterAnalysis(
                data, report_type=args.report_type, allow_mismatch=args.allow_mismatch).Analyze()
            if report is None:
                print('No report available', file=sys.stderr)
                sys.exit(1)
            elif isinstance(report, str):
                print(report, file=f)
            else:
                json.dump(report, fp=f, indent=2)
        except (KeyboardInterrupt, BrokenPipeError):
            sys.exit()

    @staticmethod
    def run_analysis(args=None):
        """
        Parse arguments (if needed), load merged data, run analysis, write output.
        Returns True on success. May call sys.exit from --list-formats.
        """
        if args is None:
            args = ClusterBusterAnalysis.parse_args()
        if not args.files:
            print('Error: at least one file or directory must be specified', file=sys.stderr)
            return False

        from ..loader.ClusterBusterLoader import ClusterBusterLoader

        dir_args = ClusterBusterAnalysis._dir_args_from_args(args)
        try:
            data = ClusterBusterLoader(allow_mismatch=args.allow_mismatch).loadFromSpecs(dir_args)
            if args.outfile and args.outfile != '-':
                with open(args.outfile, 'w') as f:
                    ClusterBusterAnalysis._write_report(f, data, args)
            else:
                ClusterBusterAnalysis._write_report(sys.stdout, data, args)
        except ClusterBusterReportingException as exc:
            print(f'Report failed: {exc}')
            return False
        return True

    def __postprocess(self, report, status, metadata):
        import_module = None
        try:
            imported_lib = importlib.import_module(f'..{self._report_type}.analyze_postprocess', __name__)
            for i in inspect.getmembers(imported_lib):
                if i[0] == 'AnalyzePostprocess':
                    import_module = i[1]
                    break
        except (SyntaxError, ModuleNotFoundError):
            pass
        if import_module is not None:
            try:
                return import_module(
                    report, status, metadata, extras=self._extras,
                    allow_mismatch=self._allow_mismatch).Postprocess()
            except TypeError as exc:
                raise _ClusterBusterAnalysisImportFailed(self._report_type, exc) from None
        else:
            return report

    def Analyze(self):
        report = dict()
        metadata = dict()
        status = dict()
        if self._data is None:
            return None
        report_type = None
        if 'metadata' in self._data:
            metadata = self._data['metadata']
        if 'status' in self._data:
            status = self._data['status']
        if self._report_type == 'raw':
            return self._data
        for workload, workload_data in sorted(self._data.items()):
            if workload == 'metadata' or workload == 'status':
                continue
            failed_load = False
            load_failed_exception = None
            try:
                imported_lib = importlib.import_module(f'..{self._report_type}.{workload}_analysis', __name__)
            except (KeyboardInterrupt, BrokenPipeError):
                sys.exit(0)
            except (SyntaxError, ModuleNotFoundError) as exc:
                if isinstance(exc, ModuleNotFoundError) and exc.name.endswith(f"{workload}_analysis"):
                    print(f'Warning: no analyzer for workload {workload}', file=sys.stderr)
                    continue
                else:
                    raise type(exc)('%s reporter: %s: %s' % (workload, exc.__class__.__name__, exc)) from None
            except Exception as exc:
                print(f'Warning: no analyzer for workload {workload} {exc}', file=sys.stderr)
                continue
            if failed_load:
                raise type(load_failed_exception)
            try:
                for i in inspect.getmembers(imported_lib):
                    if i[0] == f'{workload}_analysis':
                        report[workload] = i[1](workload, workload_data, metadata).Analyze()
                        if report_type is None:
                            report_type = type(report[workload])
                        elif not isinstance(report[workload], report_type):
                            raise _ClusterBusterAnalysisIncompatibleReportTypes(workload, report_type, report[workload])
            except (KeyboardInterrupt, BrokenPipeError):
                sys.exit()
            except Exception as exc:
                raise exc from None
        if report_type == str:
            return self.__postprocess('\n\n'.join([str(v) for v in report.values()]), status, metadata)
        elif report_type == dict or report_type == list:
            report['metadata'] = metadata
            for v in self.job_metadata_vars():
                if v in metadata:
                    report['metadata'][v] = metadata[v]
            for v in self.job_status_vars():
                if v in status:
                    report['metadata'][v] = status[v]
            if 'failed' in status and len(status['failed']) > 0:
                report['metadata']['failed'] = status['failed']
            return self.__postprocess(report, status, metadata)
        elif report_type is None:
            return None
        else:
            raise _ClusterBusterAnalysisBadReportType(report_type)
