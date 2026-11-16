# Changelog

## Unreleased

Lessons from [the recorded run](examples/recorded-run):

- The example's `tests.yaml` named actions by their reasoning alias and the topic by the `start_agent` block. The Testing Center reports the action definition name and `topic_selector`; the example now uses both.
- Phase 5: judge anything that depends on an action's result with live actions, because simulated actions invent the result.
- Phase 0: `recon --no-orgs` when the session is recorded or shared.
- `platform-gotchas.md`: simulated actions, Testing Center names, published agents that the Metadata API cannot delete, intermittent CLI failures, Windows long paths, and the first `perms` run of a new agent user.

## 0.2.0 (2026-11-16)

### Added

- **Release log** (#1). With `--state`, a passing `active` appends to `releases.json` the org, the version, the hash of every file the recorded gates checked, the git commit and whether those files had uncommitted changes. `status` shows the last release per org as `SHIPPED`, or `DRIFT` with the files changed since. `git diff <commit> <commit> -- spec.md` then shows what changed in the contract between two versions.
- Unit tests for `perms` and `active`, with the org answers faked: they no longer need an org to be tested.
- A comparison row for which spec shipped as which version.
- Issue and pull request templates, and one required CI check (`all-gates`).

### Fixed

- `coverage` could pass with a use case untested. It counted a `# UC-nn` anywhere in the file; now only on a live test case: the comment lines right above an uncommented `- utterance:`, or that line itself.
- `state.json` was tied to one machine. Input paths are now relative to the state file, and hashes read CRLF as LF, so a committed state does not go `STALE` on another clone, in CI, or after a checkout with `core.autocrlf`. State files from 0.1.0 still read; files with CRLF go `STALE` once.
- Gates running in parallel lost each other's results or read a half-written state file. Access is now serialised with a lock file, and writes are atomic.
- `recon` and `status` crashed on JSON with a BOM, and a corrupt state file gave a traceback with exit 1. Both are handled; unreadable JSON is exit 2 (could not run).
- An `sf` call that never answered hung the gate. It now times out after 120 s (`MALFOY_SF_TIMEOUT`).
- CI ran twice for every branch push. It now runs on push only for `main`; branches are tested by their pull request.

## 0.1.0

First version.

- `malfoy` agent and `build-agent` playbook: eight phases from reading the repo to observing real sessions.
- `gates.py`: `doctor`, `recon`, `spec`, `lint`, `coverage`, `perms`, `active`, `status`, with stale detection.
- `install-upstream.sh`: installs the `agentforce-*` skills from `forcedotcom/sf-skills`.
- Example: `examples/order-status`.
