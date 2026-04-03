# Workload options test suite

This directory exercises **workload-specific option bundles** by invoking `clusterbuster` with representative flags derived from [`lib/workloads/*.workload`](../../lib/workloads/).

**Dependencies:** Python 3 with **PyYAML** (e.g. `python3-pyyaml`) to load [`cases.yaml`](cases.yaml).

## Quick start (dry run)

From the repository root (requires `kubectl` or `oc` in `PATH` for `clusterbuster` startup checks):

```bash
./tests/workload-options/run-workload-option-tests.sh --mode dry
```

Equivalent: `./tests/workload-options/run-workload-option-tests.sh` (dry is the default).

Reports are written under `tests/workload-options/reports/run_YYYYMMDD_HHMMSS/`:

- `results.json` — **default**; full run metadata and a `results` array; each row has `clusterbuster_args` as a JSON **array** of argv strings (use `--results-format tsv` or `both` for TSV only / both)
- `results.tsv` — optional (`--results-format tsv` or `both`); tab-separated rows with **`clusterbuster_args` last** (space-joined tokens for display)
- `SUMMARY.md` — counts and failure details
- `<id>.log` (stdout and stderr) — captured per case
- **Live only:** `artifacts/<id>/` — clusterbuster `--artifactdir` output (pod logs under `Logs/`, describes, report JSON, etc.) when `--artifacts` is enabled (default for live)

## Pod vs VM targets

Use **`--deployment-targets`** to control whether each case runs as **pods** (default), **VMs** (`--deployment_type=vm`), or **both** (`all` or `pod,vm`). When more than one target is selected, case ids are suffixed with `--pod` / `--vm` in `results.tsv`. Per-case overrides are supported in YAML via `deployment_targets:` on a case or under `defaults:`.

## Stress / repeat (live)

Run the full suite up to *N* times and stop on the first failure (or after *N* consecutive passes):

```bash
./tests/workload-options/run-live-repeat.sh 10
```

Each iteration uses a fresh `--report-dir` (`run_YYYYMMDD_HHMMSS_iterK`). On failure, inspect `reports/run_*/.../artifacts/<case-id>/Logs/` and the per-case `<id>.log` in that report directory.

## Live runs (real cluster)

Use `--mode live` so the script does **not** pass `-n`. You need a working kubeconfig (`KUBECONFIG` or default) and credentials for your cluster.

**Optional remote execution** (same pattern as other repo scripts):

```bash
ssh user@host 'cd /path/to/clusterbuster && git pull && ./tests/workload-options/run-workload-option-tests.sh --mode live'
```

## Command-line options

Run `./tests/workload-options/run-workload-option-tests.sh --help` for the full list.

| Option | Meaning |
|--------|---------|
| `-m`, `--mode` | `dry` (default) or `live` |
| `--cb` | Path to `clusterbuster` (default: `./clusterbuster` from repo root via script location) |
| `--cases-file` | Path to [`cases.yaml`](cases.yaml) |
| `--report-dir` | Output directory (default: timestamped dir under `reports/`) |
| `--deployment-targets` | `pod`, `vm`, `pod,vm`, or `all` (default: `pod`) |
| `-p`, `--priority` | If set to `P0` or `P1`, only rows with that priority |
| `-w`, `--workload` | If set, only rows whose workload column matches |
| `--global-timeout` | Live: seconds for `--timeout` (default 2400); `0` omits `--timeout` |
| `--report-format` | Live: value for `--report` (default `raw`) |
| `--metrics` | Live: allow metrics (omit `--force-no-metrics`) |
| `--no-metrics` | Live: pass `--force-no-metrics` (default for live) |
| `--artifacts` | Live (default): pass `--artifactdir` per case and `--retrieve-successful-logs=1` |
| `--no-artifacts` | Live: skip `--artifactdir` |
| `--results-format` | `json` (default), `tsv`, or `both` — primary machine-readable output file(s) |

## Cases file

[`cases.yaml`](cases.yaml) groups cases under **`cases:`** by **priority** (`P0`, `P1`, …). Optional `defaults:` may set `deployment_targets:` — the deployment types **allowed** for each case (default in-repo: `[pod, vm]`). The runner’s **`--deployment-targets`** selects which of those to run (e.g. `pod` only, `vm` only, or `all` for both).

Each case entry includes:

- **`id`**, **`workload`**, **`run_mode`** (`dry`, `live`, or `both`), **`expect_fail`** (boolean), **`clusterbuster_args`** — a **YAML list of argv tokens** (strings) passed to `clusterbuster` after any `-n` / live options (for example `["-w", "sleep", "--workload-runtime=8"]`). Do not repeat **`priority`** on the case; it comes from the enclosing group key.

[`load_cases_yaml.py`](load_cases_yaml.py) expands the file to the tab-separated stream consumed by the bash driver (joining `clusterbuster_args` with spaces for the shell).

See [`TEST_PLAN.md`](TEST_PLAN.md) for coverage goals.
