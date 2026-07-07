from memsub.memory import NullMemory, ContextStuffingMemory, RetrievalMemory, build_memory
from memsub.policy import MemoryPolicy
from memsub.store import Episode


def _summary(session_id, text="COMPLETED: 4/5 credits awarded: 80. Tasks: task-a"):
    return Episode(content=text, run_id="r", session_id=session_id, kind="session_summary")


def test_null_memory_is_noop():
    m = NullMemory()
    m.write(_summary("1"))
    r = m.recall("anything")
    assert not r.used_memory
    assert m.size() == 0


def test_context_stuffing_recency_order():
    m = ContextStuffingMemory()
    m.write(_summary("1", "COMPLETED: 3/5 session one"))
    m.write(_summary("2", "COMPLETED: 4/5 session two"))
    r = m.recall("report")
    # newest session should appear first in the injected block
    assert r.text.index("session two") < r.text.index("session one")
    assert r.used_memory


def test_retrieval_memory_end_to_end():
    m = RetrievalMemory(policy=MemoryPolicy(retrieval_k=3))
    m.write(_summary("1", "COMPLETED: 3/5 dataset audit and baseline"))
    m.write(_summary("2", "COMPLETED: 4/5 ablation sweep results"))
    r = m.recall("how did the ablation sweep go for session 2")
    assert r.used_memory
    assert any("ablation" in ep.content for ep in r.episodes)
    assert r.facts  # structured facts surfaced


def test_retrieval_manipulation_signal():
    # recall for a fresh session should retrieve a *prior* session id
    m = RetrievalMemory()
    m.write(_summary("1"))
    m.write(_summary("2"))
    r = m.recall("session 3 tasks")
    prior = [ep.session_id for ep in r.episodes if ep.session_id != "3"]
    assert prior  # retrieved at least one prior session


def test_memory_ignores_non_summary_kinds():
    m = RetrievalMemory()
    m.write(Episode(content="scratch note", run_id="r", session_id="1", kind="scratch"))
    assert m.size() == 0


def test_build_memory_factory():
    assert build_memory("none").name == "none"
    assert build_memory("context_stuffing").name == "context_stuffing"
    assert build_memory("retrieval").name == "retrieval"
