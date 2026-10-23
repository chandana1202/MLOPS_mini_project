import asyncio, json, os
from fastapi import FastAPI, Request

app = FastAPI()
DELAY = float(os.getenv("STUB_DELAY_S", "1.5"))
ROUTE = {"A": "CAT_A_CENTRAL", "B": "CAT_B_STATE", "NONE": "NO_CLEARANCE"}


def extract(req):
    out = {k: req.get(k) for k in ("sector", "capacity", "capacity_unit", "area_ha", "state",
                                    "distance_to_protected_area_km", "protected_area_type")}
    out["uncertain"] = []
    return out


def reason(data):
    ent, ps = data["request_entities"], data["retrieved_passages"]
    rows = [p for p in ps if p["type"] == "schedule_row"]
    gcs = [p for p in ps if p["type"] == "general_condition"]
    if not rows:
        return {"action": "tool", "tool": "search_corpus",
                "args": {"query": ent["sector"], "types": ["schedule_row"], "k": 3}}
    if not gcs:
        return {"action": "tool", "tool": "search_corpus",
                "args": {"query": "general condition category location protected area",
                         "types": ["general_condition"]}}
    cat = rows[0].get("meta", {}).get("category_default", "B")
    d, area = ent.get("distance_to_protected_area_km"), ent.get("protected_area_type")
    if cat == "B" and area not in (None, "none") and d is not None and d <= 10:
        cat = "A"
    return {"action": "final", "route": ROUTE.get(cat, "CAT_B_STATE"),
            "basis": "Stub response for load testing.",
            "citations": [{"clause_id": p["clause_id"], "quote": p["text"][:120]} for p in (rows[0], gcs[0])],
            "escalation_reason": None}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    system, user = body["messages"][0]["content"], body["messages"][-1]["content"]
    await asyncio.sleep(DELAY)
    out = extract(json.loads(user)) if "extract structured fields" in system.lower() else reason(json.loads(user))
    text = json.dumps(out)
    return {"choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": len(system + user) // 4, "completion_tokens": len(text) // 4}}


@app.get("/health")
def health():
    return {"ok": True}