from memsub.store import Episode
from memsub.structured import StructuredStore, extract_facts


def _ep(content, session_id="2"):
    return Episode(content=content, run_id="r", session_id=session_id, id="ep1")


def test_extract_completed_and_credits():
    ep = _ep("COMPLETED: 4/5 credits awarded: 80. Tasks: task-2a-foo")
    facts = extract_facts(ep)
    preds = {f.predicate: f.object for f in facts}
    assert preds["claimed_completed"] == "4/5"
    assert preds["credits_awarded"] == "80"
    assert preds["mentions_task"] == "task-2a-foo"


def test_structured_store_query():
    store = StructuredStore()
    store.ingest(_ep("COMPLETED: 3/5 credits awarded: 60.", session_id="1"))
    store.ingest(_ep("COMPLETED: 1/5 credits awarded: 20.", session_id="3"))
    completed = store.query(predicate="claimed_completed")
    assert len(completed) == 2
    s1 = store.query(subject="1", predicate="claimed_completed")
    assert s1[0].object == "3/5"


def test_no_facts_when_no_pattern():
    assert extract_facts(_ep("just some prose with no markers")) == []
