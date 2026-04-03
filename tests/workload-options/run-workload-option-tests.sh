#!/usr/bin/env bash

# Copyright 2026 Robert Krawitz/Red Hat
# AI-assisted tooling (Cursor Agent); see repository CONTRIBUTING if present.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
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
# Run workload option bundles from cases.yaml; write results.json (default) and/or results.tsv and SUMMARY.md.
#
# Usage: ./tests/workload-options/run-workload-option-tests.sh [options]
#
# Requires: Python 3 with PyYAML (cases.yaml is expanded via load_cases_yaml.py).
#
#   -m, --mode dry|live     Test mode: dry adds -n to clusterbuster (default: dry)
#       --cb PATH           Path to clusterbuster script (default: repo ./clusterbuster)
#       --cases-file PATH   Path to cases.yaml
#       --report-dir PATH   Output directory (default: reports/run_YYYYMMDD_HHMMSS)
#       --deployment-targets pod|vm|pod,vm|all
#                           Which deployment types to run per case (default: pod).
#                           "all" is the same as pod,vm. VM runs pass --deployment_type=vm.
#   -p, --priority P0|P1   Only run rows with this priority
#   -w, --workload NAME     Only run rows whose workload column matches
#       --metrics           Live: do not pass --force-no-metrics (use cluster metrics)
#       --no-metrics        Live: pass --force-no-metrics (default for live)
#       --report-format F   Live: --report=F for clusterbuster (default: raw)
#       --global-timeout N  Live: --timeout=N seconds; 0 omits --timeout (default: 2400)
#       --results-format json|tsv|both
#                           Primary results file: json (default), tsv, or both
#   -h, --help              Show this help
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
LOADER_PY="$SCRIPT_DIR/load_cases_yaml.py"

usage() {
	cat <<'USAGE'
Run workload option bundles from cases.yaml; write results.json (default), optional results.tsv, and SUMMARY.md.

Usage: run-workload-option-tests.sh [options]

  -m, --mode dry|live     Test mode: dry adds -n to clusterbuster (default: dry)
      --cb PATH           Path to clusterbuster script (default: repo ./clusterbuster)
      --cases-file PATH   Path to cases.yaml
      --report-dir PATH   Output directory (default: reports/run_YYYYMMDD_HHMMSS)
      --deployment-targets pod|vm|pod,vm|all
                          Deployment types to exercise (default: pod). "all" => pod and vm.
  -p, --priority P0|P1   Only run rows with this priority
  -w, --workload NAME     Only run rows whose workload column matches
      --metrics           Live: do not pass --force-no-metrics (use cluster metrics)
      --no-metrics        Live: pass --force-no-metrics (default for live)
      --report-format F   Live: --report=F for clusterbuster (default: raw)
      --global-timeout N  Live: --timeout=N seconds; 0 omits --timeout (default: 2400)
  --artifacts             Live: save per-case clusterbuster artifacts under REPORT_DIR/artifacts/<id>/ (default: on)
  --no-artifacts          Live: do not pass --artifactdir
  --results-format json|tsv|both
                          Write results.json (array argv per case), results.tsv, or both (default: json)
  -h, --help              Show this help
USAGE
}

MODE=dry
CB="$REPO_ROOT/clusterbuster"
CASES_FILE="$SCRIPT_DIR/cases.yaml"
FILTER_PRIORITY=
FILTER_WORKLOAD=
METRICS_CHOICE=
REPORT_FORMAT=raw
GLOBAL_TIMEOUT=2400
REPORT_DIR_CLI=
SAVE_ARTIFACTS=1
DEPLOYMENT_TARGETS=pod
RESULTS_FORMAT=json
TS=$(date +%Y%m%d_%H%M%S)

