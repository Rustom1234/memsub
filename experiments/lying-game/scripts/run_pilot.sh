#!/usr/bin/env bash
# Full pilot batch: all experiment cells, parallel, resumable (skip-existing).
set -e
cd "$(dirname "$0")/.."
PY=.venv/bin/python
P=${PILOT_PARALLEL:-6}

echo "=== CELL A (memory vs control), n=5, haiku, judge x2 ==="
$PY scripts/run_experiment.py --cell A --backend cli --model haiku --n-runs 5 \
    --judge --judge-model haiku --judge-replicas 2 --parallel "$P" --skip-existing

echo "=== CELL E (truthful baseline), n=3, haiku, judge x2 ==="
$PY scripts/run_experiment.py --cell E --backend cli --model haiku --n-runs 3 \
    --judge --judge-model haiku --judge-replicas 2 --parallel "$P" --skip-existing

echo "=== CELL C (memory-type ablation), n=3, haiku ==="
$PY scripts/run_experiment.py --cell C --backend cli --model haiku --n-runs 3 \
    --judge --judge-model haiku --parallel "$P" --skip-existing

echo "=== CELL B (memory-length ablation), n=2, haiku ==="
$PY scripts/run_experiment.py --cell B --backend cli --model haiku --n-runs 2 \
    --judge --judge-model haiku --parallel "$P" --skip-existing

echo "=== CELL D (capability), n=2, haiku+sonnet ==="
$PY scripts/run_experiment.py --cell D --backend cli --models haiku sonnet --n-runs 2 \
    --judge --judge-model haiku --parallel "$P" --skip-existing

echo "=== CELL F (probing depth 0/2/5 x memory), n=2, haiku ==="
$PY scripts/run_experiment.py --cell F --backend cli --model haiku --n-runs 2 \
    --judge --judge-model haiku --parallel "$P" --skip-existing

echo "=== PILOT COMPLETE ==="
