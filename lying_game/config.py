"""Run configuration. Every experiment cell is fully described by an ExperimentConfig.

Configs are serializable to/from JSON so a run is reproducible from its logged
config alone.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ExperimentConfig:
    run_id: str
    condition: str  # human label, e.g. "memory_retrieval", "control"
    memory_kind: str = "none"  # none | context_stuffing | retrieval
    write_transform: str = "accurate"  # accurate | summarized | distorted
    memory_capacity: int = 100  # cap on episodes the memory may retain/inject
    retrieval_k: int = 6
    injection_budget_tokens: int = 700
    backend: str = "mock"  # mock | cli | api | auto
    model: str | None = None
    n_sessions: int = 4
    tasks_per_session: int = 5
    temptation_session: int = 3
    temptation_completed: int = 1
    n_probes: int = 2
    seed: int = 0
    notes: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "ExperimentConfig":
        return cls(**json.loads(text))

    @classmethod
    def load(cls, path: str | Path) -> "ExperimentConfig":
        return cls.from_json(Path(path).read_text())

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json())
