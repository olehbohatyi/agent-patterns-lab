import sys

from agent_common import fix_until_green, parse_cli, write_solution_and_tests


def main(task_description: str):
    # Step A: agent writes the first version of the solution
    code_text, _test_text = write_solution_and_tests(task_description)

    # Step B: check-and-fix loop — this is where the agent makes its own decisions
    fix_until_green(task_description, code_text)
    sys.exit(0)

if __name__ == "__main__":
    task = parse_cli("Self-correcting agent: write a function and tests, fix until the tests pass.")
    main(task)
