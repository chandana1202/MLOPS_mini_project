import sys
from retrieval.store import ChromaStore

for p in ChromaStore().search(sys.argv[1], k=6):
    print(
        round(p["score"], 3),
        p["type"],
        p["clause_id"],
        p["text"][:70].replace("\n", " ")
    )
