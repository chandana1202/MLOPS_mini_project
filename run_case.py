import json, sys
from dotenv import load_dotenv

load_dotenv()

from agents.llm import get_llm
from agents.graph import run_graph
from retrieval.store import ChromaStore


dist = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0

req = {
    "sector": "Synthetic organic chemicals industry",
    "capacity": 10,
    "capacity_unit": "units",
    "area_ha": 5,
    "state": "Tamil Nadu",
    "distance_to_protected_area_km": dist,
    "protected_area_type": "protected_areas",
    "description": "industrial project near a national park",
}

r = run_graph(req, get_llm(), ChromaStore())

keys = (
    "route",
    "basis",
    "citations",
    "escalation_reason",
    "verified",
    "attempts",
    "steps",
    "tool_calls",
    "elapsed_s",
)

print(json.dumps(r["trace"], indent=1)[:6000])
print("MODEL USED:", r["usage"][0]["model"])