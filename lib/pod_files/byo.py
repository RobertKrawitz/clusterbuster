#!/usr/bin/env python3

import argparse
import json
import os
import shutil
from shutil import which

from clusterbuster_pod_client import clusterbuster_pod_client


class byo_client(clusterbuster_pod_client):
    """
    bring your own workload for clusterbuster
    """

    def __init__(self):
        try:
            super().__init__()
            p = argparse.ArgumentParser()
            p.add_argument('--processes', type=int, required=True)
            p.add_argument('--workdir', required=True)
            try:
                idx = self._args.index('--')
            except ValueError:
                self._abort('byo: -- separator required before workload command and arguments')
            pre, post = self._args[:idx], self._args[idx + 1:]
            args = p.parse_args(pre)
            self._set_processes(args.processes)
            self.workdir = args.workdir
            command = post[0]
            self.args = post[1:]
            self.system_podfile_dir = os.environ.get('SYSTEM_PODFILE_DIR', '.')
            self.podfile_dir = os.environ.get('USER_PODFILE_DIR', '.')
            os.environ['CB_APIVERSION'] = 'v1'
            os.environ['LIBDIR'] = self.podfile_dir
            os.environ['CB_PODNAME'] = self._podname()
            os.environ['CB_CONTAINER'] = self._container()
            os.environ['CB_NAMESPACE'] = self._namespace()
            os.makedirs(self.workdir, 0o755, exist_ok=True)
            if command.startswith('/'):
                self.command = command
            else:
                pod_src = os.path.join(self.podfile_dir, command)
                if os.path.isfile(pod_src):
                    shutil.copy(pod_src, self.workdir)
                    self.command = os.path.join(self.workdir, command)
                    os.chmod(self.command, 0o755)
                else:
                    resolved = which(command)
                    if resolved:
                        self.command = resolved
                    else:
                        raise FileNotFoundError(
                            f"byo: command {command!r} not found under "
                            f"{self.podfile_dir} and not on PATH"
                        )
            for tool in ('drop-cache', 'do-sync'):
                shutil.copy(os.path.join(self.system_podfile_dir, tool), self.workdir)
                os.chmod(os.path.join(self.workdir, tool), 0o755)
            self._timestamp("Running setup iteration")
            os.environ['PATH'] = f"{self.workdir}:{os.environ['PATH']}"
            os.chdir(self.workdir)
            self._run_command(self.command, '--setup', self.args)
        except Exception as err:
            self._abort(f"Init failed! {err} {' '.join(self._args)}")

    def runit(self, process: int):
        os.environ['CB_INDEX'] = str(process)
        os.environ['CB_ID'] = self._idname()
        elapsed_time = 0
        data_start_time = self._adjusted_time()
        ucpu, scpu = self._cputimes()
        os.chdir(self.workdir)
        success, answer, stderr = self._run_command(self.command, self.args)
        data_end_time = self._adjusted_time()
        ucpu, scpu = self._cputimes(ucpu, scpu)
        elapsed_time = data_end_time - data_start_time
        results = {}
        if success:
            try:
                results = json.loads(answer)
            except json.decoder.JSONDecodeError as e:
                results['Status'] = 'FAIL'
                results['Error'] = str(e)
                results['Output'] = answer
        else:
            results['Status'] = 'FAIL'
            results['Output'] = answer
            results['Error'] = stderr
        self._report_results(data_start_time, data_end_time, elapsed_time, ucpu, scpu, results)


byo_client().run_workload()
