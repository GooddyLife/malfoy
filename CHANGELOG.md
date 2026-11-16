# Changelog

## Unreleased

- `coverage` counts a `# UC-nn` tag only when it sits on a live test case: the comment lines right above an uncommented `- utterance:`, or that line itself. A tag on a commented-out case or in a note used to pass the gate.
- `recon` and `status` read JSON with a BOM, and a corrupt state file is exit 2 (could not run) instead of a traceback.
- Calls to `sf` time out after 120 s (`MALFOY_SF_TIMEOUT`) instead of hanging the gate.
- Unit tests for `perms` and `active`, with the org answers faked: they no longer need an org to be tested.
- `state.json` is portable: input paths are stored relative to the state file, and hashes read CRLF as LF, so a committed state does not go `STALE` on another clone, in CI, or after a checkout with `core.autocrlf`. State files from 0.1.0 still read; files with CRLF go `STALE` once.
- Gates running in parallel no longer lose each other's results or read a half-written state file: access is serialised with a lock file, and writes are atomic.

## 0.1.0

First version.

- `malfoy` agent and `build-agent` playbook: eight phases from reading the repo to observing real sessions.
- `gates.py`: `doctor`, `recon`, `spec`, `lint`, `coverage`, `perms`, `active`, `status`, with stale detection.
- `install-upstream.sh`: installs the `agentforce-*` skills from `forcedotcom/sf-skills`.
- Example: `examples/order-status`.
