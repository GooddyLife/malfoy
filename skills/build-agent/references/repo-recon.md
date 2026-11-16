# Reading the repository

Read this at Phase 0, right after `gates.py recon`. The script counts and lists; you read and decide.

## What to read

1. **Every entry in `rulesToRead`** from the recon output (`CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`, docs folders). Follow pointers inside them to other documents about deployment, agents or environments.
2. **The existing agents** listed under `agents`: open each `.agent` file's `config`, `access` and `language` blocks. They show the naming convention, the agent user and the locale this team already uses.
3. **CI definitions** listed under `ci`: they show which branch deploys to which org and what runs on a pull request.

## What to extract

Write the profile with these headings. Keep it short; it is read at the start of every later session.

| Heading | What goes in it |
|---------|-----------------|
| Environments | each org alias, what it is for, and which branch (if any) maps to it. Mark the production org. |
| Deploy rules | who may deploy where, what needs approval, what must never be done from the CLI |
| Agent conventions | naming of agents, actions, permission sets; the agent user; the default locale |
| Reusable actions | Flows and Apex classes that existing agents already call, and others that look callable |
| Testing | where test specs live, whether the org has Testing Center, what CI runs |
| Open questions | anything the rules do not say and you will need (ask once, then record the answer) |

Save it where it survives the session: your agent memory if you have one, otherwise `.malfoy/repo-profile.md`. On later sessions run `recon` again and re-read only what changed.

## Precedence

The repository's rules override this skill's defaults, always. When the repo is silent, use these defaults and tell the user you are doing so:

- Treat any org not explicitly marked as a sandbox or scratch org as production.
- Ask before the first deploy to each org, even a sandbox.
- Production: prepare, validate, and ask for approval naming the agent and the version. Never activate on your own initiative.
- If the default org of the CLI is production, pass `-o` explicitly on every command.

The `kind` field in the recon output says `production-or-dev` when the CLI cannot tell a production org from a Developer Edition one. Ask.

## What not to do

- Do not print usernames, org IDs or instance URLs into the profile. Aliases are enough.
- Do not retrieve or deploy anything during recon. It is read-only.
- Do not copy rules into the profile word for word. Link to the file and summarise the decision.
