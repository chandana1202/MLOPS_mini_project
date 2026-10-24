# EIA screening agent

A two-agent LLM service that screens a project description and routes it to **Category A (central)**, **Category B (state)**, **no clearance**, or **escalates** to a human. Every answer cites exact clauses from the regulation text, and a verifier checks each quote against the retrieved passages before the answer is returned.

> Screening opinion only. An accredited consultant makes the determination.

## How it works

```
POST /runs --> Redis queue (rq) --> worker --> extract (no tools) --> reason (tools: search, get_clause)
                                                                          |
GET /runs/{id} <-- result  <-- guardrails <-- verify quotes <------------+
```

- **Extractor** reads the raw text, has no tools. **Reasoner** sees only structured entities and can search the clause index. **Verifier** checks that every citation's quote appears in a retrieved passage; on failure the reasoner retries with a different strategy (`default` -> `modifier_focus` -> `clause_lookup`, at most 3 attempts).
- **Retrieval:** ChromaDB with `all-MiniLM-L6-v2` embeddings over `corpus/curated`.
- **Resilience:** per-call timeout, bounded retries with backoff, and a Redis-backed circuit breaker. If the provider is down the answer is `ESCALATED` / `provider_unavailable`, never a guess.
- **Guardrails:** input validation (rejects injection, toxic text, negative values, conflicting fields), output filter, step / tool-call / time budgets.
- **Cost tracking:** each model call records prompt tokens, completion tokens and `cost` (from `PRICE_*_PER_1K`).

## Run it

```bash
cp .env.example .env            # fill LLM_PROVIDER, LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, PRICE_*
docker compose up -d --build
curl localhost:8000/health
```

Open `http://localhost:8000/` for the web client, or use the API:

```bash
curl -s -X POST localhost:8000/runs -H 'Content-Type: application/json' -d '{
  "sector":"Synthetic organic chemicals industry","capacity":10,"capacity_unit":"units",
  "area_ha":5,"state":"Tamil Nadu","protected_area_type":"protected_areas",
  "distance_to_protected_area_km":2}'
curl -s localhost:8000/runs/<run_id>
```

With `LLM_PROVIDER=stub` (the default) everything runs offline with a scripted model. See `docs/runbook.md` for operations and troubleshooting.

## API

| Method | Path | Notes |
|---|---|---|
| POST | `/runs` | `202` with `run_id`; `422` if validation rejects the input |
| GET | `/runs/{id}` | `status`: queued / started / finished / failed; `result` when finished |
| GET | `/breaker` | `{"open": bool}` circuit-breaker state |
| GET | `/health` | liveness |

## Evaluate, load-test, cost

```bash
python -m eval.run --label <name>        # 12 scenarios -> eval/results_<name>.json
python -m eval.cost                      # per-step cost and cost per 100 runs
pytest tests/                            # offline tests (stub model)
```

Load test (stubbed LLM, 1.5 s per call) with Locust, results in `loadtest/`. SLO in `docs/slo.md`.

## Repo layout

| Path | Contents |
|---|---|
| `agents/` | extractor/reasoner/verifier (`core.py`), graph, tools, guardrails, LLM client, resilience |
| `api/` | FastAPI service |
| `worker/` | rq worker and `run_case` |
| `retrieval/` | ingest and Chroma store |
| `corpus/` | raw and curated regulation text |
| `prompts/` | versioned prompts (`reasoner_v1`, `reasoner_v2`, `extractor_v1`) |
| `eval/` | scenarios, runner, saved results |
| `loadtest/` | Locust file and CSV results |
| `stub/` | fake OpenAI-compatible provider for load tests |
| `docs/` | SLO, runbook, incident log |
| `web/` | single-page client |