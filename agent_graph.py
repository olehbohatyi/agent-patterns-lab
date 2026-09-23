import sys

from agent_common import (
    NotPythonError, call_claude, clean_code, fix_until_green, run_tests,
    write_solution_and_tests,
)
from agent_review import aggregate_verdict, run_diamond_review

def route_fix(category_verdicts: dict, review_results: dict, task_description: str,
              code_text: str, test_text: str) -> str:
    """Multi-target: builds one fix prompt covering every blocked category in this
    attempt, rather than picking a single winner (by category priority) and
    silently dropping the rest for the attempt — the gap found in NOTES.md's
    two-category-block test."""
    blocked = [name for name, v in category_verdicts.items() if v == "BLOCK"]
    print(f"\n[route_fix] Categories blocked this attempt: {blocked}")
    print(f"[route_fix] Routing to: multi-target fix covering {blocked}")

    findings = "\n\n".join(f"[{name.upper()}]\n{review_results[name]}" for name in blocked)

    return (
        f"Here is the current content of solution.py:\n\n{code_text}\n\n"
        f"Here is the current content of test_solution.py:\n\n{test_text}\n\n"
        f"This code was flagged by {len(blocked)} independent review(s):\n\n{findings}\n\n"
        f"Fix the function for this task: {task_description}, in the file solution.py, "
        "so that ALL of the issues above are addressed simultaneously. If any two "
        "findings appear to conflict, resolve them as best you can and note the "
        "tension in a comment, rather than fixing one at the expense of leaving the "
        "other unresolved. Make the smallest change that addresses the findings. Keep "
        "the function name. Do not change any exception type or any behavior that the "
        "existing tests in test_solution.py assert — every existing test must still "
        "pass. Do not write, save, or create any files yourself — respond "
        "with the fixed code of the whole function as plain text only, no markdown, "
        "no explanations."
    )

def summarize_unresolved(history: dict) -> str:
    """Turns each still-blocked category's per-attempt record into a mechanical
    round-by-round transcript for a human to read, instead of a verdict about
    whether the category is 'stuck'. Only the facts available without trusting
    any model self-report: whether solution.py's text changed since the category
    was last reviewed, and whether the block verdict changed."""
    lines = ["\n=== Unresolved categories — round-by-round history ===\n"]
    any_unresolved = False
    for name, entries in history.items():
        if not entries[-1][1]:  # last entry not blocked — resolved or never blocked
            continue
        any_unresolved = True
        lines.append(f"[{name.upper()}] blocked at exhaustion")
        prev_code = None
        prev_blocked = None
        for attempt, blocked, code, review in entries:
            if prev_code is None:
                transition = "first review"
            elif code != prev_code:
                transition = "changed, unresolved" if blocked else "changed, resolved"
            elif blocked != prev_blocked:
                transition = "unchanged, verdict flipped (likely reviewer nondeterminism)"
            else:
                transition = "unchanged, still blocked (declined or no-op fix)"
            lines.append(f"  attempt {attempt}: {'BLOCK' if blocked else 'OK'} — {transition}")
            lines.append(f"    review: {review[:300]}")
            prev_code, prev_blocked = code, blocked
        lines.append("")
    if not any_unresolved:
        lines.append("(none — every category that ever blocked was resolved by exhaustion)")
    return "\n".join(lines)

def main(task_description: str):
    # Step A: agent writes the first version of the solution
    code_text, test_text = write_solution_and_tests(task_description)

    # Step B: check-and-fix loop — this is where the agent makes its own decisions
    code_text = fix_until_green(task_description, code_text)

    # Step C: diamond review, with a graph loop for routed fixes — the route isn't
    # fixed in advance, it's built from what the previous node (the aggregator)
    # returned, unlike the diamond's always-the-same fan-out/fan-in shape.
    #
    # history[category] accumulates one entry per attempt where that category was
    # reviewed: (attempt_number, blocked, code_text_at_review_time, review_text).
    # This is deliberately just a record of facts (was the code identical to the
    # last round? did the verdict change?), not a judgment about whether a fix
    # "really" addressed a finding — that judgment would have to trust either the
    # fix's own account of what it did, or a second model call guessing whether a
    # diff is "relevant" to a finding, both of which are exactly the kind of
    # self-report this project's NOTES.md/FINDINGS.md documents as unreliable (see
    # "Comments that assert properties the code doesn't have"). So on exhaustion,
    # main() prints the history instead of trying to classify it.
    MAX_GRAPH_ATTEMPTS = 2
    history = {}
    for graph_attempt in range(1, MAX_GRAPH_ATTEMPTS + 1):
        print(f"\n=== Diamond review (graph attempt {graph_attempt}) ===")
        review_results = run_diamond_review(code_text, test_text)
        for name, verdict in review_results.items():
            print(f"\n--- {name.upper()} ---\n{verdict[:300]}")

        final_pass, final_report, category_verdicts = aggregate_verdict(review_results)
        print(f"\n=== Verdict: {'PASS' if final_pass else 'FAIL'} ===")
        print(final_report)

        for name, verdict in category_verdicts.items():
            history.setdefault(name, []).append(
                (graph_attempt, verdict == "BLOCK", code_text, review_results[name])
            )

        if final_pass:
            print("\n✅ Graph converged — all categories OK.")
            sys.exit(0)

        if graph_attempt == MAX_GRAPH_ATTEMPTS:
            print("\n❌ Graph attempt limit exhausted.")
            print(summarize_unresolved(history))
            sys.exit(1)

        fix_prompt = route_fix(category_verdicts, review_results, task_description, code_text, test_text)
        fixed_code = call_claude(fix_prompt)
        try:
            candidate_code = clean_code(fixed_code)
        except NotPythonError as e:
            print(f"\n⚠️ Fix produced invalid Python, treating as a failed attempt: {e}")
            continue  # solution.py unchanged, next attempt re-reviews the same code

        with open("solution.py", "w") as f:
            f.write(candidate_code)

        # Re-verify tests still pass after the routed fix, before reviewing again
        tests_passed, test_output = run_tests()
        if not tests_passed:
            print(f"\n⚠️ Fix broke tests, treating as a failed attempt:\n{test_output[-500:]}")
            # Revert to the last known-good code so the next attempt starts clean,
            # rather than compounding a broken fix with another fix on top of it.
            with open("solution.py", "w") as f:
                f.write(code_text)
            continue

        code_text = candidate_code

if __name__ == "__main__":
    task = sys.argv[1]
    main(task)
