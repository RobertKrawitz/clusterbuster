# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""Unit tests for clusterbuster.driver.cluster — ClusterInterface."""

from __future__ import annotations

from clusterbuster.driver.cluster import ClusterInterface, ClusterError


class TestClusterInterfaceDryRun:
    def test_run_skips_in_dry_run(self):
        ci = ClusterInterface(oc_path="/bin/echo", doit=False)
        result = ci.run("get", "pods", dry_run_skip=True)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_run_executes_when_no_skip(self):
        ci = ClusterInterface(oc_path="/bin/echo", doit=False)
        result = ci.run("hello", "world", dry_run_skip=False)
        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_run_fatal_skips_in_dry_run(self):
        ci = ClusterInterface(oc_path="/bin/echo", doit=False)
        result = ci.run_fatal("create", "-f", "-")
        assert result.returncode == 0

    def test_create_skips_in_dry_run(self):
        ci = ClusterInterface(oc_path="/bin/echo", doit=False)
        result = ci.create("apiVersion: v1\nkind: Pod")
        assert result.returncode == 0


class TestClusterInterfaceReal:
    def test_run_echo(self):
        ci = ClusterInterface(oc_path="/bin/echo", doit=True)
        result = ci.run("hello", filter_output=False)
        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_run_check_raises(self):
        ci = ClusterInterface(oc_path="/bin/false", doit=True)
        try:
            ci.run(check=True)
            assert False, "Should have raised"
        except ClusterError:
            pass

    def test_run_fatal_raises(self):
        ci = ClusterInterface(oc_path="/bin/false", doit=True)
        try:
            ci.run_fatal()
            assert False, "Should have raised"
        except ClusterError as exc:
            assert exc.kubefail is True

    def test_run_fatal_no_kubefail(self):
        ci = ClusterInterface(oc_path="/bin/false", doit=True)
        try:
            ci.run_fatal(kubefail=False)
            assert False, "Should have raised"
        except ClusterError as exc:
            assert exc.kubefail is False

    def test_output_filtering(self):
        ci = ClusterInterface(oc_path="/bin/echo", doit=True)
        result = ci.run("created", filter_output=True)
        assert "created" not in result.stdout or result.stdout.strip() == ""

    def test_watch_context_manager(self):
        ci = ClusterInterface(oc_path="/bin/echo", doit=True)
        with ci.watch("line1\nline2") as lines:
            collected = list(lines)
        assert len(collected) >= 1
