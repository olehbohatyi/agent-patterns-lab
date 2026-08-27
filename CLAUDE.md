# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A minimal demonstration of an "agent loop" pattern: [agent.py](agent.py) shells out to the `claude` CLI
(non-interactively, via `claude -p "<prompt>"`) to generate a solution and its tests, then repeatedly
runs pytest and feeds failures back to Claude as a new prompt until the tests pass or `MAX_ATTEMPTS` (3)
is reached. [solution.py](solution.py) and [test_solution.py](test_solution.py) are the *output* of that
loop for a single example task (`is_prime(n)`), not hand-authored source — running `agent.py` overwrites
both files.

## Commands

Run the tests:
```bash
pytest test_solution.py -v
```

Run a single test:
```bash
pytest test_solution.py -v -k test_is_prime_large_prime
```

Run the full agent loop (overwrites `solution.py` and `test_solution.py`, requires the `claude` CLI on
PATH and takes multiple LLM round-trips):
```bash
python agent.py
```

## Architecture notes

- `call_claude()` invokes `claude -p` as a subprocess with a 120s timeout and returns raw stdout — there
  is no structured output parsing, just string prompts in and code out.
- `clean_code()` strips a leading/trailing ```` ``` ```` markdown fence if the model adds one despite
  being told not to; all written files pass through this before hitting disk.
- The fix loop in `main()` is intentionally simple: on failure it re-prompts with the *entire* pytest
  output appended, asking for a fixed version of the whole function — there's no diffing or partial
  patching.
- Exit codes carry the outcome: `0` if tests pass within the attempt budget, `1` if the budget is
  exhausted with tests still failing — useful for scripting/CI around this loop.
