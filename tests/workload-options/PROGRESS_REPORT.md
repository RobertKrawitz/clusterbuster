# Workload options suite — progress report

## Interface

- Prefer **command-line options** (`./run-workload-option-tests --help`); options are flags only (no `CB_TEST_*` environment fallbacks).

## Dry run

- **Command**: `./tests/workload-options/run-workload-option-tests --mode dry` (dry is the default).

## Live run on `scale`

- **Command**: `ssh scale 'cd rlk/clusterbuster && git pull && ./tests/workload-options/run-workload-option-tests --mode live'`
- **Latest successful full run** (representative): `run_20260402_172914/` under `tests/workload-options/reports/` on the remote checkout — **26 PASS**, **0 FAIL**, **2 SKIP** (expected dry-only rows when `--mode live`).
- Default live **`--timeout`** is **2400** seconds (`--global-timeout`; override or `0` to omit).

## Sysbench case

- P0 row exercises **`memory`** workload with a short `--sysbench-time` (avoids long-running CPU workloads on loaded clusters).

## Flakiness

- Occasional cluster-side errors (e.g. sync pod `137`, `AlreadyExists`) can fail a case; a **re-run** of the full suite often clears them.
