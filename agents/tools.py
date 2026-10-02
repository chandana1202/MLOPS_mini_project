"""Tools the agent chooses to call. Retrieval is a tool, not a pipeline stage (A1)."""
import os

MIN_SCORE = float(os.getenv("MIN_RETRIEVAL_SCORE", "0.25"))


class Tools:
    specs = {
        "search_corpus": 'args {"query": str, "types": optional list of '
                         '"schedule_row"|"general_condition"|"other", "k": optional int}',
        "get_clause": 'args {"clause_id": str} - fetch one clause by identifier',
        "get_request_fields": "no args - the validated structured fields of this request",
    }

    def __init__(self, store, request):
        self.store, self.request = store, request

    def call(self, name, args):
        try:
            if name == "search_corpus":
                ps = self.store.search(args["query"], int(args.get("k", 4)), args.get("types"))
                return {"passages": ps, "note": f"{len(ps)} passages"}
            if name == "get_clause":
                p = self.store.get_clause(args["clause_id"])
                return {"passages": [p] if p else [], "note": "found" if p else "clause not found"}
            if name == "get_request_fields":
                return {"passages": [], "note": self.request}
            return {"passages": [], "note": f"unknown tool {name!r}"}
        except Exception as e:  # a tool failure is an observation, not a crash
            return {"passages": [], "note": f"tool error: {e}"}
