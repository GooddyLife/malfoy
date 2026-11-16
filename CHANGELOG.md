# Changelog

## Unreleased

- `coverage` counts a `# UC-nn` tag only when it sits on a live test case: the comment lines right above an uncommented `- utterance:`, or that line itself. A tag on a commented-out case or in a note used to pass the gate.
- `recon` and `status` read JSON with a BOM, and a corrupt state file is exit 2 (could not run) instead of a traceback.
- Calls to `sf` time out after 120 s (`MALFOY_SF_TIMEOUT`) instead of hanging the gate.
- Unit tests for `perms` and `active`, with the org answers faked: they no longer need an org to be tested.

## 0.1.0

First version.

- `malfoy` agent and `build-agent` playbook: eight phases from reading the repo to observing real sessions.
- `gates.py`: `doctor`, `recon`, `spec`, `lint`, `coverage`, `perms`, `active`, `status`, with stale detection.
- `install-upstream.sh`: installs the `agentforce-*` skills from `forcedotcom/sf-skills`.
- Example: `examples/order-status`.
