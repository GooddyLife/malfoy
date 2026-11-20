# Changelog

## Unreleased

Nothing yet.

## 0.3.0 (2026-11-20)

Acknowledgements: thank **Arjun Gupta** and **Mohith Kumar** for feedback on LinkedIn that drove these changes.

### Added

- **`handoff` gate** (H1–H4). Fails when the spec promises a live transfer but the `.agent` has no `@utils.escalate`, when `**Handoff mode:** support-channels-only` still promises a transfer, or when that mode is declared while `@utils.escalate` remains wired.
- **Messy real records in the contract.** `spec` rule S8 requires `**Messy records:**` or `**Realistic test data:**` under `## Environments` or `## Data & Privacy`. `coverage` rule C6 requires at least one test tagged `# messy-data` when that declaration exists; C7 warns when those tests omit `recordId` / `recordIds`.
- **`## Salesforce Data Access`** in the spec (optional table). `perms --spec` checks `ObjectPermissions` and `FieldPermissions` for declared reads and edits (P9–P12). Without the section, behaviour matches 0.2.0.
- Unit tests for every new rule, with org answers faked as before.
- README FAQ: which spec and tests shipped as which org version (`releases.json` + `DRIFT`).
- Comparison table rows for messy records and live handoff.

### Changed

- Playbook, discovery reference and `examples/order-status` updated so the example passes the new gates.
- `platform-gotchas.md`: messy-record testing, handoff without an action, and how `perms --spec` works.

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