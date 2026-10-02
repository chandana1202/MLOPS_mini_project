"""Idempotent ingestion CLI (D3).
  python -m retrieval.ingest --raw corpus/raw --curated corpus/curated [--reembed]

Chunking strategy (answers the section-3 difficulty):
  * Schedule rows and the General Condition are CURATED, one chunk each, with typed metadata
    (type=schedule_row | general_condition). The condition is its own chunk, so the agent can fetch
    it by id or by a type-filtered search even though no Schedule row mentions it.
  * Everything else (amendments, OMs) is chunked by page/paragraph window with type=other.
Re-embedding: chunks whose content_hash and embedding_model are unchanged are skipped; --reembed
forces embedding of all chunks whose model differs (or all, if combined with a model change)."""
import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path


def sha(s):
    return hashlib.sha1(s.encode()).hexdigest()[:16]


def chunk_text(text, max_chars=1200, overlap=150):
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if cur and len(cur) + len(p) > max_chars:
            chunks.append(cur)
            cur = cur[-overlap:] + "\n" + p
        else:
            cur = (cur + "\n\n" + p) if cur else p
    if cur:
        chunks.append(cur)
    return chunks


def load_curated(d):
    out = []
    for f in sorted(Path(d).glob("*.jsonl")):
        if f.name.endswith(".example.jsonl"):
            continue
        for line in f.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def load_pdfs(d):
    from pypdf import PdfReader
    out, needs_ocr = [], []
    for f in sorted(Path(d).glob("*.pdf")):
        pages = PdfReader(str(f)).pages
        got = 0
        for pn, page in enumerate(pages, 1):
            text = (page.extract_text() or "").strip()
            for i, c in enumerate(chunk_text(text)):
                got += 1
                out.append({"clause_id": f"{f.stem}:p{pn}:{i}", "type": "other",
                            "source": f.name, "text": c, "meta": {"page": pn}})
        if got == 0:
            needs_ocr.append(f.name)
    return out, needs_ocr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="corpus/raw")
    ap.add_argument("--curated", default="corpus/curated")
    ap.add_argument("--reembed", action="store_true")
    ap.add_argument("--meta-out", default="index_meta.json")
    a = ap.parse_args()

    from retrieval.store import ChromaStore
    store = ChromaStore()
    t0 = time.monotonic()
    curated = load_curated(a.curated)
    pdf_chunks, needs_ocr = load_pdfs(a.raw)
    chunks = curated + pdf_chunks
    seen = set()
    for c in chunks:
        assert c["clause_id"] not in seen, f"duplicate clause_id {c['clause_id']}"
        seen.add(c["clause_id"])
        c["content_hash"] = sha(c["text"])
    have = store.existing([c["clause_id"] for c in chunks]) if chunks else {}
    todo = [c for c in chunks
            if a.reembed or have.get(c["clause_id"], {}).get("content_hash") != c["content_hash"]
            or have.get(c["clause_id"], {}).get("embedding_model") != store.model_name]
    for i in range(0, len(todo), 64):
        store.upsert(todo[i:i + 64])
    secs = time.monotonic() - t0
    meta = {"built_at": datetime.now(timezone.utc).isoformat(), "embedding_model": store.model_name,
            "chunks_total": len(chunks), "chunks_embedded_this_run": len(todo),
            "sources": sorted({c.get("source", "") for c in chunks}), "needs_ocr": needs_ocr,
            "seconds": round(secs, 2), "chunks_per_s": round(len(todo) / secs, 2) if secs else 0}
    Path(a.meta_out).write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
