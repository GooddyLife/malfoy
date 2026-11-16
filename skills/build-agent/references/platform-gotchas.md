# Platform gotchas

Read this before writing or reviewing `.agent` instructions (Phase 4) and before judging a test (Phase 5). Each item was observed on real Agent Script projects during 2026, with compiler `@sf-agentscript/agentforce` 2.9.x. The platform changes; when one of these matters to a decision, prove it in the org you are working in using the method given.

The upstream `agentforce-generate` skill has its own `production-gotchas.md` and `known-issues.md`. These are the ones it does not cover, or that cost the most time.

## Permissions fail silently

A service agent runs as its `default_agent_user`, not as the person who built it. When that user lacks access to an Apex class, a field or an object, the action does not raise an error the conversation can see. It returns empty, the agent carries on, and it may tell the customer it did what it did not do. The symptom reported is "it did not save", never "permission error".

- Check before testing: `gates.py perms` covers the user, Apex class access and Flow state. Field and object permissions are not derivable from the `.agent` file; check them against the fields each action reads and writes.
- Check as the agent user, by query. Confirming the permission set on your own admin user proves nothing.
- Where it is visible: in a preview trace, the enabled-tools step lists withheld actions with the reason `NO_USER_ACCESS`.

## A good reply is not proof that the action ran

The model can produce a plausible answer without calling the action. The proof that an action executed is the function step in the trace, with the input it received. Judge every test on the trace.

A common cause: the instructions describe the valid format of an input ("ask for the code in the format ABC-0000"). The model then validates the format itself and answers "not found" without calling anything. Tell it the opposite: call the action with the text as typed; the action validates, not you.

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

## Test with the input real people send

Test utterances written by the builder are complete, polite and correctly formatted. Real users send fragments, typos, two questions at once and answers to a question you did not ask. Include those, and include a user who changes their mind halfway.
