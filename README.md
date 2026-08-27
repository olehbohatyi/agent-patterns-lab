# agent-phase0

A minimal demonstration of an "agent loop": a script that calls the `claude` CLI to generate a
solution and its tests, then repeatedly runs pytest and feeds failures back to Claude as a new
prompt until the tests pass or an attempt limit is reached.

## How it works

1. `agent.py` prompts Claude to write `is_prime(n)` into [solution.py](solution.py).
2. It then prompts Claude to write pytest tests for that function into [test_solution.py](test_solution.py).
3. It runs the tests. If they fail, it sends the pytest output back to Claude and asks for a fix,
   up to `MAX_ATTEMPTS` (3) times. It stops as soon as the tests pass, or exits with an error if the
   attempt budget runs out.

`solution.py` and `test_solution.py` in this repo are the output of that loop for the `is_prime`
example — running `agent.py` again will overwrite both.

## Requirements

- Python 3
- [pytest](https://pytest.org)
- The `claude` CLI available on `PATH` (only needed to run `agent.py` itself, not to run the tests)

## Usage

Run the tests:

```bash
pytest test_solution.py -v
```

Run the full agent loop (overwrites `solution.py` and `test_solution.py`):

```bash
python agent.py
```
