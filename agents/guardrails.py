"""Input and output guardrails (L4). Pure Python so the API image stays light."""
import re

INJECTION = [
    r"ignore (all |any |the )?(previous|prior|above) (instructions|rules|prompts?)",
    r"disregard (all |any |the )?(previous|prior|above|your)",
    r"system prompt", r"you are now", r"act as (an?|the) ", r"developer mode", r"jailbreak",
    r"override", r"reveal (your|the) (prompt|instructions)",
    r"(classify|route|treat|mark|set) (this|it|the project|the category)[^.]{0,30}as (category|cat)",
    r"</?(system|assistant|user)>",
]
TOXIC = ["idiot", "moron", "stupid", "shut up", "hate you", "kill you"]   # extend as needed
FORBIDDEN_OUTPUT = [
    r"clearance (is|has been|was) (granted|approved|issued)",
    r"\bapproved\b", r"\bgranted\b",
    r"you (may|can) (proceed|start|begin)", r"no further (approval|clearance)",
]


def scan_text(text):
    t = text.lower()
    out = [f"prompt_injection_suspected:{p}" for p in INJECTION if re.search(p, t)]
    out += [f"toxic_language:{w}" for w in TOXIC if w in t]
    return out


def check_request(body):
    """Scan every string field of the raw body; returns field-level errors (empty = clean)."""
    errs = []
    for k, v in (body or {}).items():
        if isinstance(v, str):
            for r in scan_text(v):
                errs.append({"field": k, "msg": r.split(":")[0]})
    return errs


def filter_output(result):
    """Never issue a determination we are not authorised to make."""
    text = f"{result.get('basis', '')} {result.get('escalation_reason') or ''}".lower()
    hit = [p for p in FORBIDDEN_OUTPUT if re.search(p, text)] + [w for w in TOXIC if w in text]
    if hit:
        return {**result, "route": "ESCALATED", "verified": False,
                "escalation_reason": "output_filter_blocked",
                "basis": "The generated opinion was blocked by the output filter.",
                "citations": []}
    return result