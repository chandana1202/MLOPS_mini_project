import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, ValidationError, model_validator
from redis import Redis
from rq import Queue
from rq.job import Job

from agents.guardrails import check_request

redis = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379"))
queue = Queue("runs", connection=redis)
app = FastAPI(title="EIA screening")

# MUST match the "areas" values in the General Condition rule in clauses.jsonl
AreaType = Literal["protected_areas", "none"]


class ProjectRequest(BaseModel):
    sector: str = Field(min_length=3, max_length=200)
    capacity: float = Field(gt=0)
    capacity_unit: str = Field(min_length=1, max_length=30)
    area_ha: float = Field(gt=0, lt=1_000_000)
    state: str = Field(min_length=2, max_length=60)
    protected_area_type: AreaType                      # required: no silent default
    distance_to_protected_area_km: Optional[float] = Field(default=None, ge=0, le=20000)
    description: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def cross_field(self):
        if self.protected_area_type == "none" and self.distance_to_protected_area_km is not None:
            raise ValueError("distance given but protected_area_type is 'none'")
        return self


class Feedback(BaseModel):
    satisfactory: bool
    reason: str = Field(default="", max_length=500)


def quarantine(body, errors):
    rec = {"at": datetime.now(timezone.utc).isoformat(), "body": body, "errors": errors}
    redis.rpush("quarantine", json.dumps(rec))


@app.get("/", include_in_schema=False)
def index():
    p = Path("web/index.html")
    if not p.exists():
        raise HTTPException(404, "web/index.html not found")
    return FileResponse(p, media_type="text/html; charset=utf-8")


@app.post("/runs", status_code=202)
def submit(body: dict):
    # 1. guardrails: injection / toxicity scan on every string field, before any agent starts
    g = check_request(body)
    if g:
        quarantine(body, g)
        raise HTTPException(422, {"rejected": True, "guardrail": True, "errors": g})
    # 2. data contract: schema, range, categorical and cross-field validation
    try:
        req = ProjectRequest(**body)
    except ValidationError as e:
        errs = [{"field": ".".join(map(str, x["loc"])), "msg": x["msg"]} for x in e.errors()]
        quarantine(body, errs)
        raise HTTPException(422, {"rejected": True, "errors": errs})
    # 3. only now is a run created
    run_id = str(uuid.uuid4())
    queue.enqueue("worker.tasks.run_case", req.model_dump(), job_id=run_id,
                  job_timeout=180, result_ttl=86400)
    redis.incr("metrics:runs_submitted")
    return {"run_id": run_id, "status": "queued"}


@app.get("/runs/{run_id}")
def status(run_id: str):
    try:
        job = Job.fetch(run_id, connection=redis)
    except Exception:
        raise HTTPException(404, "unknown run")
    s = job.get_status()
    out = {"run_id": run_id, "status": s}
    if s == "finished":
        out["result"] = job.result
    elif s == "failed":
        out["error"] = "run failed; not a confident answer"
        out["detail"] = (job.exc_info or "").strip().splitlines()[-1:]
    return JSONResponse(out, media_type="application/json; charset=utf-8")


@app.post("/runs/{run_id}/feedback")
def feedback(run_id: str, fb: Feedback):
    rec = {"run_id": run_id, "satisfactory": fb.satisfactory, "reason": fb.reason,
           "at": datetime.now(timezone.utc).isoformat()}
    redis.rpush("feedback", json.dumps(rec))
    return {"saved": True}


@app.get("/feedback/summary")
def feedback_summary():
    items = [json.loads(x) for x in redis.lrange("feedback", 0, -1)]
    ok = sum(1 for i in items if i["satisfactory"])
    return {"total": len(items), "satisfactory": ok, "unsatisfactory": len(items) - ok,
            "recent_reasons": [i["reason"] for i in items if not i["satisfactory"] and i["reason"]][-10:]}


@app.get("/quarantine")
def quarantined():
    return [json.loads(x) for x in redis.lrange("quarantine", 0, 49)]


@app.post("/demo/llm_down")
def llm_down(on: bool = True):
    """Demo item 8: simulate a provider outage (the worker's LLM wrapper checks this flag)."""
    if on:
        redis.set("demo:llm_down", "1")
    else:
        redis.delete("demo:llm_down")
    return {"llm_down": on}


@app.get("/breaker")
def breaker():
    return {"open": bool(redis.exists("breaker:open")),
            "failures": int(redis.get("breaker:fails") or 0),
            "llm_retries_total": int(redis.get("metrics:llm_retries") or 0)}


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    """Prometheus text format: counters plus index freshness (D3)."""
    lines = [
        f"eia_runs_submitted_total {int(redis.get('metrics:runs_submitted') or 0)}",
        f"eia_llm_retries_total {int(redis.get('metrics:llm_retries') or 0)}",
        f"eia_breaker_open {1 if redis.exists('breaker:open') else 0}",
        f"eia_quarantined_total {redis.llen('quarantine')}",
        f"eia_queue_depth {len(queue)}",
        f"eia_feedback_total {redis.llen('feedback')}",
    ]
    for p in ("/data/index_meta.json", "index_meta.json"):
        if Path(p).exists():
            m = json.loads(Path(p).read_text(encoding="utf-8"))
            lines += [f"eia_index_chunks_total {m.get('chunks_total', 0)}",
                      f"eia_index_chunks_per_second {m.get('chunks_per_s', 0)}",
                      f"eia_index_build_seconds {m.get('seconds', 0)}"]
            break
    return "\n".join(lines) + "\n"


@app.get("/health")
def health():
    redis.ping()
    return {"ok": True}