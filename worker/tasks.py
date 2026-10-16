from dotenv import load_dotenv
load_dotenv()
from agents.llm import get_llm
from agents.graph import run_graph
from retrieval.store import ChromaStore

STORE = ChromaStore()   # loaded once per worker process
LLM = get_llm()


def run_case(request: dict):
    return run_graph(request, LLM, STORE)