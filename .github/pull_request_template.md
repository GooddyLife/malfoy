## What and why

<!-- Link the issue: Closes #n -->

## Checklist

- [ ] A new or changed gate rule comes with a test in `tests/test_gates.py` that breaks the example and expects the right code
- [ ] `python -m unittest discover -s tests` passes locally (CI runs it on Linux, macOS and Windows)
- [ ] Standard library only, no new dependency
- [ ] Gates still only read: no DML, no deploys, no writes besides the state file
- [ ] No real data: no org IDs, usernames, customer or company names
- [ ] Any claim added to the README is backed by a check shown here
