"""Batch evaluation over eval/scenarios.json against the running API.
   python -m eval.run --label baseline"""
import argparse
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from agents.core import _json
from agents.llm import get_llm

API = "http://localhost:8000"
JUDGE = ("You are a strict evaluator of a regulatory screening answer. You get the request fields, the "
         "retrieved passages and the answer (route + basis). Return ONLY JSON "
         '{"groundedness": <0..1: fraction of factual claims in the basis directly supported by the passages>, '
         '"faithfulness": <1 if the route follows from the passages and request fields without contradicting '
         "either, else 0>}.")


def norm(s):
    return re.sub(r"\s+", " ", s).strip().lower()


def http(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def run_one(sc):
    code, resp = http("POST", "/runs", sc["request"])
    if code == 422:
        return "REJECTED", None
    if code != 202:
        return f"HTTP_{code}", None
    t0 = time.time()
    while time.time() - t0 < 180:
        time.sleep(2)
        _, s = http("GET", f"/runs/{resp['run_id']}")
        if s.get("status") == "finished":
            return s["result"]["route"], s["result"]
        if s.get("status") == "failed":
            return "FAILED", None
    return "TIMEOUT", None


def wait_breaker_closed():
    for _ in range(40):
        _, b = http("GET", "/breaker")
        if not b.get("open"):
            return
        time.sleep(5)


def run_one_robust(sc):
    """Provider outages are invalid samples, not answers: wait and retry, never score them."""
    for attempt in range(3):
        wait_breaker_closed()
        outcome, res = run_one(sc)
        if res and res.get("escalation_reason") == "provider_unavailable":
            print(f"   provider unavailable ({str(res.get('error'))[:150]}); "
                  f"waiting 60 s, retry {attempt + 1}/3", flush=True)
            time.sleep(60)
            continue
        return outcome, res
    return "PROVIDER_UNAVAILABLE", None


def judge(llm, sc, res):
    ctx = {k: v["text"][:1500] for k, v in res.get("passages", {}).items()}
    user = json.dumps({"request": sc["request"], "passages": ctx,
                       "answer": {"route": res["route"], "basis": res["basis"]}})
    for attempt in range(3):
        try:
            j = _json(llm.complete(JUDGE, user, role="judge", timeout=30).text)
            return float(j["groundedness"]), float(j["faithfulness"])
        except Exception as e:
            print(f"   judge attempt {attempt + 1} failed: {type(e).__name__}: {str(e)[:80]}", flush=True)
            time.sleep(5 * (attempt + 1))
    return None, None


def avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    ap.add_argument("--scenarios", default="eval/scenarios.json")
    a = ap.parse_args()

    llm, rows = get_llm(), []
    scenarios = json.loads(Path(a.scenarios).read_text(encoding="utf-8"))

    for sc in scenarios:
        print(f"running {sc['id']} ...", flush=True)
        outcome, res = run_one_robust(sc)
        invalid = outcome == "PROVIDER_UNAVAILABLE"
        reason = (res or {}).get("escalation_reason")
        ok = (not invalid and outcome == sc["expect_route"]
              and (not sc.get("expect_reason") or sc["expect_reason"] in (reason or "")))
        row = {"id": sc["id"], "outcome": outcome, "route_ok": ok, "invalid": invalid,
               "attempts": None, "tokens": None, "elapsed_s": None, "cites": 0, "bad_cites": 0,
               "recall": None, "ctx_rel": None, "grounded": None, "faithful": None, "reason": reason}
        if res:
            ps = res.get("passages", {})
            cites = res.get("citations", [])
            bad = [c for c in cites if c["clause_id"] not in ps
                   or norm(c.get("quote", "")) not in norm(ps[c["clause_id"]]["text"])]
            row.update(cites=len(cites), bad_cites=len(bad), attempts=res.get("attempts"),
                       elapsed_s=res.get("elapsed_s"),
                       tokens=sum(u["prompt_tokens"] + u["completion_tokens"] for u in res.get("usage", [])))
            exp = set(sc["expect_cited"])
            if exp and res["route"] != "ESCALATED":
                row["recall"] = len(exp & {c["clause_id"] for c in cites}) / len(exp)
                row["ctx_rel"] = len(exp & set(ps)) / len(ps) if ps else 0.0
                row["grounded"], row["faithful"] = judge(llm, sc, res)
        rows.append(row)
        Path(f"eval/results_{a.label}.partial.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"{row['id']:28} {outcome:14} ok={ok!s:5} attempts={row['attempts']} "
              f"bad_cites={row['bad_cites']} reason={row['reason']}", flush=True)
        time.sleep(25)                                  # stay under the free-tier per-minute limit

    valid = [r for r in rows if not r["invalid"]]
    total_c = sum(r["cites"] for r in valid)
    summary = {"label": a.label,
               "scenarios": len(rows), "invalid_samples": len(rows) - len(valid),
               "route_accuracy": avg([1.0 if r["route_ok"] else 0.0 for r in valid]),
               "citation_recall": avg([r["recall"] for r in valid]),
               "hallucination_rate": round(sum(r["bad_cites"] for r in valid) / total_c, 3) if total_c else None,
               "context_relevance": avg([r["ctx_rel"] for r in valid]),
               "groundedness": avg([r["grounded"] for r in valid]),
               "faithfulness": avg([r["faithful"] for r in valid]),
               "mean_attempts": avg([r["attempts"] for r in valid]),
               "mean_tokens": avg([r["tokens"] for r in valid]),
               "mean_elapsed_s": avg([r["elapsed_s"] for r in valid])}
    print("\n" + json.dumps(summary, indent=2))
    Path(f"eval/results_{a.label}.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()