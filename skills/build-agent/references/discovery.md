# Discovery: from a request to an approved spec

Read this at Phase 1. The goal is a spec that someone else could build from without asking the requester anything.

## How to ask

- **Start from what you already have.** Read the request, the repo profile and any document the user pointed to. Fill in what they answer. Ask only for what is left.
- **Match the person.** If they answer in product terms, ask one question at a time and give an example answer with each. If they hand you a spec or talk in subagents and actions, ask everything that is open in one message.
- **Ask for outcomes first, mechanics last.** "What should be different once this agent exists?" comes before "which Flow does it call?".
- **Do not accept a non-answer.** "It should be helpful" is not a guardrail. Ask for the sentence the agent must never say, or the request it must refuse.
- **Write down assumptions as questions.** If you had to assume something to keep going, list it under the section it affects, marked `ASSUMED`, and confirm it before approval.

## What the spec must answer

The first six are sections this skill adds. The rest come from the `agentforce-generate` template and are filled in Phase 2.

| Section | The question behind it | A good answer looks like |
|---------|------------------------|--------------------------|
| Purpose & Scope | What is the one job? Who talks to it, on which channel? | "Tell a customer where their order is, on website chat." |
| Use Cases | What will people actually ask, and what must happen each time? | A table with an ID per row (`UC-01`), a type, the situation, the required behaviour |
| Success Metric | How will anyone know it worked, in a number, by when? | "70% of order-status chats end without a transfer, first 30 days." |
| Out of Scope | What will it be asked that it must not do? | A short list. "Refunds. Address changes." |
| Human Handoff | When does a person take over, and where does the conversation go? | The trigger and the queue. "On request, or after two failed lookups." Add `**Escalation target:**` when it is not `@utils.escalate` (a Flow, Apex alias, or queue handoff). If the agent will only point to email or phone, add `**Handoff mode:** support-channels-only`, word informational `handoff` use cases without promising a transfer, and do not wire escalation |
| Data & Privacy | What does it read, write and collect? What may it show, and only after what check? | Named fields and the check that unlocks them. Add `**Messy records:**` naming which sandbox copy holds old or incomplete rows for testing |
| Environments | Which orgs, in what order, and who approves each move? | A table: stage, org, approver. Name a partial- or full-copy sandbox where realistic records live |

### Use case types

Every spec needs at least one of each of the first three, and the `spec` gate refuses a spec with no `guardrail`.

| Type | Meaning |
|------|---------|
| `happy` | the request the agent exists for, with everything it needs |
| `recovery` | the same request with something missing or wrong |
| `guardrail` | something the agent must refuse |
| `adjacent` | close to its job but outside it |
| `handoff` | the person wants a human |

The use case IDs are the thread through everything after: each test is tagged with the ID it covers, and the `coverage` gate fails while any ID has no test.

## Questions people forget to answer

Ask these when the request did not cover them. They are the ones that surface in production otherwise.

1. What does the agent do when the action it depends on returns nothing?
2. Which of its answers must come only from an action result, never from the model?
3. What language do users write in? Is it always the same one?
4. Who is the agent user it runs as, and who can change that user's permissions?
5. What happens to a conversation that the user abandons halfway?
6. Is there anything it may say to a verified person that it must not say to an anonymous one?
7. What is the rollback if the new version misbehaves in production?
8. Where will tests run against messy real records (oldest accounts, duplicate keys, half-filled fields), not only clean sample rows?

In Phase 2, add a `## Salesforce Data Access` table (one row per action) listing objects and fields the action reads or writes, in backticks. `perms --spec` checks the agent user's permission sets against it.

## Definition of ready

The spec is ready for approval when the `spec` gate passes, no line is marked `ASSUMED`, and the requester has read the Use Cases table and said it is complete. Show the whole spec for approval, not a summary of it.
