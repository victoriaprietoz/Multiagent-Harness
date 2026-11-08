# Introduction to Agent Harnesses

`first_harness.py` is a small educational introduction to building an agent harness: a program that coordinates AI agents, shares state, and checks their results.

Using Codex CLI, a coordinator chooses between four roles—planner, programmer, tester, and fixer—to build a simple calculator. Python runs unit tests, enforces workflow rules, and stops when the tests pass or the eight-step limit is reached.

## Run

Requires Python 3.10+ and an installed, authenticated Codex CLI. No additional Python packages are needed.

```powershell
python first_harness.py
```

The demo creates its calculator, tests, and decision schema in `demo_harness_codex`.
