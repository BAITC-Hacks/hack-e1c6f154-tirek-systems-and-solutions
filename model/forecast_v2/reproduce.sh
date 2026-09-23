#!/usr/bin/env bash
set -euo pipefail
# Run from the repository root. All private data and row-level predictions stay outside Git.
# Example: PYTHON=/path/to/venv/bin/python bash model/forecast_v2/reproduce.sh /path/to/inputs /tmp/forecast-v2-private /tmp/forecast-v2-reproduced
: "${PYTHON:=python3}"
INPUT_DIR="${1:?Pass the partner input directory}"
PRIVATE_DIR="${2:?Pass a private output directory outside this repository}"
OUTPUT_DIR="${3:?Pass a fresh output directory for the reproduced run}"
"$PYTHON" -m unittest model.forecast_v2.test_forecast_v2 -v
"$PYTHON" -m model.forecast_v2.run select --inputs "$INPUT_DIR" --private "$PRIVATE_DIR" --cache "$PRIVATE_DIR/cache.pkl" --output "$OUTPUT_DIR" --iterations 500 --threads 4
"$PYTHON" -m model.forecast_v2.refine --private "$PRIVATE_DIR" --output "$OUTPUT_DIR"
"$PYTHON" -m model.forecast_v2.ablation --cache "$PRIVATE_DIR/cache.pkl" --output "$OUTPUT_DIR"
"$PYTHON" -m model.forecast_v2.run evaluate --inputs "$INPUT_DIR" --private "$PRIVATE_DIR" --cache "$PRIVATE_DIR/cache.pkl" --output "$OUTPUT_DIR"
"$PYTHON" -m model.forecast_v2.run fit --inputs "$INPUT_DIR" --private "$PRIVATE_DIR" --cache "$PRIVATE_DIR/cache.pkl" --output "$OUTPUT_DIR"
"$PYTHON" -m model.forecast_v2.run infer --inputs "$INPUT_DIR" --private "$PRIVATE_DIR" --cache "$PRIVATE_DIR/cache.pkl" --output "$OUTPUT_DIR"
"$PYTHON" -m model.forecast_v2.verify --inputs "$INPUT_DIR" --private "$PRIVATE_DIR" --artifacts "$OUTPUT_DIR"
"$PYTHON" -m model.forecast_v2.report --artifacts "$OUTPUT_DIR" --output "$OUTPUT_DIR/REPORT.md"
