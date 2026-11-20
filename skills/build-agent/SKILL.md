---
name: build-agent
description: Guides building a Salesforce Agentforce agent end to end, from product-owner style discovery to a tested, activation-ready agent, running deterministic gates between phases and delegating each step to the agentforce-* skills. Use when the user asks to create, design, build, test or ship an Agentforce agent, says "I need an agent that...", "build me an agent", "take this agent to production", "is this agent ready to activate", or wants a guided flow for Agent Script (.agent files, aiAuthoringBundle). Do NOT use for a small edit to an existing .agent file (use agentforce-generate), for analysing a production session or trace (use agentforce-observe), or for an Einstein Bot that is not being migrated to Agentforce.
license: MIT
metadata:
  author: GooddyLife
  version: 0.3.0
---

# Build an Agentforce agent, end to end

You are the conductor. The `agentforce-*` skills from `forcedotcom/sf-skills` already know how to write Agent Script, design a persona, test and observe. This skill decides the order, says what must be true before moving on, and proves it with a script instead of with memory.

## Rules that never bend

1. **The repository's rules win.** Whatever the repo says about environments, who may deploy where, and what needs approval overrides every default in this skill. Read them in Phase 0 before touching anything.
2. **Evidence, not reports.** A gate passed only if its output is in the conversation. Never write "tested", "deployed" or "active" without the command output that shows it. A subagent saying it passed is not evidence; the output it pasted is.
3. **Production needs a yes for that exact version.** Never publish or activate in a production org without explicit approval, in this conversation, naming the agent and the version. Approval for a sandbox does not carry over.
4. **Never invent an answer.** If discovery leaves something open, ask. If you cannot ask (you are running as a subagent), stop and return the open questions.
5. **Conduct, do not re-implement.** Invoke the upstream skill for each step. Do not paraphrase its content from memory.
6. **Made-up data only.** Real customer data never goes into a spec, a test or an example.

## The gates

Run them with Python 3.9+. The script only reads: files on disk, and the org through `sf data query`.

```bash
python "${CLAUDE_SKILL_DIR}/scripts/gates.py" <gate> [args] --state .malfoy/<AgentApiName>/state.json
```

If `${CLAUDE_SKILL_DIR}` shows up unresolved, the script is at `scripts/gates.py` next to this file.

| Gate | Checks | Needs |
|------|--------|-------|
| `doctor` | `sf` on PATH, upstream skills installed | nothing |
| `recon [REPO]` | prints the repo profile as JSON: packages, existing agents, rule files to read, CI, orgs | a DX project |
| `spec SPEC.md` | every section present and filled, no template placeholder left, use cases have IDs, at least one guardrail, structured `**Messy records:**` (S8). `--final`: every action is `EXISTS`; non-empty `## Salesforce Data Access` when actions exist (S9) | the spec |
| `lint FILE.agent` | pitfalls the compiler does not report (codes L1 to L7). `--final`: unfinished markers become errors | the `.agent` |
| `coverage SPEC.md TESTS.yaml [-o ORG] [--final] [--agent FILE.agent]` | every `UC-nn` tagged; `# messy-data` when S8 applies (C6–C11 with `-o`); each refuse item `# OOS-nn` (C12); customer-facing needs `# security` or `# prompt-injection` (C13) | spec and tests; `-o` needs an org |
| `handoff SPEC.md FILE.agent [--tests TESTS.yaml] [--results RESULTS.json]` | escalation path vs `**Escalation target:**` / `support-channels-only` (H1–H6); instruction heuristics (H5/H9); `--tests` / `--results` for trace proof (H7–H11) | spec and `.agent` |
| `perms FILE.agent -o ORG [--spec SPEC.md] [--tests TESTS.yaml]` | Apex/Flow access; optional object/field table (`--spec`, P9–P12); `UserRecordAccess` on test record IDs (`--tests`, P13) | an authenticated org |
| `active AGENT -o ORG` | which version is `Active`, read from the org. `--version N` to require one. With `--state`, a pass appends to `releases.json` which files shipped as that version, with the commit | an authenticated org |
| `testrun TESTS.yaml -o ORG` | records a passing agent test run in the state file (`test_run`: time, org API version, bundled platform release, hash of `tests.yaml`). Use `--result-json` from `sf agent test run` or `--pass` after trace evidence. Rules T1–T4 | an authenticated org |
| `observe SPEC.md -o ORG` | after go-live, compares the `## Success Metric` to live data. Warns on M4 when below target; never blocks release. Uses MessagingSession SOQL when it works, else `--input` JSON with documented counts from observability exports. Rules M1–M5 | an authenticated org and `--state` |
| `status STATE.json` | what passed, failed, never ran, or went `STALE` because its input changed. `--require a,b,c`. Also the last release per org: `SHIPPED`, or `DRIFT` with the files changed since; `RETEST` lines (T1–T3) when the suite should run again after a seasonal release or org API change (`-o ORG`) | the state file |

