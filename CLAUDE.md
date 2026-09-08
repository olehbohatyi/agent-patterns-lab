# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A demonstration and comparison of "agent loop" patterns: scripts that shell out to the `claude` CLI
(non-interactively, via `claude -p "<prompt>"`) to generate a solution and its tests for a given task,
then check the result against pytest.

- [agent_loop.py](agent_loop.py) — self-correcting: on test failure it feeds the real pytest output back
  to Claude and asks for a fix, up to `MAX_ATTEMPTS` (3) times, stopping as soon as tests pass.
- [agent_linear.py](agent_linear.py) — same generate-solution / generate-tests / run-tests flow, but a
  single attempt with no fix loop.

Both take the task description as `sys.argv[1]`. [solution.py](solution.py) and
[test_solution.py](test_solution.py) are generated output overwritten on every run — they are
gitignored, not committed. [NOTES.md](NOTES.md) tracks the experiment log and conclusions phase by
phase (Phase 0: baseline loop agent; Phase 1: linear vs. loop comparison across 5 tasks).

## Commands

Run the tests for whatever solution/tests were last generated:
```bash
pytest test_solution.py -v
```

Run a single test:
```bash
pytest test_solution.py -v -k <test_name>
```

Run either agent on a task (overwrites `solution.py` and `test_solution.py`, requires the `claude` CLI
on PATH and one or more LLM round-trips):
```bash
python agent_loop.py "<task description>"
python agent_linear.py "<task description>"
```

## Architecture notes

- `call_claude()` invokes `claude -p` as a subprocess with a 120s timeout and returns raw stdout — there
  is no structured output parsing, just string prompts in and code out. Prompts explicitly tell Claude
  not to write files itself (no tool use) — earlier versions that just said "save it in a single file"
  caused Claude to invoke its own Write tool instead of returning code as text, which the subprocess
  can't approve non-interactively and which silently corrupted the generated files.
- `clean_code()` extracts a fenced code block if one appears anywhere in the response, then validates
  the result parses as Python via `ast.parse`. If it doesn't parse (e.g. Claude returned prose/a
  refusal instead of code), it raises `NotPythonError` rather than writing broken content to disk.
- The fix loop in `agent_loop.py`'s `main()` is intentionally simple: on failure it re-prompts with the
  *entire* pytest output appended, asking for a fixed version of the whole function — there's no
  diffing or partial patching.
- Exit codes carry the outcome: `0` if tests pass within the attempt budget, `1` if the budget is
  exhausted (loop) or the single attempt failed (linear) — useful for scripting/CI around these loops.
