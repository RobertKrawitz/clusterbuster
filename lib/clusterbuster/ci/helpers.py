# Copyright 2026 Robert Krawitz/Red Hat
# Licensed under the Apache License, Version 2.0

from __future__ import annotations

import ast
import os
import operator
import subprocess


def compute_timeout(timeout: int, job_timeout: int) -> int:
    """Parity with ``compute_timeout`` in run-perf-ci-suite."""
    if timeout <= 0:
        timeout = job_timeout
    if timeout < 0:
        timeout = -timeout
    return timeout


def _eval_arith_node(node: ast.AST) -> float:
    """Evaluate a restricted arithmetic AST (no calls, names, or attributes)."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError("invalid constant in expression")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _eval_arith_node(node.operand)
        return +v if isinstance(node.op, ast.UAdd) else -v
    if isinstance(node, ast.BinOp):
        left = _eval_arith_node(node.left)
        right = _eval_arith_node(node.right)
        op = node.op
        if isinstance(op, ast.Add):
            return left + right
        if isinstance(op, ast.Sub):
            return left - right
        if isinstance(op, ast.Mult):
            return left * right
        if isinstance(op, ast.Div):
            return left / right
        if isinstance(op, ast.FloorDiv):
            return operator.floordiv(left, right)
        if isinstance(op, ast.Mod):
            return left % right
    raise ValueError("unsupported arithmetic construct")


def computeit(expr: str) -> int:
    """Integer result of a simple arithmetic expression (bash ``bc`` subset).

    Uses a restricted AST (no ``eval`` of arbitrary code): only numeric constants
    and ``+ - * / // %`` on subexpressions.
    """
    tree = ast.parse(expr.strip(), mode="eval")
    if not isinstance(tree, ast.Expression):
        raise ValueError("expected a single expression")
    return int(_eval_arith_node(tree.body))


def get_node_memory_bytes(node: str, oc: str | None = None) -> int:
    """Allocatable memory on a node, in bytes (``kubectl``/``oc``)."""
    from clusterbuster.ci.compat.sizes import parse_size

    cmd = oc or os.environ.get("OC") or os.environ.get("KUBECTL") or "oc"
    proc = subprocess.run(
        [cmd, "get", "node", node, "-ojsonpath={.status.allocatable.memory}"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or "oc get node failed")
    return int(parse_size(proc.stdout.strip()))


def roundup_fio(num: int, base: int = 1048576) -> int:
    answer = ((num + (base - 1)) // base) * base
    return max(answer, base)


def roundup_interval(base: int, interval: int) -> int:
    """``roundup`` from files.ci."""
    return ((base + interval - 1) // interval) * interval
