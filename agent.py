import subprocess
import sys

MAX_ATTEMPTS = 3

def call_claude(prompt: str) -> str:
    """Calls Claude Code in non-interactive mode and returns the response."""
    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True,
        text=True,
        timeout=120
    )
    return result.stdout.strip()

def clean_code(text: str) -> str:
    """Strips the markdown wrapper if the LLM added it anyway."""
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # remove the first line ```python
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text

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
        "Save it in a single file. Output ONLY the code, no markdown, no explanations."
    )
    with open("solution.py", "w") as f:
        f.write(clean_code(code))

    tests = call_claude(
        f"Write pytest tests for the function described here: {task_description}. "
        "The function is already implemented in solution.py. "
        "Output ONLY the test code, no markdown, no explanations."
    )
    with open("test_solution.py", "w") as f:
        f.write(clean_code(tests))

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
            "Output ONLY the fixed code of the whole function, no markdown, no explanations."
        )
        fixed_code = call_claude(fix_prompt)
        with open("solution.py", "w") as f:
            f.write(clean_code(fixed_code))

if __name__ == "__main__":
    task = sys.argv[1]
    main(task)
