# agent-patterns-lab

A minimal demonstration of an "agent loop": a script that calls the `claude` CLI to generate a
solution and its tests, then repeatedly runs pytest and feeds failures back to Claude as a new
prompt until the tests pass or an attempt limit is reached.

The repo also compares that loop against a "linear" agent that gets only one attempt, to measure
whether the self-correction loop actually improves reliability (see [NOTES.md](NOTES.md); that
first comparison turned out not to establish it — see the correction in [FINDINGS.md](FINDINGS.md)).

**Start with [FINDINGS.md](FINDINGS.md)**: the consolidated, corrected results of the research phase,
with confidence levels and known gaps. [NOTES.md](NOTES.md) is the chronological lab notebook with the
evidence and every correction.

## How it works

- [agent_loop.py](agent_loop.py) — the self-correcting agent. It prompts Claude to write a function
  for a given task into [solution.py](solution.py), prompts Claude to write pytest tests for it into
  [test_solution.py](test_solution.py), then runs the tests. If they fail, it sends the pytest output
  back to Claude and asks for a fix, up to `MAX_ATTEMPTS` (3) times. It stops as soon as the tests
  pass, or exits with an error if the attempt budget runs out.
- [agent_linear.py](agent_linear.py) — the same generate-solution / generate-tests / run-tests flow,
  but with no fix loop: it runs the tests exactly once and reports the result, whatever it is.
- [agent_diamond.py](agent_diamond.py) — builds on the loop agent, but once tests pass it fans out to
  4 independent reviewers (security, performance, style, test coverage) run in parallel. Each review
  is then judged in isolation by a separate verifier that sees only that one review and answers
  BLOCK/OK against an explicit severity rubric — including a lane check, so an honest out-of-lane
  mention ("this is a correctness bug, not a security issue") doesn't block the wrong category; Python
  — not the model — computes the final verdict, failing if any category blocks.

  Calibration probes in [NOTES.md](NOTES.md) drove that design. The first version asked one verifier
  for a single holistic PASS/FAIL, and it waved through both a real path-traversal flaw and a
  hand-planted O(n²) regression, on the grounds that neither was a *demonstrated* failure in current
  usage — it was effectively punishing reviewers for honest hedging ("if untrusted input...", "at
  scale..."). An explicit rubric forbidding hedges as grounds for dismissal fixed that, but the lane
  check that followed traded some of that robustness away: when the category that actually owns a
  defect stays silent in a given run while everyone else correctly disclaims it as out-of-lane, nothing
  blocks. A known limit remains regardless: the verifier reads only review text, never the code, so the
  pattern is only as reliable as the honesty and completeness of its reviewers.

  Model tiering (sonnet vs. haiku reviewers) was also probed: routine findings tier down cleanly, but
  haiku's habit of restating an obvious defect across every lens — while sonnet stays scoped — turned
  out to make haiku *more* robust to the lane-check regression above, not less. See `NOTES.md` Phase 3.

- [agent_graph.py](agent_graph.py) — adds routing on top of the diamond pattern: whichever categories
  blocked are all passed into a single fix prompt (`route_fix()`), so one fix pass can address several
  findings at once instead of picking one category and dropping the rest. On a test-breaking fix, the
  loop reverts to the last known-good code and treats it as a failed attempt rather than exiting
  immediately, then re-reviews, up to `MAX_GRAPH_ATTEMPTS` (2). Routing itself is reliable — the right
  categories reach the fix prompt every time — but fix quality varies by category: security fixes, across
  several runs, either declined the finding outright (satisfying it by comment rather than code) or
  "fixed" it in a way that broke the task's own "accept any path" requirement (early runs did this via a
  hardcoded, cwd-scoped directory sandbox — plausibly tied to the older, per-category fix prompt this
  file used before switching to the single multi-target one, though that's not confirmed); performance
  fixes were clean whenever they fired, but the *reviewer* itself missed the underlying defect on some
  runs, so the route never got a chance to trigger. Also surfaced along the way: `claude -p` isn't a
  sandboxed blank slate — it can read files in the working directory unless told not to (verified
  directly), though the diamond judge doesn't do so in practice. See `FINDINGS.md` and `NOTES.md` Phase 4.

The scripts share their code through [agent_common.py](agent_common.py) (the `claude` call, output
validation, the test runner, and the write-solution/fix-until-green steps) and
[agent_review.py](agent_review.py) (the reviewers, the judge and the aggregation used by the diamond and
graph agents), so each `agent_*.py` is mostly its own control flow.

All four scripts take the task description as a command-line argument, and all overwrite
`solution.py` and `test_solution.py` on each run — those two files are generated output, not
hand-authored source, and are gitignored.

## Requirements

- Python 3
- [pytest](https://pytest.org)
- The `claude` CLI available on `PATH` (only needed to run the agent scripts themselves, not to run
  the tests)
- Optional, only for `--backend api`: `pip install anthropic` and an `ANTHROPIC_API_KEY` in the environment

## Usage

Run the tests for whatever solution/tests were last generated:

```bash
pytest test_solution.py -v
```

Run the self-correcting loop agent on a task:

```bash
python agent_loop.py "reverse a string"
```

Run the single-attempt linear agent on the same task:

```bash
python agent_linear.py "reverse a string"
```

Run the loop agent plus diamond review (4 parallel reviewers + aggregator) on a task:

```bash
python agent_diamond.py "reverse a string"
```

Run the diamond agent plus multi-target routed fixes (re-reviews after each fix, up to 2 rounds):

```bash
python agent_graph.py "reverse a string"
```

### Backends

Every agent accepts `--backend {local,api}`. The default, `local`, calls the `claude -p` CLI; `api` calls
the Anthropic Messages API through the Python SDK (model aliases `sonnet`/`haiku`/`opus` map to real API
model IDs in `agent_common.py`):

```bash
python agent_graph.py "reverse a string" --backend api
```

The two backends are not interchangeable systems: `claude -p` runs inside the repository and can read files
there, while an API call sees only the prompt. Everything in `FINDINGS.md` was measured on the local
backend, so it may not carry over to `api`. The switch itself is covered by `pytest test_backend.py`, which
makes no live API calls.

### Judges

The diamond and graph agents grade each review with a judge, selected by `--judge {llm,jev}`. The default,
`llm`, is the isolated `claude` verdict call the research was done with. `jev` asks a
[TypeSafe](https://docs.typesafe.ai) Jev model one yes/no question per review, using the same rubric text, and
blocks at or above a probability threshold (`JEV_BLOCK_THRESHOLD`, an uncalibrated 0.5). It needs
`uv pip install typesafe-sdk` and `TYPESAFE_API_KEY` in the environment; any failure to get an answer blocks,
with the failure category named in the report:

```bash
python agent_graph.py "reverse a string" --judge jev
```

This sends review text (which quotes generated code) to a third party, and it has not been calibrated against
the LLM judge yet; see `NOTES.md`. `pytest test_judge.py` covers it with a faked SDK and makes no live calls.

