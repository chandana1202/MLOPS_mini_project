import json, os, subprocess
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

def ok(flag, label, extra=""):
    print(("[x] " if flag else "[ ] ") + label + (f"   {extra}" if extra else ""))

def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, shell=True).stdout.strip()
    except Exception:
        return ""

print("=== A. Agent and retrieval ===")
ok(Path("agents/core.py").exists(), "agent core")
ok(Path("tests/test_agent.py").exists(), "offline tests")

cl = Path("corpus/curated/clauses.jsonl")
rows = [json.loads(l) for l in cl.read_text(encoding="utf-8").splitlines() if l.strip()] if cl.exists() else []
sched = [r for r in rows if r["type"] == "schedule_row"]
gc = [r for r in rows if r["type"] == "general_condition"]
ok(len(sched) >= 1, "curated Schedule rows", f"({len(sched)} rows; want ~5+)")
ok(len(gc) >= 1, "curated General Condition")
if gc:
    rules = gc[0]["meta"].get("rules", [])
    print("    GC rule distance(s):", [r.get("max_distance_km") for r in rules], "(should be 10)")

raw = list(Path("corpus/raw").glob("*.pdf"))
ok(len(raw) >= 1, "PDFs in corpus/raw", f"({len(raw)} files: {[p.name for p in raw]})")
ok(Path(".chroma").exists(), "index built (.chroma)")
ok(Path("eval").exists() and any(Path("eval").glob("*.json*")), "eval scenarios file")

print("\n=== B. Real LLM ===")
prov = os.getenv("LLM_PROVIDER", "stub")
ok(prov != "stub", "LLM_PROVIDER is not stub", f"(now: {prov}, model: {os.getenv('LLM_MODEL')})")
dbg = sh('findstr /s /n "DEBUG LLM" agents\\*.py')
ok(not dbg, "debug prints removed", dbg[:120])

print("\n=== C. Service ===")
ok(Path("api/main.py").exists(), "api/main.py (FastAPI)")
ok(Path("worker/tasks.py").exists(), "worker/tasks.py")
ok(Path("Dockerfile").exists(), "Dockerfile")
ok(Path("docker-compose.yml").exists(), "docker-compose.yml")
ok(Path(".dockerignore").exists(), ".dockerignore")
print("    docker:", sh("docker --version") or "NOT INSTALLED / not on PATH")
print("    running containers:", sh('docker ps --format "{{.Names}}"') or "none")

print("\n=== D. Git ===")
print(sh("git log --oneline -8"))
print("uncommitted:", sh("git status --short") or "nothing")