import ast
import re
import subprocess
import sys

def call_claude(prompt: str) -> str:
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
    result = subprocess.run(
        ["pytest", "test_solution.py", "-v"],
        capture_output=True,
        text=True
    )
    passed = result.returncode == 0
    return passed, result.stdout + result.stderr

def main(task_description: str):
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

    passed, output = run_tests()
    print(f"Result: {'PASSED' if passed else 'FAILED'}")
    print(output[-500:])
    return passed

if __name__ == "__main__":
    task = sys.argv[1]
    result = main(task)
    sys.exit(0 if result else 1)
