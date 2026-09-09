# agent-patterns-lab

A minimal demonstration of an "agent loop": a script that calls the `claude` CLI to generate a
solution and its tests, then repeatedly runs pytest and feeds failures back to Claude as a new
prompt until the tests pass or an attempt limit is reached.

The repo also compares that loop against a "linear" agent that gets only one attempt, to measure
whether the self-correction loop actually improves reliability (see [NOTES.md](NOTES.md)).

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
  BLOCK/OK against an explicit severity rubric; Python — not the model — computes the final verdict,
  failing if any category blocks.

  Calibration probes in [NOTES.md](NOTES.md) drove that design. The first version asked one verifier
  for a single holistic PASS/FAIL, and it waved through both a real path-traversal flaw and a
  hand-planted O(n²) regression, on the grounds that neither was a *demonstrated* failure in current
  usage — it was effectively punishing reviewers for honest hedging ("if untrusted input...", "at
  scale..."). Per-category isolation alone didn't fix that; an explicit rubric forbidding hedges as
  grounds for dismissal did, and both probes now correctly fail.

  A known limit remains: the verifier reads only review text, never the code. A fabricated "no issues
  found" review passes cleanly, so the pattern is only as reliable as the honesty of its reviewers.

All three scripts take the task description as a command-line argument, and all overwrite
`solution.py` and `test_solution.py` on each run — those two files are generated output, not
hand-authored source, and are gitignored.

## Requirements

- Python 3
- [pytest](https://pytest.org)
- The `claude` CLI available on `PATH` (only needed to run the agent scripts themselves, not to run
  the tests)

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