Exit code 0 is pass, 1 is fail, 2 means the gate could not run (say so; it is not a pass). When a gate fails, fix the cause and run it again. Do not argue with it, and do not move on with a `STALE` gate.

## The phases

| # | Phase | Skill to invoke | Gate to pass before moving on |
|---|-------|-----------------|-------------------------------|
| 0 | Set up and read the repo | none | `doctor`, `recon` |
| 1 | Discovery: the spec | `agentforce-generate` (spec template) | `spec`, then the user approves the spec |
| 2 | Architecture | `agentforce-generate` | `spec` again |
| 3 | Persona | `agentforce-persona-generate` | score at or above the agreed minimum |
| 4 | Build | `agentforce-generate` | local compile clean, `lint`, `handoff`, `perms` |
| 5 | Test | `agentforce-test` | `coverage`, every test passing on trace evidence |
| 6 | Release | `agentforce-generate` | `status`, `spec --final`, `lint --final`, `handoff`, then `perms` and `active` per org |
| 7 | Observe | `agentforce-observe`, `agentforce-d360-analyze` | `testrun` after Phase 5; `observe` once the Success Metric window has traffic; `status -o` for `RETEST` |

Keep every artifact of one agent in `.malfoy/<AgentApiName>/`: `spec.md`, `tests.yaml`, `state.json`, `releases.json`. Unless the repo's rules say otherwise, suggest committing the folder with the agent: the commits in `releases.json` then show what changed in the spec between any two versions.

### Phase 0: set up and read the repo

Run `doctor`. If a required skill is missing, offer to run `scripts/install-upstream.sh` (it overwrites local copies of the `agentforce-*` skills). Then run `recon` and read [references/repo-recon.md](references/repo-recon.md) to turn its output into a repo profile. `recon` lists the alias of every org authenticated on the machine; when the session is recorded or shared, run it with `--no-orgs` and name the target org instead.

Classify the request before going on:

- **New agent:** continue to Phase 1.
- **Change to an existing agent:** have `agentforce-generate` reverse-engineer its spec first, then enter at Phase 1 with that spec.
- **Einstein Bot to migrate:** invoke `agentforce-bot-upgrade`, then enter at Phase 2 with the spec it produced.
- **Legacy (planner bundle) agent to migrate:** follow "Migrate a Legacy Agent" in `agentforce-generate`, then enter at Phase 2.
- **"Is it ready?":** skip to the gates of Phase 6 on the files that exist and report what is missing.

### Phase 1: discovery

Read [references/discovery.md](references/discovery.md). Ask only what is still unknown. Write `.malfoy/<AgentApiName>/spec.md` from the template in `agentforce-generate` (`assets/agent-spec-template.md`) plus the six sections discovery adds. Run `spec`. Then show the spec and get an explicit approval: it is the contract for everything after.

### Phase 2: architecture

Invoke `agentforce-generate` and follow its design guidance: subagent map, posture per subagent, variables only where justified. Map every action to something that already exists in the repo or the org before proposing new code; the repo profile and the skill's `discover.py` tell you what exists. Mark each action `EXISTS`, `NEEDS STUB` or `NEEDS CREATION`. Update the spec and run `spec` again.

### Phase 3: persona

Invoke `agentforce-persona-generate`. Agree a minimum score with the user before scoring (default 80 of 100). For a small internal agent the short path (DRAFT then ENCODE) is enough; say that you took it.

### Phase 4: build

Read [references/platform-gotchas.md](references/platform-gotchas.md) before writing instructions. Invoke `agentforce-generate` ("Create an Agent"). Compile locally as that skill requires, then run `lint` and `handoff`. Deploy dependencies to the build org only as the repo's rules allow, then run `perms` against that org (pass `--spec` once `## Salesforce Data Access` is filled in). A `perms` or `handoff` failure is a blocker: permission problems and broken handoff promises are silent at runtime.

