"""The diamond review machinery shared by agent_diamond.py and agent_graph.py: four
parallel reviewers, one isolated judge call per review, and the Python-side
aggregation that fails if any category blocks. See NOTES.md / FINDINGS.md for why the
judge prompt, the VERDICT marker and the per-category aggregation are shaped this way."""
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from agent_common import call_claude, get_judge, load_env_key

# Model is per-reviewer so tiering can be tested one role at a time. The judge in
# judge_review() is deliberately left on the default (sonnet) — changing reviewers
# and judge together would make a regression impossible to attribute.
REVIEWERS = {
    "security": {
        "model": "sonnet",
        "instruction": "Review this code for security issues (e.g. injection, unsafe input handling). "
                       "List any problems found, or say 'No issues found' if none.",
    },
    "performance": {
        "model": "sonnet",
        "instruction": "Review this code for performance issues (e.g. inefficient loops, unnecessary work). "
                       "List any problems found, or say 'No issues found' if none.",
    },
    "style": {
        "model": "sonnet",
        "instruction": "Review this code for style issues (e.g. naming, readability, PEP8). "
                       "List any problems found, or say 'No issues found' if none.",
    },
    "test_coverage": {
        "model": "sonnet",
        "instruction": "Review the test file for coverage gaps (e.g. missing edge cases). "
                       "List any gaps found, or say 'No issues found' if none.",
    },
}

def run_reviewer(name: str, config: dict, code: str, tests: str) -> tuple[str, str]:
    prompt = f"{config['instruction']}\n\nCode (solution.py):\n{code}\n\nTests (test_solution.py):\n{tests}"
    result = call_claude(prompt, model=config["model"])
    return name, result

def run_diamond_review(code: str, tests: str) -> dict:
    """Fans out to 4 independent reviewers in parallel (the diamond's split),
    each with a different lens on the same code and tests."""
    results = {}
    with ThreadPoolExecutor(max_workers=len(REVIEWERS)) as executor:
        futures = [
            executor.submit(run_reviewer, name, config, code, tests)
            for name, config in REVIEWERS.items()
        ]
        for future in futures:
            name, verdict = future.result()
            results[name] = verdict
    return results

def _judge_rubric(name: str) -> str:
    """The severity rubric (BLOCK criteria, anti-hedge clause, lane check, OK cases),
    shared verbatim by the LLM judge and the Jev judge so the two differ in substrate
    only, not in wording."""
    return (
        "Severity rubric — BLOCK if the review describes ANY of the following AND "
        f"the defect genuinely belongs to the {name} lens (not a defect the reviewer "
        "is only mentioning about a different lens), even conditionally or hedged "
        "with 'if untrusted input' / 'at scale' / 'in some cases':\n"
        "- A security flaw that would trigger under any input the function's own "
        "signature does not rule out (e.g. no explicit trust boundary, no validation)\n"
        "- An algorithmic complexity defect (e.g. O(n²) or worse) in a function whose "
        "stated purpose is that exact operation, regardless of current test input size\n"
        "- A correctness bug, including tests that encode incorrect behavior as expected\n\n"
        "Do NOT treat a hedge or conditional phrasing ('if...', 'at scale...', "
        "'could be...') as evidence an in-lane issue is unproven or minor — a real "
        "defect described conditionally is still a defect.\n\n"
        "Do NOT block on a defect the review explicitly identifies as belonging to a "
        "different lens (e.g. a security review noting 'this is a correctness bug, "
        f"not a security issue' — if the lens is '{name}', that is an out-of-lane "
        "mention, not a finding in this lens, so it does not block this category; it "
        "will be judged by the lens it actually belongs to).\n\n"
        "OK for genuine style nitpicks, missing docstrings, coverage suggestions for "
        "behavior that already works correctly, or an accurate out-of-lane mention "
        "with no in-lane defect of its own.\n\n"
    )

def judge_review_llm(name: str, review: str) -> tuple[str, str, str]:
    """Judges one review in isolation — this call never sees the other three, so a
    real finding can't be softened by sitting next to clean reports."""
    prompt = (
        "You are a strict, isolated verifier. You have no context on how this code "
        "was written or fixed — you are judging only the review text below.\n\n"
        f"This review's assigned lens is: {name}.\n\n"
        f"Review ({name}):\n{review}\n\n"
        f"{_judge_rubric(name)}"
        "Think through your reasoning first if you need to. Then output your final "
        "verdict on its own last line, in exactly this format and nothing else:\n\n"
        "VERDICT: BLOCK\n"
        "or\n"
        "VERDICT: OK"
    )
    response = call_claude(prompt)
    return name, parse_verdict(response), response

# --- Jev judge ------------------------------------------------------------------
# One holistic yes/no question per review — deliberately NOT split per BLOCK criterion,
# so the swap from the LLM judge changes the substrate and nothing else (the rubric text
# is the same object, see _judge_rubric). A per-criterion split is a separate, later
# experiment. Verdict is BLOCK when the returned probability of "yes, block" reaches the
# threshold. 0.5 is an uncalibrated starting point (the docs suggest validating
# thresholds on your own data), not a measured value.
JEV_BLOCK_THRESHOLD = 0.5

_jev_client = None
_jev_client_lock = threading.Lock()

def _get_jev_client():
    """One shared TypeSafe client (the aggregator judges four reviews in threads). The
    key comes from TYPESAFE_API_KEY in the environment, or failing that a gitignored .env
    (only that one variable is read) — never from code. A failed
    construction is not cached."""
    global _jev_client
    with _jev_client_lock:
        if _jev_client is None:
            import typesafe_sdk
            load_env_key("TYPESAFE_API_KEY")  # gitignored .env fallback; env var wins
            _jev_client = typesafe_sdk.TypeSafeClient()
        return _jev_client

