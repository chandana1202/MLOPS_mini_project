from dotenv import load_dotenv
load_dotenv()
from agents.graph import run_graph
from agents.guardrails import filter_output
from agents.llm import LLMError, get_llm
from agents.resilience import ResilientLLM
from retrieval.store import ChromaStore

STORE = ChromaStore()                 # loaded once per worker process
LLM = ResilientLLM(get_llm())


def run_case(request: dict):
    try:
        result = run_graph(request, LLM, STORE)
    except LLMError as e:             # graceful degradation: never a confident answer
        result = {"route": "ESCALATED", "verified": False, "citations": [],
                  "basis": "The language-model provider is unavailable; no determination was made.",
                  "escalation_reason": "provider_unavailable", "error": str(e)}
    return filter_output(result)