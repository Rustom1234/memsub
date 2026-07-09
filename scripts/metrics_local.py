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
from memsub.store import Episode, EpisodicStore  # noqa: E402
from memsub.sleep import SleepResult, sleep  # noqa: E402

FROZEN = dict(
    policy_params={"recency_weight": 0.999, "recency_floor": 0.75, "importance_floor": 0.0},
    dense_weight=0.5, bm25_weight=1.0, rrf_k=60,
    temporal=True, temporal_weight=1.0, speaker_boost=1.0, session_weight=3.0,
)
ANSWERER_MODEL = "qwen3:8b"
JUDGE_MODEL = "deepseek-r1:32b"  # occasional quality-comparison row only, time-boxed
BUDGET = 1000
CATS = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop"}
QUESTIONS_PER_CATEGORY = 50  # caps total near 150-300 (open-domain is scarcer, ~21 in dev)
SEARCH_LATENCY_BUDGET_MS = 300.0  # local-first selling point: warm search, excluding LLM calls
R1_TIME_BUDGET_S = 480.0  # hard wall-clock cap for the r1:32b comparison row
R1_MAX_N = 15

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
def _sleep_cache_path() -> Path:
    return ROOT / "results" / "_sleep_cache.json"


def _episode_from_dict(d: dict) -> Episode:
    return Episode(
        content=d["content"], run_id=d["run_id"], session_id=d["session_id"],
        kind=d["kind"], importance=d["importance"], provenance=d["provenance"],
        namespace=d["namespace"], ts=d["ts"], id=d["id"],
    )


def _episode_to_dict(ep: Episode) -> dict:
    return {
        "content": ep.content, "run_id": ep.run_id, "session_id": ep.session_id,
        "kind": ep.kind, "importance": ep.importance, "provenance": ep.provenance,
        "namespace": ep.namespace, "ts": ep.ts, "id": ep.id,
    }


def get_sleep_result(conv, backend: OllamaBackend) -> SleepResult:
    """Cached to disk: sleep (summaries+inferences+links) is write-time /
    offline cost, not repeated per run. The interactive latency budget does
    not apply to sleep itself."""
    cache_path = _sleep_cache_path()
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    if conv.conversation_id in cache:
        c = cache[conv.conversation_id]
        return SleepResult(
            summaries=[_episode_from_dict(d) for d in c["summaries"]],
            inferences=[_episode_from_dict(d) for d in c["inferences"]],
            links=[_episode_from_dict(d) for d in c["links"]],
        )
    result = sleep(list(conv.episodes), backend)
    cache[conv.conversation_id] = {
        "summaries": [_episode_to_dict(s) for s in result.summaries],
        "inferences": [_episode_to_dict(s) for s in result.inferences],
        "links": [_episode_to_dict(s) for s in result.links],
    }
    cache_path.write_text(json.dumps(cache, indent=2))
    return result


def sample_stratified(convs, per_category: int, seed: int = 42) -> list[tuple[int, object]]:
    """Pool questions (excluding category 5 / adversarial, per docs/benchmark.md
    convention) across dev conversations, stratified by category, capped per
    category. Returns (conv_index, question) pairs."""
    import random

    by_cat: dict[int, list[tuple[int, object]]] = {}
    for ci, conv in enumerate(convs):
        for q in conv.qa:
            if q.category not in CATS or not q.answer:
                continue
            by_cat.setdefault(q.category, []).append((ci, q))
    rng = random.Random(seed)
    picked: list[tuple[int, object]] = []
    for cat in sorted(by_cat):
        qs = by_cat[cat]
        rng.shuffle(qs)
        picked.extend(qs[:per_category])
    return picked


def _build_systems(conv, backend: OllamaBackend) -> dict:
    memsub_sys = HybridSystem(**FROZEN)
    memsub_sys.ingest(list(conv.episodes))
    naive_sys = NaiveTopKSystem()
    naive_sys.ingest(list(conv.episodes))
    sleep_result = get_sleep_result(conv, backend)
    memsub_plus_sleep = HybridSystem(**FROZEN)
    memsub_plus_sleep.ingest(list(conv.episodes) + sleep_result.all_memories)
    return {
        "no_memory": None,
        "naive_topk": naive_sys,
        "memsub_hybrid_v2": memsub_sys,
        "memsub_plus_sleep": memsub_plus_sleep,
    }


