"""Tests for the pure/shared pieces of agent_common.py and agent_review.py that earlier bugs came
from (clean_code, find_undefined_names, parse_verdict) and for the failure handling added after the
2026-10-07 review (a failed `claude -p` call raises, run_tests can't be vacuous, exit code 2 is
infrastructure failure). No live calls: subprocess.run and call_claude are stubbed."""
import ast
import subprocess
import sys
import types

import pytest

import agent_common
import agent_review
from agent_common import (
    CallFailedError, fix_until_green, NotPythonError, clean_code, find_undefined_names, run_cli, run_tests,
    imports_solution, write_solution_and_tests,
)
from agent_review import parse_verdict


def fake_run(returncode=0, stdout="", stderr="", raises=None):
    def run(cmd, **kwargs):
        run.cmd, run.kwargs = cmd, kwargs
        if raises:
            raise raises
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    return run


# --- clean_code ---------------------------------------------------------------------

def test_clean_code_accepts_plain_code():
    assert clean_code("def f(x):\n    return x\n") == "def f(x):\n    return x"


def test_clean_code_extracts_a_fenced_block_anywhere():
    assert clean_code("Here you go:\n```python\ndef f():\n    return 1\n```\nDone.") == "def f():\n    return 1"


@pytest.mark.parametrize("text", ["", "   \n", "I cannot write that, sorry.", "def f(:\n    pass"])
def test_clean_code_rejects_empty_prose_and_syntax_errors(text):
    with pytest.raises(NotPythonError):
        clean_code(text)


def test_clean_code_rejects_a_missing_import():
    with pytest.raises(NotPythonError, match="undefined name"):
        clean_code("def f(p):\n    return os.path.exists(p)\n")


# --- find_undefined_names -----------------------------------------------------------

def undefined(src):
    return find_undefined_names(ast.parse(src))


def test_undefined_names_flags_missing_import_and_accepts_imports_and_builtins():
    assert undefined("def f(p):\n    return os.path.exists(p)") == {"os"}
    assert undefined("import os\ndef f(p):\n    return os.path.exists(p) and len(p)") == set()


def test_undefined_names_counts_params_comprehensions_and_except_names_as_bound():
    src = "def f(xs):\n    try:\n        return [y for y in xs]\n    except ValueError as e:\n        return e"
    assert undefined(src) == set()


def test_undefined_names_is_deliberately_global_a_name_bound_anywhere_counts_everywhere():
    # Documented as conservative: it can miss a real scoping bug.
    src = "def a():\n    os = 1\ndef b():\n    return os.getcwd()"
    assert undefined(src) == set()


@pytest.mark.xfail(reason="known quirk: match-statement capture patterns are not treated as bound names",
                   strict=True)
def test_undefined_names_accepts_match_capture_patterns():
    assert undefined("def f(x):\n    match x:\n        case [a, *rest]:\n            return rest") == set()


# --- parse_verdict ------------------------------------------------------------------

@pytest.mark.parametrize("response, expected", [
    ("reasoning\nVERDICT: BLOCK", "BLOCK"),
    ("reasoning\nVERDICT: OK", "OK"),
    ("verdict: ok", "OK"),                       # case-insensitive
    ("BLOCK — wait, no...\nVERDICT: OK", "OK"),   # the marker wins over a leading word
    ("", "BLOCK"),
    ("no marker at all", "BLOCK"),
    ("VERDICT: OK\nVERDICT: BLOCK", "BLOCK"),     # conflicting markers
    ("VERDICT: OK\nVERDICT: OK", "BLOCK"),        # duplicate markers are ambiguous too
    ("**VERDICT: OK**", "BLOCK"),                 # strict: fails safe (known false-block quirk)
    ("VERDICT: OK.", "BLOCK"),
])
def test_parse_verdict(response, expected):
    assert parse_verdict(response) == expected


# --- a failed `claude -p` call raises instead of reading as an answer ----------------

def test_local_call_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(returncode=1, stdout="", stderr="login required"))
    with pytest.raises(CallFailedError, match="status 1.*login required"):
        agent_common._call_local("hi", "sonnet")


def test_local_call_raises_on_empty_output(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(returncode=0, stdout="  \n"))
    with pytest.raises(CallFailedError, match="empty"):
        agent_common._call_local("hi", "sonnet")


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired("claude", 120), FileNotFoundError("claude")])
def test_local_call_lets_timeouts_and_a_missing_cli_propagate(monkeypatch, error):
    monkeypatch.setattr(subprocess, "run", fake_run(raises=error))
    with pytest.raises(type(error)):
        agent_common._call_local("hi", "sonnet")


def test_a_failed_reviewer_call_aborts_the_review_instead_of_becoming_an_empty_review(monkeypatch):
    def failing(prompt, model="sonnet"):
        raise CallFailedError("boom")
    monkeypatch.setattr(agent_review, "call_claude", failing)
    with pytest.raises(CallFailedError):
        agent_review.run_diamond_review("code", "tests")


