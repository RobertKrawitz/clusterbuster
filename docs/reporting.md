# Clusterbuster reporting layout

> Reporting documentation: code written by Cursor (Auto).

This document describes how report generation, loading, and analysis are organized under `lib/clusterbuster/reporting/`, and how to extend the command-line interfaces for `clusterbuster-report` and `analyze-clusterbuster-report`.

## Entry points

| Script | Role |
|--------|------|
| `clusterbuster-report` | Thin wrapper; calls `ClusterBusterReporter.print_report()`. |
| `analyze-clusterbuster-report` | Thin wrapper; calls `ClusterBusterAnalysis.run_analysis()`. |

**Inputs:** Each command takes one or more paths to report JSON files, report directories, or CI result bundles. Reading those artifacts does not use cluster credentials.

You may keep unpacked CI bundles (e.g. `cb-ci-*` directories) or `cb-ci-*.tar` archives under the repo root for validation; they are **gitignored** and are not part of the project tree.

Parsing lives in the Python classes:

- `ClusterBusterReporter.augment_parser()` / `parse_args()` / `print_report()`
- `ClusterBusterAnalysis.augment_parser()` / `parse_args()` / `run_analysis()`

## Directory layout

### `reporter/` — human-readable reports from JSON

- **`ClusterBusterReporter.py`** — orchestrates formats, metrics, and global CLI options; discovers workload reporters.
- **`<workload>_reporter.py`** — one module per workload class (e.g. `fio_reporter.py`). Each defines a class named like the file stem (`fio_reporter`) extending `ClusterBusterReporter`.

**Workload CLI extension:** implement a static method on that class:

```python
@staticmethod
def __augment_parser_workload(parser):
    group = parser.add_argument_group('My workload options')
    group.add_argument('--my-flag', ...)
```

See `memory_reporter.py` for a full example. `ClusterBusterReporter.augment_parser()` imports every `*_reporter.py` and invokes the name-mangled hook `_{workload}_reporter__augment_parser_workload(parser)` on the matching class.

**Memory workload timeline (`--timeline-column`):** Only the memory reporter builds an optional per-node timeline table (verbose reports, `--timeline-file`, and sometimes the summary “Timeline” field). The table columns are defined in `memory_reporter.COLUMNS` (headers and printf-style `format` / `precise_format`). You can override that schema from the CLI without editing Python by passing **repeatable** `--timeline-column COLUMN:SPEC` arguments to `clusterbuster-report` when processing a memory workload report. Each `SPEC` is semicolon-separated `key=value` pairs for that column’s metadata; see the `memory_reporter` class docstring and `clusterbuster-report --help` for the full grammar. **Shell note:** to drop a column, the value looks like `-jobs:`; pass it as `--timeline-column=-jobs:` (equals form) or `'--timeline-column' '-jobs:'` so the leading `-` is not parsed as a separate flag.

**Examples (memory workload):**

```bash
RUNDIR=cb-ci-2026_03_26T20_24_14+0000/memory-runc-0000-R120-16P-2pr-268435456B-srandom
clusterbuster-report -o summary --timeline-file ./memory-timeline.tsv --timeline-format tsv "$RUNDIR"

clusterbuster-report -o summary --timeline-file ./memory-timeline.tsv --timeline-format tsv \
  --timeline-column 'jobs:header=Active jobs' "$RUNDIR"

clusterbuster-report -o summary --timeline-file ./memory-timeline.tsv --timeline-format tsv \
  --timeline-column=-container_CPU: "$RUNDIR"

clusterbuster-report -o summary --timeline-file ./memory-timeline.tsv --timeline-format tsv \
  --timeline-column 'jobs:header=Jobs' \
  --timeline-column 'time:precise_format=%.6f' \
  --timeline-column=-container_CPU: "$RUNDIR"

clusterbuster-report -o verbose --timeline-column 'jobs:header=Jobs' "$RUNDIR"
```

### `loader/` — merge multiple runs for analysis

- **`ClusterBusterLoader` loads** report directories (including CI result bundles) and delegates per workload to **`<workload>_loader.py`** classes that extend `ClusterBusterLoadOneReportBase`.

Loaders merge job metadata into a single structure consumed by analysis. The analyze CLI passes options such as `--allow-mismatch` directly into the loader instead of re-parsing `argv` fragments.

### `analysis/` — structured analysis by report type

- **`ClusterBusterAnalysis.py`** — orchestrates analysis formats (`ci`, `spreadsheet`, `summary`, `raw`), dispatches to per-workload analyzers, and optional post-processing.

Under each report type directory (e.g. `analysis/ci/`):

| File pattern | Purpose |
|--------------|---------|
| `<workload>_analysis.py` | Class `<workload>_analysis` extending `ClusterBusterAnalyzeOneBase` (or a generic base such as `CIAnalysis`). Implements `Analyze()`. |
| `analyze_postprocess.py` | Optional class `AnalyzePostprocess` extending `ClusterBusterPostprocessBase`. Implements `Postprocess()` to adjust the merged report. |

**Workload CLI extension (analyze):** same pattern as reporters, on the analyzer class:

```python
@staticmethod
def __augment_parser_workload(parser):
    parser.add_argument_group('My workload analysis options')
```

`ClusterBusterAnalysis.augment_parser()` walks each existing `analysis/<format>/` directory and calls each workload hook.

**Report-type CLI extension:** on `AnalyzePostprocess` in `analyze_postprocess.py`:

```python
@staticmethod
def __augment_parser_report_type(parser):
    parser.add_argument_group('CI analysis options')
```

Hooks are optional; empty implementations may use `pass` until new flags are needed.

## Typical flows

1. **Report:** JSON (or directory) → `clusterbuster-report` → `ClusterBusterReporter` → workload `*_reporter` → text/JSON/Python output.
2. **Analyze:** One or more run directories or CI bundles → `analyze-clusterbuster-report` → `ClusterBusterLoader` → merged dict → `ClusterBusterAnalysis` → per-workload `*_analysis` → optional `AnalyzePostprocess` → JSON or text.

## See also

- `clusterbuster.md` in this directory for broader Clusterbuster usage.
