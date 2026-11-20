# How the comparison was checked

The table in the [README](../README.md#the-official-skills-with-and-without-him) compares the `agentforce-*` skills of [forcedotcom/sf-skills](https://github.com/forcedotcom/sf-skills) used on their own with the same skills conducted by Malfoy. Every row on the upstream side was checked by reading or searching the upstream files at commit `788b83f` (package version 1.70.0) on 16 November 2026. To repeat it:

```bash
git clone --depth 1 https://github.com/forcedotcom/sf-skills && cd sf-skills/skills
```

Upstream changes often. If a row is out of date, open an issue: a wrong comparison is a bug.

## What this is not

It is not a benchmark. Nothing here measures whether agents built one way fail less than agents built the other. It compares what each approach enforces with a check, and what it leaves to the judgement of the model. A side-by-side run on the same request is planned and will be published with its transcripts, whatever it shows.

## Row by row

| Row | Upstream evidence | Malfoy side |
|-----|-------------------|-------------|
| Where you start | `ls -d agentforce-*/` lists nine skills. Each has its own trigger description; none conducts the others end to end | `agents/malfoy.md` and the phase table in `skills/build-agent/SKILL.md` |
| Persona | `grep -rl persona-generate agentforce-generate/` returns no file: the build skill never points to the persona skill | Phase 3 of the playbook |
| When a phase is done | `agentforce-generate/SKILL.md`, under "Deploy, Publish, and Activate": "Do not proceed through a blocking result." It is an instruction; no script records a result | `gates.py` exit codes and the `--state` file |
| The spec | `agentforce-generate/assets/agent-spec-template.md` has Purpose & Scope, Behavioral Intent, Subagent Posture, Subagent Map, Variables, Actions, Action Invocation Strategy, Deterministic Controls, Architecture Pattern and Agent Configuration. Searching the template and `references/agent-design-and-spec-creation.md` for `metric`, `privacy` or `pii` finds nothing | `SPEC_SECTIONS` in `gates.py`; rules S1 to S8 |
| Tests against the spec | `agentforce-generate/SKILL.md`: "During testing, test coverage maps against it." No file maps test cases to spec entries | `gate_coverage` in `gates.py`; rules C3 and C6 |
| Messy real records | No requirement to name a sandbox copy or test against incomplete production-shaped rows | S8 and C6 in `gates.py` |
| Live handoff | Handoff behaviour is prose in instructions; no check that `@utils.escalate` exists | `gate_handoff` in `gates.py`; rules H1 to H4 |
| The user the agent runs as | `agentforce-generate/references/agent-user-setup.md` documents `PermissionSetAssignment` queries as setup steps. `grep -rl SetupEntityAccess agentforce-*/` returns no file: nothing checks class access for that user | `gate_perms` in `gates.py`; rules P1 to P12 with optional `## Salesforce Data Access` |
| What the compiler stays quiet about | The compiler diagnostics, plus `references/known-issues.md` and `references/production-gotchas.md` to read | `gate_lint` in `gates.py`; rules L1 to L7 |
| "It's active" | `agentforce-generate/references/agent-access-guide.md` documents a `BotVersion` query to run | `gate_active` in `gates.py`; rules A1 to A3 |
| A file edited after its check passed | `agentforce-test/SKILL.md` tells the model to rewrite the security suite after any change to the agent. It is an instruction: no file records which version of an input a check ran on, so nothing detects the edit | `status` in `gates.py` compares a hash of each input |
| Which spec shipped as which version | `agentforce-generate/SKILL.md` saves the spec to `<AgentName>-AgentSpec.md` in the project. Searching the nine skills for a record of the published version next to the spec, or a hash of it, returns none: the only hashes are in `agentforce-hyperclassifier-configure` and `agentforce-offline-experimentation-run`, for experiment inputs | `record_release` and the release lines of `status` in `gates.py` |
| Your repo's deploy and approval rules | Searching the nine skills for `CLAUDE.md`, `AGENTS.md` or `CONTRIBUTING` returns no file. The design reference does tell the model to scan existing code and project metadata for context before asking questions | Phase 0 and `references/repo-recon.md` |

## Where upstream already covers the ground

Being fair to the skills Malfoy depends on:

- **Targets exist.** `agentforce-generate/scripts/discover.py` checks that the Flow, Apex and retriever targets of an `.agent` file exist in the org. Malfoy's `perms` overlaps with it on Flows and adds the access check for the agent user.
- **Compilation.** The upstream skill requires a local compile before anything is deployed. Malfoy relies on it and adds nothing there.
- **Approval before publishing.** Upstream also tells the model to get explicit approval before publishing or activating. Malfoy adds the read-back afterwards.
- **Security testing.** The OWASP-based test mode is entirely upstream's.
