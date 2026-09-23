import sys

from agent_common import fix_until_green, parse_cli, write_solution_and_tests
from agent_review import aggregate_verdict, run_diamond_review

def main(task_description: str):
    # Step A: agent writes the first version of the solution
    code_text, test_text = write_solution_and_tests(task_description)

    # Step B: check-and-fix loop — this is where the agent makes its own decisions
    code_text = fix_until_green(task_description, code_text)

    # Step C: diamond review — 4 parallel reviewers, then an asymmetric aggregator
    print("\n=== Running diamond review (4 parallel reviewers) ===")
    review_results = run_diamond_review(code_text, test_text)
    for name, verdict in review_results.items():
        print(f"\n--- {name.upper()} ---\n{verdict[:300]}")

    final_pass, final_verdict, _category_verdicts = aggregate_verdict(review_results)
    print(f"\n=== Final verdict: {'PASS' if final_pass else 'FAIL'} ===")
    print(final_verdict)

    sys.exit(0 if final_pass else 1)

if __name__ == "__main__":
    task = parse_cli("Loop agent plus a four-reviewer diamond review of the passing code.")
    main(task)
