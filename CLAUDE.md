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
- [agent_graph.py](agent_graph.py) — builds on `agent_diamond.py` by routing: which category blocked
  picks a category-specific fix prompt (security, performance, or a generic fallback), applies the fix,
  re-reviews, and repeats up to `MAX_GRAPH_ATTEMPTS` (2) rather than reviewing once and stopping.

The four scripts share their code through two modules: [agent_common.py](agent_common.py) (`call_claude`,
`clean_code` and its undefined-name check, `run_tests`, and the two steps every agent repeats —
`write_solution_and_tests` and `fix_until_green`) and [agent_review.py](agent_review.py) (the four
reviewers, `judge_review`, `parse_verdict`, `aggregate_verdict`), the latter used by `agent_diamond.py`
and `agent_graph.py`. Each `agent_*.py` is now mostly its own `main()` flow.

All four take the task description as `sys.argv[1]`. [solution.py](solution.py) and
[test_solution.py](test_solution.py) are generated output overwritten on every run — they are
gitignored, not committed. Because all four scripts share those same two filenames, concurrent runs
in the same directory clobber each other — run one at a time.

[FINDINGS.md](FINDINGS.md) is the consolidated, corrected synthesis of the research phase — read it
first. [NOTES.md](NOTES.md) tracks the experiment log and conclusions phase by phase (Phase 0: baseline loop
agent; Phase 1: linear vs. loop comparison across 5 tasks, whose headline result was later found
unsupported — see the correction at the end of NOTES.md; Phase 2: diamond pattern, reviewer and
aggregator calibration probes, including a lane-aware rubric fix and its trade-offs; Phase 3: sonnet
vs. haiku reviewer tiering; Phase 4: graph routing, security vs. performance route comparison, and a
`claude -p` tool-access caveat). Its findings are the reason several prompts here are worded the way
they are — read it before "simplifying" them.

## Commands

Run the tests for whatever solution/tests were last generated:
```bash
pytest test_solution.py -v
```

Run the tests for the shared infrastructure's local/API backend switch (no live API calls; the
`anthropic` module is faked):
```bash
pytest test_backend.py -v
```

Run the tests for the `--judge` switch and the Jev judge (faked `typesafe_sdk`, no live calls):
```bash
pytest test_judge.py -v
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
python agent_graph.py "<task description>"
```
Each accepts `--backend {local,api}` (default `local`, the `claude -p` CLI; `api` uses the Anthropic SDK
and needs `pip install anthropic` plus `ANTHROPIC_API_KEY`). Don't run `--backend api` casually — it makes
billable requests. The backends are different systems (see the note under Architecture).
`agent_diamond.py`/`agent_graph.py` also accept `--judge {llm,jev}` (default `llm`; `jev` needs
`uv pip install typesafe-sdk` and `TYPESAFE_API_KEY`, and sends review text to TypeSafe).

Re-run just the diamond review against whatever is already on disk, skipping code generation (useful
for probing reviewer/judge behavior on hand-planted code):
```bash
python3 -c "
from agent_review import run_diamond_review, aggregate_verdict
code = open('solution.py').read()
tests = open('test_solution.py').read()
passed, report, _category_verdicts = aggregate_verdict(run_diamond_review(code, tests))
print('PASS' if passed else 'FAIL'); print(report)
"
```

`aggregate_verdict()` returns a 3-tuple (`passed, report, category_verdicts`); the per-category dict is
what `route_fix()` in `agent_graph.py` reads to build its fix prompt (`agent_diamond.py` ignores it).
Entering the graph loop's body directly (review → route → fix → re-test, skipping `main()`'s Step A/B
code generation) is how the Phase 4 probes in `NOTES.md` were run. (Before the shared-module refactor,
`agent_diamond.py` had its own copy returning a 2-tuple; older NOTES.md entries may show that.)

## Architecture notes

- `call_claude()` (in `agent_common.py`) dispatches on a module-level backend set once by `parse_cli()`:
  `local` invokes `claude -p` as a subprocess with a 120s timeout and returns raw stdout; `api` makes one
  Anthropic Messages API call (aliases mapped to real model IDs, one shared client, SDK errors propagate
  rather than reading as an empty answer). All findings were measured on `local`; `claude -p` can read the
  working directory and the API can't, so treat the backends as different systems. There is no structured
  output parsing, just string prompts in and code out. Prompts explicitly tell Claude
  not to write files itself (no tool use) — earlier versions that just said "save it in a single file"
  caused Claude to invoke its own Write tool instead of returning code as text, which the subprocess
  can't approve non-interactively and which silently corrupted the generated files.
