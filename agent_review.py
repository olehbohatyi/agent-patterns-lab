"""The diamond review machinery shared by agent_diamond.py and agent_graph.py: four
parallel reviewers, one isolated judge call per review, and the Python-side
aggregation that fails if any category blocks. See NOTES.md / FINDINGS.md for why the
judge prompt, the VERDICT marker and the per-category aggregation are shaped this way."""
import re
from concurrent.futures import ThreadPoolExecutor

from agent_common import call_claude

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

def judge_review(name: str, review: str) -> tuple[str, str, str]:
    """Judges one review in isolation — this call never sees the other three, so a
    real finding can't be softened by sitting next to clean reports."""
    prompt = (
        "You are a strict, isolated verifier. You have no context on how this code "
        "was written or fixed — you are judging only the review text below.\n\n"
        f"This review's assigned lens is: {name}.\n\n"
        f"Review ({name}):\n{review}\n\n"
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
        "Think through your reasoning first if you need to. Then output your final "
        "verdict on its own last line, in exactly this format and nothing else:\n\n"
        "VERDICT: BLOCK\n"
        "or\n"
        "VERDICT: OK"
    )
    response = call_claude(prompt)
    return name, parse_verdict(response), response

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
