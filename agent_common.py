"""Code shared by every agent_*.py script: the claude call, output validation, the
test runner, and the two steps every agent repeats (write solution + tests; fix
until the tests pass). Extracted from four near-identical copies; see NOTES.md for
why several of these pieces are shaped the way they are."""
import ast
import builtins
import re
import subprocess
import sys

MAX_ATTEMPTS = 3


def call_claude(prompt: str, model: str = "sonnet") -> str:
    """Calls Claude Code in non-interactive mode and returns the response."""
    result = subprocess.run(
        ["claude", "--model", model, "-p", prompt],
        capture_output=True,
        text=True,
        timeout=120
    )
    return result.stdout.strip()

class NotPythonError(RuntimeError):
    """Raised when claude's response isn't valid Python (e.g. a refusal or explanation)."""

_ALWAYS_DEFINED = set(dir(builtins)) | {
    "__name__", "__file__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__",
}

def find_undefined_names(tree: ast.AST) -> set[str]:
    """Static check for names referenced but never bound anywhere in the module —
    catches the common case of a missing import (e.g. `os.path.X` used without
    `import os`) that ast.parse()'s syntax-only check can't, without needing to
    actually call the generated function (which ast.parse and a bare exec() of the
    module both miss, since a name used only inside a function body isn't touched
    until that function is called).

    Deliberately not full scope resolution: any name bound ANYWHERE in the module,
    at any nesting level, counts as defined everywhere. That's conservative in the
    safe direction — it can miss a real scoping bug, but won't flag well-formed
    code as broken."""
    defined = set(_ALWAYS_DEFINED)
    loaded = set()

    class Visitor(ast.NodeVisitor):
        def visit_Name(self, node):
            (defined if isinstance(node.ctx, ast.Store) else loaded).add(node.id)
            self.generic_visit(node)

        def visit_Import(self, node):
            for alias in node.names:
                defined.add((alias.asname or alias.name).split(".")[0])

        def visit_ImportFrom(self, node):
            for alias in node.names:
                defined.add(alias.asname or alias.name)

        def _visit_function(self, node):
            defined.add(node.name)
            args = node.args
            for arg in args.posonlyargs + args.args + args.kwonlyargs:
                defined.add(arg.arg)
            if args.vararg:
                defined.add(args.vararg.arg)
            if args.kwarg:
                defined.add(args.kwarg.arg)
            self.generic_visit(node)

        visit_FunctionDef = _visit_function
        visit_AsyncFunctionDef = _visit_function

        def visit_Lambda(self, node):
            for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
                defined.add(arg.arg)
            self.generic_visit(node)

        def visit_ClassDef(self, node):
            defined.add(node.name)
            self.generic_visit(node)

        def visit_ExceptHandler(self, node):
            if node.name:
                defined.add(node.name)
            self.generic_visit(node)

    Visitor().visit(tree)
    return loaded - defined

def clean_code(text: str) -> str:
    """Extracts a fenced code block if present anywhere in the response, then
    validates the result parses as Python and references no undefined names (e.g.
    a missing import). Raises NotPythonError instead of returning prose/refusals,
    or code that would fail at runtime with a NameError pytest would otherwise be
    the first to catch — silently writing either to solution.py/test_solution.py."""
    match = re.search(r"```(?:python)?\n(.*?)\n```", text, re.DOTALL)
    code = match.group(1) if match else text.strip()

    if not code.strip():
        raise NotPythonError("claude returned empty output instead of code")

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise NotPythonError(
            f"claude did not return valid Python code:\n\n{text[:500]}"
        ) from e

    undefined = find_undefined_names(tree)
    if undefined:
        raise NotPythonError(
            f"code references undefined name(s) {sorted(undefined)} — likely a "
            f"missing import:\n\n{text[:500]}"
        )
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

def write_solution_and_tests(task_description: str, announce: bool = True) -> tuple[str, str]:
    """Step A, shared by every agent: generate solution.py, then generate a test file
    that is shown solution.py's actual content, writing both to disk. Exits with a
    message if the model returns something that isn't valid Python."""
    if announce:
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
        test_text = clean_code(tests)
    except NotPythonError as e:
        sys.exit(f"❌ claude refused to write test_solution.py: {e}")
    with open("test_solution.py", "w") as f:
        f.write(test_text)
    return code_text, test_text

def fix_until_green(task_description: str, code_text: str) -> str:
    """Step B, shared by the loop, diamond and graph agents: run the tests; on failure
    re-prompt with the full pytest output and rewrite solution.py, up to MAX_ATTEMPTS.
    Returns the passing code; exits 1 if the attempt budget runs out."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        passed, output = run_tests()
        print(f"\n=== Attempt {attempt}: tests {'PASSED' if passed else 'FAILED'} ===")
        print(output[-500:])  # last 500 characters of output

        if passed:
            print("\n✅ The agent decided to stop on its own — tests are green.")
            return code_text

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
