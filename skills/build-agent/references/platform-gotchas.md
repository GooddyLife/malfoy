# Platform gotchas

Read this before writing or reviewing `.agent` instructions (Phase 4) and before judging a test (Phase 5). Each item was observed on real Agent Script projects during 2026, with compiler `@sf-agentscript/agentforce` 2.9.x. The platform changes; when one of these matters to a decision, prove it in the org you are working in using the method given.

The upstream `agentforce-generate` skill has its own `production-gotchas.md` and `known-issues.md`. These are the ones it does not cover, or that cost the most time.

## Permissions fail silently

A service agent runs as its `default_agent_user`, not as the person who built it. When that user lacks access to an Apex class, a field or an object, the action does not raise an error the conversation can see. It returns empty, the agent carries on, and it may tell the customer it did what it did not do. The symptom reported is "it did not save", never "permission error".

- Check before testing: `gates.py perms` covers the user, Apex class access and Flow state. Pass `--spec` when the spec has `## Salesforce Data Access`: then the gate checks object and field reads (and edits if declared) via `ObjectPermissions` and `FieldPermissions`. Without that section, only Apex and Flow targets are checked.
- Check as the agent user, by query. Confirming the permission set on your own admin user proves nothing.
- Where it is visible: in a preview trace, the enabled-tools step lists withheld actions with the reason `NO_USER_ACCESS`.
- A user from `sf org create agent-user` gets the standard Agentforce permission sets and nothing else: no access to your Apex classes. Expect `perms` P5 on its first run ([recorded run](../../../examples/recorded-run)).

## A good reply is not proof that the action ran

The model can produce a plausible answer without calling the action. The proof that an action executed is the function step in the trace, with the input it received. Judge every test on the trace.

A common cause: the instructions describe the valid format of an input ("ask for the code in the format ABC-0000"). The model then validates the format itself and answers "not found" without calling anything. Tell it the opposite: call the action with the text as typed; the action validates, not you.

## Simulated actions invent the result

`sf agent preview start --simulate-actions` does not call the action: the model writes its output. In the recorded run, a probe asked the agent to confirm a protocol that does not exist; the simulated lookup returned `found: true` with a made-up status, and the agent repeated it. The same probe with `--use-live-actions` got "not found". Simulation tests routing and whether the action is chosen. Anything that depends on what the action returns, fabrication above all, needs live actions in a sandbox with made-up data.

## Declare the locale

`language:` is optional, but leaving it out does not mean "no locale". The runtime has been seen resolving `en_US` and instructing the model to reply only in that language. A long prompt in another language hides this until the planner switches to a system topic (inappropriate content, safety limit), where your instructions are not applied and the reply comes out in English mid-conversation. Repeating "answer in Portuguese" in `system:` does not fix it. Declaring `default_locale` does. `gates.py lint` reports a missing block as L1.

Prove it in the trace: look for the resolved locale in the state variables of any node entry step.

## `#` inside a `|` block is prompt text

`instructions: |` is a literal block. A line starting with `#` inside it is content, sent to the model on every turn. Notes to colleagues written there become instructions, and cost tokens each turn. Comments anywhere else in the file are removed at compile time. `gates.py lint` reports these as L3; a Markdown heading you meant for the model is a legitimate use, so it is a warning.

## The agent user is per org

`default_agent_user` now belongs under `access:` (the compiler warns when it is under `config:`). Its value is a username that exists in one org. Promoting the same file to the next org without changing it fails or, worse, binds to the wrong user. The same applies to any record ID written into the file: `gates.py lint` reports them as L6.

## "Active" needs proof from the org

A deploy that reports success does not mean the version is active, and some activations only take effect through the UI. Read the state back:

```sql
SELECT VersionNumber, Status FROM BotVersion
WHERE BotDefinition.DeveloperName = 'YourAgent'
ORDER BY VersionNumber DESC
```

The relationship is `BotDefinition`, not `Bot` (that one fails with an invalid relationship error), and the Tooling API does not expose `BotVersion`; use the standard API. `gates.py active` runs the check and fails when no version, or not the expected one, is `Active`.

## Flows called by the agent must be active

A Flow target that exists only as a draft fails at runtime, and a Flow deployed as a draft can report a successful deploy. `gates.py perms` checks that each `flow://` target has an active version in the org.

## Testing Center names are not the ones in your instructions

Measured on a sandbox with Salesforce CLI 2.140 and a published Agent Script agent:

- `expectedActions` matches the action's **definition** name (the key under `actions:`, such as `lookup_order`), not the alias in `reasoning: actions:` (such as `lookup`). Some guidance says the opposite; check the action names in a first run's results.
- For an agent with only `start_agent` and no subagents, the reported topic is `topic_selector`, not the name of the `start_agent` block. Declare it on every case: leaving `expectedTopic` out still produces a failed topic assertion.

## A published agent cannot be deleted through the Metadata API

`sf project delete source` on the `Bot`, `GenAiPlannerBundle` and `AiAuthoringBundle` of a published agent fails with `invalid cross reference id`: the pieces reference each other. Deactivate it (`sf agent deactivate`, then `gates.py active` to prove it), and delete it in Setup. A draft that was never published can be deleted with the CLI. Use names that make a test agent obvious.

## The CLI fails intermittently

During one session, `sf agent preview` returned `Agent ... not found` and `read EINVAL` between calls that worked, `sf agent test run` lost its poll the same way, and `sf agent publish` once failed with `Error refreshing connection`. Retrying worked each time. Before retrying a publish, query `BotVersion` to see whether the first attempt created a version. A lost test poll can be resumed with `sf agent test results --job-id`.

On Windows, the metadata `sf agent publish` retrieves has paths long enough that git cannot add them without `git config core.longpaths true`.

## Test with the input real people send

Test utterances written by the builder are complete, polite and correctly formatted. Real users send fragments, typos, two questions at once and answers to a question you did not ask. Include those, and include a user who changes their mind halfway.

## Clean sample rows are not the production test

Builders often test on rows they just created. Production traffic hits the oldest accounts: missing emails, legacy duplicate keys, half-filled custom fields, records another integration owns. The spec must name where messy rows live (`**Messy records:**` under `## Environments` or `## Data & Privacy`), and `coverage` requires at least one live test tagged `# messy-data` with a `recordId` pointing at a row in that sandbox.

## A handoff promise without an action

Instructions that say "transfer to support" or "I will connect you to an agent" with no escalation wired fail in production as a confident lie. Wire the target named in `**Escalation target:**` (default `@utils.escalate`, or a Flow/Apex action), or declare `**Handoff mode:** support-channels-only` in `## Human Handoff` and word informational `handoff` use cases so they only name phone, email or portal links without promising a transfer.

`handoff` checks the spec, the `.agent` (including reasoning/system instructions, with a keyword heuristic in support-channels-only mode), and—when you pass `--tests`—that live handoff cases list an escalation action in `expectedActions`. With `--results`, it checks org traces for an escalation step (heuristic name match).

Tag each refuse line from `## Out of Scope` with `# OOS-01`, `# OOS-02`, … on a test case. Customer-facing agents need at least one test tagged `# security` or `# prompt-injection`.
