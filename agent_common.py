"""Code shared by every agent_*.py script: the claude call, output validation, the
test runner, and the two steps every agent repeats (write solution + tests; fix
until the tests pass). Extracted from four near-identical copies; see NOTES.md for
why several of these pieces are shaped the way they are."""
import argparse
import ast
import builtins
import re
import subprocess
import sys
import threading

MAX_ATTEMPTS = 3

# --- Backends -------------------------------------------------------------------
# "local" shells out to the `claude -p` CLI (the default; every result in NOTES.md /
# FINDINGS.md was measured this way). "api" calls the Anthropic Messages API through
# the Python SDK instead. The two are NOT interchangeable systems: `claude -p` runs
# inside the working directory and can read files there, an API call sees only the
# prompt (see the "Backends" caveat in FINDINGS.md).
BACKENDS = ("local", "api")
_backend = "local"

# CLI aliases used throughout the agents -> API model IDs. IDs are from the Models
# overview page (platform.claude.com/docs/en/models/overview); the API needs real
# IDs. Whether the CLI's "sonnet"/"haiku" aliases resolve to exactly these models has
# not been verified. Names not in the map pass through, so a full ID also works.
API_MODEL_IDS = {
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5-20251001",
    "opus": "claude-opus-5-5",
}
API_MAX_TOKENS = 16000        # thinking tokens count toward max_tokens (docs: thinking-steering-and-cost)
API_TIMEOUT_SECONDS = 120.0   # same limit as the local backend's subprocess timeout

_api_client = None
_api_client_lock = threading.Lock()

def set_backend(name: str) -> None:
    """Selects the backend for every later call_claude(). Call once, before any agent
    work starts (parse_cli() does this from --backend)."""
    global _backend
    if name not in BACKENDS:
        raise ValueError(f"unknown backend {name!r}; expected one of {BACKENDS}")
    _backend = name

def get_backend() -> str:
    return _backend

def resolve_api_model(model: str) -> str:
    return API_MODEL_IDS.get(model, model)

def _get_api_client():
    """One shared Anthropic client (the diamond's reviewers call from several threads).
    The API key comes from ANTHROPIC_API_KEY in the environment — never from code."""
    global _api_client
    with _api_client_lock:
        if _api_client is None:
            try:
                import anthropic
            except ImportError as e:
                raise RuntimeError(
                    "--backend api needs the Anthropic SDK: pip install anthropic "
                    "(and set ANTHROPIC_API_KEY in the environment)"
                ) from e
            _api_client = anthropic.Anthropic(timeout=API_TIMEOUT_SECONDS)
        return _api_client

def _call_api(prompt: str, model: str) -> str:
    """One Messages API call. SDK errors (auth, rate limit, timeout, after the SDK's own
    retries) propagate: a failed model call should stop the run, not read as an empty
    answer."""
    client = _get_api_client()
    response = client.messages.create(
        model=resolve_api_model(model),
        max_tokens=API_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "max_tokens":
        print(f"⚠️ API response hit max_tokens={API_MAX_TOKENS} and may be truncated",
              file=sys.stderr)
    return "".join(block.text for block in response.content if block.type == "text").strip()

def _call_local(prompt: str, model: str) -> str:
    """Calls Claude Code in non-interactive mode and returns the response."""
    result = subprocess.run(
        ["claude", "--model", model, "-p", prompt],
        capture_output=True,
        text=True,
        timeout=120
    )
    return result.stdout.strip()

def call_claude(prompt: str, model: str = "sonnet") -> str:
    """Sends one prompt to Claude and returns the response text, through the active
    backend (see set_backend): the local `claude -p` CLI by default, or the API."""
    if _backend == "api":
        return _call_api(prompt, model)
    return _call_local(prompt, model)

def parse_cli(description: str | None = None) -> str:
    """Shared command line for every agent script: a task description plus an optional
    --backend {local,api} (default local). Applies the backend and returns the task."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("task", help="description of the function to write")
    parser.add_argument("--backend", choices=BACKENDS, default="local",
                        help="local: the `claude -p` CLI (default); api: the Anthropic API "
                             "(needs `pip install anthropic` and ANTHROPIC_API_KEY)")
    args = parser.parse_args()
    set_backend(args.backend)
    return args.task


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