while [[ $# -gt 0 ]]; do
	case "$1" in
		-m|--mode) MODE=$2; shift 2 ;;
		--mode=*) MODE=${1#*=}; shift ;;
		--cb) CB=$2; shift 2 ;;
		--cb=*) CB=${1#*=}; shift ;;
		--cases-file|--cases) CASES_FILE=$2; shift 2 ;;
		--cases-file=*) CASES_FILE=${1#*=}; shift ;;
		--report-dir) REPORT_DIR_CLI=$2; shift 2 ;;
		--report-dir=*) REPORT_DIR_CLI=${1#*=}; shift ;;
		--deployment-targets) DEPLOYMENT_TARGETS=$2; shift 2 ;;
		--deployment-targets=*) DEPLOYMENT_TARGETS=${1#*=}; shift ;;
		-p|--priority) FILTER_PRIORITY=$2; shift 2 ;;
		--priority=*) FILTER_PRIORITY=${1#*=}; shift ;;
		-w|--workload) FILTER_WORKLOAD=$2; shift 2 ;;
		--workload=*) FILTER_WORKLOAD=${1#*=}; shift ;;
		--metrics) METRICS_CHOICE=1; shift ;;
		--no-metrics) METRICS_CHOICE=0; shift ;;
		--report-format) REPORT_FORMAT=$2; shift 2 ;;
		--report-format=*) REPORT_FORMAT=${1#*=}; shift ;;
		--global-timeout) GLOBAL_TIMEOUT=$2; shift 2 ;;
		--global-timeout=*) GLOBAL_TIMEOUT=${1#*=}; shift ;;
		--artifacts) SAVE_ARTIFACTS=1; shift ;;
		--no-artifacts) SAVE_ARTIFACTS=0; shift ;;
		--results-format) RESULTS_FORMAT=$2; shift 2 ;;
		--results-format=*) RESULTS_FORMAT=${1#*=}; shift ;;
		-h|--help) usage; exit 0 ;;
		*) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
	esac
done

if [[ -z "${METRICS_CHOICE:-}" ]]; then
	METRICS_CHOICE=0
fi

case "$RESULTS_FORMAT" in
	json|tsv|both) ;;
	*) echo "Invalid --results-format: $RESULTS_FORMAT (use json, tsv, or both)" >&2; exit 2 ;;
esac

REPORT_DIR=${REPORT_DIR_CLI:-"$SCRIPT_DIR/reports/run_${TS}"}
mkdir -p "$REPORT_DIR"

RESULTS_JSON="$REPORT_DIR/results.json"
RESULTS_TSV="$REPORT_DIR/results.tsv"
JSONL="$REPORT_DIR/.results.jsonl"
SUMMARY_MD="$REPORT_DIR/SUMMARY.md"

if [[ ! -e "$CB" ]]; then
	echo "clusterbuster not found at $CB" >&2
	exit 2
fi
if [[ ! -e "$LOADER_PY" ]]; then
	echo "missing $LOADER_PY" >&2
	exit 2
fi

case "$MODE" in
	dry|live) ;;
	*) echo "Invalid --mode: $MODE (use dry or live)" >&2; exit 2 ;;
esac

if [[ "$RESULTS_FORMAT" == tsv || "$RESULTS_FORMAT" == both ]]; then
	header='id	workload	priority	run_mode	expect_fail	exit_code	status	seconds	deployment_target	clusterbuster_args'
	echo "$header" >"$RESULTS_TSV"
fi
if [[ "$RESULTS_FORMAT" == json || "$RESULTS_FORMAT" == both ]]; then
	: >"$JSONL"
fi

pass=0
fail=0
skip=0
FAIL_LINES=()
SKIP_LINES=()

# Append one result object as JSON Lines (internal); merged into results.json at end.
jsonl_append() {
	# Args: path jsonl | id wl pr rm ef dep st ec sec skip_reason args_json
	python3 -c '
import json, sys
path = sys.argv[1]
args_json = sys.argv[2]
id_, wl, pr, rm, ef, dep, status_s = sys.argv[3:10]
ec, sec, skr = sys.argv[10:13]
args = json.loads(args_json)
obj = {
    "id": id_,
    "workload": wl,
    "priority": pr,
    "run_mode": rm,
    "expect_fail": int(ef),
    "deployment_target": dep,
    "status": status_s,
}
if ec:
    obj["exit_code"] = int(ec)
else:
    obj["exit_code"] = None
if sec:
    obj["seconds"] = int(sec)
else:
    obj["seconds"] = None
if skr:
    obj["skip_reason"] = skr
obj["clusterbuster_args"] = args
with open(path, "a", encoding="utf-8") as f:
    f.write(json.dumps(obj, ensure_ascii=False) + "\n")
' "$@"
}

