# Incident log

Real problems hit while building and evaluating the service. Each entry: what we saw, why it happened, the fix, and what now prevents it.
Fields marked **(fill in)** need the exact date, command or error text from your terminal.

| # | Incident | Area | Severity |
|---|----------|------|----------|
| 1 | Docker engine not running after a crashed build | Build / infra | Blocked all work |
| 2 | Three services collided on one image name | Build / compose | Wrong container ran |
| 3 | `"none"` default escalated every normal request | Agent logic | Wrong answers |
| 4 | `worker.tasks` missing `run_case` after a bad file copy | Deploy | Every run failed |
| 5 | Free-tier provider outage during the eval | Provider / eval | Invalid eval samples |
| 6 | s05 returned unparseable model output | Model behaviour | One scenario escalated |

---

## 1. Docker engine not running after the crashed build
- **Date:** (fill in)
- **Symptom:** `docker compose` commands failed with a connection error to the Docker daemon after an image build crashed part-way.
- **Cause:** The Docker engine had stopped when the build crashed. The CLI was fine; nothing was listening behind it.
- **Fix:** Restart Docker Desktop / the engine, wait until `docker info` succeeds, then rebuild.
- **Prevention:** Run `docker info` before any compose command. The runbook has this as step 0 of "stack will not start".

## 2. Three services collided on one image name
- **Date:** (fill in)
- **Symptom:** A container started but ran the wrong code (for example the `api` service executing the worker command).
- **Cause:** `indexer`, `worker` and `api` are all built from the same `Dockerfile`. With no explicit `image:` names, compose tagged the builds with the same name, and the last build overwrote the others.
- **Fix:** Give every built service its own image name. `docker-compose.yml` now sets `eia-indexer`, `eia-worker` and `eia-api`, and the load-test override adds `eia-stub`.
- **Prevention:** One distinct `image:` per service. After any build, `docker compose images` should list four different names.

## 3. The `"none"` default escalated every normal request
- **Date:** (fill in)
- **Symptom:** Ordinary requests that should have routed to Category A or B came back `ESCALATED` with `proximity_uncertain`.
- **Cause:** `protected_area_type` defaulted to the string `"none"` but the code treated a missing `distance_to_protected_area_km` as "proximity unknown" even when there is no protected area, so the uncertainty guard fired on every request without a distance.
- **Fix:** Distance is only required when `protected_area_type` is something other than `"none"`.
- **Prevention:** Scenarios `s06_no_protected_area` (should route to `CAT_B_STATE`) and `s07_distance_missing` (should escalate) pin both sides of the rule. The load test also sends a `"none"` body on purpose.
- **Note:** `s07` still escalating is the *correct* behaviour: a protected area is named but the distance is missing.

## 4. `worker.tasks` missing `run_case` after a bad file copy
- **Date:** (fill in)
- **Symptom:** Every submitted run ended in `failed`; the worker log showed it could not import or find `run_case` in `worker.tasks`.
- **Cause:** A file copy put the wrong content into the module (an older or truncated `worker/tasks.py`), so the function the queue enqueues by name did not exist.
- **Fix:** Restore `worker/tasks.py` from version control and restart the worker.
- **Prevention:** `python -c "from worker.tasks import run_case"` inside the worker image is now a startup check in the runbook. `git status` before rebuilding shows an unexpected change to the file.

## 5. Free-tier outage during the eval
- **Date:** (fill in)
- **Symptom:** Part-way through an eval run the provider started erroring; retries failed, the circuit breaker opened, and scenarios came back `ESCALATED` with `provider_unavailable`.
- **Cause:** Free-tier rate limit / outage at the provider. The resilience layer did its job: it never produced a confident answer without the model.
- **Fix:** `eval/run.py` now treats a provider outage as an *invalid sample*, not an answer: it waits for the breaker to close, sleeps 60 s, retries up to 3 times, and reports `invalid_samples` separately in the summary. It also sleeps 25 s between scenarios to stay under the per-minute limit.
- **Prevention:** Never score a run with `invalid_samples > 0` without saying so. Re-run the affected scenarios.

## 6. s05 returned unparseable model output
- **Date:** (fill in)
- **Symptom:** In the baseline eval, `s05_25km` ended `ESCALATED` with `unparseable_model_output` after 2 attempts and 5,666 tokens. Expected route was not met (route accuracy 0.917 = 11 of 12).
- **Cause:** The model returned text that was not valid JSON for the reasoner step even after the retry strategy changed.
- **Fix:** None needed for safety: the system escalates instead of guessing. The failure is recorded as a real limitation in the report.
- **Prevention / follow-up:** Candidate fixes are a stricter JSON instruction in the prompt or a lenient JSON extractor. Do not change the prompt without re-running the full eval.