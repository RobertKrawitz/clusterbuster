# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0
# Written by Anthropic Claude
"""Label-based cleanup and host cache dropping."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cluster import ClusterInterface
    from .config import ClusterbusterConfig

_LOG = logging.getLogger(__name__)

_MAX_QUERY_FAILURES = 5


def _has_labeled_namespaces(
    cluster: ClusterInterface,
    config: ClusterbusterConfig,
) -> bool:
    """Check whether any clusterbuster-labeled namespaces exist."""
    for label in (f"{config.basename}-sync", config.basename):
        result = cluster.run(
            "get", "namespace", f"-l{label}",
            "-oname", "--no-headers",
            dry_run_skip=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return True
    return False


def do_cleanup(
    cluster: ClusterInterface,
    config: ClusterbusterConfig,
    *,
    pre: bool = False,
    force: bool = False,
    override_remove_namespaces: int | None = None,
) -> bool:
    """Delete clusterbuster-labeled objects.

    Returns True on success, False on failure.

    When *pre* is True (precleanup) and ``remove_namespaces == -1``
    (auto-detect), bash checks whether labeled namespaces already exist
    on the cluster and sets ``remove_namespaces = 0`` so that
    precleanup uses ``oc delete all`` instead of ``oc delete namespace``
    — preserving namespaces (and their PVCs) for reuse.
    """
    if not config.doit:
        return True

    rm_ns = override_remove_namespaces if override_remove_namespaces is not None else config.remove_namespaces

    if pre and config.use_namespaces and rm_ns == -1:
        if _has_labeled_namespaces(cluster, config):
            rm_ns = 0

    ok = _do_cleanup(cluster, config, label=f"{config.basename}-sync", remove_namespaces=rm_ns)
    ok = _do_cleanup(cluster, config, label=config.basename, remove_namespaces=rm_ns) and ok

    if not ok and (force or config.force_cleanup_timeout):
        timeout = config.force_cleanup_timeout or "60s"
        _LOG.warning("First cleanup pass failed; retrying with --force (timeout=%s)", timeout)
        _force_cleanup(cluster, config, timeout=timeout, remove_namespaces=rm_ns)
        ok = True

    if config.use_namespaces and rm_ns != 0:
        _wait_for_namespace_deletion(cluster, config)

    return ok


def _do_cleanup(
    cluster: ClusterInterface,
    config: ClusterbusterConfig,
    *,
    label: str,
    force: bool = False,
    timeout: str = "",
    remove_namespaces: int | None = None,
) -> bool:
    """Inner cleanup: delete objects matching a label selector."""
    selector = f"-l{label}"
    ok = True
    rm_ns = remove_namespaces if remove_namespaces is not None else config.remove_namespaces

    if config.use_namespaces and rm_ns != 0:
        result = cluster.delete(
            "namespace", selector, "--ignore-not-found=true",
            timeout=timeout or None,
            force=force,
        )
        if result.returncode != 0:
            ok = False
    else:
        result = cluster.delete(
            "all", selector, "-A", "--ignore-not-found=true",
            timeout=timeout or None,
            force=force,
        )
        if result.returncode != 0:
            ok = False

    return ok


def _force_cleanup(
    cluster: ClusterInterface,
    config: ClusterbusterConfig,
    *,
    timeout: str,
    remove_namespaces: int,
) -> None:
    """Multi-step force cleanup matching bash's escalation sequence.

    Bash first force-deletes all labeled *objects* (clearing finalizers),
    and only then force-deletes *namespaces*.  Deleting namespaces
    directly can hang when resources inside them have stuck finalizers.
    """
    if config.deployment_type == "vm":
        cluster.delete(
            "all", f"-l{config.basename}-sync", "-A", "--ignore-not-found=true",
            force=True, timeout=timeout,
        )

    cluster.delete(
        "all", f"-l{config.basename}", "-A", "--ignore-not-found=true",
        force=True, timeout=timeout,
    )

    if config.use_namespaces and remove_namespaces != 0:
        cluster.delete(
            "namespace", f"-l{config.basename}", "--ignore-not-found=true",
            force=True, timeout=timeout,
        )


def _wait_for_namespace_deletion(
    cluster: ClusterInterface,
    config: ClusterbusterConfig,
    *,
    poll_interval: int = 5,
) -> None:
    """Wait for labeled namespaces to finish terminating.

    Waits indefinitely as long as namespaces are still visible.
    If the cluster cannot be queried at all (repeated API errors),
    gives up after ``_MAX_QUERY_FAILURES`` consecutive failures.
    """
    waited = 0
    consecutive_failures = 0
    while True:
        result = cluster.run(
            "get", "namespace",
            f"-l{config.basename}",
            "-oname", "--no-headers",
            dry_run_skip=False,
        )
        if result.returncode != 0:
            consecutive_failures += 1
            if consecutive_failures >= _MAX_QUERY_FAILURES:
                _LOG.warning(
                    "Cannot query namespaces (%d consecutive failures); "
                    "proceeding without confirmation",
                    consecutive_failures,
                )
                return
        elif not result.stdout.strip():
            result2 = cluster.run(
                "get", "namespace",
                f"-l{config.basename}-sync",
                "-oname", "--no-headers",
                dry_run_skip=False,
            )
            if result2.returncode == 0 and not result2.stdout.strip():
                return
            consecutive_failures = 0
        else:
            consecutive_failures = 0
        time.sleep(poll_interval)
        waited += poll_interval
        if waited % 30 == 0:
            _LOG.info("Waiting for namespace deletion (%ds so far)", waited)


def drop_host_caches(
    cluster: ClusterInterface,
    config: ClusterbusterConfig,
) -> None:
    """Drop filesystem caches on worker nodes via oc debug.

    Blocking call with internal parallelism.
    """
    if not config.doit:
        return
    if not config.drop_node_cache and not config.drop_all_node_cache:
        return

    nodes = _get_cache_drop_nodes(cluster, config)
    if not nodes:
        _LOG.warning("No nodes found for cache drop")
        return

    _LOG.info("Dropping host caches on %d node(s)", len(nodes))

    def _drop_one(node: str) -> None:
        cluster.debug_node(
            node, "--", "chroot", "/host", "sh", "-c",
            "sync; echo 3 > /proc/sys/vm/drop_caches",
        )

    with ThreadPoolExecutor(max_workers=len(nodes)) as pool:
        futures = {pool.submit(_drop_one, n): n for n in nodes}
        for f in as_completed(futures):
            exc = f.exception()
            if exc is not None:
                _LOG.warning("Cache drop failed on %s: %s", futures[f], exc)


def _get_cache_drop_nodes(
    cluster: ClusterInterface,
    config: ClusterbusterConfig,
) -> list[str]:
    """Determine which nodes to drop caches on."""
    if config.pin_nodes and not config.drop_all_node_cache:
        return list(set(config.pin_nodes.values()))

    result = cluster.run(
        "get", "node",
        f"-l{config.node_selector}",
        "-oname", "--no-headers",
        dry_run_skip=False,
    )
    if result.returncode != 0:
        return []
    nodes = [
        line.removeprefix("node/")
        for line in result.stdout.strip().splitlines()
        if line.strip()
    ]
    return nodes