tsv_args_cell() {
	python3 -c 'import json,sys; print(" ".join(json.loads(sys.argv[1])))' "$1"
}

run_one() {
	local id=$1 workload=$2 priority=$3 run_mode=$4 expect_fail=$5 dep_target=$6 args_json=$7
	local should_run=0
	case "$run_mode" in
		dry)  [[ "$MODE" == dry ]] && should_run=1 ;;
		live) [[ "$MODE" == live ]] && should_run=1 ;;
		both) should_run=1 ;;
		*)    echo "Invalid run_mode '$run_mode' in row $id" >&2; return 1 ;;
	esac

	if [[ -n "$FILTER_PRIORITY" && "$priority" != "$FILTER_PRIORITY" ]]; then
		if [[ "$RESULTS_FORMAT" == tsv || "$RESULTS_FORMAT" == both ]]; then
			printf '%s\t%s\t%s\t%s\t%s\t\tSKIP\t\t%s\t%s\n' "$id" "$workload" "$priority" "$run_mode" "$expect_fail" "$dep_target" "$(tsv_args_cell "$args_json")" >>"$RESULTS_TSV"
		fi
		if [[ "$RESULTS_FORMAT" == json || "$RESULTS_FORMAT" == both ]]; then
			jsonl_append "$JSONL" "$args_json" "$id" "$workload" "$priority" "$run_mode" "$expect_fail" "$dep_target" SKIP "" "" "priority filter ($priority != $FILTER_PRIORITY)"
		fi
		((skip++)) || true
		SKIP_LINES+=("$id: priority filter ($priority != $FILTER_PRIORITY)")
		return 0
	fi

	if [[ -n "$FILTER_WORKLOAD" && "$workload" != "$FILTER_WORKLOAD" ]]; then
		if [[ "$RESULTS_FORMAT" == tsv || "$RESULTS_FORMAT" == both ]]; then
			printf '%s\t%s\t%s\t%s\t%s\t\tSKIP\t\t%s\t%s\n' "$id" "$workload" "$priority" "$run_mode" "$expect_fail" "$dep_target" "$(tsv_args_cell "$args_json")" >>"$RESULTS_TSV"
		fi
		if [[ "$RESULTS_FORMAT" == json || "$RESULTS_FORMAT" == both ]]; then
			jsonl_append "$JSONL" "$args_json" "$id" "$workload" "$priority" "$run_mode" "$expect_fail" "$dep_target" SKIP "" "" "workload filter"
		fi
		((skip++)) || true
		SKIP_LINES+=("$id: workload filter")
		return 0
	fi

	if (( ! should_run )); then
		if [[ "$RESULTS_FORMAT" == tsv || "$RESULTS_FORMAT" == both ]]; then
			printf '%s\t%s\t%s\t%s\t%s\t\tSKIP\t\t%s\t%s\n' "$id" "$workload" "$priority" "$run_mode" "$expect_fail" "$dep_target" "$(tsv_args_cell "$args_json")" >>"$RESULTS_TSV"
		fi
		if [[ "$RESULTS_FORMAT" == json || "$RESULTS_FORMAT" == both ]]; then
			jsonl_append "$JSONL" "$args_json" "$id" "$workload" "$priority" "$run_mode" "$expect_fail" "$dep_target" SKIP "" "" "run_mode=$run_mode with --mode=$MODE"
		fi
		((skip++)) || true
		SKIP_LINES+=("$id: run_mode=$run_mode with --mode=$MODE")
		return 0
	fi

	local -a cmd=()
	cmd+=("$CB")
	if [[ "$MODE" == dry ]]; then
		cmd+=(-n)
	elif [[ "$MODE" == live ]]; then
		[[ "$METRICS_CHOICE" != 1 ]] && cmd+=(--force-no-metrics)
		cmd+=(--report="${REPORT_FORMAT:-raw}")
		if [[ "${GLOBAL_TIMEOUT:-2400}" != 0 ]]; then
			cmd+=(--timeout="${GLOBAL_TIMEOUT:-2400}")
		fi
	fi
	if [[ "$dep_target" == vm ]]; then
		cmd+=(--deployment_type=vm)
	fi
	if [[ "$MODE" == live && "$SAVE_ARTIFACTS" != 0 ]]; then
		mkdir -p "$REPORT_DIR/artifacts/$id"
		cmd+=(--artifactdir="$REPORT_DIR/artifacts/$id")
		cmd+=(--retrieve-successful-logs=1)
	fi
	local -a extra=()
	mapfile -t extra < <(python3 -c 'import json,sys; [print(x) for x in json.loads(sys.argv[1])]' "$args_json")
	cmd+=("${extra[@]}")

	local start=$SECONDS
	local ec=0
	set +e
	"${cmd[@]}" &>"$REPORT_DIR/${id}.log"
	ec=$?
	set -e
	local dur=$((SECONDS - start))

	local ok=0
	if [[ "$expect_fail" == 1 ]]; then
		(( ec != 0 )) && ok=1
	else
		(( ec == 0 )) && ok=1
	fi

	local status
	if (( ok )); then
		status=PASS
		((pass++)) || true
	else
		status=FAIL
		((fail++)) || true
		local art_hint=
		[[ "$SAVE_ARTIFACTS" != 0 ]] && art_hint=" artifacts: $REPORT_DIR/artifacts/$id/"
		local _argshow
		_argshow=$(tsv_args_cell "$args_json")
		FAIL_LINES+=("$id exit=$ec expect_fail=$expect_fail target=$dep_target args: ${_argshow} (see $REPORT_DIR/${id}.log$art_hint)")
	fi

	if [[ "$RESULTS_FORMAT" == tsv || "$RESULTS_FORMAT" == both ]]; then
		printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
			"$id" "$workload" "$priority" "$run_mode" "$expect_fail" "$ec" "$status" "$dur" "$dep_target" "$(tsv_args_cell "$args_json")" >>"$RESULTS_TSV"
	fi
	if [[ "$RESULTS_FORMAT" == json || "$RESULTS_FORMAT" == both ]]; then
		jsonl_append "$JSONL" "$args_json" "$id" "$workload" "$priority" "$run_mode" "$expect_fail" "$dep_target" "$status" "$ec" "$dur" ""
	fi
}

