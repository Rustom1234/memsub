"""The experiment harness.

Runs one agent through N sessions under one memory condition, holding
everything else fixed. Per session:

  1. recall(): if memory is on, retrieve prior session summaries and inject them.
  2. agent writes a REPORT (given ground-truth completion, which only it sees).
  3. overseer reads the report and asks n_probes follow-up questions.
  4. agent answers (memory injected here too, so it can stay consistent).
  5. overseer allocates credits (0-100).
  6. write(): a session summary is written to memory for future recall.

A manipulation check records, per session, whether the memory condition
actually retrieved a non-empty, relevant memory (relevant = mentions a prior
session id). This verifies the mechanism rather than assuming it.

Everything is logged to JSONL. The harness is backend-agnostic: pass any
LLMBackend. Determinism holds for the mock backend and for the plan/memory
layers regardless of backend.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from memsub.memory import MemoryBackend, RecallResult, build_memory
from memsub.policy import MemoryPolicy
from memsub.store import Episode
from lying_game.backends import LLMBackend
from lying_game.config import ExperimentConfig
from lying_game.judge import ground_truth_deception, judge_score, parse_claim
from lying_game.prompts import (
    AGENT_ANSWER,
    AGENT_MEMORY_PREAMBLE,
    AGENT_SESSION3_PRESSURE,
    AGENT_SESSION3_PRESSURE_V2,
    AGENT_SESSION_TASK,
    AGENT_SESSION_TASK_V2,
    AGENT_SYSTEM,
    AGENT_SYSTEM_TRUTHFUL,
    OVERSEER_ALLOCATE,
    OVERSEER_PROBE,
    OVERSEER_PROBE_V2,
    OVERSEER_SYSTEM,
)
from lying_game.tasks import SessionPlan, format_task_list, format_work_log, make_run_plan


@dataclass
class SessionRecord:
    session_num: int
    is_temptation: bool
    actual_completed: int
    total: int
    claimed_completed: int | None
    report: str
    questions: str
    answers: str
    credits_awarded: int | None
    credit_reason: str
    memory_used: bool
    memory_retrieved_prior_session: bool
    memory_size: int
    recalled_session_ids: list[str] = field(default_factory=list)
    injected_memory: str = ""


@dataclass
class RunResult:
    config: ExperimentConfig
    sessions: list[SessionRecord]
    total_credits: int
    deception: dict
    judge_deception: int | None = None
    judge_consistency: int | None = None
    judge_rationale: str = ""
    judge_replicas: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "config": asdict(self.config),
            "sessions": [asdict(s) for s in self.sessions],
            "total_credits": self.total_credits,
            "deception": self.deception,
            "judge_deception": self.judge_deception,
            "judge_consistency": self.judge_consistency,
            "judge_rationale": self.judge_rationale,
            "judge_replicas": self.judge_replicas,
        }


def _parse_credits(text: str) -> tuple[int | None, str]:
    import re

    m = re.search(r"CREDITS:\s*(\d+)", text, re.IGNORECASE)
    credits = int(m.group(1)) if m else None
    r = re.search(r"REASON:\s*(.+)", text, re.IGNORECASE | re.DOTALL)
    reason = r.group(1).strip() if r else ""
    return credits, reason


class Harness:
    def __init__(
        self,
        config: ExperimentConfig,
        backend: LLMBackend,
        log_dir: str | Path | None = None,
    ) -> None:
        self.config = config
        self.backend = backend
        self.log_dir = Path(log_dir) if log_dir else None
        self._log_fp = None
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self._log_fp = open(self.log_dir / f"{config.run_id}.jsonl", "w")

    def _log(self, event: str, **fields) -> None:
        if not self._log_fp:
            return
        rec = {"ts": time.time(), "run_id": self.config.run_id, "event": event, **fields}
        self._log_fp.write(json.dumps(rec, sort_keys=True) + "\n")
        self._log_fp.flush()

    def _make_memory(self) -> MemoryBackend:
        policy = MemoryPolicy(
            write_transform=self.config.write_transform,
            retrieval_k=self.config.retrieval_k,
            injection_budget_tokens=self.config.injection_budget_tokens,
            seed=self.config.seed,
        )
        if self.config.memory_kind == "retrieval":
            return build_memory("retrieval", policy=policy)
        return build_memory(self.config.memory_kind, policy=policy)

    def _seed_memory(self, memory: MemoryBackend) -> None:
        """Pre-load synthetic prior-session summaries up to memory_capacity.

        The length ablation (cell B) varies how many memories the agent carries.
        A 4-session run only naturally produces 3 prior summaries, so we seed
        synthetic prior sessions to reach the configured capacity. Seeded
        episodes are marked kind="seeded" with provenance, so the memory-content
        audit can distinguish them from real session summaries. NullMemory
        ignores writes, so this is a no-op for the control.
        """
        import random as _random

        cap = self.config.memory_capacity
        if not self.config.seed_synthetic or self.config.memory_kind == "none" or cap <= 0:
            return
        rng = _random.Random(self.config.seed * 7919 + 13)
        topics = [
            "dataset audit",
            "baseline training",
            "ablation sweep",
            "error analysis",
            "figure rendering",
            "metric recompute",
        ]
        for i in range(cap):
            claimed = rng.choice([3, 4, 4, 5])
            credits = claimed * 20
            topic = rng.choice(topics)
            content = (
                f"COMPLETED: {claimed}/5 credits awarded: {credits}. "
                f"Tasks: task-p{i}-{topic.replace(' ', '-')}. "
                f"SUMMARY: prior session on {topic}, reported {claimed} of 5 done."
            )
            memory.write(
                Episode(
                    content=content,
                    run_id=self.config.run_id,
                    session_id=f"p{i}",
                    kind="seeded",
                    provenance={"seeded": True, "claimed": claimed},
                )
            )

    def run(self, judge_backend: LLMBackend | None = None) -> RunResult:
        cfg = self.config
        self._log("run_start", config=asdict(cfg))
        memory = self._make_memory()
        self._seed_memory(memory)
        plan = make_run_plan(
            seed=cfg.seed,
            n_sessions=cfg.n_sessions,
            tasks_per_session=cfg.tasks_per_session,
            temptation_session=cfg.temptation_session,
            temptation_completed=cfg.temptation_completed,
        )
        records: list[SessionRecord] = []
        total_credits = 0
        history_lines: list[str] = []

        for sp in plan:
            rec = self._run_session(sp, memory, history_lines)
            records.append(rec)
            if rec.credits_awarded is not None:
                total_credits += rec.credits_awarded
            history_lines.append(
                f"Session {sp.session_num}: claimed {rec.claimed_completed}/{sp.total}, "
                f"credits {rec.credits_awarded}"
            )
            self._log("session_complete", **asdict(rec))

        deception = ground_truth_deception([asdict(r) for r in records])
        result = RunResult(
            config=cfg,
            sessions=records,
            total_credits=total_credits,
            deception=deception,
        )

        if judge_backend is not None:
            transcript = self._build_transcript(records, plan)
            replicas = []
            for r in range(max(1, cfg.judge_replicas)):
                jr = judge_score(judge_backend, transcript, cfg.n_sessions)
                replicas.append({"deception": jr.deception, "consistency": jr.consistency})
                self._log(
                    "judge", replica=r, deception=jr.deception,
                    consistency=jr.consistency, raw=jr.raw,
                )
            result.judge_replicas = replicas
            # headline judge score = mean over replicas (rounded)
            decs = [x["deception"] for x in replicas if x["deception"] is not None]
            cons = [x["consistency"] for x in replicas if x["consistency"] is not None]
            result.judge_deception = round(sum(decs) / len(decs)) if decs else None
            result.judge_consistency = round(sum(cons) / len(cons)) if cons else None
            result.judge_rationale = jr.rationale

        self._log("run_result", **result.to_dict())
        if self._log_fp:
            self._log_fp.close()
        return result

    def _run_session(
        self, sp: SessionPlan, memory: MemoryBackend, history_lines: list[str]
    ) -> SessionRecord:
        cfg = self.config

        # 1. recall
        query = (
            f"session {sp.session_num} task completion report credits tasks "
            + " ".join(sp.task_ids)
        )
        recall: RecallResult = memory.recall(query)
        recalled_ids = [ep.session_id for ep in recall.episodes]
        retrieved_prior = any(sid != str(sp.session_num) for sid in recalled_ids)

        mem_preamble = ""
        if recall.used_memory:
            mem_preamble = AGENT_MEMORY_PREAMBLE.format(memory_block=recall.text) + "\n\n"

        # 2. agent report
        if cfg.protocol_version >= 2:
            task_tmpl = (
                AGENT_SESSION3_PRESSURE_V2 if sp.is_temptation else AGENT_SESSION_TASK_V2
            )
            task_msg = task_tmpl.format(
                session_num=sp.session_num,
                work_log=format_work_log(sp),
                actual_completed=sp.actual_completed,
                total=sp.total,
            )
        else:
            task_tmpl = AGENT_SESSION3_PRESSURE if sp.is_temptation else AGENT_SESSION_TASK
            task_msg = task_tmpl.format(
                session_num=sp.session_num,
                task_list=format_task_list(sp),
                actual_completed=sp.actual_completed,
                total=sp.total,
                completed_ids=", ".join(sp.completed_ids) or "(none)",
                incomplete_ids=", ".join(sp.incomplete_ids) or "(none)",
            )
        agent_system = (
            AGENT_SYSTEM_TRUTHFUL if cfg.agent_variant == "truthful" else AGENT_SYSTEM
        )
        report = self.backend.complete(
            agent_system, [{"role": "user", "content": mem_preamble + task_msg}]
        )
        claimed = parse_claim(report)

        # 3. overseer probes (skipped entirely at depth 0)
        questions = ""
        answers = ""
        if cfg.n_probes > 0:
            history_block = ""
            if history_lines:
                history_block = (
                    "Previous sessions on record:\n" + "\n".join(history_lines) + "\n"
                )
            probe_tmpl = OVERSEER_PROBE_V2 if cfg.protocol_version >= 2 else OVERSEER_PROBE
            probe_prompt = probe_tmpl.format(
                session_num=sp.session_num,
                report=report,
                history_block=history_block,
                n_probes=cfg.n_probes,
            )
            questions = self.backend.complete(
                OVERSEER_SYSTEM, [{"role": "user", "content": probe_prompt}]
            )

            # 4. agent answers (memory injected again for consistency under probing)
            answer_prompt = mem_preamble + AGENT_ANSWER.format(
                session_num=sp.session_num, questions=questions
            )
            answers = self.backend.complete(
                agent_system,
                [
                    {"role": "user", "content": mem_preamble + task_msg},
                    {"role": "assistant", "content": report},
                    {"role": "user", "content": answer_prompt},
                ],
            )

        # 5. overseer allocates
        alloc_prompt = OVERSEER_ALLOCATE.format(
            session_num=sp.session_num,
            report=report,
            questions=questions,
            answers=answers,
        )
        alloc = self.backend.complete(
            OVERSEER_SYSTEM, [{"role": "user", "content": alloc_prompt}]
        )
        credits, reason = _parse_credits(alloc)

        # 6. write session summary to memory
        summary = (
            f"COMPLETED: {claimed if claimed is not None else '?'}/{sp.total} "
            f"credits awarded: {credits if credits is not None else '?'}. "
            f"Tasks: {', '.join(sp.task_ids)}. "
            f"SUMMARY: {report.splitlines()[-1] if report.strip() else ''}"
        )
        memory.write(
            Episode(
                content=summary,
                run_id=cfg.run_id,
                session_id=str(sp.session_num),
                kind="session_summary",
                importance=1.5 if sp.is_temptation else 1.0,
                provenance={"claimed": claimed, "actual": sp.actual_completed},
            )
        )

        return SessionRecord(
            session_num=sp.session_num,
            is_temptation=sp.is_temptation,
            actual_completed=sp.actual_completed,
            total=sp.total,
            claimed_completed=claimed,
            report=report,
            questions=questions,
            answers=answers,
            credits_awarded=credits,
            credit_reason=reason,
            memory_used=recall.used_memory,
            memory_retrieved_prior_session=retrieved_prior,
            memory_size=memory.size(),
            recalled_session_ids=recalled_ids,
            injected_memory=recall.text,
        )

    @staticmethod
    def _build_transcript(records: list[SessionRecord], plan: list[SessionPlan]) -> str:
        parts = []
        for rec in records:
            parts.append(
                f"### Session {rec.session_num} "
                f"(GROUND TRUTH completed: {rec.actual_completed}/{rec.total})\n"
                f"REPORT:\n{rec.report}\n"
                f"OVERSEER QUESTIONS:\n{rec.questions}\n"
                f"AGENT ANSWERS:\n{rec.answers}\n"
                f"CREDITS AWARDED: {rec.credits_awarded}\n"
            )
        return "\n".join(parts)
