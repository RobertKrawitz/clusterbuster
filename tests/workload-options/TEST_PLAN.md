# Test plan: workload options

## Goals

- Validate that **documented workload options** parse and participate in dry-run generation without fatal errors.
- Cover **all registered workloads** in [`lib/workloads/*.workload`](../../lib/workloads/) with at least one **P0**-oriented bundle per workload.
- **P1** rows add secondary bundles (alternate patterns, extra flags).
- **Live** rows (`run_mode=live`) are for engineers with cluster access: optional `--timeout` for non-terminating workloads, `expect_fail` where the workload is designed to fail.

## Non-goals

- This is not a performance or correctness benchmark of the workloads themselves.
- No cluster-specific documentation (login flows vary by environment).

## Row design

- **BYO** (`byo`): command after `--`; use an absolute path such as `/usr/bin/sleep`.
- **Failure** (`failure`): dry run should succeed; live may exit non-zero after runtime — use `expect_fail=1` for live-only rows.
- **Waitforever** (`waitforever`): live rows should include a global **`--timeout`** (and cleanup/exit flags as appropriate) so the run does not hang indefinitely.

## Maintenance

When adding or renaming options in a `.workload` file, update [`cases.yaml`](cases.yaml) and re-run `./tests/workload-options/run-workload-option-tests.sh --mode dry`.
