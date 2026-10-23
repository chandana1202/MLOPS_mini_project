import random
import time
from locust import HttpUser, between, task

BASE = {"sector": "Synthetic organic chemicals industry", "capacity": 10, "capacity_unit": "units",
        "area_ha": 5, "state": "Tamil Nadu"}
BODIES = [{**BASE, "protected_area_type": "protected_areas", "distance_to_protected_area_km": 2},
          {**BASE, "protected_area_type": "none"}]


class Screener(HttpUser):
    wait_time = between(0.5, 1.5)

    @task
    def full_run(self):
        t0 = time.perf_counter()
        with self.client.post("/runs", json=random.choice(BODIES), name="POST /runs",
                              catch_response=True) as r:
            if r.status_code != 202:
                r.failure(f"submit {r.status_code}")
                return
            run_id = r.json()["run_id"]
        status = "timeout"
        while time.perf_counter() - t0 < 120:
            time.sleep(1)
            g = self.client.get(f"/runs/{run_id}", name="GET /runs/{id}")
            status = g.json().get("status")
            if status in ("finished", "failed"):
                break
        self.environment.events.request.fire(
            request_type="RUN", name="submit_to_result",
            response_time=(time.perf_counter() - t0) * 1000, response_length=0,
            exception=None if status == "finished" else Exception(status), context={})