### Phase 5: test

Invoke `agentforce-test`. Write `tests.yaml` in Testing Center format: `# UC-nn`, `# OOS-nn` per refuse item, `# security` or `# prompt-injection` for customer-facing agents, and `# messy-data` with real `recordId` values when S8 applies. Run `coverage --agent` (and `coverage -o` + `perms --tests` on the build org before `--final`). Run `handoff --tests` and, after an org suite, `handoff --results`. Preview and Testing Center runs need trace evidence; use live actions when results matter. When the suite passes, run `testrun --state` for later `RETEST` hints on `status`.

### Phase 6: release

Run `status --require spec,lint,coverage,handoff,perms`, then `spec --final`, `lint --final` and `handoff` with `--tests` again (and `--results` when org output is available). For each environment in the spec, in order:

1. Run `perms --spec --tests` against that org.
2. Show the org, the agent and the version, and get approval as the repo's rules require (always for production).
3. Before a production activation, note the version that is active now. That is the rollback.
4. Publish and activate following "Deploy, Publish, and Activate" in `agentforce-generate`.
5. Run `active` with `--version N --state` and show its output. Only then say it is active. The pass records the release in `releases.json`; if it says there were uncommitted changes, tell the user the commit alone will not reproduce that version.

### Phase 7: observe

This phase is mandatory after go-live, not optional prose.

1. **Re-test when the platform moves.** On a schedule and before major changes, run `status STATE.json -o ORG`. Treat every `RETEST` line (T1–T3) as work: no recorded passing test, a bundled seasonal GA after the last run, or the org API version no longer matches what was recorded. Re-run the agent test suite and `testrun` with `--state`.
2. **Success Metric.** Parse the window from `## Success Metric` (anchor: `releases.json` `at` for that org). Run `observe SPEC.md -o ORG --state STATE.json`. It warns on M4 when the rate is below target; it does not fail the release. If MessagingSession is not a faithful view (common for Agentforce Session Tracing in Data Cloud), export counts from observability and pass `--input` with `total_sessions`, `sessions_without_transfer`, and a `source` string you can cite.
3. **Deep dives.** Offer `agentforce-observe` or `agentforce-d360-analyze` when the gates flag a problem or the user asks. Use `agentforce-hyperclassifier-configure` and `agentforce-offline-experimentation-run` only when asked; replaying real conversations through another model moves customer data, so confirm the data may leave the org first.

`doctor --state STATE.json -o ORG` surfaces the same `RETEST` warnings as a pre-flight check.

## Examples

**Someone new.** User says: "I need an agent that tells customers where their order is." Run Phase 0, then ask the discovery questions one at a time with an example answer for each, write the spec, run `spec`, show it for approval, and continue phase by phase, saying which phase you are in and which gate comes next.

**Someone experienced.** User pastes a full Agent Spec and says it is approved. Save it, run `spec`. If it passes, go straight to Phase 2. If it fails, ask only for what the gate reported missing.

**"Is OrderStatus ready for production?"** Run `status`, `spec --final`, `lint --final`, `perms -o <production>` and report each result with its output. Do not activate anything; the question was whether it is ready.

## Troubleshooting

- **`CANNOT RUN ... sf is not on PATH`:** the Salesforce CLI is missing or the shell cannot see it. Org gates cannot run; say so instead of skipping them.
- **`doctor` reports D3:** a required upstream skill is not in the folders it looked in. Run `scripts/install-upstream.sh`, or set `MALFOY_SKILL_ROOTS` if the skills live somewhere else.
- **`status` says `STALE`:** a file changed after its gate passed. Run that gate again.
- **`status` or `doctor` says `RETEST`:** the agent is still `SHIPPED` but the recorded test run predates a newer bundled Salesforce GA and/or the org API version changed. Re-run tests and `testrun`.
- **`observe` M5:** session data is not available through the SOQL proxy. Use `--input` with documented counts, or enable Agentforce Session Tracing and Tableau Next per `agentforce-observe`.
- **`perms` P5:** the agent user has no access to an Apex class. Add the class to a permission set assigned to that user, deploy, run `perms` again.
- **The org has no Testing Center:** run preview tests only, and write in the release notes that the batch suite did not run.
