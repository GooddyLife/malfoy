# Contributing

Malfoy is small on purpose: one agent, one playbook, one script of gates. Contributions that keep it that way are welcome.

## What this project wants most

**A new check for a way agents fail in production.** If you lost a day to something a script could have caught, open an issue describing:

1. What you saw (the symptom, as it was reported to you).
2. What the cause turned out to be.
3. How to prove it, in a file or with a read-only query.

If it can be checked deterministically it becomes a gate rule. If it cannot, it may belong in [`platform-gotchas.md`](skills/build-agent/references/platform-gotchas.md), with the way to prove it.

## Rules for a pull request

- **A gate rule comes with a test.** Each test in [`tests/test_gates.py`](tests/test_gates.py) breaks the passing example in one way and expects the right code. Add yours the same way. Run `python -m unittest discover -s tests`.
- **Standard library only.** `gates.py` has no dependencies and stays that way.
- **Gates read, they never write.** No DML, no deploys, no file changes outside the state file.
- **No real data.** No org IDs, usernames, customer names or company names in code, tests, examples or issues. Use made-up ones.
- **Do not copy upstream skill content in.** Malfoy invokes the `agentforce-*` skills; he does not redistribute them.
- **Claims need evidence.** If the README says something was checked, the pull request shows the check.

## What will be declined

New phases, a second agent, wrappers around the Salesforce CLI, and anything that makes a gate pass more easily.
