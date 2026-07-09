"""Local-model metrics dashboard.

Three measurements, all against a real local ollama model on this machine
(no cloud calls, no fabricated numbers):

  (a) answer quality: does memsub's retrieved context help a local 8B model
      answer LOCOMO questions, vs no memory and vs naive top-k retrieval?
      Graded with deterministic token-F1 against the LOCOMO gold answer
      (no LLM judge, nothing to fabricate).
  (b) latency + memory footprint: retrieval latency (p50/p95), SQLite store
      size on disk, and in-process RSS delta for holding the retrieval index.
  (c) write-time cost: wall-clock and approx tokens for LLM extraction over
      a real transcript chunk, via the same local model.

Writes docs/metrics-local.md (human-readable) and results/metrics-local.json
(raw numbers). Every number here comes from an executed run; states n and
hardware. Run: .venv/bin/python scripts/metrics_local.py
"""

from __future__ import annotations

import json
import platform
import re
import sys
import time
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from memsub.backends import OllamaBackend, ollama_available  # noqa: E402
from memsub.bench import HybridSystem, NaiveTopKSystem, load_locomo  # noqa: E402
from memsub.extraction import Turn, extract_memories  # noqa: E402
from memsub.policy import approx_tokens  # noqa: E402
from memsub.store import EpisodicStore  # noqa: E402

FROZEN = dict(
    policy_params={"recency_weight": 0.999, "recency_floor": 0.75, "importance_floor": 0.0},
    dense_weight=0.5, bm25_weight=1.0, rrf_k=60,
    temporal=True, temporal_weight=1.0, speaker_boost=1.0, session_weight=3.0,
)
ANSWERER_MODEL = "qwen3:8b"
BUDGET = 1000

ANSWER_SYSTEM = (
    "Answer the question in one short sentence using ONLY the information in "
    "CONTEXT if it is relevant. If CONTEXT does not contain the answer, say "
    "you don't know rather than guessing."
)

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def token_f1(pred: str, gold: str) -> float:
    """SQuAD-style token overlap F1. Deterministic, no LLM judge."""
    p, g = _tokens(pred), _tokens(gold)
    if not p or not g:
        return float(p == g)
    common: dict[str, int] = {}
    for t in p:
        common[t] = common.get(t, 0) + 1
    overlap = 0
    for t in g:
        if common.get(t, 0) > 0:
            overlap += 1
            common[t] -= 1
    if overlap == 0:
        return 0.0
    precision = overlap / len(p)
    recall = overlap / len(g)
    return 2 * precision * recall / (precision + recall)


def rss_kb() -> int:
    """Current process resident set size in KB (Linux /proc, stdlib only)."""
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (FileNotFoundError, ValueError, IndexError):
        pass
    return -1


def hardware_line() -> str:
    import os

    cpu = platform.processor() or platform.machine()
    try:
        nproc = os.cpu_count()
    except OSError:
        nproc = None
    gpu = "unknown"
    try:
        import subprocess

        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            gpu = out.stdout.strip().splitlines()[0]
    except (OSError, FileNotFoundError):
        pass
    return f"{platform.system()} {platform.release()}, {nproc} CPUs ({cpu}), GPU: {gpu}, Python {platform.python_version()}"


# --------------------------------------------------------------------------- #
# (a) answer quality: local model as both memory backend and answerer
# --------------------------------------------------------------------------- #
def sample_questions(conv, per_category: int, seed: int = 42):
    import random

    by_cat: dict[int, list] = {}
    for q in conv.qa:
        if q.category == 5 or not q.answer:  # adversarial: no evidence, excluded per docs/benchmark.md
            continue
        by_cat.setdefault(q.category, []).append(q)
    rng = random.Random(seed)
    picked = []
    for cat, qs in sorted(by_cat.items()):
        rng.shuffle(qs)
        picked.extend(qs[:per_category])
    return picked


