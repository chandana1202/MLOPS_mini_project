"""Cost per run / per 100 runs, read from the per-step `usage` of a finished run.

    python -m eval.cost                       # submits one request to the running API, then prints the cost
    python -m eval.cost --run-id <id>         # reads an existing finished run instead
    python -m eval.cost --mean-tokens 4627    # rough estimate only (needs PRICE_* in .env, assumes 85/15 in/out)

Prices come from PRICE_IN_PER_1K / PRICE_OUT_PER_1K in .env (USD per 1,000 tokens).
Providers quote per 1M tokens: divide by 1,000 (e.g. $0.50 / 1M  ->  0.0005).
"""
import argparse
import json
import os
import time
import urllib.request
from collections import defaultdict

from dotenv import load_dotenv
load_dotenv()

API = os.getenv("API_URL", "http://localhost:8000")
SAMPLE = {"sector": "Synthetic organic chemicals industry", "capacity": 10, "capacity_unit": "units",
          "area_ha": 5, "state": "Tamil Nadu", "protected_area_type": "protected_areas",
          "distance_to_protected_area_km": 2}


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def get_result(run_id):
    for _ in range(90):
        s = call("GET", f"/runs/{run_id}")
        if s.get("status") == "finished":
            return s["result"]
        if s.get("status") == "failed":
            raise SystemExit("run failed")
        time.sleep(2)
    raise SystemExit("timed out waiting for the run")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id")
    ap.add_argument("--mean-tokens", type=float)
    a = ap.parse_args()
    pin, pout = float(os.getenv("PRICE_IN_PER_1K", "0")), float(os.getenv("PRICE_OUT_PER_1K", "0"))
    if pin == 0 and pout == 0:
        print("WARNING: PRICE_IN_PER_1K / PRICE_OUT_PER_1K are 0 in .env, so every cost will print as 0.")

    if a.mean_tokens:
        c = a.mean_tokens / 1000 * (0.85 * pin + 0.15 * pout)
        print(f"rough: {a.mean_tokens:.0f} tokens/run ~ ${c:.5f}/run ~ ${c * 100:.3f} per 100 runs (85/15 split assumed)")
        return

    run_id = a.run_id or call("POST", "/runs", SAMPLE)["run_id"]
    res = get_result(run_id)
    steps = res.get("usage", [])
    by = defaultdict(lambda: [0, 0, 0.0])
    print(f"{'step':14}{'prompt':>8}{'completion':>12}{'cost USD':>12}")
    for u in steps:
        # the cost field was computed by the worker with the prices in ITS environment
        cost = u.get("cost", u["prompt_tokens"] / 1000 * pin + u["completion_tokens"] / 1000 * pout)
        by[u["step"]][0] += u["prompt_tokens"]
        by[u["step"]][1] += u["completion_tokens"]
        by[u["step"]][2] += cost
        print(f"{u['step']:14}{u['prompt_tokens']:>8}{u['completion_tokens']:>12}{cost:>12.6f}")
    tp = sum(v[0] for v in by.values())
    tc = sum(v[1] for v in by.values())
    total = sum(v[2] for v in by.values())
    print(f"{'TOTAL':14}{tp:>8}{tc:>12}{total:>12.6f}")
    print(f"\nmodel: {steps[0]['model'] if steps else '?'}   route: {res['route']}   attempts: {res.get('attempts')}")
    print(f"cost per run  : ${total:.6f}")
    print(f"cost per 100  : ${total * 100:.4f}")
    if by:
        top = max(by, key=lambda k: by[k][2])
        print(f"biggest step  : {top} ({by[top][2] / total * 100:.0f}% of cost)" if total else "biggest step  : n/a (cost is 0)")


if __name__ == "__main__":
    main()