- `clean_code()` extracts a fenced code block if one appears anywhere in the response, then validates
  the result parses as Python via `ast.parse` AND that it references no undefined names (via
  `find_undefined_names()`, a conservative static check — anything bound anywhere in the module counts
  as defined everywhere). The second check exists because `ast.parse` alone missed a real bug: a fix
  that used `os.path.*` without `import os` parsed fine and only failed at runtime, inside a function
  body pytest doesn't invoke until the test actually runs — `ast.parse`/a bare `exec()` of the module
  both miss that, since nothing calls the function at parse/exec time. If either check fails,
  `clean_code()` raises `NotPythonError` rather than writing broken content to disk.
- The test-generation prompt embeds `solution.py`'s actual content rather than just referencing the
  file by name. Without it the model can't know the real function name and guesses the import, which
  produced test files that failed with `NameError` — and the fix loop can't recover from that, since
  it only ever rewrites `solution.py`, never the test file.
- `fix_until_green()` (in `agent_common.py`, used by the loop, diamond and graph agents) is intentionally simple: on failure it re-prompts with the
  *entire* pytest output appended, asking for a fixed version of the whole function — there's no
  diffing or partial patching.
- In `agent_review.py` (used by `agent_diamond.py` and `agent_graph.py`), `judge_review()` grades one review at a time and never sees the other three;
  `aggregate_verdict()` then decides in Python (fail if any category blocks) rather than asking the
  model for a holistic verdict. The judge prompt carries an explicit severity rubric, including a
  clause that hedged phrasing ("if untrusted input...", "at scale...") is not grounds for dismissal —
  without it, judges waved through a real path-traversal flaw and an O(n²) defect as "not
  demonstrated." The rubric also carries a lane check: a defect the reviewer explicitly attributes to a
  different lens ("this is a correctness bug, not a security issue") doesn't block the category it was
  mentioned in. That closed one failure mode but opened another — if the category that actually owns a
  defect stays silent in a given run while every other reviewer correctly disclaims it as out-of-lane,
  nothing blocks at all (see NOTES.md Phase 3's "regression more severe than the fix"). Unparseable
  judge responses default to BLOCK, not OK; the judge is required to end its response with an explicit
  `VERDICT: BLOCK`/`VERDICT: OK` marker, parsed via `parse_verdict()` — reading just the first word was
  tried first and was actively wrong, since a judge reasoning aloud before answering ("BLOCK — wait,
  no... OK") got scored on the word it started with, not the verdict it reached.
- `--judge jev` swaps only the judge's substrate: `judge_review_jev()` asks one holistic Noul question per
  review with the same rubric (`_judge_rubric()`), blocks at `JEV_BLOCK_THRESHOLD` (0.5, uncalibrated), and
  fails safe to BLOCK on any error with the category named (`sdk-missing`, `schema-error`, `api-error`,
  `client-error`, `bad-response`). It is deliberately not split per criterion (that would change two things at
  once), and its only live calibration is a small 27-review comparison (`calibration/`, NOTES.md "Jev judge, stage 1 calibration") with no near-boundary cases, so the threshold is untested. Keep `judge_review()`
  as the dispatcher; `aggregate_verdict()` is unchanged.
- Known limit of the diamond design: the judge reads only review text, never the code, so a reviewer
  that misses a defect entirely cannot be caught downstream (confirmed directly by feeding a fabricated
  "no issues found" review against genuinely vulnerable code — clean OK). Giving the judge the code
  would close that gap but turn it into a fifth reviewer rather than an independent check. Separately:
  `claude -p` is not sandboxed by default — it can read files in the working directory (e.g. via git
  context, or actively via Read) unless told not to. Verified the judge doesn't do this in practice (3
  runs, no leaked file content), but a prompt relying on isolation should say "do not use tools" /
  "do not read any files" explicitly rather than assume it.
- `agent_graph.py`'s `route_fix()` picks a fix prompt by which category blocked (security → add
  validation/trust boundary; performance → fix the algorithmic complexity; anything else → generic),
  then the graph loop re-reviews after each fix rather than reviewing once, up to `MAX_GRAPH_ATTEMPTS`
  (2). Routing itself is reliable — right category, right prompt, every time it fires. Fix quality is
  not: the security route, across several runs, either refused to produce a fix at all or "fixed" the
  flaw by hardcoding a cwd-scoped directory sandbox that silently breaks the task's own "accept any
  path" requirement — a real, reproducible tension between "add a trust boundary" and a task spec that
  explicitly asks for unrestricted input, which one fix prompt can't reliably resolve. The performance
  route's fixes were clean every time they fired, but the reviewer itself sometimes misses the O(n²)
  pattern entirely, so routing never gets a chance to trigger.
- Exit codes carry the outcome: `0` if tests pass within the attempt budget, `1` if the budget is
  exhausted (loop) or the single attempt failed (linear). `agent_diamond.py`/`agent_graph.py`
  additionally exit `1` when review blocks, even with green tests — useful for scripting/CI around
  these loops.