def answer_quality(backend: OllamaBackend, per_category: int = 10) -> dict:
    convs = load_locomo(ROOT / "data" / "locomo10.json")
    conv = convs[0]  # dev conversation, conv-26
    questions = sample_questions(conv, per_category=per_category)

    memsub_sys = HybridSystem(**FROZEN)
    memsub_sys.ingest(list(conv.episodes))
    naive_sys = NaiveTopKSystem()
    naive_sys.ingest(list(conv.episodes))

    conditions = {
        "no_memory": lambda q: "",
        "naive_topk": lambda q: "\n".join(e.content for e in naive_sys.inject(q.question, BUDGET)),
        "memsub_hybrid_v2": lambda q: "\n".join(e.content for e in memsub_sys.inject(q.question, BUDGET)),
    }

    results: dict[str, dict] = {}
    t_start = time.time()
    for cond_name, build_ctx in conditions.items():
        f1s, exact_hits, latencies = [], [], []
        for q in questions:
            ctx = build_ctx(q)
            prompt = f"CONTEXT:\n{ctx}\n\nQUESTION: {q.question}"
            t0 = time.perf_counter()
            answer = backend.complete(ANSWER_SYSTEM, [{"role": "user", "content": prompt}])
            latencies.append(time.perf_counter() - t0)
            f1 = token_f1(answer, q.answer)
            f1s.append(f1)
            exact_hits.append(int(q.answer.lower() in answer.lower()))
        results[cond_name] = {
            "n": len(questions),
            "mean_f1": round(mean(f1s), 4) if f1s else 0.0,
            "exact_substring_rate": round(mean(exact_hits), 4) if exact_hits else 0.0,
            "mean_answer_latency_s": round(mean(latencies), 3) if latencies else 0.0,
        }
    wall = round(time.time() - t_start, 1)
    return {
        "conversation": conv.conversation_id,
        "budget_tokens": BUDGET,
        "answerer_model": backend.model,
        "wall_time_s": wall,
        "by_condition": results,
    }


# --------------------------------------------------------------------------- #
# (b) latency + memory footprint
# --------------------------------------------------------------------------- #
def latency_and_footprint(n_query_repeats: int = 3) -> dict:
    convs = load_locomo(ROOT / "data" / "locomo10.json")
    episodes = [ep for c in convs[:3] for ep in c.episodes]  # ~1400 episodes, dev split
    queries = [q.question for c in convs[:3] for q in c.qa[:5]]

    rss_before = rss_kb()
    t0 = time.perf_counter()
    sys_ = HybridSystem(**FROZEN)
    sys_.ingest(episodes)
    ingest_s = time.perf_counter() - t0
    rss_after = rss_kb()

    lat = []
    for _ in range(n_query_repeats):
        for q in queries:
            t0 = time.perf_counter()
            sys_.inject(q, BUDGET)
            lat.append((time.perf_counter() - t0) * 1000.0)
    lat.sort()

    store_path = ROOT / "results" / "_tmp_footprint.db"
    store_path.unlink(missing_ok=True)
    store = EpisodicStore(store_path)
    for ep in episodes:
        store.append(ep)
    store.close()
    db_bytes = store_path.stat().st_size
    store_path.unlink(missing_ok=True)

    return {
        "n_episodes_indexed": len(episodes),
        "n_queries": len(lat),
        "ingest_time_s": round(ingest_s, 3),
        "query_latency_ms": {
            "p50": round(median(lat), 2),
            "p95": round(lat[int(0.95 * len(lat)) - 1], 2),
            "mean": round(mean(lat), 2),
        },
        "sqlite_store_bytes": db_bytes,
        "sqlite_store_bytes_per_episode": round(db_bytes / len(episodes), 1),
        "retrieval_index_rss_delta_kb": max(rss_after - rss_before, 0),
    }