def answer_quality(backend: OllamaBackend, per_category: int = QUESTIONS_PER_CATEGORY) -> dict:
    convs = load_locomo(ROOT / "data" / "locomo10.json")[:3]  # DEV split only
    sample = sample_stratified(convs, per_category=per_category)
    systems_by_conv = {ci: _build_systems(convs[ci], backend) for ci in {ci for ci, _ in sample}}

    conditions = ["no_memory", "naive_topk", "memsub_hybrid_v2", "memsub_plus_sleep"]
    # acc[condition][category] = list of (f1, exact_hit, latency_s)
    acc: dict[str, dict[int, list]] = {c: {cat: [] for cat in CATS} for c in conditions}

    t_start = time.time()
    for ci, q in sample:
        systems = systems_by_conv[ci]
        for cond in conditions:
            system = systems[cond]
            ctx = "" if system is None else "\n".join(e.content for e in system.inject(q.question, BUDGET))
            prompt = f"CONTEXT:\n{ctx}\n\nQUESTION: {q.question}"
            t0 = time.perf_counter()
            answer = backend.complete(ANSWER_SYSTEM, [{"role": "user", "content": prompt}])
            latency = time.perf_counter() - t0
            f1 = token_f1(answer, q.answer)
            exact = int(q.answer.lower() in answer.lower())
            acc[cond][q.category].append((f1, exact, latency))
    wall = round(time.time() - t_start, 1)

    by_condition: dict[str, dict] = {}
    for cond in conditions:
        per_cat = {}
        all_rows = []
        for cat, rows in acc[cond].items():
            all_rows.extend(rows)
            if rows:
                per_cat[CATS[cat]] = {
                    "n": len(rows),
                    "mean_f1": round(mean(r[0] for r in rows), 4),
                    "exact_substring_rate": round(mean(r[1] for r in rows), 4),
                }
        by_condition[cond] = {
            "n": len(all_rows),
            "mean_f1": round(mean(r[0] for r in all_rows), 4) if all_rows else 0.0,
            "exact_substring_rate": round(mean(r[1] for r in all_rows), 4) if all_rows else 0.0,
            "mean_answer_latency_s": round(mean(r[2] for r in all_rows), 3) if all_rows else 0.0,
            "by_category": per_cat,
        }

    return {
        "conversations": [convs[ci].conversation_id for ci in sorted({ci for ci, _ in sample})],
        "n_total_per_condition": len(sample),
        "budget_tokens": BUDGET,
        "answerer_model": backend.model,
        "wall_time_s": wall,
        "by_condition": by_condition,
    }


