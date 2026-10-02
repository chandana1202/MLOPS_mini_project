"""Offline tests (no network, no chroma). Fixtures are SYNTHETIC placeholders, not legal text.
Replace with real curated clauses + eval set once the corpus is confirmed."""
import os
from agents.core import Limits, run_sync
from agents.stub_llm import StubLLM
from retrieval.store import InMemoryStore

os.environ.setdefault("PROMPTS_DIR", os.path.join(os.path.dirname(__file__), "..", "prompts"))

CHUNKS = [
    {"clause_id": "TEST-SCHED-1", "type": "schedule_row", "source": "fixture",
     "text": "TEST FIXTURE: test_industrial projects fall in Category B in this row.",
     "meta": {"sector": "test_industrial", "category_default": "B"}},
    {"clause_id": "TEST-GC-1", "type": "general_condition", "source": "fixture",
     "text": "TEST FIXTURE: General condition: a Category B project located near a protected area "
             "is treated as Category A (category depends on location).",
     "meta": {"rules": [{"from": "B", "to": "A", "max_distance_km": 5,
                         "areas": ["national_park"]}]}},
]
STORE = InMemoryStore(CHUNKS)


def req(**kw):
    base = {"sector": "test_industrial", "capacity": 10, "capacity_unit": "x", "area_ha": 3,
            "state": "TN", "distance_to_protected_area_km": 2.0,
            "protected_area_type": "national_park", "description": "plant"}
    return {**base, **kw}


def test_section_4_1_upgrades_to_category_a_and_cites_both():
    r = run_sync(req(), StubLLM(), STORE)
    assert r["route"] == "CAT_A_CENTRAL" and r["verified"]
    assert {c["clause_id"] for c in r["citations"]} == {"TEST-SCHED-1", "TEST-GC-1"}


def test_far_from_park_stays_category_b():
    r = run_sync(req(distance_to_protected_area_km=20.0), StubLLM(), STORE)
    assert r["route"] == "CAT_B_STATE" and r["verified"]


def test_out_of_corpus_escalates():
    r = run_sync(req(sector="test_unknown"), StubLLM(), STORE)
    assert r["route"] == "ESCALATED" and r["escalation_reason"] == "sector_not_in_corpus"


def test_uncertain_proximity_escalates_without_tool_calls():
    r = run_sync(req(distance_to_protected_area_km=None), StubLLM(), STORE)
    assert r["route"] == "ESCALATED" and r["escalation_reason"] == "proximity_uncertain"
    assert r["tool_calls"] == 0


def test_verifier_catches_bad_citation_then_recovers(monkeypatch):
    monkeypatch.setenv("STUB_BAD_CITATION", "1")
    r = run_sync(req(), StubLLM(), STORE)
    assert r["attempts"] == 2 and r["verified"] and r["route"] == "CAT_A_CENTRAL"
    assert any(t["kind"] == "verify" and not t["passed"] for t in r["trace"])


def test_tool_budget_escalates():
    r = run_sync(req(), StubLLM(), STORE, Limits(max_tool_calls=0))
    assert r["route"] == "ESCALATED" and r["escalation_reason"] == "max_tool_calls_exceeded"


def test_usage_recorded_per_step():
    r = run_sync(req(), StubLLM(), STORE)
    assert {u["step"] for u in r["usage"]} >= {"extract", "reason_1"}
