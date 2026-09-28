"""usage: run_cal.py jev|llm out.json [limit]   (run from the repo dir; python with typesafe-sdk for jev)

llm mode logs each call's `modelUsage` (via `claude --output-format json`), not just the verdict text,
so an alias drift (e.g. "sonnet" resolving to a different model on a future rerun) is visible in the
results file instead of silently invalidating the comparison. This only changes how run_cal.py itself
invokes the CLI, not agent_common.call_claude()."""
import json, subprocess, sys, time, threading
sys.path.insert(0, ".")
import agent_review
from concurrent.futures import ThreadPoolExecutor
SP = __import__("os").path.dirname(__import__("os").path.abspath(__file__)) + "/"
mode, out = sys.argv[1], sys.argv[2]
limit = int(sys.argv[3]) if len(sys.argv) > 3 else None
C = json.load(open(SP+"corpus.json"))[:limit]
tl = threading.local()
if mode == "jev":
    real = agent_review._get_jev_client()     # loads TYPESAFE_API_KEY from .env, builds the real client
    class Rec:
        def system_one(self, *a, **k):
            r = real.system_one(*a, **k); tl.last = r; return r
    agent_review._jev_client = Rec()
elif mode == "llm":
    def call_claude_json(prompt, model="sonnet"):
        # Same command agent_common.call_claude() runs, plus --output-format json so modelUsage is
        # available; `result` is the same text plain stdout would have been.
        proc = subprocess.run(
            ["claude", "--model", model, "-p", prompt, "--output-format", "json"],
            capture_output=True, text=True, timeout=120,
        )
        payload = json.loads(proc.stdout)
        tl.last = payload.get("modelUsage")
        return payload["result"].strip()
    agent_review.call_claude = call_claude_json
def one(job):
    c, rep = job
    t = time.time(); tl.last = None
    if mode == "jev":
        _, verdict, text = agent_review.judge_review_jev(c["lens"], c["review"])
        r = tl.last; extra = {}
        if r is not None:
            extra = dict(p=r.nouls["block"].noul, model=r.model, usage=str(r.usage))
    else:
        _, verdict, text = agent_review.judge_review_llm(c["lens"], c["review"])
        extra = dict(model_usage=tl.last)
    return dict(id=c["id"], rep=rep, verdict=verdict, text=text[:1500] if mode == "jev" else text, secs=round(time.time()-t, 2), **extra)
jobs = [(c, r) for c in C for r in (1, 2, 3)]
with ThreadPoolExecutor(4) as ex:
    res = list(ex.map(one, jobs))
json.dump(res, open(out, "w"), indent=1)
print(mode, len(res), "calls;", sum(1 for r in res if "INTEGRATION ERROR" in r["text"]), "integration errors")
