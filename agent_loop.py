import ast
import re
import subprocess
import sys

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

def main(task_description: str):
    # Step A: agent writes the first version of the solution
    print("=== Attempt 1: writing the first version ===")
    code = call_claude(
        f"Write a Python function for this task: {task_description}. "
        "Do not write, save, or create any files yourself — respond with the code as plain "
        "text only, no markdown, no explanations."
    )
    try:
        clean = clean_code(code)
    except NotPythonError as e:
        sys.exit(f"❌ claude refused to write solution.py: {e}")
    with open("solution.py", "w") as f:
        f.write(clean)

    tests = call_claude(
        f"Write pytest tests for the function described here: {task_description}. "
        "The function is already implemented in solution.py. "
        "Do not write, save, or create any files yourself — respond with the test code as "
        "plain text only, no markdown, no explanations."
    )
    try:
        clean = clean_code(tests)
    except NotPythonError as e:
        sys.exit(f"❌ claude refused to write test_solution.py: {e}")
    with open("test_solution.py", "w") as f:
        f.write(clean)

    # Step B: check-and-fix loop — this is where the agent makes its own decisions
    for attempt in range(1, MAX_ATTEMPTS + 1):
        passed, output = run_tests()
        print(f"\n=== Attempt {attempt}: tests {'PASSED' if passed else 'FAILED'} ===")
        print(output[-500:])  # last 500 characters of output

        if passed:
            print("\n✅ The agent decided to stop on its own — tests are green.")
            sys.exit(0)

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
            clean = clean_code(fixed_code)
        except NotPythonError as e:
            sys.exit(f"❌ claude refused to fix solution.py: {e}")
        with open("solution.py", "w") as f:
            f.write(clean)

if __name__ == "__main__":
    task = sys.argv[1]
    main(task)
