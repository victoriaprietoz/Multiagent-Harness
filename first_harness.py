"""Educational multi-agent harness using Codex CLI with ChatGPT.

Requirements: Python 3.10+, Codex CLI installed and authenticated.
Run: python first_harness.py (PowerShell, Windows).
No pip packages or OpenAI API key are required.

Each `codex exec` call is independent. Python shares the state;
there is no automatic memory between these processes.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

WORK_DIR = Path(__file__).resolve().parent / "demo_harness_codex"
WORK_DIR.mkdir(exist_ok=True)
MAX_STEPS = 8
CODEX_TIMEOUT = 240

# A deliberately small exercise with tests controlled by the harness.
OBJECTIVE = (
    "Create calculator.py with add(a, b) and divide(a, b). "
    "Division by zero must raise ValueError. Do not use external dependencies."
)
TESTS = '''import unittest
from calculator import add, divide

class CalculatorTests(unittest.TestCase):
    def test_addition(self):
        self.assertEqual(add(2, 3), 5)
        self.assertEqual(add(-2, 1), -1)

    def test_division(self):
        self.assertEqual(divide(8, 2), 4)

    def test_zero(self):
        with self.assertRaises(ValueError):
            divide(1, 0)

if __name__ == "__main__":
    unittest.main()
'''
(WORK_DIR / "test_calculator.py").write_text(TESTS, encoding="utf-8")

SCHEMA = {
    "type": "object",
    "properties": {
        "agent": {
            "type": "string",
            "enum": ["planner", "programmer", "tester", "fixer", "finish"],
        },
        "reason": {"type": "string"},
    },
    "required": ["agent", "reason"],
    "additionalProperties": False,
}
(WORK_DIR / "decision.schema.json").write_text(
    json.dumps(SCHEMA, ensure_ascii=False, indent=2), encoding="utf-8"
)


def find_codex():
    """Find Codex in PATH or in a local VS Code extension on Windows."""
    executable = shutil.which("codex")
    if executable:
        return executable
    if sys.platform == "win32":
        candidates = []
        for editor_dir in (".vscode", ".vscode-insiders"):
            extensions = Path.home() / editor_dir / "extensions"
            candidates.extend(extensions.glob("openai.chatgpt-*/bin/windows-*/codex.exe"))
        for candidate in sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True):
            try:
                result = subprocess.run(
                    [str(candidate), "--version"], capture_output=True, timeout=10, check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if result.returncode == 0:
                return str(candidate)
    raise RuntimeError(
        "Cannot find Codex in PATH or the VS Code extensions. "
        "Install Codex CLI, then reopen your terminal."
    )


def call_codex(prompt, permission="read-only", structured_json=False):
    """Call Codex as a process, without an SDK or a separate API key."""
    executable = find_codex()
    command = [
        executable, "exec", "--skip-git-repo-check",
        "--sandbox", permission,
    ]
    if structured_json:
        command += ["--output-schema", str(WORK_DIR / "decision.schema.json")]
    command += ["-"]  # Read the prompt from stdin to avoid Windows quoting issues.

    try:
        result = subprocess.run(
            command, input=prompt, cwd=WORK_DIR,
            text=True, encoding="utf-8", errors="replace",
            capture_output=True, timeout=CODEX_TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Codex exceeded the time limit for this call.") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"Codex exited with error {result.returncode}:\n{result.stderr[-2500:]}"
        )
    output = result.stdout.strip()
    if not output:
        raise RuntimeError("Codex did not return a final response.")
    return output


def decide(state):
    """The coordinator uses Codex to choose the next agent dynamically."""
    instructions = f"""
You are the COORDINATOR of four agents. Never write files.
Objective: {OBJECTIVE}
Current state: {json.dumps(state, ensure_ascii=False)}
Choose ONE action:
- planner: if it is worth making a plan first (only once).
- programmer: if calculator.py still needs to be implemented.
- tester: if new or fixed code needs testing.
- fixer: if the tests have failed.
- finish: only if tests_passed=true.
Prefer the most useful action; you may skip the planner.
Respond strictly according to the provided JSON schema.
"""
    decision = json.loads(call_codex(instructions, structured_json=True))
    return decision["agent"], decision["reason"]


def run_tests():
    """Python performs validation; we do not rely on an LLM's opinion."""
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-p", "test_calculator.py", "-v"],
        cwd=WORK_DIR, capture_output=True, text=True, timeout=30, check=False,
    )
    log = (result.stdout + "\n" + result.stderr)[-3500:]
    return result.returncode == 0, log


