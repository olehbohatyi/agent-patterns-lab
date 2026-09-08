import ast
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

MAX_ATTEMPTS = 3

def call_claude(prompt: str) -> str:
    """Calls Claude Code in non-interactive mode and returns the response."""
    result = subprocess.run(
        ["claude", "--model", "sonnet", "-p", prompt],
        capture_output=True,
        text=True,
        timeout=120
    )
    return result.stdout.strip()

class NotPythonError(RuntimeError):
    """Raised when claude's response isn't valid Python (e.g. a refusal or explanation)."""

def clean_code(text: str) -> str:
    """Extracts a fenced code block if present anywhere in the response, then
    validates the result parses as Python. Raises NotPythonError instead of
    returning prose/refusals that would silently corrupt solution.py or
    test_solution.py."""
    match = re.search(r"```(?:python)?\n(.*?)\n```", text, re.DOTALL)
    code = match.group(1) if match else text.strip()

    try:
        ast.parse(code)
    except SyntaxError as e:
        raise NotPythonError(
            f"claude did not return valid Python code:\n\n{text[:500]}"
        ) from e
    return code

def run_tests() -> tuple[bool, str]:
    """Runs pytest and returns (success, output)."""
    result = subprocess.run(
        ["pytest", "test_solution.py", "-v"],
        capture_output=True,
        text=True
    )
    passed = result.returncode == 0
    return passed, result.stdout + result.stderr

REVIEWERS = {
    "security": "Review this code for security issues (e.g. injection, unsafe input handling). "
                "List any problems found, or say 'No issues found' if none.",
    "performance": "Review this code for performance issues (e.g. inefficient loops, unnecessary work). "
                   "List any problems found, or say 'No issues found' if none.",
    "style": "Review this code for style issues (e.g. naming, readability, PEP8). "
             "List any problems found, or say 'No issues found' if none.",
    "test_coverage": "Review the test file for coverage gaps (e.g. missing edge cases). "
                      "List any gaps found, or say 'No issues found' if none.",
}

def run_reviewer(name: str, instruction: str, code: str, tests: str) -> tuple[str, str]:
    prompt = f"{instruction}\n\nCode (solution.py):\n{code}\n\nTests (test_solution.py):\n{tests}"
    result = call_claude(prompt)
    return name, result

def run_diamond_review(code: str, tests: str) -> dict:
    """Fans out to 4 independent reviewers in parallel (the diamond's split),
    each with a different lens on the same code and tests."""
    results = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(run_reviewer, name, instruction, code, tests)
            for name, instruction in REVIEWERS.items()
        ]
        for future in futures:
            name, verdict = future.result()
            results[name] = verdict
    return results

def aggregate_verdict(results: dict) -> tuple[bool, str]:
    """The diamond's join: a verifier with no context on how the code was written
    or fixed, deciding PASS/FAIL from the 4 independent reviews alone. Asymmetric
    from the agent that wrote the code, so it can't just agree with itself."""
    combined = "\n\n".join(f"[{name.upper()}]\n{verdict}" for name, verdict in results.items())
    prompt = (
        "You are a final verifier with no prior context about how this code was written. "
        "Here are 4 independent code reviews:\n\n"
        f"{combined}\n\n"
        "Based ONLY on these reviews, answer with exactly one word first: PASS or FAIL. "
        "Then on a new line, explain briefly why."
    )
    verdict_text = call_claude(prompt)
    passed = verdict_text.strip().upper().startswith("PASS")
    return passed, verdict_text

def main(task_description: str):
    # Step A: agent writes the first version of the solution
    print("=== Attempt 1: writing the first version ===")
    code = call_claude(
        f"Write a Python function for this task: {task_description}. "
        "Do not write, save, or create any files yourself — respond with the code as plain "
        "text only, no markdown, no explanations."
    )
    try:
        code_text = clean_code(code)
    except NotPythonError as e:
        sys.exit(f"❌ claude refused to write solution.py: {e}")
    with open("solution.py", "w") as f:
        f.write(code_text)

    tests = call_claude(
        f"Here is the content of solution.py:\n\n{code_text}\n\n"
        f"Write pytest tests for this code. The task it implements: {task_description}. "
        "The tests will run in the same directory as solution.py. "
        "Do not write, save, or create any files yourself — respond with the test code as "
        "plain text only, no markdown, no explanations."
    )
    try:
        tests_text = clean_code(tests)
    except NotPythonError as e:
        sys.exit(f"❌ claude refused to write test_solution.py: {e}")
    with open("test_solution.py", "w") as f:
        f.write(tests_text)

    # Step B: check-and-fix loop — this is where the agent makes its own decisions
    tests_passed = False
    for attempt in range(1, MAX_ATTEMPTS + 1):
        tests_passed, output = run_tests()
        print(f"\n=== Attempt {attempt}: tests {'PASSED' if tests_passed else 'FAILED'} ===")
        print(output[-500:])  # last 500 characters of output

        if tests_passed:
            print("\n✅ The agent decided to stop on its own — tests are green.")
            break

        if attempt == MAX_ATTEMPTS:
            print("\n❌ Attempt limit exhausted, the agent gave up.")
            sys.exit(1)

        # The agent forms a new prompt on its own based on the actual error
        fix_prompt = (
            f"Here is the pytest output for the file solution.py:\n\n{output}\n\n"
            f"Fix the function for this task: {task_description}, in the file solution.py, so that the tests pass. "
            "Do not write, save, or create any files yourself — respond with the fixed code of "
            "the whole function as plain text only, no markdown, no explanations."
        )
        fixed_code = call_claude(fix_prompt)
        try:
            code_text = clean_code(fixed_code)
        except NotPythonError as e:
            sys.exit(f"❌ claude refused to fix solution.py: {e}")
        with open("solution.py", "w") as f:
            f.write(code_text)

    # Step C: diamond review — 4 parallel reviewers, then an asymmetric aggregator
    print("\n=== Running diamond review (4 parallel reviewers) ===")
    review_results = run_diamond_review(code_text, tests_text)
    for name, verdict in review_results.items():
        print(f"\n--- {name.upper()} ---\n{verdict[:300]}")

    final_pass, final_verdict = aggregate_verdict(review_results)
    print(f"\n=== Final verdict: {'PASS' if final_pass else 'FAIL'} ===")
    print(final_verdict)

    sys.exit(0 if final_pass else 1)

if __name__ == "__main__":
    task = sys.argv[1]
    main(task)
