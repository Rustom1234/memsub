# The Lying Game (archived research experiment)

An AI-safety deception experiment (DeChant 2025 design, arxiv 2501.11739): an
agent reports its work to an LLM overseer to earn compute credits; the memory
condition injects prior-session summaries, the control does not. The question
was whether episodic memory helps an agent sustain deception under probing.

This directory holds the complete harness, configs, results, analysis
scripts, and paper draft. The repo pivoted to the memory PRODUCT (the top
level of this repository); this experiment is preserved intact here and, in
its original top-level layout, on the git branch `lying-game-experiment`.

Status when archived: all seven cells (A-G) complete at their pre-registered n
(61 runs, 244 sessions). Full-matrix null on objective deception (0 of 61
runs, 0 of 244 sessions overstated claimed completion), scoped statistically
in docs/findings.md. The one graded, non-null effect measured is write-transform
quality on judge-rated cross-session consistency (cell C: accurate 88.3,
summarized 59.7, distorted 46.0). The runner resumes cleanly with
`--skip-existing`.

Run from the repo root:

```bash
python experiments/lying-game/scripts/run_experiment.py --cell A --backend mock --n-runs 3
python -m pytest experiments/lying-game/tests
```

The harness depends on the top-level `memsub` library as it existed at the
pivot; `conftest.py` and the scripts add the repo root to sys.path. If future
memsub changes break the harness, use branch `lying-game-experiment`.
