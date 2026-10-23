\# SLO (declared before load testing)



\*\*p95 submission-to-final-result latency <= 15 s at 10 concurrent users, with the LLM stubbed (1.5 s per call), error rate < 1%.\*\*



Justification: screening happens before any assessment, and the decision it informs is measured in months

(Category A vs B). A user waiting for a screening opinion tolerates tens of seconds but not minutes. A run makes about

4 model calls plus retrieval, so \~7 s is the unloaded floor. 15 s leaves roughly 2x headroom for queueing.