while IFS=$'\t' read -r id workload priority run_mode expect_fail dep_target args_json || [[ -n "${id:-}" ]]; do
	[[ -z "${id:-}" ]] && continue
	[[ "$id" =~ ^# ]] && continue
	run_one "$id" "$workload" "$priority" "$run_mode" "$expect_fail" "$dep_target" "$args_json"
done < <(python3 "$LOADER_PY" "$CASES_FILE" "$DEPLOYMENT_TARGETS")

{
	echo "# Workload option tests — $TS"
	echo
	echo "- **mode**: $MODE"
	echo "- **Cases file**: \`$CASES_FILE\`"
	echo "- **clusterbuster**: \`$CB\`"
	echo "- **deployment-targets**: $DEPLOYMENT_TARGETS"
	if [[ -n "$FILTER_PRIORITY" ]]; then echo "- **priority filter**: $FILTER_PRIORITY"; fi
	if [[ -n "$FILTER_WORKLOAD" ]]; then echo "- **workload filter**: $FILTER_WORKLOAD"; fi
	if [[ "$MODE" == live ]]; then
		echo "- **metrics**: $([[ "$METRICS_CHOICE" == 1 ]] && echo enabled || echo disabled)"
		echo "- **report-format**: ${REPORT_FORMAT:-raw}"
		echo "- **global-timeout**: ${GLOBAL_TIMEOUT:-2400}"
		echo "- **artifacts**: $([[ "$SAVE_ARTIFACTS" != 0 ]] && echo "yes (\`$REPORT_DIR/artifacts/\`)" || echo no)"
	fi
	echo "- **results-format**: $RESULTS_FORMAT"
	echo
	echo "## Counts"
	echo
	echo "| Result | Count |"
	echo "|--------|-------|"
	echo "| PASS | $pass |"
	echo "| FAIL | $fail |"
	echo "| SKIP | $skip |"
	echo
	if ((${#FAIL_LINES[@]})); then
		echo "## Failures"
		echo
		for line in "${FAIL_LINES[@]}"; do
			echo "- $line"
		done
		echo
	fi
	if ((${#SKIP_LINES[@]})); then
		echo "## Skips (first 20)"
		echo
		i=0
		for line in "${SKIP_LINES[@]}"; do
			echo "- $line"
			((i++)) || true
			((i >= 20)) && break
		done
		echo
	fi
	case "$RESULTS_FORMAT" in
		json) echo "Full results: \`results.json\` (clusterbuster_args as JSON arrays)" ;;
		tsv) echo "Full table: \`results.tsv\` (clusterbuster_args last column, space-joined)" ;;
		both) echo "Full results: \`results.json\` and \`results.tsv\`" ;;
	esac
} >"$SUMMARY_MD"

if [[ "$RESULTS_FORMAT" == json || "$RESULTS_FORMAT" == both ]]; then
	export CB_JSONL="$JSONL" CB_OUT="$RESULTS_JSON" CB_RUN_ID="$TS" CB_MODE="$MODE" CB_CASES_FILE="$CASES_FILE" \
		CB_CLUSTERBUSTER="$CB" CB_DEPLOYMENT_TARGETS="$DEPLOYMENT_TARGETS" CB_PASS="$pass" CB_FAIL="$fail" CB_SKIP="$skip" \
		CB_FILTER_PRIORITY="${FILTER_PRIORITY:-}" CB_FILTER_WORKLOAD="${FILTER_WORKLOAD:-}" \
		CB_METRICS_CHOICE="${METRICS_CHOICE:-}" CB_REPORT_FORMAT="${REPORT_FORMAT:-raw}" CB_GLOBAL_TIMEOUT="${GLOBAL_TIMEOUT:-2400}" \
		CB_SAVE_ARTIFACTS="${SAVE_ARTIFACTS:-1}"
	python3 - <<'PY'
import json
import os

with open(os.environ["CB_JSONL"], encoding="utf-8") as f:
    results = [json.loads(line) for line in f if line.strip()]

doc = {
    "schema_version": 1,
    "run_id": os.environ["CB_RUN_ID"],
    "mode": os.environ["CB_MODE"],
    "cases_file": os.environ["CB_CASES_FILE"],
    "clusterbuster": os.environ["CB_CLUSTERBUSTER"],
    "deployment_targets": os.environ["CB_DEPLOYMENT_TARGETS"],
    "counts": {
        "pass": int(os.environ["CB_PASS"]),
        "fail": int(os.environ["CB_FAIL"]),
        "skip": int(os.environ["CB_SKIP"]),
    },
    "results": results,
}
fp = os.environ.get("CB_FILTER_PRIORITY") or ""
fw = os.environ.get("CB_FILTER_WORKLOAD") or ""
if fp or fw:
    doc["filters"] = {}
    if fp:
        doc["filters"]["priority"] = fp
    if fw:
        doc["filters"]["workload"] = fw
if os.environ["CB_MODE"] == "live":
    doc["live_options"] = {
        "metrics": os.environ["CB_METRICS_CHOICE"] == "1",
        "report_format": os.environ["CB_REPORT_FORMAT"],
        "global_timeout": int(os.environ["CB_GLOBAL_TIMEOUT"]),
        "artifacts": os.environ["CB_SAVE_ARTIFACTS"] != "0",
    }

with open(os.environ["CB_OUT"], "w", encoding="utf-8") as out:
    json.dump(doc, out, indent=2, ensure_ascii=False)
    out.write("\n")
PY
	rm -f "$JSONL"
fi

echo "Wrote $SUMMARY_MD"
[[ "$RESULTS_FORMAT" == json || "$RESULTS_FORMAT" == both ]] && echo "Wrote $RESULTS_JSON"
[[ "$RESULTS_FORMAT" == tsv || "$RESULTS_FORMAT" == both ]] && echo "Wrote $RESULTS_TSV"
if (( fail > 0 )); then
	exit 1
fi
exit 0
