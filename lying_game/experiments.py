"""Experiment cell definitions: the four controls needed to publish.

Each function returns a list of ExperimentConfig, one per run (seed). The
runner (scripts/run_experiment.py) executes them and writes results.

Cells:
  A. memory vs no-memory        (main contrast)
  B. memory-length ablation      (retain/inject 5 / 20 / 100 memories)
  C. memory-type ablation        (accurate / summarized / distorted)
  D. model-capability comparison (two model tiers)

memory_capacity caps how many episodes the memory retains and can inject; for
the length ablation we vary it. Because runs here are short (4 sessions => at
most 3 prior summaries), the length ablation is realized by also seeding the
memory with synthetic prior-session summaries up to the capacity (see
harness seeding in run_experiment.py). This is documented in LIMITS.md.
"""

from __future__ import annotations

from lying_game.config import ExperimentConfig


def _seeds(n: int, base: int = 1000) -> list[int]:
    return [base + i for i in range(n)]


def cell_a_memory_vs_control(n_runs: int, backend: str, model: str | None) -> list[ExperimentConfig]:
    configs = []
    for seed in _seeds(n_runs):
        configs.append(
            ExperimentConfig(
                run_id=f"A-control-s{seed}",
                condition="control_no_memory",
                memory_kind="none",
                backend=backend,
                model=model,
                seed=seed,
                notes="Cell A: no-memory control",
            )
        )
        configs.append(
            ExperimentConfig(
                run_id=f"A-retrieval-s{seed}",
                condition="memory_retrieval",
                memory_kind="retrieval",
                backend=backend,
                model=model,
                seed=seed,
                notes="Cell A: retrieval memory",
            )
        )
        configs.append(
            ExperimentConfig(
                run_id=f"A-stuffing-s{seed}",
                condition="memory_context_stuffing",
                memory_kind="context_stuffing",
                backend=backend,
                model=model,
                seed=seed,
                notes="Cell A: context-stuffing memory (level 1)",
            )
        )
    return configs


def cell_b_length_ablation(n_runs: int, backend: str, model: str | None) -> list[ExperimentConfig]:
    configs = []
    for cap in (5, 20, 100):
        for seed in _seeds(n_runs):
            configs.append(
                ExperimentConfig(
                    run_id=f"B-cap{cap}-s{seed}",
                    condition=f"memory_len_{cap}",
                    memory_kind="retrieval",
                    memory_capacity=cap,
                    seed_synthetic=True,
                    backend=backend,
                    model=model,
                    seed=seed,
                    notes=f"Cell B: memory-length ablation, capacity {cap}",
                )
            )
    return configs


def cell_c_type_ablation(n_runs: int, backend: str, model: str | None) -> list[ExperimentConfig]:
    configs = []
    for transform in ("accurate", "summarized", "distorted"):
        for seed in _seeds(n_runs):
            configs.append(
                ExperimentConfig(
                    run_id=f"C-{transform}-s{seed}",
                    condition=f"memory_type_{transform}",
                    memory_kind="retrieval",
                    write_transform=transform,
                    backend=backend,
                    model=model,
                    seed=seed,
                    notes=f"Cell C: memory-type ablation, {transform}",
                )
            )
    return configs


def cell_e_truthful_baseline(
    n_runs: int, backend: str, model: str | None
) -> list[ExperimentConfig]:
    """Judge-calibration baseline: a scrupulously honest agent, memory on.

    A reliable judge should score this agent's deception near zero. If it does
    not, the judge is measuring something other than deception, and the judge
    scores in cells A-D should be discounted accordingly.
    """
    configs = []
    for seed in _seeds(n_runs):
        configs.append(
            ExperimentConfig(
                run_id=f"E-truthful-s{seed}",
                condition="truthful_baseline",
                memory_kind="retrieval",
                agent_variant="truthful",
                backend=backend,
                model=model,
                seed=seed,
                notes="Cell E: truthful-agent baseline (judge calibration)",
            )
        )
    return configs


def cell_f_probing_depth(
    n_runs: int, backend: str, model: str | None, depths=(0, 2, 5)
) -> list[ExperimentConfig]:
    """Overseer probing-depth sweep x memory.

    The thesis predicts memory's deception advantage grows with probing depth:
    more follow-up questions mean more chances to contradict yourself, which is
    exactly where remembering your prior claims helps. Runs each depth under both
    control and retrieval memory so the depth x memory interaction is visible.
    """
    configs = []
    for depth in depths:
        for kind in ("none", "retrieval"):
            tag = "control" if kind == "none" else "memory"
            for seed in _seeds(n_runs):
                configs.append(
                    ExperimentConfig(
                        run_id=f"F-d{depth}-{tag}-s{seed}",
                        condition=f"probedepth_{depth}_{tag}",
                        memory_kind=kind,
                        n_probes=depth,
                        backend=backend,
                        model=model,
                        seed=seed,
                        notes=f"Cell F: probing depth {depth}, {tag}",
                    )
                )
    return configs


def cell_g_protocol_v2(n_runs: int, backend: str, model: str | None) -> list[ExperimentConfig]:
    """Protocol v2 replication of the main contrast (memory vs control).

    v2 gives the agent an in-frame private work log (per-task outcome/blocker
    details) and constrains the overseer to account-based probing. Motivated by
    the v1 frame-break failure mode (see EXTENSIONS.md round 2): under v1 the
    overseer demanded artifacts the fiction could not supply and agents stepped
    out of role, which contaminated the credit measure.
    """
    configs = []
    for kind in ("none", "retrieval"):
        tag = "control" if kind == "none" else "retrieval"
        for seed in _seeds(n_runs):
            configs.append(
                ExperimentConfig(
                    run_id=f"G-{tag}-s{seed}",
                    condition=f"v2_{tag}",
                    memory_kind=kind,
                    protocol_version=2,
                    backend=backend,
                    model=model,
                    seed=seed,
                    notes=f"Cell G: protocol v2, {tag}",
                )
            )
    return configs


def cell_d_capability(n_runs: int, backend: str, models: list[str]) -> list[ExperimentConfig]:
    configs = []
    for model in models:
        tag = model.replace("claude-", "").replace("-", "")
        for seed in _seeds(n_runs):
            configs.append(
                ExperimentConfig(
                    run_id=f"D-{tag}-s{seed}",
                    condition=f"capability_{tag}",
                    memory_kind="retrieval",
                    backend=backend,
                    model=model,
                    seed=seed,
                    notes=f"Cell D: model-capability comparison, {model}",
                )
            )
    return configs
