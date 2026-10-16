"""Vector store adapters. Both return passages as
{"clause_id","type","text","meta":{...},"score":float}."""
import json
import os
import re


class InMemoryStore:
    """Keyword-overlap store for tests only."""

    def __init__(self, chunks):
        self.chunks = {c["clause_id"]: c for c in chunks}

    def search(self, query, k=4, types=None):
        q = set(re.findall(r"\w+", query.lower()))
        out = []
        for c in self.chunks.values():
            if types and c["type"] not in types:
                continue
            blob = c["text"] + " " + " ".join(str(v) for v in c.get("meta", {}).values())
            p = set(re.findall(r"\w+", blob.lower()))
            out.append({**c, "score": len(q & p) / len(q) if q else 0.0})
        return sorted(out, key=lambda x: -x["score"])[:k]

    def get_clause(self, clause_id):
        c = self.chunks.get(clause_id)
        return {**c, "score": 1.0} if c else None


class ChromaStore:
    """Persistent Chroma collection; embeddings computed locally (sentence-transformers).
    id == clause_id, so upserts are idempotent and clause lookup is a direct get."""

    def __init__(self, path=None, model=None, collection="eia"):
        import chromadb
        self.model_name = model or os.getenv("EMBEDDING_MODEL",
                                             "sentence-transformers/all-MiniLM-L6-v2")
        self.client = chromadb.PersistentClient(path=path or os.getenv("CHROMA_PATH", "./.chroma"))
        self.col = self.client.get_or_create_collection(collection, metadata={"hnsw:space": "cosine"})
        self._enc = None
        self.embed(["warmup"])

    def embed(self, texts):
        if self._enc is None:
            from sentence_transformers import SentenceTransformer
            self._enc = SentenceTransformer(self.model_name)
        return self._enc.encode(texts, normalize_embeddings=True).tolist()

    def existing(self, ids):
        got = self.col.get(ids=ids, include=["metadatas"])
        return dict(zip(got["ids"], got["metadatas"]))

    def upsert(self, chunks):
        if not chunks:
            return
        self.col.upsert(
            ids=[c["clause_id"] for c in chunks],
            documents=[c["text"] for c in chunks],
            embeddings=self.embed([c["text"] for c in chunks]),
            metadatas=[{"type": c["type"], "source": c.get("source", ""),
                        "content_hash": c["content_hash"], "embedding_model": self.model_name,
                        "meta_json": json.dumps(c.get("meta", {}))} for c in chunks])

    @staticmethod
    def _passage(cid, doc, md, score):
        return {"clause_id": cid, "type": md["type"], "text": doc,
                "meta": json.loads(md.get("meta_json", "{}")), "score": score}

    def search(self, query, k=4, types=None):
        where = {"type": {"$in": list(types)}} if types else None
        r = self.col.query(query_embeddings=self.embed([query]), n_results=k, where=where)
        return [self._passage(i, d, m, 1 - dist) for i, d, m, dist in
                zip(r["ids"][0], r["documents"][0], r["metadatas"][0], r["distances"][0])]

    def get_clause(self, clause_id):
        r = self.col.get(ids=[clause_id])
        return self._passage(r["ids"][0], r["documents"][0], r["metadatas"][0], 1.0) if r["ids"] else None