def test_a_failed_judge_call_propagates_instead_of_reading_as_a_verdict(monkeypatch):
    def failing(prompt, model="sonnet"):
        raise CallFailedError("boom")
    monkeypatch.setattr(agent_review, "call_claude", failing)
    with pytest.raises(CallFailedError):
        agent_review.aggregate_verdict({"security": "No issues found."})


# --- run_tests ----------------------------------------------------------------------

def test_run_tests_uses_this_interpreter_and_a_timeout(monkeypatch):
    run = fake_run(stdout="== 3 passed in 0.01s ==")
    monkeypatch.setattr(subprocess, "run", run)
    assert run_tests() == (True, "== 3 passed in 0.01s ==")
    assert run.cmd[:3] == [sys.executable, "-m", "pytest"]
    assert run.kwargs["timeout"] == agent_common.RUN_TESTS_TIMEOUT_SECONDS


@pytest.mark.parametrize("stdout", ["== 2 skipped in 0.01s ==", "no tests ran", ""])
def test_run_tests_is_not_green_without_a_passed_test_even_on_exit_zero(monkeypatch, stdout):
    monkeypatch.setattr(subprocess, "run", fake_run(returncode=0, stdout=stdout))
    assert run_tests()[0] is False


def test_run_tests_failure_is_a_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(returncode=1, stdout="== 1 failed, 2 passed =="))
    assert run_tests()[0] is False


def test_run_tests_timeout_is_a_failure_with_the_reason_in_the_output(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(raises=subprocess.TimeoutExpired("pytest", 1)))
    passed, output = run_tests()
    assert passed is False and "timed out" in output


# --- tests must import solution -----------------------------------------------------

@pytest.mark.parametrize("src, expected", [
    ("from solution import f\n\ndef test_f():\n    assert f(1) == 1", True),
    ("import solution\n\ndef test_f():\n    assert solution.f(1) == 1", True),
    ("def test_nothing():\n    assert True", False),
    ("import os\nfrom solutions import f", False),
])
def test_imports_solution(src, expected):
    assert imports_solution(src) is expected


def test_write_step_refuses_tests_that_never_import_the_solution(monkeypatch, tmp_path):
    replies = iter(["def f(x):\n    return x\n", "def test_nothing():\n    assert True\n"])
    monkeypatch.setattr(agent_common, "call_claude", lambda prompt, model="sonnet": next(replies))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        write_solution_and_tests("identity", announce=False)
    assert "never imports `solution`" in str(exc.value)


# --- exit codes ---------------------------------------------------------------------

def test_run_cli_returns_normally_and_passes_systemexit_through():
    run_cli(lambda task: None, "t")

    def exits(task):
        sys.exit(1)
    with pytest.raises(SystemExit) as exc:
        run_cli(exits, "t")
    assert exc.value.code == 1


@pytest.mark.parametrize("error", [CallFailedError("x"), subprocess.TimeoutExpired("claude", 1), RuntimeError("sdk")])
def test_run_cli_maps_infrastructure_errors_to_exit_2(error, capsys):
    def boom(task):
        raise error
    with pytest.raises(SystemExit) as exc:
        run_cli(boom, "t")
    assert exc.value.code == 2
    assert "infrastructure failure" in capsys.readouterr().err


def test_a_pytest_timeout_is_a_failed_attempt_fed_back_to_the_fix_loop_not_an_infrastructure_error(monkeypatch, tmp_path):
    """Generated code stuck in a loop is the model's output failing: the timeout message goes
    into the fix prompt and the attempt is retried; exit 2 stays for the harness itself."""
    monkeypatch.chdir(tmp_path)
    outcomes = iter([subprocess.TimeoutExpired("pytest", 1), types.SimpleNamespace(
        returncode=0, stdout="== 1 passed ==", stderr="")])

    def run(cmd, **kwargs):
        o = next(outcomes)
        if isinstance(o, Exception):
            raise o
        return o

    prompts = []
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(agent_common, "call_claude",
                        lambda prompt, model="sonnet": prompts.append(prompt) or "def f():\n    return 1\n")
    assert fix_until_green("task", "def f():\n    while True:\n        pass\n") == "def f():\n    return 1"
    assert len(prompts) == 1 and "timed out" in prompts[0]


def test_a_pytest_timeout_that_never_clears_exits_1_not_2(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(subprocess, "run", fake_run(raises=subprocess.TimeoutExpired("pytest", 1)))
    monkeypatch.setattr(agent_common, "call_claude", lambda prompt, model="sonnet": "def f():\n    return 1\n")
    with pytest.raises(SystemExit) as exc:
        run_cli(lambda t: fix_until_green(t, "def f():\n    pass\n"), "task")
    assert exc.value.code == 1