def run_agent(name, state):
    """Four roles. Permissions apply to the Codex process, not a different model."""
    context = f"Objective: {OBJECTIVE}\nPlan: {state['plan']}\n"
    if name == "planner":
        state["plan"] = call_codex(
            context + "You are the PLANNER. Write a concise plan. Do not edit files.",
            permission="read-only",
        )[:1500]
        print("  Plan:", state["plan"][:180])

    elif name == "programmer":
        print("  Codex will create calculator.py in the demo folder.")
        call_codex(
            context + "You are the PROGRAMMER. Implement calculator.py in this folder. "
            "Do not change test_calculator.py or decision.schema.json. "
            "Work only in this folder. Do not install dependencies.",
            permission="workspace-write",
        )
        state["tests_run"] = False
        state["tests_passed"] = False

    elif name == "tester":
        # The reviewer may inspect files but cannot modify them.
        review = call_codex(
            context + "You are the TESTER. Read calculator.py and test_calculator.py. "
            "Briefly identify potential defects. Do not modify files.",
            permission="read-only",
        )
        ok, errors = run_tests()
        state["tests_run"] = True
        state["tests_passed"] = ok
        state["errors"] = "" if ok else errors
        print("  Tests:", "PASSED" if ok else "FAILED")
        print("  Review:", review[:180].replace("\n", " "))
        if not ok:
            print("  Error:", errors[-450:])

    elif name == "fixer":
        call_codex(
            context + "You are the FIXER. Fix calculator.py to pass these tests:\n"
            + state["errors"] + "\nDo not edit the tests or other files.",
            permission="workspace-write",
        )
        state["tests_run"] = False
        state["tests_passed"] = False

    # Always restore the controlled tests if someone modified them.
    test_file = WORK_DIR / "test_calculator.py"
    if test_file.read_text(encoding="utf-8") != TESTS:
        test_file.write_text(TESTS, encoding="utf-8")
        print("  Warning: the original tests were restored.")


def guard_decision(agent, state):
    """AI proposes actions; Python enforces the hard rules."""
    code_exists = (WORK_DIR / "calculator.py").exists()
    if state["tests_passed"]:
        return "finish"  # Never repeat after the tests pass.
    if agent == "finish":
        return "tester" if code_exists else "programmer"
    if agent == "planner" and state["plan"]:
        return "tester" if code_exists else "programmer"
    if agent in ("tester", "fixer") and not code_exists:
        return "programmer"
    if agent == "fixer" and not (state["tests_run"] and not state["tests_passed"]):
        return "tester" if code_exists else "programmer"
    if agent == "programmer" and code_exists:
        return "tester"
    return agent


def main():
    state = {
        "plan": "",
        "tests_run": False,
        "tests_passed": False,
        "errors": "",
        "history": [],
    }
    print("Working folder:", WORK_DIR)
    print("The harness can run up to", MAX_STEPS, "agents.\n")
    for step in range(1, MAX_STEPS + 1):
        proposal, reason = decide(state)
        agent = guard_decision(proposal, state)
        print(f"Step {step}: coordinator proposes {proposal} ({reason})")
        if agent != proposal:
            print(f"  Harness rule: run {agent} instead.")
        if agent == "finish":
            print("\nSUCCESS: tests passed. File:", WORK_DIR / "calculator.py")
            return
        run_agent(agent, state)
        state["history"].append(agent)
        state["history"] = state["history"][-8:]

    if state["tests_passed"]:
        print("\nSUCCESS: tests passed. File:", WORK_DIR / "calculator.py")
    else:
        print("\nSTOPPED: step limit reached without passing the tests.")
        print("Check:", WORK_DIR)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print("\nERROR:", exc, file=sys.stderr)
        sys.exit(1)
