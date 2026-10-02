"""Agent core: plain-Python nodes shared by the LangGraph graph (graph.py) and run_sync().
Agent 1 = extract (sees raw free text, has NO tools).  Agent 2 = reason (has tools, sees only
structured entities, never the raw description).  Verifier = verify (no tools)."""
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from agents.tools import MIN_SCORE, Tools

ROUTES = ("NO_CLEARANCE", "CAT_B_STATE", "CAT_A_CENTRAL", "ESCALATED")
STRATEGIES = ("default", "modifier_focus", "clause_lookup")
DIRECTIVES = {
    "default": "",
    "modifier_focus": "Previous answer failed verification. Explicitly search for General Conditions, "
                      "notes and amendments that modify the category, then cite exact quotes.",
    "clause_lookup": "Previous attempts failed. Use get_clause on identifiers you have seen and copy "
                     "quotes verbatim; escalate if you still cannot support an answer.",
}
MAX_ATTEMPTS = 3
DISCLAIMER = "Screening opinion only; an accredited consultant makes the determination."


def load_prompt(name):
    return yaml.safe_load((Path(os.getenv("PROMPTS_DIR", "prompts")) / f"{name}.yaml").read_text())


@dataclass
class Limits:
    max_steps: int = int(os.getenv("MAX_STEPS", "8"))
    max_tool_calls: int = int(os.getenv("MAX_TOOL_CALLS", "8"))
    timeout_s: float = float(os.getenv("RUN_TIMEOUT_S", "60"))


@dataclass
class RunState:
    request: dict
    tools: Tools
    limits: Limits
    started: float = field(default_factory=time.monotonic)
    entities: dict = field(default_factory=dict)
    retrieved: dict = field(default_factory=dict)
    trace: list = field(default_factory=list)
    usage: list = field(default_factory=list)
    steps: int = 0
    tool_calls: int = 0
    attempts: int = 0
    strategy: str = "default"
    failures: list = field(default_factory=list)
    last_note: object = None
    draft: dict | None = None
    verification: dict | None = None
    result: dict | None = None
    reasoner_prompt: str = field(default_factory=lambda: os.getenv("REASONER_PROMPT", "reasoner_v1"))


def new_state(request, store, limits=None):
    return RunState(request=request, tools=Tools(store, request), limits=limits or Limits())


def _norm(s):
    return re.sub(r"\s+", " ", s).strip().lower()


def _json(text):
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text or "", re.S)
        try:
            return json.loads(m.group(0)) if m else None
        except Exception:
            return None


def _log(s, kind, **kw):
    s.trace.append({"t": round(time.monotonic() - s.started, 3), "kind": kind, **kw})


def _llm(s, llm, step, system, user, role, context=None):
    r = llm.complete(system, user, role=role, context=context)
    pin, pout = float(os.getenv("PRICE_IN_PER_1K", "0")), float(os.getenv("PRICE_OUT_PER_1K", "0"))
    s.usage.append({"step": step, "model": r.model, "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens, "latency_s": round(r.latency_s, 3),
                    "cost": r.prompt_tokens / 1000 * pin + r.completion_tokens / 1000 * pout})
    return r


def _escalate(s, reason, basis=""):
    s.draft = {"action": "final", "route": "ESCALATED", "basis": basis or reason,
               "citations": [], "escalation_reason": reason}
    s.verification = {"passed": True, "escalation": True, "failures": []}


# ---------------- Agent 1: extractor ----------------
def extract(s, llm):
    p = load_prompt(os.getenv("EXTRACTOR_PROMPT", "extractor_v1"))
    r = _llm(s, llm, "extract", p["system"], json.dumps(s.request), "extract")
    ent = _json(r.text) or {}
    for k, v in s.request.items():           # validated structured fields always win over model output
        if k != "description" and v is not None:
            ent[k] = v
    ent.setdefault("uncertain", [])
    s.entities = ent
    _log(s, "extract", prompt=p["version"], entities=ent)
    if "proximity" in ent["uncertain"] or (ent.get("protected_area_type")
                                           and ent.get("distance_to_protected_area_km") is None):
        _escalate(s, "proximity_uncertain", "Proximity to a protected area could not be determined.")


# ---------------- Agent 2: reasoner (tool loop) ----------------
def reason(s, llm):
    p = load_prompt(s.reasoner_prompt)
    s.draft = None
    while s.draft is None:
        if s.steps >= s.limits.max_steps:
            return _escalate(s, "max_steps_exceeded")
        if time.monotonic() - s.started > s.limits.timeout_s:
            return _escalate(s, "run_timeout")
        s.steps += 1
        passages = [{"clause_id": x["clause_id"], "type": x["type"], "text": x["text"]}
                    for x in s.retrieved.values()]
        user = json.dumps({"request_entities": s.entities, "tools": s.tools.specs,
                           "strategy_directive": DIRECTIVES[s.strategy],
                           "retrieved_passages": passages, "last_tool_note": s.last_note,
                           "prior_verification_failures": s.failures,
                           "steps_used": s.steps - 1, "tool_calls_used": s.tool_calls}, indent=1)
        ctx = {"entities": s.entities, "retrieved": list(s.retrieved.values()),
               "strategy": s.strategy, "min_score": MIN_SCORE}   # used by the stub only
        r = _llm(s, llm, f"reason_{s.steps}", p["system"], user, "reason", ctx)
        act = _json(r.text)
        _log(s, "reason", step=s.steps, prompt=p["version"], action=act)
        if not isinstance(act, dict):
            return _escalate(s, "unparseable_model_output")
        if act.get("action") == "final":
            s.draft = act
        elif act.get("action") == "tool":
            if s.tool_calls >= s.limits.max_tool_calls:
                return _escalate(s, "max_tool_calls_exceeded")
            s.tool_calls += 1
            t0 = time.monotonic()
            out = s.tools.call(act.get("tool"), act.get("args") or {})
            for x in out["passages"]:
                s.retrieved[x["clause_id"]] = x
            s.last_note = out["note"]
            _log(s, "tool", tool=act.get("tool"), args=act.get("args"),
                 passage_ids=[x["clause_id"] for x in out["passages"]],
                 scores=[round(x["score"], 3) for x in out["passages"]],
                 latency_s=round(time.monotonic() - t0, 4))
        else:
            return _escalate(s, "malformed_action")


