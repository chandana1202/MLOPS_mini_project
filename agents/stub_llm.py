"""Deterministic policy stub for offline wiring tests. NOT the load-test stub (Member B's
fixed-response service). It reads `context` (structured state) instead of parsing prompts."""
import json
import os
import time

from agents.llm import LLMClient, LLMResponse

ROUTE = {"A": "CAT_A_CENTRAL", "B": "CAT_B_STATE", "NONE": "NO_CLEARANCE"}


class StubLLM(LLMClient):
    def complete(self, system, user, *, role="generic", context=None, timeout=30.0):
        t0 = time.monotonic()
        if role == "extract":
            out = self._extract(json.loads(user))
        elif role == "reason":
            out = self._reason(context or {})
        else:
            out = {"supported": True}
        time.sleep(float(os.getenv("STUB_DELAY_S", "0")))
        text = json.dumps(out)
        return LLMResponse(text, len(system + user) // 4, len(text) // 4, "stub",
                           time.monotonic() - t0)

    @staticmethod
    def _extract(req):
        e = {k: req.get(k) for k in ("sector", "capacity", "capacity_unit", "area_ha", "state",
                                      "distance_to_protected_area_km", "protected_area_type")}
        e["uncertain"] = (["proximity"] if e["protected_area_type"]
                          and e["distance_to_protected_area_km"] is None else [])
        return e

    @staticmethod
    def _reason(c):
        ent, got = c["entities"], c["retrieved"]
        rows = [p for p in got if p["type"] == "schedule_row" and p["score"] >= c["min_score"]]
        gcs = [p for p in got if p["type"] == "general_condition"]
        if not got:
            return {"action": "tool", "tool": "search_corpus", "args": {"query": ent["sector"]}}
        if not rows:
            return {"action": "final", "route": "ESCALATED", "basis": "No matching Schedule row.",
                    "citations": [], "escalation_reason": "sector_not_in_corpus"}
        if not gcs:
            return {"action": "tool", "tool": "search_corpus",
                    "args": {"query": "general condition category location protected area",
                             "types": ["general_condition"]}}
        row, gc = rows[0], gcs[0]
        cat, applied = row["meta"]["category_default"], False
        d, area = ent.get("distance_to_protected_area_km"), ent.get("protected_area_type")
        for r in gc["meta"].get("rules", []):
            if cat == r["from"] and d is not None and d <= r["max_distance_km"] and area in r["areas"]:
                cat, applied = r["to"], True
        cites = [{"clause_id": row["clause_id"], "quote": row["text"][:80]}]
        if applied or row["meta"]["category_default"] == "B":
            cites.append({"clause_id": gc["clause_id"], "quote": gc["text"][:80]})
        if os.getenv("STUB_BAD_CITATION") == "1" and c["strategy"] == "default":
            cites[0]["quote"] = "this sentence appears in no retrieved passage"
        return {"action": "final", "route": ROUTE[cat], "citations": cites, "escalation_reason": None,
                "basis": f"Schedule default {row['meta']['category_default']}; "
                         f"General Condition {'applied' if applied else 'not triggered'}."}
