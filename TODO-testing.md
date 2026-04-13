# TODO: Testing changes needed when bash is deprecated

When `clusterbuster.sh` is deprecated or removed, the following
changes will be needed in the test infrastructure.

## 1. Rewrite `tests/generate_golden.py`

The current generator runs `bash clusterbuster.sh -n` for each
workload × deployment-type combination.  It must be rewritten to
call the Python `run_from_argv()` dry-run instead.

Key details:
- Use `clusterbuster.driver.run_from_argv()` with `-n` flag
- Capture stdout via `contextlib.redirect_stdout`
- Catch `SystemExit` (not just `Exception`) — some workloads
  like `byo` raise `SystemExit` when required args are missing
- Reset workload singleton state between iterations to prevent
  cross-workload leaks (same pattern as the `_reset_workloads`
  fixture in `test_golden_parity.py`)
- Import and use `_extract_object_info` from `test_golden_parity`
  to ensure the golden files and test comparison use the same
  extraction logic

## 2. Regenerate all golden files

After rewriting the generator, run it to regenerate all 56 golden
files (`tests/golden/*.json`).  The files will change because they
will now reflect the Python output (including intentional
differences like PSA labels, workdir volumes, etc.) rather than the
bash output.

All existing tests will continue to pass against the new baselines
since the test comparisons use the same extraction function.

## 3. Update documentation

Add a Testing section to `docs/clusterbuster.md` covering:
- How to run the test suite (`python -m pytest tests/ --ignore=tests/test_e2e.py`)
- The six test layers (unit, integration, golden-file regression,
  workload-options, orchestrator, e2e)
- Golden file workflow: when to regenerate, how to regenerate
  (`python tests/generate_golden.py`), review diffs, commit
  alongside code changes
- Update "Create A Workload" instructions to include the
  golden-file regeneration step

## 4. Optionally reframe test docstrings

The module docstring in `test_golden_parity.py` currently says
"compare clusterbuster (Python) against clusterbuster.sh (bash)
output."  Once bash is gone, reframe as "manifest regression tests"
that compare dry-run output against committed baselines.

## 5. Consider deprecating `tests/deep_compare.py`

This one-time analysis tool also calls `clusterbuster.sh`.  Once
bash is removed it will no longer work.  It can be kept as
historical reference or removed.
