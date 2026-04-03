#!/usr/bin/env python3

import argparse
import time

from clusterbuster_pod_client import clusterbuster_pod_client


class sleep_client(clusterbuster_pod_client):
    """
    Sleep test for clusterbuster
    """

    def __init__(self):
        try:
            super().__init__()
            p = argparse.ArgumentParser()
            p.add_argument('--runtime', type=float, required=True)
            p.add_argument('--processes', type=int, required=True)
            args = p.parse_args(self._args)
            self.sleep_time = args.runtime
            self._set_processes(args.processes)
        except Exception as err:
            self._abort(f"Init failed! {err} {' '.join(self._args)}")

    def runit(self, process: int):
        self._timestamp("runit")
        data_start_time = self._adjusted_time()
        self._timestamp(f"Got adjusted start time {data_start_time}")
        if self.sleep_time > 0:
            time.sleep(self.sleep_time)
        self._timestamp(f"Slept {self.sleep_time}")
        data_end_time = self._adjusted_time()
        self._timestamp(f"Got adjusted end time {data_start_time}")
        user, sys = self._cputimes()
        self._timestamp(f"User, system CPU time {user} {sys}")
        self._report_results(data_start_time, data_end_time, data_end_time - data_start_time, user, sys)


sleep_client().run_workload()