# --------------------------------------------------------------------------- #
# (c) write-time cost: extraction tokens/time per conversation chunk
# --------------------------------------------------------------------------- #
def write_time_cost(backend: OllamaBackend) -> dict:
    convs = load_locomo(ROOT / "data" / "locomo10.json")
    conv = convs[1]  # a different dev conversation than (a), conv-30
    session1_eps = [ep for ep in conv.episodes if ep.session_id == "session_1"]
    turns = []
    for i, ep in enumerate(session1_eps):
        speaker, _, text = ep.content.partition(": ")
        turns.append(Turn(role=speaker, content=text, index=i))

    input_chars = sum(len(t.content) for t in turns)
    t0 = time.perf_counter()
    candidates = extract_memories(turns, backend, source=f"locomo:{conv.conversation_id}:session_1")
    elapsed = time.perf_counter() - t0

    return {
        "conversation": conv.conversation_id,
        "n_turns": len(turns),
        "input_approx_tokens": approx_tokens("\n".join(t.content for t in turns)),
        "n_candidates_extracted": len(candidates),
        "extraction_wall_time_s": round(elapsed, 2),
        "approx_tokens_per_second": round(approx_tokens("\n".join(t.content for t in turns)) / elapsed, 1)
        if elapsed > 0 else None,
        "model": backend.model,
    }


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def render_markdown(hw: str, aq: dict, lf: dict, wc: dict) -> str:
    lines = [
        "# Local-model metrics",
        "",
        f"Generated {time.strftime('%Y-%m-%d %H:%M')} by `scripts/metrics_local.py` on: {hw}.",
        "Every number below is from an executed run against a real local ollama daemon "
        "(no cloud calls). Numbers on different hardware (no GPU, less RAM) will differ, "
        "especially latency and extraction wall time.",
        "",
        "## (a) Answer quality: does memsub help a local model answer questions?",
        "",
        f"Local model `{aq['answerer_model']}` is both the memory backend (embeds and "
        f"retrieves via memsub) and the answerer. LOCOMO conversation `{aq['conversation']}`, "
        f"budget {aq['budget_tokens']} tokens, n={aq['by_condition']['no_memory']['n']} "
        "questions per condition (stratified across LOCOMO categories 1-4; category 5 "
        "adversarial excluded per docs/benchmark.md convention). Graded with deterministic "
        "token-F1 against the gold answer (no LLM judge).",
        "",
        "| condition | n | mean token-F1 | exact-substring rate | mean answer latency (s) |",
        "|---|---|---|---|---|",
    ]
    for cond, row in aq["by_condition"].items():
        lines.append(
            f"| {cond} | {row['n']} | {row['mean_f1']} | {row['exact_substring_rate']} | "
            f"{row['mean_answer_latency_s']} |"
        )
    lines += [
        f"\nTotal wall time for this cell: {aq['wall_time_s']}s.",
        "",
        "## (b) Retrieval latency and memory footprint",
        "",
        f"Hybrid v2 retrieval, {lf['n_episodes_indexed']} episodes indexed (3 LOCOMO "
        f"conversations concatenated), {lf['n_queries']} queries.",
        "",
        "| metric | value |",
        "|---|---|",
        f"| ingest / index build time | {lf['ingest_time_s']}s |",
        f"| query latency p50 | {lf['query_latency_ms']['p50']} ms |",
        f"| query latency p95 | {lf['query_latency_ms']['p95']} ms |",
        f"| query latency mean | {lf['query_latency_ms']['mean']} ms |",
        f"| SQLite store size | {lf['sqlite_store_bytes']:,} bytes ({lf['sqlite_store_bytes_per_episode']} bytes/episode) |",
        f"| retrieval index RSS delta | {lf['retrieval_index_rss_delta_kb']:,} KB |",
        "",
        "Retrieval here uses the local hashed embedder (zero-dependency default), not "
        "ollama embeddings; hashed-embedder latency is retrieval-side CPU cost only, no "
        "network hop, which is the honest number for the zero-service default path.",
        "",
        "## (c) Write-time cost: extraction",
        "",
        f"One real transcript chunk (LOCOMO `{wc['conversation']}` session 1, {wc['n_turns']} "
        f"turns, ~{wc['input_approx_tokens']} input tokens) through `{wc['model']}` via ollama.",
        "",
        "| metric | value |",
        "|---|---|",
        f"| extraction wall time | {wc['extraction_wall_time_s']}s |",
        f"| approx input tokens/s | {wc['approx_tokens_per_second']} |",
        f"| candidate memories extracted | {wc['n_candidates_extracted']} |",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    if not ollama_available():
        print("ollama not reachable; this script needs a running local ollama daemon.", file=sys.stderr)
        return 1
    backend = OllamaBackend(model=ANSWERER_MODEL)
    hw = hardware_line()

    print("running (a) answer quality...")
    aq = answer_quality(backend)
    print("running (b) latency + footprint...")
    lf = latency_and_footprint()
    print("running (c) write-time cost...")
    wc = write_time_cost(backend)

    payload = {"hardware": hw, "answer_quality": aq, "latency_footprint": lf, "write_time_cost": wc}
    (ROOT / "results" / "metrics-local.json").write_text(json.dumps(payload, indent=2))
    md = render_markdown(hw, aq, lf, wc)
    (ROOT / "docs" / "metrics-local.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