# ---------------- Verifier ----------------
def verify(s, llm=None):
    d, fails = s.draft, []
    if s.verification and s.verification.get("escalation") and d["escalation_reason"] != "sector_not_in_corpus":
        return  # bounds/proximity escalations are already terminal
    if d.get("route") not in ROUTES:
        fails.append("invalid_route")
    elif d["route"] == "ESCALATED":
        if not d.get("escalation_reason"):
            fails.append("escalation_without_reason")
        if d.get("escalation_reason") == "sector_not_in_corpus" and any(
                x["type"] == "schedule_row" and x["score"] >= MIN_SCORE for x in s.retrieved.values()):
            fails.append("escalation_unjustified_matching_row_retrieved")
    else:
        cites = d.get("citations") or []
        if not cites:
            fails.append("no_citations")
        for c in cites:
            p = s.retrieved.get(c.get("clause_id"))
            if p is None:
                fails.append(f"citation_not_retrieved:{c.get('clause_id')}")
            elif not c.get("quote") or _norm(c["quote"]) not in _norm(p["text"]):
                fails.append(f"quote_not_in_passage:{c['clause_id']}")
        cited = {c.get("clause_id") for c in cites}
        rows = [s.retrieved[i] for i in cited if i in s.retrieved and s.retrieved[i]["type"] == "schedule_row"]
        gcs = [x for x in s.retrieved.values() if x["type"] == "general_condition"]
        if rows and not gcs:
            fails.append("modifier_check_missing:no_general_condition_retrieved")
        # deterministic cross-check of location-based upgrades
        dist, area = s.entities.get("distance_to_protected_area_km"), s.entities.get("protected_area_type")
        for g in gcs:
            for rule in g["meta"].get("rules", []):
                hit = (isinstance(rule.get("max_distance_km"), (int, float)) and dist is not None
                       and dist <= rule["max_distance_km"] and area in rule.get("areas", []))
                base = any(r["meta"].get("category_default") == rule["from"] for r in rows)
                if hit and base and d["route"] == "CAT_B_STATE":
                    fails.append("general_condition_applies_but_not_applied")
                if hit and base and d["route"] == "CAT_A_CENTRAL" and g["clause_id"] not in cited:
                    fails.append("general_condition_applied_but_not_cited")
        if not fails and os.getenv("VERIFY_WITH_LLM") == "1" and llm is not None:
            r = _llm(s, llm, "verify", "Does the cited text support the route? JSON {\"supported\":bool}",
                     json.dumps({"draft": d, "passages": [s.retrieved[i]["text"] for i in cited]}), "verify")
            if not (_json(r.text) or {}).get("supported"):
                fails.append("llm_entailment_failed")
    s.verification = {"passed": not fails, "failures": fails,
                      "escalation": d.get("route") == "ESCALATED"}
    _log(s, "verify", attempt=s.attempts, strategy=s.strategy, **s.verification)


def next_after_extract(s):
    return "finalize" if s.draft else "reason"


def next_after_verify(s):
    if s.verification["passed"]:
        return "finalize"
    return "retry" if s.attempts + 1 < MAX_ATTEMPTS else "finalize"


def retry(s):
    s.failures.extend(s.verification["failures"])
    s.attempts += 1
    s.strategy = STRATEGIES[min(s.attempts, len(STRATEGIES) - 1)]
    s.draft, s.verification = None, None


def finalize(s):
    ok = s.verification and s.verification["passed"]
    d = s.draft if ok else {"route": "ESCALATED", "citations": [], "basis": "Verification failed.",
                            "escalation_reason": "verification_failed: " + "; ".join(s.failures + (
                                s.verification or {}).get("failures", []))}
    s.result = {"route": d["route"], "basis": d.get("basis", ""), "citations": d.get("citations", []),
                "escalation_reason": d.get("escalation_reason"), "verified": bool(ok),
                "prompt_versions": {"reasoner": s.reasoner_prompt}, "attempts": s.attempts + 1,
                "steps": s.steps, "tool_calls": s.tool_calls, "usage": s.usage, "trace": s.trace,
                "elapsed_s": round(time.monotonic() - s.started, 3), "disclaimer": DISCLAIMER}


def run_sync(request, llm, store, limits=None):
    s = new_state(request, store, limits)
    extract(s, llm)
    if next_after_extract(s) == "reason":
        while True:
            reason(s, llm)
            verify(s, llm)
            if next_after_verify(s) == "finalize":
                break
            retry(s)
    finalize(s)
    return s.result
