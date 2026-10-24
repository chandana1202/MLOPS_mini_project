# Runbook

Services: `redis` (queue + circuit-breaker state), `indexer` (one-shot, builds the Chroma index), `worker` (runs the agent), `api` (FastAPI on :8000). Load test adds `llm-stub` (fake provider on :9000).

## 0. Before anything else
```bash
docker info                      # must succeed; if not, start Docker (incident 1)
cp .env.example .env             # first time only, then fill in the LLM_* values
docker compose config -q         # validates the compose file and .env
```

## Start / stop
```bash
docker compose up -d --build     # redis -> indexer (exits 0) -> worker + api
docker compose ps                # api should become "healthy"
curl localhost:8000/health
docker compose down              # stop; add -v to also delete the index volume
```
Load-test stack (stubbed LLM, 1.5 s per call):
```bash
docker compose -f docker-compose.yml -f docker-compose.loadtest.yml up -d --build
```

## Smoke test
```bash
curl -s -X POST localhost:8000/runs -H 'Content-Type: application/json' \
  -d '{"sector":"Synthetic organic chemicals industry","capacity":10,"capacity_unit":"units","area_ha":5,"state":"Tamil Nadu","protected_area_type":"protected_areas","distance_to_protected_area_km":2}'
# -> 202 {"run_id": "..."}      then:   curl -s localhost:8000/runs/<run_id>
```
Or open `http://localhost:8000/` for the web client.

## Symptoms and fixes

| Symptom | Likely cause | What to do |
|---|---|---|
| `Cannot connect to the Docker daemon` | Engine stopped (incident 1) | Start Docker, `docker info`, rebuild |
| A container runs the wrong command | Shared image name (incident 2) | `docker compose images` must show `eia-indexer`, `eia-worker`, `eia-api`; rebuild with `--build` |
| Every run ends `failed` | Worker cannot find `run_case` (incident 4) | `docker compose exec worker python -c "from worker.tasks import run_case"`; restore the file from git, restart worker |
| Normal requests all `ESCALATED` / `proximity_uncertain` | `"none"` handling (incident 3) | Confirm the request uses `protected_area_type: "none"` and no distance; re-run `s06` |
| Runs `ESCALATED` with `provider_unavailable` | Provider outage or rate limit, breaker open | `curl localhost:8000/breaker`. Wait for `open: false` (reset is `BREAKER_RESET_S`, default 30 s). Check the key and `LLM_BASE_URL`. Re-run, do not score these |
| Run hangs, then `max_steps_exceeded` | Agent hit `MAX_STEPS` / `MAX_TOOL_CALLS` (default 8) | Expected for out-of-corpus sectors (s08). Raise limits only if a valid case needs it |
| Run `ESCALATED` with `unparseable_model_output` | Model returned non-JSON twice (incident 6) | Re-run once; if repeatable, the prompt needs work |
| Cost shows 0 | `PRICE_*` unset in the **worker's** `.env` | Set the prices, then `docker compose up -d --force-recreate worker` |
| `422` on submit | Input validation rejected the request | Check field names, negative numbers, conflicting fields |
| Retrieval returns nothing | Index missing or empty | `docker compose run --rm indexer`, then `python check_state.py` |

## Simulating a provider outage (demo)
```bash
docker compose exec redis redis-cli set demo:llm_down 1      # every LLM call now fails
docker compose exec redis redis-cli del demo:llm_down        # recover
docker compose exec redis redis-cli del breaker:open breaker:fails   # force-close the breaker
```
Expected: runs return `ESCALATED` / `provider_unavailable`, never a confident answer.

## Running the evaluation
```bash
python -m eval.run --label baseline          # real provider; takes ~12 scenarios x 25 s pacing
```
Results: `eval/results_<label>.json` (`summary` + per-scenario `rows`). Check `invalid_samples` is 0 before trusting a summary.

## Cost check
```bash
python -m eval.cost                          # one request, per-step cost, cost per 100 runs
```

## Configuration that matters
| Variable | Default | Effect |
|---|---|---|
| `LLM_PROVIDER` | `stub` | `stub` or `openai_compat` |
| `PRICE_IN_PER_1K` / `PRICE_OUT_PER_1K` | `0` | USD per 1k tokens, used for the `cost` field |
| `LLM_RETRIES` / `LLM_TIMEOUT_S` | `3` / `30` | Per-call retry count and timeout |
| `BREAKER_THRESHOLD` / `BREAKER_RESET_S` | `3` / `30` | Failures before opening / seconds open |
| `MAX_STEPS` / `MAX_TOOL_CALLS` / `RUN_TIMEOUT_S` | `8` / `8` / `60` | Agent limits |
| `MIN_RETRIEVAL_SCORE` | `0.25` | Retrieval score cutoff |
| `REASONER_PROMPT` | `reasoner_v1` | Prompt file in `prompts/` |

## SLO
See `docs/slo.md`: p95 submission-to-result <= 15 s at 10 concurrent users with the LLM stubbed, error rate < 1%.