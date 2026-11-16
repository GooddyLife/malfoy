# Example: OrderStatus, from request to ready

One small agent followed through every phase. The store ("Acme Outdoor"), its customers and its orders are made up.

**What is real here:** the spec, the Agent Script and the tests in this folder, and the output of the local gates (`doctor`, `spec`, `lint`, `coverage`, `status`), copied from actual runs with the home folder shortened to `~`. The `.agent` file compiles with no diagnostics on the Agent Script compiler 2.9.27.

**What is not a recording:** the conversation, and the steps that need an org. The two org gate blocks (`perms`, `active`) show output from runs against a real sandbox with the agent, class and org names replaced by this example's; each says so.

Run the gates yourself from the repository root:

```bash
G="python skills/build-agent/scripts/gates.py"
cd examples/order-status
```

## The request

> I need an agent that tells customers where their order is.

## Phase 0: read the repo

Malfoy runs `doctor`, then `recon`, and reads what the recon output points to before asking anything.

```console
$ $G doctor
NOTE  doctor  skills looked up in: .claude/skills, .agents/skills, ~/.claude/skills, ~/.agents/skills
PASS  doctor  0 error(s), 0 warning(s)
```

`recon` prints a JSON profile: package directories, API version, how many Flows and classes exist, the agents already in the repo, which rule files to read (`CLAUDE.md`, `AGENTS.md`, …), CI, and the orgs the CLI knows by alias and kind. It never prints usernames.

## Phase 1: discovery

The request answers one question out of seven. He asks for the rest, one at a time:

1. Where do customers talk to it? *Website chat.*
2. What must it refuse? *Anyone else's order. Anything that is not order status.*
3. When does a person take over, and where does the chat go? *On request, or after two failed lookups. Customer Care queue.*
4. What may it show, and after what check? *Status and delivery date, once the email matches the order.*
5. How will you know it worked? *70% of these chats end without a transfer, first 30 days.*
6. Which orgs, and who approves each move? *Developer sandbox, staging, production. Support lead; plus the release owner for production.*

The answers become [`spec.md`](spec.md): five use cases with IDs, one of them a guardrail.

```console
$ $G spec spec.md --state state.json
NOTE  spec  1 of 1 action(s) still to build (fine now, an error with --final)
PASS  spec  0 error(s), 0 warning(s)
```

Then he shows the whole spec and waits for approval.

## Phases 2 and 3: architecture and persona

One job, one action, so one execution block and no subagents. The lookup is a Flow that does not exist yet: it stays `NEEDS STUB` in the spec. The persona is short: helpful, brief, never guesses.

## Phase 4: build

[`OrderStatus.agent`](OrderStatus.agent) is the result. Two lines in it come straight from the gotchas list: the instructions say the lookup validates the input ("not you"), and the locale is declared.

```console
$ $G lint OrderStatus.agent --state state.json
PASS  lint  0 error(s), 0 warning(s)
```

What `lint` says when it is not happy, here after removing the `language:` block and leaving a note inside the `system` instructions:

```console
$ $G lint Broken.agent
FAIL  lint  L1  Broken.agent  no top-level `language:` block. Declare `default_locale` explicitly; an agent without it has been seen falling back to en_US and answering in English mid-conversation
WARN  lint  L3  Broken.agent:6  line starting with `#` inside a `|` block is sent to the model as prompt text, it is not a comment
FAIL  lint  1 error(s), 1 warning(s)
```

In a real run he would now deploy the Flow to the build sandbox and run `perms` against it. On an agent whose user cannot reach a class it looks like this (output of a run against a real sandbox, with the names replaced):

```console
$ $G perms OrderStatus.agent -o build-sandbox
FAIL  perms  P5  `apex://OrderLookup`: the agent user has no access to the class. The action would come back empty and the agent would carry on as if it had worked
FAIL  perms  1 error(s), 0 warning(s)
```

## Phase 5: test

[`tests.yaml`](tests.yaml) is in the Testing Center format, each case tagged with the use case it covers.

```console
$ $G coverage spec.md tests.yaml --state state.json
PASS  coverage  0 error(s), 0 warning(s)
```

Delete the `# UC-04` tag and the gate names what is missing:

```console
$ $G coverage spec.md tests-without-uc04.yaml
FAIL  coverage  C3  tests-without-uc04.yaml  `UC-04` has no test. Tag a test case with a comment such as `# UC-04`
FAIL  coverage  1 error(s), 0 warning(s)
```

## Phase 6: release

```console
$ $G status state.json
PASS    spec      2026-11-16T17:01:51+00:00
PASS    lint      2026-11-16T17:01:51+00:00
PASS    coverage  2026-11-16T17:01:51+00:00
READY: every required gate passed on the current files

$ $G spec spec.md --final
FAIL  spec  S7  1 action(s) are not `EXISTS` yet: NEEDS STUB
FAIL  spec  1 error(s), 0 warning(s)
```

`READY` for the local gates, and still not releasable: the Flow behind the only action has not been built. That is as far as this example goes without an org. With one, he would build the Flow, run `perms` per org, ask for approval naming the version, activate, and prove it (again the output of a run against a real sandbox, with the names and version replaced):

```console
$ $G active OrderStatus -o staging
NOTE  active  `OrderStatus` v1 is Active in the org (1 version(s) in total)
PASS  active  0 error(s), 0 warning(s)
```