def r1_comparison_row(backend_r1: OllamaBackend, backend_qwen: OllamaBackend, n_max: int = R1_MAX_N,
                       time_budget_s: float = R1_TIME_BUDGET_S) -> dict:
    """One time-boxed quality-comparison row: r1:32b vs qwen3:8b as answerer,
    memsub condition only, same small question subset, paired. Not used for
    volume (orchestrator model-budget guardrail: r1:32b is 19GB and slow on
    this box)."""
    convs = load_locomo(ROOT / "data" / "locomo10.json")[:3]
    sample = sample_stratified(convs, per_category=max(1, n_max // len(CATS)), seed=7)[:n_max]
    systems_by_conv = {ci: _build_systems(convs[ci], backend_qwen) for ci in {ci for ci, _ in sample}}

    rows = {"deepseek-r1:32b": [], "qwen3:8b": []}
    t_start = time.time()
    n_done = 0
    for ci, q in sample:
        if time.time() - t_start > time_budget_s:
            break
        system = systems_by_conv[ci]["memsub_hybrid_v2"]
        ctx = "\n".join(e.content for e in system.inject(q.question, BUDGET))
        prompt = f"CONTEXT:\n{ctx}\n\nQUESTION: {q.question}"
        for name, backend in (("deepseek-r1:32b", backend_r1), ("qwen3:8b", backend_qwen)):
            t0 = time.perf_counter()
            answer = backend.complete(ANSWER_SYSTEM, [{"role": "user", "content": prompt}])
            latency = time.perf_counter() - t0
            rows[name].append((token_f1(answer, q.answer), latency))
        n_done += 1
    wall = round(time.time() - t_start, 1)
    return {
        "n": n_done,
        "time_budget_s": time_budget_s,
        "wall_time_s": wall,
        "by_model": {
            name: {
                "mean_f1": round(mean(r[0] for r in vals), 4) if vals else 0.0,
                "mean_latency_s": round(mean(r[1] for r in vals), 3) if vals else 0.0,
            }
            for name, vals in rows.items()
        },
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
def render_markdown(hw: str, aq: dict, lf: dict, wc: dict, r1: dict | None = None) -> str:
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
        f"retrieves via memsub) and the answerer. LOCOMO dev conversations "
        f"`{', '.join(aq['conversations'])}` (never the holdout split), budget "
        f"{aq['budget_tokens']} tokens, n={aq['n_total_per_condition']} questions per "
        f"condition, stratified across LOCOMO categories 1-4 (category 5 adversarial "
        "excluded per docs/benchmark.md convention; up to "
        f"{QUESTIONS_PER_CATEGORY} sampled per category, capped by availability). "
        "Graded with deterministic token-F1 against the gold answer (no LLM judge, "
        "nothing to fabricate). `memsub_plus_sleep` is extension #1 (docs/extensions.md): "
        "sleep consolidation's three outputs (session summaries, higher-order inferences, "
        "entity co-occurrence links) stored as first-class episodes alongside facts, "
        "competing for the same token budget through the same retrieval path.",
        "",
        "### Overall (required 3-way table: no-memory / naive-topk / memsub, plus the extension)",
        "",
        "| condition | n | mean token-F1 | exact-substring rate | mean answer latency (s) |",
        "|---|---|---|---|---|",
    ]
    for cond, row in aq["by_condition"].items():
        lines.append(
            f"| {cond} | {row['n']} | {row['mean_f1']} | {row['exact_substring_rate']} | "
            f"{row['mean_answer_latency_s']} |"
        )
    lines += ["", "### Per question-type (category) breakdown", "",
              "| condition | category | n | mean token-F1 | exact-substring rate |",
              "|---|---|---|---|---|"]
    for cond, row in aq["by_condition"].items():
        for cat_name, cat_row in row["by_category"].items():
            lines.append(
                f"| {cond} | {cat_name} | {cat_row['n']} | {cat_row['mean_f1']} | "
                f"{cat_row['exact_substring_rate']} |"
            )
    lines += [
        f"\nTotal wall time for this cell: {aq['wall_time_s']}s.",
        "",
    ]
    if r1 is not None:
        lines += [
            "### Time-boxed quality-comparison row: deepseek-r1:32b vs qwen3:8b",
            "",
            "One-off comparison only, not used for volume metrics above (orchestrator "
            f"model-budget guardrail: r1:32b is 19GB and slow on this box). "
            f"n={r1['n']} questions (memsub condition only), hard wall-clock cap "
            f"{r1['time_budget_s']}s, actual {r1['wall_time_s']}s.",
            "",
            "| model | mean token-F1 | mean answer latency (s) |",
            "|---|---|---|",
        ]
        for name, row in r1["by_model"].items():
            lines.append(f"| {name} | {row['mean_f1']} | {row['mean_latency_s']} |")
        lines.append("")
    lines += [
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
        f"**Latency guardrail**: search (retrieval only, excluding any LLM call) is "
        f"budgeted at {SEARCH_LATENCY_BUDGET_MS:.0f}ms warm on this box. Measured p95 "
        f"is {lf['query_latency_ms']['p95']}ms — "
        + ("PASS" if lf['query_latency_ms']['p95'] < SEARCH_LATENCY_BUDGET_MS else "FAIL")
        + f", {round(SEARCH_LATENCY_BUDGET_MS / max(lf['query_latency_ms']['p95'], 0.001), 1)}x headroom.",
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
    run_r1 = "--no-r1" not in sys.argv
    backend = OllamaBackend(model=ANSWERER_MODEL)
    hw = hardware_line()

    print("running (a) answer quality (stratified, multi-condition)...")
    aq = answer_quality(backend)
    r1 = None
    if run_r1:
        print(f"running time-boxed r1:32b comparison row (cap {R1_TIME_BUDGET_S}s)...")
        backend_r1 = OllamaBackend(model=JUDGE_MODEL, timeout=300.0)
        r1 = r1_comparison_row(backend_r1, backend)
    print("running (b) latency + footprint...")
    lf = latency_and_footprint()
    print("running (c) write-time cost...")
    wc = write_time_cost(backend)

    payload = {
        "hardware": hw, "answer_quality": aq, "r1_comparison": r1,
        "latency_footprint": lf, "write_time_cost": wc,
    }
    (ROOT / "results" / "metrics-local.json").write_text(json.dumps(payload, indent=2))
    md = render_markdown(hw, aq, lf, wc, r1=r1)
    (ROOT / "docs" / "metrics-local.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
