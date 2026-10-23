import json, os, uuid
from datetime import datetime, timezone
from typing import Literal, Optional
from fastapi import FastAPI, HTTPException
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
    protected_area_type: AreaType 
    distance_to_protected_area_km: Optional[float] = Field(default=None, ge=0, le=20000)
    description: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def cross_field(self):
        if self.protected_area_type == "none" and self.distance_to_protected_area_km is not None:
            raise ValueError("distance given but protected_area_type is 'none'")
        return self


def quarantine(body, errors):
    rec = {"at": datetime.now(timezone.utc).isoformat(), "body": body, "errors": errors}
    redis.rpush("quarantine", json.dumps(rec))


@app.post("/runs", status_code=202)
def submit(body: dict):
    g = check_request(body)
    if g:
        quarantine(body, g)
        raise HTTPException(422, {"rejected": True, "guardrail": True, "errors": g})
    try:
        req = ProjectRequest(**body)
    except ValidationError as e:
        errs = [{"field": ".".join(map(str, x["loc"])), "msg": x["msg"]} for x in e.errors()]
        quarantine(body, errs)
        raise HTTPException(422, {"rejected": True, "errors": errs})
    run_id = str(uuid.uuid4())
    queue.enqueue("worker.tasks.run_case", req.model_dump(), job_id=run_id,
                  job_timeout=180, result_ttl=86400)
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
    return out


@app.get("/quarantine")
def quarantined():
    return [json.loads(x) for x in redis.lrange("quarantine", 0, 49)]


@app.get("/health")
def health():
    redis.ping()
    return {"ok": True}
@app.post("/demo/llm_down")
def llm_down(on: bool = True):
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