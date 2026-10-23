import os
from redis import Redis
from rq import Queue
from rq.worker import SimpleWorker
import worker.tasks  # noqa: F401  (loads embedding model + LLM client once, at startup)

conn = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379"))
SimpleWorker([Queue("runs", connection=conn)], connection=conn).work()