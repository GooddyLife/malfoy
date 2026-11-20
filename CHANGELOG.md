# Changelog

## Unreleased

Nothing yet.

## 0.3.0 (2026-11-20)

Acknowledgements: thank **Arjun Gupta** and **Mohith Kumar** for feedback on LinkedIn that drove these changes.

### Added

- **`handoff` gate** (H1–H4, plus H5–H11). Still fails when the spec promises a live transfer but the `.agent` has no escalation path, when `**Handoff mode:** support-channels-only` still promises a transfer, or when that mode is declared while `@utils.escalate` remains wired. Also: optional `**Escalation target:**` cross-check (H6); informational `handoff` use cases no longer force a live path; live-transfer wording in `.agent` instructions is scanned (H5/H9, heuristic); with `--tests`, live handoff use cases must list escalation in `expectedActions` (H7–H8); with `--results`, org traces must show an escalation action (H10–H11).
- **Messy real records in the contract.** S8 requires structured `org \`ALIAS\`; criterion: …` with tokens such as `stale LastModifiedDate`, `empty \`Object.Field\``. `coverage` C6–C7 (`# messy-data`, `recordId`); with `coverage -o`, C8–C11 verify org alias, object resolution, and messy rows in SOQL.
- **`coverage` refuse and security** (C12–C14). Each `## Out of Scope` item becomes `OOS-nn` and must appear on a test; customer-facing agents need `# security` or `# prompt-injection` (C13).
- **`## Salesforce Data Access` when there are actions.** S9 warns while actions exist but the table is empty, and errors with `--final`.
- **Record visibility for the agent user.** `perms --tests TESTS.yaml` uses read-only `UserRecordAccess` on every `recordId` / `recordIds` in the tests (P13): field access alone does not prove the row is visible under OWD and sharing.
- **`## Salesforce Data Access`** in the spec (optional table). `perms --spec` checks `ObjectPermissions` and `FieldPermissions` for declared reads and edits (P9–P12). Without the section, behaviour matches 0.2.0.
- Unit tests for every new rule, with org answers faked as before.
- README FAQ: which spec and tests shipped as which org version (`releases.json` + `DRIFT`).
- Comparison table rows for messy records and live handoff.
- **`testrun` gate** (T1–T4). Records a passing agent test run in `state.json` (`test_run`: timestamp, org API version, bundled platform release, hash of `tests.yaml`). `status` and `doctor --state` emit `RETEST` (T1–T3) when a `SHIPPED` org needs the suite again after a seasonal GA or API upgrade; bundled GA dates are deterministic, patch levels are not.
- **`observe` gate** (M1–M5). Post-launch check of `## Success Metric` against MessagingSession SOQL or `--input` JSON; warns below target (M4), never blocks release. Documents when Data Cloud observability is the faithful source.
- Phase 7 in the playbook is mandatory: `testrun`, `observe`, and `status -o` for `RETEST`.

### Fixed (first end-to-end run against a real sandbox)

Every gate was run against a real full-copy sandbox with a dedicated read-only demo agent, a real `sf agent test run`, and messy records that exist in the org. The fakes in the unit tests had drifted from what the org and the CLI return; these are the fixes, each with a regression test built from the measured shapes.

- **`testrun`** read a top-level `success` flag that `sf agent test run` never writes, so every real run failed T4. It now reads `testCases[].testResults[]` (bare file from `--output-dir` or the `--json` envelope), fails T4 per failed case with the failing assertions, and warns T5 when the case count differs from `tests.yaml`.
- **`handoff --results`** parsed no rows from real output (wrong keys, `COMPLETED` read as "not passed"), so it passed without checking anything. It now reads `inputs.utterance`, `generatedData.actionsSequence` and `generatedData.topic`, checks failed handoff rows too, never flags non-handoff rows, and warns H11 when a handoff test has no result row.
- **Escalation on the trace:** Testing Center reports `@utils.escalate` as the topic `human` with an empty action list. H8 and H10 accept `expectedTopic: human` / topic `human`; `examples/order-status` UC-04 uses it.
- **`org_api_version`** queried `Organization.ApiVersion`, a column that does not exist: `testrun` on a pass, `status -o` and `doctor -o` exited 2. It now reads `apiVersion` from `sf org display`.
- **`observe`** read `SELECT COUNT()` from `records` (always empty; the count is `totalSize`) and counted sessions created after the window instead of inside it. It now counts the window `[release, release + N days]` and says the MessagingSession proxy is org-wide.
- **`coverage` C7/C11 and `perms` P13** lost `recordId` when another key (`expectedTopic`, …) came before it in the case, so the org checks of messy rows never ran. One parser now reads the whole case, in any key order, including `recordIds:` lists; tags anywhere in the comment block count.
- **`perms` P11** failed on fields that have no `FieldPermissions` row by design (`CaseNumber`, `Status`, `LastModifiedDate`, `Name`). It reads `EntityParticle.IsPermissionable`: those fields follow the object, and a declared field the object does not have is a P11 error.
- **`perms` P8** no longer counts an Omni-Channel `outbound_route_name` flow as a flow the agent user runs.
- **`lint` L6** read 15-letter words such as `OmniChannelFlow` as record Ids. Ids now need the reserved `0` in position 6.
- **`status`** given `releases.json` crashed with a `TypeError`; it now exits 2 with a clear message.
- **Release calendar:** Winter '27 (GA 2026-10-12) added; `doctor` warns D5 when the newest bundled GA is over 150 days old.
- A soft-wrapped `**Messy records:**` criterion is read whole; `## Success Metric` also parses Portuguese (`sem transferência`, `primeiros 30 dias`).
- Documented: `sf agent test create` drops `recordId` (Malfoy-only key; the utterance must carry the record reference), and the platform reports prompt-injection attempts as the topic `Prompt_Injection`.

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