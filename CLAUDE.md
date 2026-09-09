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
- [agent_diamond.py](agent_diamond.py) — the loop agent plus a "diamond" review stage: once tests pass,
  4 reviewers (security, performance, style, test coverage) run in parallel over the same code, then
  each review is judged in isolation for BLOCK/OK and Python computes the final verdict.

All three take the task description as `sys.argv[1]`. [solution.py](solution.py) and
[test_solution.py](test_solution.py) are generated output overwritten on every run — they are
gitignored, not committed. Because all three scripts share those same two filenames, concurrent runs
in the same directory clobber each other — run one at a time.

[NOTES.md](NOTES.md) tracks the experiment log and conclusions phase by phase (Phase 0: baseline loop
agent; Phase 1: linear vs. loop comparison across 5 tasks; Phase 2: diamond pattern, reviewer and
aggregator calibration probes). Its findings are the reason several prompts here are worded the way
they are — read it before "simplifying" them.

## Commands

Run the tests for whatever solution/tests were last generated:
```bash
pytest test_solution.py -v
```

Run a single test:
```bash
pytest test_solution.py -v -k <test_name>
```

Run any agent on a task (overwrites `solution.py` and `test_solution.py`, requires the `claude` CLI
on PATH and one or more LLM round-trips):
```bash
python agent_loop.py "<task description>"
python agent_linear.py "<task description>"
python agent_diamond.py "<task description>"
```

Re-run just the diamond review against whatever is already on disk, skipping code generation (useful
for probing reviewer/judge behavior on hand-planted code):
```bash
python3 -c "
from agent_diamond import run_diamond_review, aggregate_verdict
code = open('solution.py').read()
tests = open('test_solution.py').read()
passed, report = aggregate_verdict(run_diamond_review(code, tests))
print('PASS' if passed else 'FAIL'); print(report)
"
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
- The test-generation prompt embeds `solution.py`'s actual content rather than just referencing the
  file by name. Without it the model can't know the real function name and guesses the import, which
  produced test files that failed with `NameError` — and the fix loop can't recover from that, since
  it only ever rewrites `solution.py`, never the test file.
- The fix loop in `agent_loop.py`'s `main()` is intentionally simple: on failure it re-prompts with the
  *entire* pytest output appended, asking for a fixed version of the whole function — there's no
  diffing or partial patching.
- In `agent_diamond.py`, `judge_review()` grades one review at a time and never sees the other three;
  `aggregate_verdict()` then decides in Python (fail if any category blocks) rather than asking the
  model for a holistic verdict. The judge prompt carries an explicit severity rubric, including a
  clause that hedged phrasing ("if untrusted input...", "at scale...") is not grounds for dismissal —
  without it, judges waved through a real path-traversal flaw and an O(n²) defect as "not
  demonstrated." Unparseable judge responses default to BLOCK, not OK.
- Known limit of the diamond design: the judge reads only review text, never the code, so a reviewer
  that misses a defect entirely cannot be caught downstream. Giving the judge the code would close
  that gap but turn it into a fifth reviewer rather than an independent check.
- Exit codes carry the outcome: `0` if tests pass within the attempt budget, `1` if the budget is
  exhausted (loop) or the single attempt failed (linear). `agent_diamond.py` additionally exits `1`
  when review blocks, even with green tests — useful for scripting/CI around these loops.
