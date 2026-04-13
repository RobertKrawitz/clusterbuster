# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""Cluster interface: subprocess wrapper around oc/kubectl."""

from __future__ import annotations

import contextlib
import json
import logging
import re
import shutil
import subprocess
from collections.abc import Iterator
from typing import IO, Any

_LOG = logging.getLogger(__name__)

_NOISE_RE = re.compile(
    r"^(No resources found|.*\b(deleted|created|labeled|condition met))$"
)


class ClusterError(Exception):
    """Raised when a cluster operation fails fatally."""

    def __init__(self, message: str, *, kubefail: bool = False):
        prefix = "__KUBEFAIL__ " if kubefail else ""
        super().__init__(f"{prefix}{message}")
        self.kubefail = kubefail


class ClusterInterface:
    """Subprocess-based wrapper around ``oc`` / ``kubectl``.

    Consolidates the 5 bash ``__OC`` … ``_____OC`` wrappers into a
    single class with explicit parameters for error handling, dry-run,
    and output filtering.
    """

    def __init__(
        self,
        oc_path: str | None = None,
        *,
        doit: bool = True,
        verbose: bool = False,
        debug_conditions: dict[str, str] | None = None,
    ):
        if oc_path:
            self._oc = oc_path
        else:
            self._oc = shutil.which("oc") or shutil.which("kubectl") or "oc"
        self._doit = doit
        self._verbose = verbose
        self._debug = debug_conditions or {}

    @property
    def oc_path(self) -> str:
        """Public accessor for the oc/kubectl binary path."""
        return self._oc

    def _debug_log(self, *args: str) -> None:
        if "kubectl" in self._debug or "all" in self._debug:
            _LOG.debug("oc %s", " ".join(args))

    # -- Core run method (equivalent to __OC) --------------------------------

    def run(
        self,
        *args: str,
        check: bool = False,
        dry_run_skip: bool = True,
        filter_output: bool = True,
        log_output: bool = False,
        log_errors: bool = True,
        stdin_data: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run an oc/kubectl command.

        Args:
            check: Raise :class:`ClusterError` on non-zero exit.
            dry_run_skip: Skip execution in dry-run mode.
            filter_output: Suppress noisy success lines from output.
            log_output: Log stdout/stderr (for mutating ops like
                create/apply/delete whose output belongs in stderr.log).
            log_errors: Log stderr on non-zero exit.  Set ``False``
                for probe commands where failure is expected (e.g.
                checking whether a namespace exists).
            stdin_data: Data to pipe to stdin.
        """
        self._debug_log(*args)

        if not self._doit and dry_run_skip:
            _LOG.debug("(skipped) %s %s", self._oc, " ".join(args))
            return subprocess.CompletedProcess(
                args=[self._oc, *args], returncode=0, stdout="", stderr=""
            )

        result = subprocess.run(
            [self._oc, *args],
            capture_output=True,
            text=True,
            input=stdin_data,
        )

        if log_output and result.stdout:
            for ln in result.stdout.splitlines():
                if ln:
                    _LOG.info("%s", ln)
        if result.stderr:
            lvl = logging.WARNING if result.returncode != 0 else logging.INFO
            if log_output or (log_errors and result.returncode != 0):
                for ln in result.stderr.splitlines():
                    if ln:
                        _LOG.log(lvl, "%s %s: %s", self._oc, args[0], ln)

        if filter_output and result.stdout:
            lines = [
                ln
                for ln in result.stdout.splitlines()
                if not _NOISE_RE.match(ln)
            ]
            result = subprocess.CompletedProcess(
                args=result.args,
                returncode=result.returncode,
                stdout="\n".join(lines) + ("\n" if lines else ""),
                stderr=result.stderr,
            )

        if check and result.returncode != 0:
            msg = f"{self._oc} {' '.join(args)} failed"
            if result.stderr:
                msg += f": {result.stderr.strip()}"
            raise ClusterError(msg)

        return result

    # -- Fatal variants (equivalent to _OC / ___OC) -------------------------

    def run_fatal(
        self, *args: str, kubefail: bool = True
    ) -> subprocess.CompletedProcess[str]:
        """Run and raise :class:`ClusterError` on failure."""
        self._debug_log(*args)

        if not self._doit:
            _LOG.debug("(skipped) %s %s", self._oc, " ".join(args))
            return subprocess.CompletedProcess(
                args=[self._oc, *args], returncode=0, stdout="", stderr=""
            )

        result = subprocess.run(
            [self._oc, *args], capture_output=True, text=True
        )

        if result.stdout:
            for ln in result.stdout.splitlines():
                if ln:
                    _LOG.info("%s", ln)
        if result.stderr:
            lvl = logging.WARNING if result.returncode != 0 else logging.INFO
            for ln in result.stderr.splitlines():
                if ln:
                    _LOG.log(lvl, "%s %s: %s", self._oc, args[0], ln)

        if result.returncode != 0:
            msg = f"{self._oc} {' '.join(args)} failed!"
            if result.stderr:
                msg += f"\n{result.stderr.strip()}"
            raise ClusterError(msg, kubefail=kubefail)

        return result

    # -- exec ----------------------------------------------------------------

    def exec_(
        self, *args: str, stdin_data: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        """``oc exec`` / ``oc rsh``."""
        self._debug_log("exec", *args)
        return subprocess.run(
            [self._oc, "exec", *args],
            capture_output=True,
            text=True,
            input=stdin_data,
        )

    # -- Structured get (JSON) -----------------------------------------------

    def get_json(self, *args: str) -> Any:
        """``oc get -ojson`` — returns parsed JSON."""
        result = self.run(
            "get", "-ojson", *args, dry_run_skip=False, filter_output=False
        )
        if result.returncode != 0:
            _LOG.warning(
                "oc get -ojson %s failed (rc=%d): %s",
                " ".join(args), result.returncode, result.stderr.strip(),
            )
            return {}
        if not result.stdout.strip():
            _LOG.warning("oc get -ojson %s returned empty output", " ".join(args))
            return {}
        return json.loads(result.stdout)

    # -- Streaming watch (context manager) -----------------------------------

    @contextlib.contextmanager
    def watch(self, *args: str) -> Iterator[Iterator[str]]:
        """``oc get -w`` — streaming line iterator.

        Yields lines as they arrive from ``oc get -w``.  The backing
        subprocess is killed when the context manager exits.

        Usage::

            with cluster.watch("get", "pod", "-w") as lines:
                for line in lines:
                    ...
        """
        self._debug_log(*args)
        proc = subprocess.Popen(
            [self._oc, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert proc.stdout is not None

            def _iter_lines(stream: IO[str]) -> Iterator[str]:
                for line in stream:
                    yield line.rstrip("\n")

            yield _iter_lines(proc.stdout)
        finally:
            proc.kill()
            proc.wait()

    # -- Convenience wrappers ------------------------------------------------

    def create(
        self,
        yaml_docs: str,
        *,
        validate: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """``oc create -f -`` with YAML piped to stdin."""
        cmd = ["create", "-f", "-"]
        if not validate:
            cmd.insert(1, "--validate=false")
        return self.run(*cmd, log_output=True, stdin_data=yaml_docs)

    def apply(
        self,
        yaml_docs: str,
        *,
        validate: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """``oc apply -f -`` with YAML piped to stdin.

        Unlike :meth:`create`, ``apply`` is idempotent: existing objects
        are updated rather than rejected with ``AlreadyExists``.
        """
        cmd = ["apply", "-f", "-"]
        if not validate:
            cmd.insert(1, "--validate=false")
        return self.run(*cmd, log_output=True, stdin_data=yaml_docs)

    def delete(
        self,
        *args: str,
        timeout: str | None = None,
        force: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """``oc delete``."""
        cmd = ["delete", *args]
        if timeout:
            cmd.append(f"--timeout={timeout}")
        if force:
            cmd.extend(["--force", "--grace-period=0"])
        return self.run(*cmd, filter_output=False, log_output=True)

    def label(self, *args: str) -> subprocess.CompletedProcess[str]:
        """``oc label``."""
        return self.run_fatal("label", *args, kubefail=False)

    def logs(self, *args: str) -> str:
        """``oc logs`` — returns stdout."""
        result = self.run(
            "logs", *args, dry_run_skip=False, filter_output=False
        )
        return result.stdout

    def describe(self, *args: str) -> str:
        """``oc describe`` — returns stdout."""
        result = self.run(
            "describe", *args, dry_run_skip=False, filter_output=False
        )
        return result.stdout

    def wait(self, *args: str) -> subprocess.CompletedProcess[str]:
        """``oc wait``."""
        return self.run("wait", *args, dry_run_skip=False, filter_output=False)

    def adm(self, *args: str) -> subprocess.CompletedProcess[str]:
        """``oc adm`` (policy, etc.)."""
        return self.run_fatal("adm", *args, kubefail=False)

    def debug_node(
        self, node: str, *args: str
    ) -> subprocess.CompletedProcess[str]:
        """``oc debug node/``."""
        return self.run(
            "debug", f"node/{node}", *args,
            dry_run_skip=False, filter_output=False,
        )
