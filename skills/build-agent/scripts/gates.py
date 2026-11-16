#!/usr/bin/env python3
"""Malfoy gates: deterministic checks between the phases of building an Agentforce agent.

    gates.py doctor                          tools and upstream skills are installed
    gates.py recon [REPO] [--no-orgs]        repo profile as JSON (read-only)
    gates.py spec SPEC.md [--final]          Agent Spec is complete
    gates.py lint FILE.agent [--final]       pitfalls the compiler does not report
    gates.py coverage SPEC.md TESTS.yaml     every use case has a test
    gates.py perms FILE.agent -o ORG         the agent user can reach the action targets
    gates.py active AGENT -o ORG [--version N]   which version is active, from the org
    gates.py status STATE.json [--require a,b]   what passed, what failed, what went stale

Exit code: 0 pass, 1 fail, 2 could not run. With --state FILE a gate records its
result and a hash of its input files, so `status` can tell "passed" from "passed
on a file that has changed since". Stdlib only, Python 3.9+. Org gates only read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_SKILLS = ["agentforce-generate", "agentforce-persona-generate", "agentforce-test"]
OPTIONAL_SKILLS = [
    "agentforce-observe", "agentforce-d360-analyze", "agentforce-architecture-analyze",
    "agentforce-bot-upgrade", "agentforce-hyperclassifier-configure",
    "agentforce-offline-experimentation-run",
]
# Sections of the Agent Spec. The second half is the upstream agentforce-generate
# template; the first half is what Malfoy's discovery adds on top of it.
SPEC_SECTIONS = [
    "Purpose & Scope", "Use Cases", "Success Metric", "Out of Scope", "Human Handoff",
    "Data & Privacy", "Environments",
    "Behavioral Intent", "Subagent Posture", "Subagent Map", "Actions", "Agent Configuration",
]
PLACEHOLDERS = [
    "Agent_API_Name", "Agent Display Name", "Describe the agent's purpose", "action_name",
    "apex://ClassName", "username@identifier.org", "TBD", "TODO", "???",
]
UNFINISHED = re.compile(r"\b(TODO|FIXME|NEEDS STUB|NEEDS CREATION|PLACEHOLDER)\b")
UC = re.compile(r"\bUC-\d+\b")
TOP_BLOCK = re.compile(r"^([A-Za-z_]\w*)(?:\s+[\w.]+)?\s*:")
LITERAL_BLOCK = re.compile(r"^(\s*)[\w.\-]+\s*:\s*\|[+-]?\d*\s*$")
SF_TIMEOUT = int(os.environ.get("MALFOY_SF_TIMEOUT", "120"))  # seconds per sf call
RECORD_ID = re.compile(r"""["'](0[0-9A-Za-z]{14}(?:[0-9A-Za-z]{3})?)["']""")


class CannotRun(Exception):
    """The gate could not reach what it needs (no sf CLI, no org, bad file)."""


class Gate:
    def __init__(self, name: str):
        self.name, self.errors, self.warns, self.notes = name, [], [], []

    def error(self, code: str, msg: str, where: str = ""):
        self.errors.append((code, where, msg))

    def warn(self, code: str, msg: str, where: str = ""):
        self.warns.append((code, where, msg))

    def note(self, msg: str):
        self.notes.append(msg)

    def finish(self, state: str | None = None, inputs: list[str] | None = None) -> int:
        for level, rows in (("FAIL", self.errors), ("WARN", self.warns)):
            for code, where, msg in rows:
                print(f"{level}  {self.name}  {code}  {where + '  ' if where else ''}{msg}")
        for msg in self.notes:
            print(f"NOTE  {self.name}  {msg}")
        ok = not self.errors
        print(f"{'PASS' if ok else 'FAIL'}  {self.name}  "
              f"{len(self.errors)} error(s), {len(self.warns)} warning(s)")
        if state:
            record(state, self.name, ok, inputs or [], len(self.errors), len(self.warns))
        return 0 if ok else 1


def text_of(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise CannotRun(f"cannot read {path}: {exc.strerror}") from exc


def json_of(path: Path) -> dict:
    """A JSON file, or {} if it does not exist. Tolerates a BOM (Windows editors add one)."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise CannotRun(f"cannot read {path} as JSON: {exc}") from exc


def digest(path: str | Path) -> str:
    """Hash of the file with CRLF read as LF, so a checkout with core.autocrlf is not a change."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:16]


def relative_to(path: str, folder: Path) -> str:
    """`path` relative to `folder` with `/`, so a committed state file works on any clone."""
    try:
        return Path(os.path.relpath(Path(path).resolve(), folder.resolve())).as_posix()
    except ValueError:  # another drive on Windows: no relative path exists
        return str(Path(path).resolve())


@contextmanager
def locked(state: Path, wait: float = 10.0):
    """Serialise access to the state file across processes. Gates may run in parallel, and
    a read-modify-write without this loses results or reads a half-written file."""
    lock = state.with_name(state.name + ".lock")
    state.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except (FileExistsError, PermissionError):  # Windows: PermissionError while it is being deleted
            # ponytail: age-based stale-lock break; fine while a holder needs milliseconds
            with suppress(OSError):
                if time.time() - lock.stat().st_mtime > wait:
                    lock.unlink()  # left behind by a run that crashed
            if time.monotonic() > deadline:
                raise CannotRun(f"{lock} is held by another run; delete it if none is running")
            time.sleep(0.02)
    try:
        yield
    finally:
        with suppress(FileNotFoundError):
            lock.unlink()


def record(state: str, gate: str, ok: bool, inputs: list[str], errors: int, warns: int):
    path = Path(state)
    with locked(path):
        data = json_of(path)
        data[gate] = {
            "ok": ok, "errors": errors, "warnings": warns,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "inputs": {relative_to(p, path.parent): digest(p) for p in inputs},
        }
        temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)  # a crash mid-write leaves the old file, never half of one


# ---------------------------------------------------------------- spec

def sections(markdown: str) -> dict[str, tuple[int, list[str]]]:
    """Map each `## Heading` (normalised) to (line number, body lines)."""
    out, current = {}, None
    for number, line in enumerate(markdown.splitlines(), 1):
        if line.startswith("## "):
            current = re.sub(r"\s*\(.*?\)\s*$", "", line[3:]).strip().lower()
            out[current] = (number, [])
        elif current:
            out[current][1].append(line)
    return out


def use_case_ids(spec: str) -> list[str]:
    _, body = sections(spec).get("use cases", (0, []))
    return [m.group(0) for line in body if line.lstrip().startswith("|")
            for m in [UC.search(line)] if m]


def gate_spec(path: str, final: bool = False) -> Gate:
    gate, spec = Gate("spec"), text_of(path)
    found = sections(spec)
    for title in SPEC_SECTIONS:
        entry = found.get(title.lower())
        if entry is None:
            gate.error("S1", f"section `## {title}` is missing")
        elif not any(l.strip() and not l.strip().startswith("<!--") for l in entry[1]):
            gate.error("S2", f"section `## {title}` is empty", f"{path}:{entry[0]}")
    for number, line in enumerate(spec.splitlines(), 1):
        for token in PLACEHOLDERS:
            if token in line:
                gate.error("S3", f"placeholder `{token}` was never replaced", f"{path}:{number}")
    ids = use_case_ids(spec)
    if not ids:
        gate.error("S4", "no use case rows with an ID like `UC-01` in `## Use Cases`")
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        gate.error("S5", f"use case ID `{dup}` is used more than once")
    _, cases = found.get("use cases", (0, []))
    if ids and not any("guardrail" in l.lower() for l in cases if UC.search(l)):
        gate.error("S6", "no use case of type `guardrail`: say what the agent must refuse")
    statuses = re.findall(r"\*\*Status:\*\*\s*([A-Z ]+)", spec)
    pending = [s.strip() for s in statuses if s.strip() != "EXISTS"]
    if pending and final:
        gate.error("S7", f"{len(pending)} action(s) are not `EXISTS` yet: {', '.join(pending)}")
    elif pending:
        gate.note(f"{len(pending)} of {len(statuses)} action(s) still to build "
                  "(fine now, an error with --final)")
    return gate


# ---------------------------------------------------------------- lint

def top_blocks(lines: list[str]) -> dict[str, list[tuple[int, int]]]:
    """Top-level blocks of an .agent file: name -> [(first line index, end index)]."""
    starts = [(i, m.group(1)) for i, l in enumerate(lines)
              if l and not l[0].isspace() and not l.startswith("#")
              for m in [TOP_BLOCK.match(l)] if m]
    out: dict[str, list[tuple[int, int]]] = {}
    for n, (i, name) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        out.setdefault(name, []).append((i, end))
    return out


def key_value(lines: list[str], span: tuple[int, int], key: str) -> tuple[int, str] | None:
    pattern = re.compile(rf"^\s+{re.escape(key)}\s*:\s*(.*?)\s*$")
    for i in range(span[0] + 1, span[1]):
        match = pattern.match(lines[i])
        if match and not lines[i].lstrip().startswith("#"):
            return i, match.group(1).strip("\"'")
    return None


def agent_user(lines: list[str]) -> tuple[int, str, str] | None:
    """(line index, username, block it was found in) of default_agent_user."""
    blocks = top_blocks(lines)
    for block in ("access", "config"):
        for span in blocks.get(block, []):
            hit = key_value(lines, span, "default_agent_user")
            if hit:
                return hit[0], hit[1], block
    return None


def gate_lint(path: str, final: bool = False) -> Gate:
    gate, lines = Gate("lint"), text_of(path).splitlines()
    blocks = top_blocks(lines)
    if "language" not in blocks:
        gate.error("L1", "no top-level `language:` block. Declare `default_locale` "
                         "explicitly; an agent without it has been seen falling back to "
                         "en_US and answering in English mid-conversation", path)
    user = agent_user(lines)
    if user and user[2] == "config":
        gate.warn("L2", "`default_agent_user` is under `config:`; it moved to `access:` "
                        "(the compiler reports it as deprecated)", f"{path}:{user[0] + 1}")
    if user and re.fullmatch(r"005[0-9A-Za-z]{12}(?:[0-9A-Za-z]{3})?", user[1]):
        gate.error("L4", "`default_agent_user` must be a username, not a user ID",
                   f"{path}:{user[0] + 1}")
    agent_type = next((v for s in blocks.get("config", [])
                       for v in [key_value(lines, s, "agent_type")] if v), None)
    if agent_type and "Service" in agent_type[1] and not user:
        gate.error("L5", "a service agent needs `access: default_agent_user:`", path)

    i = 0
    while i < len(lines):  # L3: `#` inside a literal `|` block is prompt text
        match = LITERAL_BLOCK.match(lines[i])
        i += 1
        if not match:
            continue
        indent = len(match.group(1))
        while i < len(lines) and (not lines[i].strip()
                                  or len(lines[i]) - len(lines[i].lstrip()) > indent):
            if lines[i].lstrip().startswith("#"):
                gate.warn("L3", "line starting with `#` inside a `|` block is sent to the "
                                "model as prompt text, it is not a comment",
                          f"{path}:{i + 1}")
            i += 1

    for number, line in enumerate(lines, 1):
        if not line.lstrip().startswith("#"):
            for record_id in RECORD_ID.findall(line):
                gate.warn("L6", f"hardcoded record ID `{record_id}`: it will not exist in "
                                "the next org", f"{path}:{number}")
        marker = UNFINISHED.search(line)
        if marker:
            (gate.error if final else gate.warn)(
                "L7", f"unfinished marker `{marker.group(1)}`", f"{path}:{number}")
    return gate


# ---------------------------------------------------------------- coverage

def tagged_ids(tests: str) -> set[str]:
    """UC IDs tagged on a live test case: in the comment lines right above an uncommented
    `- utterance:`, or on the same line. A tag anywhere else (a commented-out case, a
    note) does not count, or a test that does not run would pass the gate."""
    tagged, pending = set(), set()
    for line in tests.splitlines():
        text = line.strip()
        if text.startswith("#") and "utterance" not in text:
            pending |= set(UC.findall(text))
        elif re.match(r"-\s*utterance\s*:", text):
            tagged |= pending | set(UC.findall(line))
            pending = set()
        elif text:
            pending = set()
    return tagged


def gate_coverage(spec_path: str, tests_path: str) -> Gate:
    gate, tests = Gate("coverage"), text_of(tests_path)
    wanted, tested = set(use_case_ids(text_of(spec_path))), tagged_ids(tests)
    if not wanted:
        gate.error("C1", "the spec has no use case IDs to cover", spec_path)
    cases = len(re.findall(r"^\s*-\s*utterance\s*:", tests, re.M))
    if not cases:
        gate.error("C2", "no `- utterance:` test cases found", tests_path)
    for missing in sorted(wanted - tested):
        gate.error("C3", f"`{missing}` has no test. Put `# {missing}` on the line above "
                         "a test case's `- utterance:`", tests_path)
    for extra in sorted(tested - wanted):
        gate.warn("C4", f"`{extra}` is tested but is not in the spec", tests_path)
    outcomes = len(re.findall(r"^\s*expectedOutcome\s*:", tests, re.M))
    if outcomes < cases:
        gate.warn("C5", f"{cases - outcomes} of {cases} test case(s) have no "
                        "`expectedOutcome`", tests_path)
    return gate


# ---------------------------------------------------------------- org gates

def sf(*args: str) -> dict:
    exe = shutil.which("sf")
    if not exe:
        raise CannotRun("Salesforce CLI (`sf`) is not on PATH")
    try:
        done = subprocess.run([exe, *args, "--json"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=SF_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise CannotRun(f"`sf {' '.join(args[:2])}` gave no answer in {SF_TIMEOUT}s: "
                        "check the org's auth and the network") from exc
    try:
        data = json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        raise CannotRun(f"`sf {' '.join(args[:2])}` returned no JSON: "
                        f"{(done.stderr or done.stdout).strip()[:300]}") from exc
    if data.get("status", 0) != 0:
        raise CannotRun(f"`sf {' '.join(args[:2])}` failed: {data.get('message', '')[:300]}")
    return data.get("result", {})


def soql(org: str, query: str) -> list[dict]:
    return sf("data", "query", "-q", query, "-o", org).get("records", [])


def quoted(values) -> str:
    return ",".join("'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"
                    for v in values)


def gate_perms(path: str, org: str) -> Gate:
    gate, text = Gate("perms"), text_of(path)
    lines = text.splitlines()
    apex = sorted({re.split(r"[$.]", name)[0]
                   for name in re.findall(r"apex://([\w$.]+)", text)})
    flows = sorted(set(re.findall(r"flow://(\w+)", text)))
    user = agent_user(lines)
    if not user:
        gate.note("no `default_agent_user`: an employee agent runs as the person using it, "
                  "so their permissions decide. Nothing to check here.")
        return gate
    people = soql(org, "SELECT Id, IsActive, Profile.UserLicense.Name FROM User "
                       f"WHERE Username = {quoted([user[1]])}")
    if not people:
        gate.error("P1", "`default_agent_user` does not exist in this org", path)
        return gate
    person = people[0]
    if not person.get("IsActive"):
        gate.error("P2", "the agent user is inactive")
    license_name = ((person.get("Profile") or {}).get("UserLicense") or {}).get("Name")
    if license_name != "Einstein Agent":
        gate.warn("P3", f"the agent user's license is `{license_name}`, "
                        "expected `Einstein Agent`")
    sets = soql(org, "SELECT PermissionSetId, PermissionSet.PermissionsRunFlow "
                     f"FROM PermissionSetAssignment WHERE AssigneeId = '{person['Id']}'")
    set_ids = [row["PermissionSetId"] for row in sets]
    if apex:
        classes = {row["Name"]: row["Id"] for row in soql(
            org, f"SELECT Id, Name FROM ApexClass WHERE Name IN ({quoted(apex)})")}
        for name in apex:
            if name not in classes:
                gate.error("P4", f"`apex://{name}`: class is not in this org")
        granted = {row["SetupEntityId"] for row in soql(
            org, "SELECT SetupEntityId FROM SetupEntityAccess "
                 f"WHERE SetupEntityType = 'ApexClass' AND ParentId IN ({quoted(set_ids)}) "
                 f"AND SetupEntityId IN ({quoted(classes.values())})")} if classes else set()
        for name, class_id in sorted(classes.items()):
            if class_id not in granted:
                gate.error("P5", f"`apex://{name}`: the agent user has no access to the class. "
                                 "The action would come back empty and the agent would "
                                 "carry on as if it had worked")
    if flows:
        active = {row["ApiName"]: row.get("ActiveVersionId") for row in soql(
            org, "SELECT ApiName, ActiveVersionId FROM FlowDefinitionView "
                 f"WHERE ApiName IN ({quoted(flows)})")}
        for name in flows:
            if name not in active:
                gate.error("P6", f"`flow://{name}`: flow is not in this org")
            elif not active[name]:
                gate.error("P7", f"`flow://{name}`: flow has no active version")
        if not any((row.get("PermissionSet") or {}).get("PermissionsRunFlow") for row in sets):
            gate.warn("P8", "none of the agent user's permission sets grants Run Flows")
    gate.note(f"checked {len(apex)} Apex and {len(flows)} Flow target(s) against "
              f"{len(set_ids)} permission set(s). Field and object permissions for what "
              "the actions read or write are NOT checked here.")
    return gate


def gate_active(agent: str, org: str, version: int | None = None) -> Gate:
    gate = Gate("active")
    rows = soql(org, "SELECT VersionNumber, Status FROM BotVersion WHERE BotDefinitionId IN "
                     f"(SELECT Id FROM BotDefinition WHERE DeveloperName = {quoted([agent])}) "
                     "ORDER BY VersionNumber DESC")
    live = [row["VersionNumber"] for row in rows if row.get("Status") == "Active"]
    if not rows:
        gate.error("A1", f"no agent named `{agent}` in this org")
    elif not live:
        gate.error("A2", f"`{agent}` has {len(rows)} version(s) and none is active")
    elif version is not None and version not in live:
        gate.error("A3", f"version {version} is not the active one (active: v{live[0]})")
    else:
        gate.note(f"`{agent}` v{live[0]} is Active in the org "
                  f"({len(rows)} version(s) in total)")
    return gate


# ---------------------------------------------------------------- doctor, recon, status

def skill_roots() -> list[Path]:
    env = os.environ.get("MALFOY_SKILL_ROOTS")
    if env:
        return [Path(p) for p in env.split(os.pathsep) if p]
    home = Path.home()
    return [Path(".claude/skills"), Path(".agents/skills"),
            home / ".claude/skills", home / ".agents/skills"]


def gate_doctor() -> Gate:
    gate = Gate("doctor")
    if sys.version_info < (3, 9):
        gate.error("D1", f"Python 3.9+ needed, this is {sys.version.split()[0]}")
    for tool, needed in (("sf", True), ("node", False), ("git", False)):
        if shutil.which(tool):
            continue
        (gate.error if needed else gate.warn)(
            "D2", f"`{tool}` is not on PATH" + ("" if needed else " (used by some phases)"))
    roots = skill_roots()
    for name, needed in [(s, True) for s in REQUIRED_SKILLS] + [(s, False) for s in OPTIONAL_SKILLS]:
        if any((root / name / "SKILL.md").is_file() for root in roots):
            continue
        if needed:
            gate.error("D3", f"skill `{name}` is not installed. Run scripts/install-upstream.sh")
        else:
            gate.warn("D4", f"optional skill `{name}` is not installed")
    gate.note("skills looked up in: " + ", ".join(str(r) for r in roots))
    return gate


def recon(repo: str, orgs: bool = True) -> dict:
    root = Path(repo)
    project_file = root / "sfdx-project.json"
    if not project_file.is_file():
        raise CannotRun(f"{project_file} not found: not a Salesforce DX project")
    project = json_of(project_file)
    kinds = ["aiAuthoringBundles", "bots", "genAiPlannerBundles", "aiEvaluationDefinitions",
             "flows", "classes", "permissionsets"]
    counts, agents = dict.fromkeys(kinds, 0), {"aiAuthoringBundles": [], "bots": []}
    for package in project.get("packageDirectories", []):
        for folder, dirs, files in os.walk(root / package.get("path", "force-app")):
            kind = Path(folder).name
            if kind in agents:
                agents[kind] += sorted(dirs)
            if kind in kinds:
                counts[kind] += len(dirs) if kind in agents or kind == "genAiPlannerBundles" \
                    else len([f for f in files if not f.endswith(".cls-meta.xml")])
    rules = [name for name in ("CLAUDE.md", "AGENTS.md", ".cursorrules", "CONTRIBUTING.md",
                               "README.md", ".github/copilot-instructions.md", ".claude/docs",
                               "docs") if (root / name).exists()]
    ci = [name for name in (".github/workflows", ".gitlab-ci.yml", "bitbucket-pipelines.yml",
                            "azure-pipelines.yml", "Jenkinsfile") if (root / name).exists()]
    profile = {
        "packageDirectories": [p.get("path") for p in project.get("packageDirectories", [])],
        "sourceApiVersion": project.get("sourceApiVersion"),
        "metadata": counts, "agents": agents,
        "rulesToRead": rules, "ci": ci,
    }
    if orgs:
        try:
            listing = sf("org", "list")
            seen = {}  # sandboxes are listed twice by the CLI; key on username, never print it
            for group in ("nonScratchOrgs", "scratchOrgs"):
                for org in listing.get(group, []):
                    seen[org.get("username")] = {
                        "alias": org.get("alias"),
                        "kind": "scratch" if group == "scratchOrgs" else
                                "sandbox" if org.get("isSandbox") else "production-or-dev",
                        "default": bool(org.get("isDefaultUsername")),
                        "connected": org.get("connectedStatus") == "Connected"}
            profile["orgs"] = list(seen.values())
        except CannotRun as exc:
            profile["orgs"] = f"unknown: {exc}"
    return profile


def status(state: str, require: list[str]) -> int:
    path = Path(state)
    with locked(path):
        data = json_of(path)
    ready = True
    for gate in require + [g for g in data if g not in require]:
        entry = data.get(gate)
        if entry is None:
            verdict = "MISSING"
        elif any(not (path.parent / p).exists() or digest(path.parent / p) != h
                 for p, h in entry["inputs"].items()):  # absolute keys (0.1.0 files) still work
            verdict = "STALE"
        else:
            verdict = "PASS" if entry["ok"] else "FAIL"
        if gate in require and verdict != "PASS":
            ready = False
        print(f"{verdict:8}{gate:10}{entry['at'] if entry else 'never run'}")
    print("READY: every required gate passed on the current files" if ready else
          "NOT READY: fix the gates above, then run them again")
    return 0 if ready else 1


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="gates.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="gate", required=True)
    sub.add_parser("doctor")
    p = sub.add_parser("recon"); p.add_argument("repo", nargs="?", default=".")
    p.add_argument("--no-orgs", action="store_true")
    p = sub.add_parser("spec"); p.add_argument("spec"); p.add_argument("--final", action="store_true")
    p = sub.add_parser("lint"); p.add_argument("agent"); p.add_argument("--final", action="store_true")
    p = sub.add_parser("coverage"); p.add_argument("spec"); p.add_argument("tests")
    p = sub.add_parser("perms"); p.add_argument("agent"); p.add_argument("-o", "--org", required=True)
    p = sub.add_parser("active"); p.add_argument("agent"); p.add_argument("-o", "--org", required=True)
    p.add_argument("--version", type=int)
    p = sub.add_parser("status"); p.add_argument("state")
    p.add_argument("--require", default="spec,lint,coverage")
    for name in ("spec", "lint", "coverage", "perms", "active"):
        sub.choices[name].add_argument("--state", help="JSON file to record the result in")
    a = parser.parse_args(argv)
    try:
        if a.gate == "doctor":
            return gate_doctor().finish()
        if a.gate == "recon":
            print(json.dumps(recon(a.repo, not a.no_orgs), indent=2))
            return 0
        if a.gate == "spec":
            return gate_spec(a.spec, a.final).finish(a.state, [a.spec])
        if a.gate == "lint":
            return gate_lint(a.agent, a.final).finish(a.state, [a.agent])
        if a.gate == "coverage":
            return gate_coverage(a.spec, a.tests).finish(a.state, [a.spec, a.tests])
        if a.gate == "perms":
            return gate_perms(a.agent, a.org).finish(a.state, [a.agent])
        if a.gate == "active":
            return gate_active(a.agent, a.org, a.version).finish(a.state, [])
        return status(a.state, [g for g in a.require.split(",") if g])
    except CannotRun as exc:
        print(f"CANNOT RUN  {a.gate}  {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
