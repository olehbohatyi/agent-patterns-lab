# agent-phase0

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

Both scripts take the task description as a command-line argument, and both overwrite `solution.py`
and `test_solution.py` on each run — those two files are generated output, not hand-authored source,
and are gitignored.

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
