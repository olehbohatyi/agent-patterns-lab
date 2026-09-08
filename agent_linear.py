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

def clean_code(text: str) -> str:
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text

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

    passed, output = run_tests()
    print(f"Result: {'PASSED' if passed else 'FAILED'}")
    print(output[-500:])
    return passed

if __name__ == "__main__":
    task = sys.argv[1]
    result = main(task)
    sys.exit(0 if result else 1)
