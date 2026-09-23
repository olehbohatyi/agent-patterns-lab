import sys

from agent_common import parse_cli, run_tests, write_solution_and_tests


def main(task_description: str):
    write_solution_and_tests(task_description, announce=False)

    passed, output = run_tests()
    print(f"Result: {'PASSED' if passed else 'FAILED'}")
    print(output[-500:])
    return passed

if __name__ == "__main__":
    task = parse_cli("Single-attempt agent: write a function and tests, run the tests once.")
    result = main(task)
    sys.exit(0 if result else 1)
