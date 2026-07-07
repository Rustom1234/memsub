#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
PY=.venv/bin/python
echo "=== CELL A (memory vs control), n=5, haiku ==="
$PY scripts/run_experiment.py --cell A --backend cli --model haiku --n-runs 5 --judge --judge-model haiku
echo "=== CELL C (memory-type ablation), n=3, haiku ==="
$PY scripts/run_experiment.py --cell C --backend cli --model haiku --n-runs 3 --judge --judge-model haiku
echo "=== CELL B (memory-length ablation), n=2, haiku ==="
$PY scripts/run_experiment.py --cell B --backend cli --model haiku --n-runs 2 --judge --judge-model haiku
echo "=== CELL D (capability), n=2, haiku+sonnet ==="
$PY scripts/run_experiment.py --cell D --backend cli --models haiku sonnet --n-runs 2 --judge --judge-model haiku
echo "=== PILOT COMPLETE ==="
