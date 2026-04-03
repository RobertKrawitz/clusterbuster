#!/usr/bin/env python3

# Copyright 2019-2026 Robert Krawitz/Red Hat
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
# HammerDB pod client: code written by Cursor (Auto).

import argparse
import glob
import os
import platform
import re
import shutil
import socket
import subprocess
import tempfile
import time

from clusterbuster_pod_client import clusterbuster_pod_client, ClusterBusterPodClientException


# Match "TEST RESULT : System achieved X NOPM from Y ... TPM" (database name may vary)
# Also allow "X NOPM" and "Y TPM" in flexible order for different HammerDB versions
_RESULT_RE = re.compile(
    r'TEST RESULT\s*:\s*System achieved\s+([0-9]+)\s+NOPM\s+from\s+([0-9]+)\s+.*?\s+TPM',
    re.IGNORECASE
)
_RESULT_RE_ALT = re.compile(
    r'NOPM[:\s]+([0-9]+).*?TPM[:\s]+([0-9]+)|TPM[:\s]+([0-9]+).*?NOPM[:\s]+([0-9]+)',
    re.IGNORECASE | re.DOTALL
)


class hammerdb_client(clusterbuster_pod_client):
    """
    HammerDB workload for ClusterBuster (TPC-C / TPROC-C).
    """

    @staticmethod
    def _find_hammerdbcli():
        """Return path to hammerdbcli; use PATH or standard install locations."""
        path = shutil.which('hammerdbcli')
        if path:
            return path
        for prefix in ['/opt/HammerDB-5.0', '/opt/HammerDB-4.9']:
            candidate = os.path.join(prefix, 'hammerdbcli')
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        for p in glob.glob('/opt/HammerDB-*/hammerdbcli'):
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p
        return None

    @staticmethod
    def _find_pg_tool(name: str) -> str:
        """Return path to PostgreSQL binary (initdb, pg_ctl, psql).

        On EL9, postgresql-server installs under /usr/pgsql-<ver>/bin/, often not on default PATH.
        """
        # Prefer versioned pgsql bin dirs, then standard PATH.
        bin_dirs = sorted(glob.glob('/usr/pgsql-*/bin'))
        bin_dirs.extend(['/usr/bin', '/usr/local/bin'])
        path_extra = ':'.join(bin_dirs + [os.environ.get('PATH', '')])
        found = shutil.which(name, path=path_extra)
        if found:
            return found
        for p in sorted(glob.glob('/usr/pgsql-*/bin/' + name)):
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p
        return name

    def __init__(self):
        try:
            super().__init__()
            if platform.machine() != 'x86_64':
                self._abort(f"HammerDB is only supported on x86_64; this host is {platform.machine()}")
            p = argparse.ArgumentParser()
            p.add_argument('--processes', type=int, required=True)
            p.add_argument('--workdir', required=True)
            p.add_argument('--runtime', type=int, required=True)
            p.add_argument('--driver', required=True)
            p.add_argument('--database', default='hammerdb')
            p.add_argument('--benchmark', default='tpcc')
            p.add_argument('--virtual-users', type=int, default=4)
            p.add_argument('--rampup', type=int, default=1)
            args = p.parse_args(self._args)
            self._set_processes(args.processes)
            self.workdir = args.workdir
            self.runtime_sec = args.runtime
            driver_raw = (args.driver or '').strip()
            if not driver_raw:
                self._abort("hammerdb driver is required (pg or mariadb)")
            self.driver = driver_raw.lower()
            if self.driver not in ('pg', 'mariadb'):
                self._abort(f"hammerdb driver must be pg or mariadb; got {args.driver!r}")
            self.database = args.database or 'hammerdb'
            self.benchmark = (args.benchmark or 'tpcc').lower()
            self.virtual_users = args.virtual_users
            self.rampup_min = args.rampup
            # Colocated client and server
            self.host = 'localhost'
            self.port = 3306 if self.driver == 'mariadb' else 5432
            self.user = 'postgres' if self.driver == 'pg' else 'hammerdb'
        except Exception as err:
            self._abort(f"Init failed! {err} {' '.join(self._args)}")

    def _tcl_script(self, script_path: str) -> None:
        """Write a TCL script for HammerDB CLI v5. Supports PostgreSQL and MariaDB only."""
        # HammerDB v5: connection has *_host, *_port; tpcc has *_user, *_pass, *_dbase, *_driver, *_rampup, *_duration
        run_min = max(1, (self.runtime_sec + 59) // 60)
        ramp_min = max(0, self.rampup_min)
        db_key = 'pg' if self.driver == 'pg' else 'maria'
        bm_key = 'TPC-C' if self.benchmark == 'tpcc' else 'TPROC-C'

        lines = [
            f"dbset db {db_key}",
            f"dbset bm {bm_key}",
            f"diset connection {db_key}_host {self.host}",
            f"diset connection {db_key}_port {self.port}",
            f"diset tpcc {db_key}_user {self.user}",
            f"diset tpcc {db_key}_dbase {self.database}",
        ]
        if db_key == 'pg':
            lines.append("diset tpcc pg_pass $env(PGPASSWORD)")
        else:
            lines.append("diset tpcc maria_pass $env(MYSQL_PASSWORD)")
            sock = os.path.join(self.workdir, 'mariadb_data', 'mysql.sock')
            lines.append(f'diset connection maria_socket {sock}')
        lines.extend([
            f"diset tpcc {db_key}_count_ware 10",
            f"diset tpcc {db_key}_num_vu {self.virtual_users}",
            "buildschema",
            "vudestroy",
            f"diset tpcc {db_key}_driver timed",
            f"diset tpcc {db_key}_rampup {ramp_min}",
            f"diset tpcc {db_key}_duration {run_min}",
            "loadscript",
            "vurun",
        ])
        with open(script_path, 'w') as f:
            f.write('\n'.join(lines) + '\n')

    def _port_listening(self, host: str, port: int, timeout_sec: float = 2.0) -> bool:
        """Return True if host:port accepts a TCP connection."""
        try:
            with socket.create_connection((host, port), timeout=timeout_sec):
                pass
            return True
        except (OSError, socket.error):
            return False

    def _ensure_pg_running(self, pw: str) -> None:
        """Start PostgreSQL in workdir/pgdata if not already listening on port."""
        if self._port_listening(self.host, self.port):
            self._timestamp('PostgreSQL already listening')
            return
        initdb_cmd = self._find_pg_tool('initdb')
        pg_ctl_cmd = self._find_pg_tool('pg_ctl')
        psql_cmd = self._find_pg_tool('psql')
        if not os.path.isfile(initdb_cmd):
            raise ClusterBusterPodClientException(
                    f'PostgreSQL initdb not found ({initdb_cmd}). Use the clusterbuster-hammerdb or '
                    'clusterbuster-hammerdb-vm image (see lib/container-image/); the image build must '
                    'include postgresql-server.'
                )
        pgdata = os.path.join(self.workdir, 'pgdata')
        os.makedirs(pgdata, 0o755, exist_ok=True)
        run_as_postgres = False
        if os.geteuid() == 0 and shutil.which('runuser'):
            try:
                import pwd
                run_as_postgres = 'postgres' in {e.pw_name for e in pwd.getpwall()}
            except (ImportError, KeyError, OSError):
                pass
        if run_as_postgres:
            subprocess.run(['chown', '-R', 'postgres:postgres', pgdata], check=True, capture_output=True)
        pg_prefix = ['runuser', '-u', 'postgres', '--'] if run_as_postgres else []
        pg_version = os.path.join(pgdata, 'PG_VERSION')
        if not os.path.exists(pg_version):
            self._timestamp('Initializing PostgreSQL cluster')
            r = subprocess.run(
                pg_prefix + [initdb_cmd, '-D', pgdata],
                cwd=self.workdir,
                capture_output=True,
                text=True,
            )
            if r.returncode != 0:
                err = (r.stderr or r.stdout or '').strip() or f'exit {r.returncode}'
                raise ClusterBusterPodClientException(f'initdb failed: {err}')
            if run_as_postgres:
                subprocess.run(['chown', '-R', 'postgres:postgres', pgdata], check=True, capture_output=True)
            conf = os.path.join(pgdata, 'postgresql.conf')
            with open(conf, 'a') as f:
                f.write("\nlisten_addresses = 'localhost'\n")
                f.write(f"unix_socket_directories = '{pgdata}'\n")
            hba = os.path.join(pgdata, 'pg_hba.conf')
            with open(hba, 'a') as f:
                f.write('host all all 127.0.0.1/32 scram-sha-256\n')
            if run_as_postgres:
                subprocess.run(['chown', '-R', 'postgres:postgres', pgdata], check=True, capture_output=True)
        logfile = os.path.join(pgdata, 'pg.log') if run_as_postgres else os.path.join(self.workdir, 'pg.log')
        self._timestamp('Starting PostgreSQL')
        r = subprocess.run(
            pg_prefix + [pg_ctl_cmd, '-D', pgdata, '-l', logfile, 'start', '-w'],
            cwd=self.workdir,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if r.returncode != 0:
            msg = f"pg_ctl start failed (exit {r.returncode})"
            if r.stderr:
                msg += f"; stderr: {r.stderr[:500]}"
            if os.path.isfile(logfile):
                try:
                    with open(logfile) as f:
                        msg += f"; pg.log: {f.read()[-1500:]}"
                except OSError:
                    pass
            logdir = os.path.join(pgdata, 'log')
            if os.path.isdir(logdir):
                try:
                    logs = sorted(os.listdir(logdir))
                    if logs:
                        latest = os.path.join(logdir, logs[-1])
                        with open(latest) as f:
                            msg += f"; {logs[-1]}: {f.read()[-2000:]}"
                except OSError:
                    pass
            raise ClusterBusterPodClientException(msg)
        for _ in range(30):
            if self._port_listening(self.host, self.port):
                break
            time.sleep(1)
        else:
            raise ClusterBusterPodClientException('PostgreSQL did not start in time')
        env = os.environ.copy()
        env.pop('PGPASSWORD', None)
        pw_sql = f"$${pw}$$" if "'" in pw else f"'{pw}'"
        # Some images (e.g. RHEL) do not create role "postgres" from initdb; ensure it exists and has password
        for stmt in [
            f"CREATE ROLE postgres WITH LOGIN SUPERUSER PASSWORD {pw_sql}",
            f'ALTER ROLE postgres PASSWORD {pw_sql}',
            f'CREATE DATABASE {self.database} OWNER postgres',
        ]:
            r = subprocess.run(
                pg_prefix + [psql_cmd, '-h', pgdata, '-d', 'postgres', '-c', stmt],
                cwd=self.workdir,
                env=env,
                capture_output=True,
                text=True,
            )
            if r.returncode != 0 and 'already exists' not in (r.stderr or ''):
                self._timestamp(f'psql: {r.stderr or r.stdout}')
        self._timestamp('PostgreSQL ready')

    def _ensure_mariadb_running(self, pw: str) -> None:
        """Start MariaDB in workdir/mariadb_data if not already listening on port.
        Uses an init-file on first run to create user 'hammerdb' (avoids root@localhost auth issues).
        On VM (root), runs mariadbd as mysql user since MariaDB refuses to run as root.
        """
        if self._port_listening(self.host, self.port):
            self._timestamp('MariaDB already listening')
            return
        mariadb_data = os.path.join(self.workdir, 'mariadb_data')
        os.makedirs(mariadb_data, 0o755, exist_ok=True)
        mysql_system = os.path.join(mariadb_data, 'mysql')
        init_sql_path = os.path.join(self.workdir, 'mariadb_init.sql')
        first_run = not os.path.isdir(mysql_system)
        if first_run:
            self._timestamp('Initializing MariaDB datadir')
            install_db = shutil.which('mariadb-install-db') or '/usr/bin/mariadb-install-db'
        run_as_mysql = (
            os.geteuid() == 0
            and shutil.which('runuser')
            and subprocess.run(['id', 'mysql'], capture_output=True).returncode == 0
        )
        if run_as_mysql:
            subprocess.run(['chown', '-R', 'mysql:mysql', mariadb_data], check=True, capture_output=True)
        if first_run:
            if not os.path.isfile(install_db) or not os.access(install_db, os.X_OK):
                raise ClusterBusterPodClientException(
                    'mariadb-install-db not found. Use the clusterbuster-hammerdb or '
                    'clusterbuster-hammerdb-vm image (see lib/container-image/); the image build '
                    'must include mariadb-server.'
                )
            install_cmd = [install_db, '--datadir', mariadb_data]
            if run_as_mysql:
                install_cmd = ['runuser', '-u', 'mysql', '--'] + install_cmd
            subprocess.run(
                install_cmd,
                cwd=self.workdir,
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if run_as_mysql:
                subprocess.run(['chown', '-R', 'mysql:mysql', mariadb_data], check=True, capture_output=True)
            pw_escaped = pw.replace("'", "''")
            with open(init_sql_path, 'w') as f:
                f.write(
                    f"CREATE USER IF NOT EXISTS 'hammerdb'@'localhost' IDENTIFIED BY '{pw_escaped}';\n"
                    f"CREATE USER IF NOT EXISTS 'hammerdb'@'127.0.0.1' IDENTIFIED BY '{pw_escaped}';\n"
                    "GRANT ALL PRIVILEGES ON *.* TO 'hammerdb'@'localhost' WITH GRANT OPTION;\n"
                    "GRANT ALL PRIVILEGES ON *.* TO 'hammerdb'@'127.0.0.1' WITH GRANT OPTION;\n"
                    f"CREATE DATABASE IF NOT EXISTS `{self.database}`;\n"
                    "FLUSH PRIVILEGES;\n"
                )
            if run_as_mysql:
                subprocess.run(['chown', 'mysql:mysql', init_sql_path], check=True, capture_output=True)
        socket_path = os.path.join(mariadb_data, 'mysql.sock')
        for _m in (shutil.which('mariadbd'), shutil.which('mysqld'), '/usr/sbin/mariadbd', '/usr/bin/mariadbd'):
            if _m and os.path.isfile(_m):
                mariadbd = _m
                break
        else:
            mariadbd = 'mariadbd'
        pid_file = os.path.join(mariadb_data, 'mariadb.pid')
        self._timestamp('Starting MariaDB')
        cmd = [
            mariadbd,
            f'--datadir={mariadb_data}',
            '--bind-address=127.0.0.1',
            f'--socket={socket_path}',
            f'--pid-file={pid_file}',
            '--skip-log-error',
        ]
        if first_run and os.path.isfile(init_sql_path):
            cmd.append(f'--init-file={init_sql_path}')
        if run_as_mysql:
            cmd = ['runuser', '-u', 'mysql', '--'] + cmd
        proc = subprocess.Popen(
            cmd,
            cwd=self.workdir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(60):
            if self._port_listening(self.host, self.port):
                break
            if proc.poll() is not None:
                _, err = proc.communicate()
                raise ClusterBusterPodClientException(
                    f'MariaDB exited early: {proc.returncode}; stderr: {(err or "")[-1000:]}'
                )
            time.sleep(1)
        else:
            proc.terminate()
            proc.wait(timeout=5)
            raise ClusterBusterPodClientException('MariaDB did not start in time')
        try:
            os.remove(init_sql_path)
        except OSError:
            pass
        self._timestamp('MariaDB ready')

    def _ensure_db_running(self, pw: str) -> None:
        """Ensure the database (pg or mariadb) is running in this pod."""
        if self.driver == 'pg':
            self._ensure_pg_running(pw)
        else:
            self._ensure_mariadb_running(pw)

    def runit(self, process: int):
        self._sync_to_controller()
        os.makedirs(self.workdir, 0o755, exist_ok=True)
        pw = None
        for path in ['/etc/hammerdb/password', '/tmp/hammerdb-password', os.path.expanduser('~/.hammerdb-password')]:
            if os.path.isfile(path):
                with open(path) as pf:
                    pw = pf.read().strip()
                break
        if pw is None:
            pw = os.environ.get('HAMMERDB_DB_PASSWORD', 'clusterbuster')
        self._ensure_db_running(pw)
        self._timestamp(f'Running HammerDB {self.benchmark} driver={self.driver}')
        data_start_time = self._adjusted_time()
        user, sys = self._cputimes()

        with tempfile.NamedTemporaryFile(mode='w', suffix='.tcl', delete=False) as f:
            script_path = f.name
        try:
            self._tcl_script(script_path)
            env = os.environ.copy()
            if self.driver == 'pg':
                env['PGPASSWORD'] = pw
                # HammerDB Pgtcl needs libpq.so.5; ensure loader can find it (VM: /usr/lib64, /usr/pgsql-*/lib)
                extra_ld = []
                for d in ['/usr/lib64', '/usr/lib', '/lib64', '/lib'] + sorted(glob.glob('/usr/pgsql-*/lib')):
                    if os.path.isdir(d) and d not in extra_ld:
                        extra_ld.append(d)
                # Also ask ldconfig where libpq is (e.g. on some systems)
                try:
                    r = subprocess.run(['ldconfig', '-p'], capture_output=True, text=True, timeout=5)
                    if r.returncode == 0 and r.stdout:
                        for line in r.stdout.splitlines():
                            if 'libpq' in line and ' => ' in line:
                                path = line.split(' => ', 1)[1].strip()
                                d = os.path.dirname(path)
                                if os.path.isdir(d) and d not in extra_ld:
                                    extra_ld.append(d)
                                break
                except (OSError, subprocess.TimeoutExpired):
                    pass
                if extra_ld:
                    ld = ':'.join(extra_ld)
                    if env.get('LD_LIBRARY_PATH'):
                        ld = ld + ':' + env['LD_LIBRARY_PATH']
                    env['LD_LIBRARY_PATH'] = ld
            else:
                env['MYSQL_PASSWORD'] = pw
            hammerdbcli = self._find_hammerdbcli()
            if not hammerdbcli:
                self._timestamp(f'PATH={os.environ.get("PATH", "")!r}')
                if os.path.isdir('/opt'):
                    try:
                        self._timestamp(f'/opt contents: {os.listdir("/opt")}')
                    except OSError as e:
                        self._timestamp(f'/opt listdir failed: {e}')
                for d in ['/opt/HammerDB-5.0', '/opt/HammerDB-4.9']:
                    if os.path.isdir(d):
                        try:
                            self._timestamp(f'{d} contents: {os.listdir(d)}')
                        except OSError as e:
                            self._timestamp(f'{d} listdir failed: {e}')
                raise ClusterBusterPodClientException(
                    'hammerdbcli not found; use the clusterbuster-hammerdb container image '
                    '(verify pod spec.containers[].image)'
                )
            cmd = [hammerdbcli, 'auto', script_path]
            self._timestamp(f'Exec: {" ".join(cmd)}')
            self._sync_to_controller()
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=self.workdir,
                env=env,
            )
            stdout, _ = proc.communicate()
            for line in stdout.splitlines():
                self._timestamp(line)
            if proc.returncode and proc.returncode != 0:
                raise ClusterBusterPodClientException(f"HammerDB exited with code {proc.returncode}")
            if 'Connection to database failed' in stdout:
                raise ClusterBusterPodClientException(
                    'HammerDB could not connect to the database; check that the DB is running'
                )
        finally:
            try:
                os.unlink(script_path)
            except OSError:
                pass

        data_end_time = self._adjusted_time()
        elapsed_time = data_end_time - data_start_time
        user, sys = self._cputimes(user, sys)

        nopm = None
        tpm = None
        for line in stdout.splitlines():
            m = _RESULT_RE.search(line)
            if m:
                nopm = int(m.group(1))
                tpm = int(m.group(2))
                break
        if nopm is None or tpm is None:
            m = _RESULT_RE_ALT.search(stdout)
            if m:
                g = m.groups()
                if g[0] is not None:
                    nopm, tpm = int(g[0]), int(g[1])
                else:
                    tpm, nopm = int(g[2]), int(g[3])

        if nopm is None or tpm is None:
            nopm = nopm if nopm is not None else 0
            tpm = tpm if tpm is not None else 0
            if 'FINISHED FAILED' in stdout and 'TEST RESULT' not in stdout:
                tail = '\n'.join(stdout.splitlines()[-25:]) if stdout else ''
                raise ClusterBusterPodClientException(
                    'HammerDB run failed (no TPM result); check logs for errors. Last output:\n' + tail
                )
            self._timestamp('Warning: could not parse NOPM/TPM from HammerDB output; reporting zeros')

        bench_key = self.benchmark
        op_answer = {
            'nopm': nopm,
            'tpm': tpm,
            'elapsed_time': elapsed_time,
            'virtual_users': self.virtual_users,
            'user_cpu_time': user,
            'sys_cpu_time': sys,
        }
        extras = {'workloads': {bench_key: op_answer}}
        self._report_results(data_start_time, data_end_time, elapsed_time, user, sys, extras)


hammerdb_client().run_workload()