def _jev_failure(name: str, category: str, detail: str) -> tuple[str, str, str]:
    """Fail-safe BLOCK, worded so an integration failure can't be mistaken for a genuine
    judgment when reading a report (a judge that blocks everything is either working or
    broken — the text has to say which)."""
    return name, "BLOCK", (
        f"[jev] INTEGRATION ERROR ({category}) — failing safe to BLOCK, this is not a "
        f"judgment of the review: {detail}"
    )

def judge_review_jev(name: str, review: str) -> tuple[str, str, str]:
    """Judges one review in isolation with a single TypeSafe Noul question. Any failure
    to obtain a usable probability fails safe to BLOCK, with the failure category named:
    sdk-missing, schema-error, api-error, client-error, bad-response."""
    try:
        import typesafe_sdk
    except ImportError:
        return _jev_failure(name, "sdk-missing", "typesafe-sdk is not installed "
                            "(uv pip install typesafe-sdk)")
    try:
        question = typesafe_sdk.Noul(
            instructions=(
                f"Does the review of the code, assigned to the '{name}' lens, describe a "
                "defect that should be blocked under this rubric?\n\n" + _judge_rubric(name)
            ),
            criteria={
                "true": "The review describes an in-lane defect that meets the BLOCK rubric.",
                "false": "The review has no in-lane defect that meets the BLOCK rubric "
                         "(clean, only nitpicks, or an accurate out-of-lane mention).",
            },
        )
    except Exception as e:  # pydantic validation of the question shape
        return _jev_failure(name, "schema-error", f"{type(e).__name__}: {e}")
    try:
        response = _get_jev_client().system_one(
            {"lens": name, "review": review}, questions={"block": question},
        )
    except (typesafe_sdk.TypeSafeBadRequestError,
            typesafe_sdk.TypeSafeUnprocessableEntityError) as e:
        return _jev_failure(name, "schema-error", f"{type(e).__name__}: {e}")
    except typesafe_sdk.TypeSafeAPIError as e:
        return _jev_failure(name, "api-error", f"{type(e).__name__}: {e}")
    except typesafe_sdk.TypeSafeError as e:  # no/invalid key at client creation, timeouts, connection
        return _jev_failure(name, "client-error", f"{type(e).__name__}: {e}")
    try:
        probability = response.nouls["block"].noul
        if isinstance(probability, bool) or not isinstance(probability, (int, float)):
            raise TypeError(f"probability is not a number: {probability!r}")
        if not 0.0 <= probability <= 1.0:  # also rejects NaN
            raise ValueError(f"probability out of range: {probability!r}")
        probability = float(probability)
    except Exception as e:  # missing answer, wrong type, NaN/out of range
        return _jev_failure(name, "bad-response", f"{type(e).__name__}: {e}")
    verdict = "BLOCK" if probability >= JEV_BLOCK_THRESHOLD else "OK"
    try:
        request_id = response.request_id
    except Exception:  # the SDK raises (not returns None) when the header is absent
        request_id = None
    return name, verdict, (
        f"[jev] P(block)={probability:.3f}, threshold={JEV_BLOCK_THRESHOLD} -> {verdict} "
        f"(request_id={request_id})"
    )

def judge_review(name: str, review: str) -> tuple[str, str, str]:
    """The judge selected by --judge (llm by default). Returns (name, verdict, response)."""
    if get_judge() == "jev":
        return judge_review_jev(name, review)
    return judge_review_llm(name, review)

def parse_verdict(response: str) -> str:
    """Extracts the verdict from an explicit VERDICT: marker. Anything ambiguous —
    no marker, conflicting markers, empty response — fails safe to BLOCK.

    Reading the first word instead was actively wrong: judges that reason aloud
    ("BLOCK — wait, no, let me reconsider... OK") got scored on the word they
    started with, not the verdict they reached, which breaks fail-safe in the
    direction that matters (a judge talking itself into OK would have read as OK)."""
    matches = re.findall(r"^VERDICT:\s*(BLOCK|OK)\s*$", response.upper(), re.MULTILINE)
    if len(matches) != 1:
        return "BLOCK"
    return matches[0]

def aggregate_verdict(results: dict) -> tuple[bool, str, dict]:
    """The diamond's join: each review gets its own isolated BLOCK/OK judgment, then
    Python (not the model) decides the final PASS/FAIL — FAIL if ANY category blocks.
    A single holistic PASS/FAIL question let one real finding get smoothed over by
    three clean ones (see NOTES.md). Also returns the per-category verdicts dict —
    the graph's routing node reads this directly instead of re-parsing the report
    text to figure out which category blocked."""
    verdicts = {}
    reasoning = {}
    with ThreadPoolExecutor(max_workers=len(results)) as executor:
        futures = [
            executor.submit(judge_review, name, review)
            for name, review in results.items()
        ]
        for future in futures:
            name, verdict, response = future.result()
            verdicts[name] = verdict
            reasoning[name] = response

    passed = all(verdict == "OK" for verdict in verdicts.values())

    summary = "\n".join(f"{name}: {verdict}" for name, verdict in verdicts.items())
    details = "\n\n".join(
        f"[{name.upper()} — {verdicts[name]}]\n{response}"
        for name, response in reasoning.items()
    )
    return passed, f"{summary}\n\n{details}", verdicts
