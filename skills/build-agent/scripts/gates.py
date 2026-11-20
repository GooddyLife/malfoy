#!/usr/bin/env python3
"""Malfoy gates: deterministic checks between the phases of building an Agentforce agent.

    gates.py doctor                          tools and upstream skills are installed
    gates.py recon [REPO] [--no-orgs]        repo profile as JSON (read-only)
    gates.py spec SPEC.md [--final]          Agent Spec is complete
    gates.py lint FILE.agent [--final]       pitfalls the compiler does not report
    gates.py coverage SPEC.md TESTS.yaml [-o ORG] [--final] [--agent FILE.agent]
                                             use cases, OOS and security tags; with -o, messy rows
    gates.py handoff SPEC.md FILE.agent [--tests TESTS.yaml] [--results RESULTS.json]
                                             escalation path, trace expectations, org run output
    gates.py perms FILE.agent -o ORG         the agent user can reach the action targets
    gates.py perms FILE.agent -o ORG [--spec SPEC.md] [--tests TESTS.yaml]
                                             optional object/field and record visibility (UserRecordAccess)
    gates.py active AGENT -o ORG [--version N]   which version is active, from the org;
                                             with --state, a pass is logged in releases.json
    gates.py testrun TESTS.yaml -o ORG       record a passing agent test run in the state file
                                             (--result-json from sf agent test run, or --pass)
    gates.py observe SPEC.md -o ORG            compare live sessions to the Success Metric
                                             (--input JSON when MessagingSession is unavailable)
    gates.py status STATE.json [--require a,b]   what passed, what failed, what went stale
                                             (-o ORG for platform/API re-test hints on SHIPPED)

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
from datetime import datetime, timedelta, timezone
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
HANDOFF_MODE = re.compile(r"\*\*handoff\s+mode:\*\*\s*support-channels-only", re.I)
MESSY_SOURCE = re.compile(
    r"\*\*(?:messy\s+records|realistic\s+test\s+data):\*\*", re.I)
MESSY_STRUCTURED = re.compile(
    r"\*\*(?:messy\s+records|realistic\s+test\s+data):\*\*\s*org\s+`([^`]+)`\s*;\s*criterion:\s*(.+)",
    re.I)
CRITERION_STALE = re.compile(r"stale\s+(LastModifiedDate|CreatedDate)(?:\s*\(>(\d+)d\))?", re.I)
CRITERION_EMPTY = re.compile(r"empty\s+`([^`]+)`", re.I)
SOBJECT_NAME = re.compile(r"^[A-Za-z_][\w]*(?:__c)?$")
FIELD_TOKEN = re.compile(r"^[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)?$")
STALE_DEFAULT_DAYS = 365
TRANSFER_PROMISE = re.compile(
    r"\b(?:transfer(?:s|ring)?(?:\s+(?:to|the|right\s+away))?|(?:goes|route[ds]?|connect(?:s|ed)?)\s+to\s+"
    r"(?:a\s+)?(?:person|human|agent|queue)|live\s+(?:agent|handoff)|"
    r"hand\s+off\s+to\s+(?:a\s+)?(?:person|queue))\b", re.I)
TRANSFER_DENIAL = re.compile(
    r"\b(?:no|not|never|without)\s+(?:promis(?:e|ing)\s+)?(?:a\s+)?(?:live\s+)?transfer|"
    r"cannot\s+(?:make|perform)|does\s+not\s+transfer", re.I)
ESCALATE_IN_AGENT = re.compile(r"@utils\.escalate\b")
ESCALATION_TARGET = re.compile(
    r"\*\*\s*Escalation target:\s*\*\*\s*(.+?)(?:\s*$|\*\*)", re.I | re.M)
INFORMATIONAL_HANDOFF = re.compile(
    r"(?i)\b(?:without\s+promis(?:e|ing)|say\s+how\s+to\s+reach|"
    r"no\s+live\s+transfer|support\s+(?:phone|email|channel)|only\s+(?:name|point\s+to))",
)
OOS_TAG = re.compile(r"\bOOS-\d{2,}\b")
SECURITY_TAG = re.compile(r"\b(?:security|prompt-injection)\b", re.I)
ESCALATE_ACTION_NAME = re.compile(
    r"(?i)(?:^|_)escalat|handoff|transfer_to|live_transfer")
SALESFORCE_FIELD = re.compile(r"`([A-Za-z_][\w]*(?:__c)?)\.([A-Za-z_][\w]*)`")
SALESFORCE_OBJECT = re.compile(r"`([A-Za-z_][\w]*(?:__c)?)`")
TOP_BLOCK = re.compile(r"^([A-Za-z_]\w*)(?:\s+[\w.]+)?\s*:")
LITERAL_BLOCK = re.compile(r"^(\s*)[\w.\-]+\s*:\s*\|[+-]?\d*\s*$")
SF_TIMEOUT = int(os.environ.get("MALFOY_SF_TIMEOUT", "120"))  # seconds per sf call
STATE_META = frozenset({"test_run"})  # not gates; do not list in `status` as PASS/FAIL
RECORD_ID = re.compile(r"""["']([a-zA-Z0-9]{15}(?:[a-zA-Z0-9]{3})?)["']""")
# Bundled GA dates for seasonal Salesforce releases (calendar is deterministic; patch level is not).
PLATFORM_RELEASES = [
    ("Winter '24", "2023-10-13"), ("Spring '24", "2024-02-26"), ("Summer '24", "2024-06-10"),
    ("Winter '25", "2024-10-14"), ("Spring '25", "2025-02-25"), ("Summer '25", "2025-06-17"),
    ("Winter '26", "2025-10-14"), ("Spring '26", "2026-02-24"), ("Summer '26", "2026-06-16"),
]
SUCCESS_PCT = re.compile(
    r"(\d+)\s*%.*?without\s+(?:a\s+)?transfer", re.I | re.S)
SUCCESS_WINDOW = re.compile(r"(?:first|over\s+the\s+first)\s+(\d+)\s+days?", re.I)


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


def write_json(path: Path, data: dict):
    """Atomic write; the caller holds the lock. A crash leaves the old file, never half of one."""
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def changed_inputs(folder: Path, inputs: dict[str, str]) -> list[str]:
    """Inputs missing or different from the recorded hash. Keys are relative to `folder`;
    absolute keys (0.1.0 state files) still work, since joining keeps an absolute path."""
    return [p for p, h in inputs.items()
            if not (folder / p).exists() or digest(folder / p) != h]


def record(state: str, gate: str, ok: bool, inputs: list[str], errors: int, warns: int):
    path = Path(state)
    with locked(path):
        data = json_of(path)
        data[gate] = {"ok": ok, "errors": errors, "warnings": warns, "at": now(),
                      "inputs": {relative_to(p, path.parent): digest(p) for p in inputs}}
        write_json(path, data)


def git_head(folder: Path, files: list[str]) -> tuple[str | None, bool]:
    """(HEAD commit, whether any of `files` has uncommitted changes); (None, False) outside git."""
    git = shutil.which("git")
    if not git:
        return None, False
    run = lambda *args: subprocess.run([git, *args], cwd=folder, capture_output=True, text=True)
    head = run("rev-parse", "HEAD")
    if head.returncode:
        return None, False
    return head.stdout.strip(), bool(run("status", "--porcelain", "--", *files).stdout.strip())


def platform_release_on(day: datetime) -> tuple[str, str]:
    """Latest bundled seasonal release whose GA date is on or before `day` (UTC)."""
    stamp = day.date().isoformat()
    name, ga = PLATFORM_RELEASES[0]
    for candidate, date in PLATFORM_RELEASES:
        if date <= stamp:
            name, ga = candidate, date
    return name, ga


def newer_platform_releases(iso_timestamp: str) -> list[tuple[str, str]]:
    day = iso_timestamp[:10]
    return [(name, ga) for name, ga in PLATFORM_RELEASES if ga > day]


def org_api_version(org: str) -> str | None:
    rows = soql(org, "SELECT ApiVersion FROM Organization LIMIT 1")
    return str(rows[0]["ApiVersion"]) if rows else None


def record_test_run(state: str, org: str, tests_path: str, ok: bool,
                    api_version: str | None = None, platform: str | None = None):
    path = Path(state)
    if platform is None:
        platform, _ = platform_release_on(datetime.now(timezone.utc))
    with locked(path):
        data = json_of(path)
        data["test_run"] = {
            "ok": ok, "at": now(), "org": org,
            "org_api_version": api_version,
            "platform_release": platform,
            "inputs": {relative_to(tests_path, path.parent): digest(tests_path)},
        }
        write_json(path, data)


def retest_reasons(state: Path, org: str | None) -> list[tuple[str, str, str]]:
    """(verdict, code, message) when a SHIPPED agent may need its suite re-run."""
    data = json_of(state)
    run = data.get("test_run")
    log = state.with_name("releases.json")
    with locked(log):
        releases = json_of(log).get("releases", [])
    latest = {r["org"]: r for r in releases}
    reasons: list[tuple[str, str, str]] = []
    targets = [org] if org else sorted(latest)
    for target in targets:
        if target not in latest:
            continue
        if not run or not run.get("ok"):
            reasons.append(("RETEST", "T1",
                            f"{target}: no passing test run in state — rerun agent tests "
                            "and record with `testrun`"))
            continue
        if run.get("org") and run["org"] != target:
            continue
        for name, ga in newer_platform_releases(run["at"]):
            reasons.append(("RETEST", "T2",
                            f"{target}: last passing test {run['at'][:10]} predates "
                            f"{name} GA ({ga}); rerun the suite on this org"))
        if org:
            live = org_api_version(org)
            recorded = run.get("org_api_version")
            if live and recorded and live != recorded:
                reasons.append(("RETEST", "T3",
                                f"{target}: test run recorded API {recorded}; org is "
                                f"now {live}; rerun the suite"))
    return reasons


def parse_success_metric(spec: str) -> tuple[int, int] | None:
    _, body = sections(spec).get("success metric", (0, []))
    text = "\n".join(body).strip()
    if not text:
        return None
    pct = SUCCESS_PCT.search(text)
    window = SUCCESS_WINDOW.search(text)
    if not pct or not window:
        return None
    return int(pct.group(1)), int(window.group(1))


def activation_time(state: Path, org: str) -> datetime | None:
    log = state.with_name("releases.json")
    with locked(log):
        releases = json_of(log).get("releases", [])
    for entry in reversed(releases):
        if entry.get("org") == org and entry.get("at"):
            return datetime.fromisoformat(entry["at"])
    return None


def session_transfer_counts(org: str, since_iso: str) -> tuple[int, int, str] | None:
    """(total sessions, sessions without transfer proxy, data source note), or None if SOQL fails."""
    since = since_iso.replace("+00:00", "Z")
    try:
        total_rows = soql(org, "SELECT COUNT() FROM MessagingSession "
                              f"WHERE CreatedDate >= {since} "
                              "AND AgentType IN ('Bot', 'Agent Bot')")
        total = total_rows[0].get("expr0", 0) if total_rows else 0
        escalated_rows = soql(org, "SELECT COUNT() FROM MessagingSession "
                                    f"WHERE CreatedDate >= {since} "
                                    "AND AgentType IN ('Bot', 'Agent Bot') "
                                    "AND (EndedByType = 'BotToAgent' OR CaseId != null)")
        escalated = escalated_rows[0].get("expr0", 0) if escalated_rows else 0
    except CannotRun:
        return None
    note = ("MessagingSession counts (bot sessions; transfer ≈ BotToAgent end or CaseId set). "
            "This is a SOQL proxy — not Agentforce Session Tracing in Data Cloud.")
    return total, max(total - escalated, 0), note


def gate_testrun(tests_path: str, org: str, result_json: str | None, passed: bool) -> Gate:
    gate = Gate("testrun")
    ok = False
    if result_json:
        try:
            payload = json.loads(Path(result_json).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise CannotRun(f"cannot read test result JSON: {exc}") from exc
        ok = bool(payload.get("success", payload.get("passed", False)))
        if not ok:
            gate.error("T4", "agent test result JSON does not report success", result_json)
    elif passed:
        ok = True
    else:
        gate.error("T4", "pass `--pass` or a successful `--result-json` from `sf agent test run`")
    if ok:
        version = org_api_version(org)
        platform, _ = platform_release_on(datetime.now(timezone.utc))
        gate.note(f"org API {version or 'unknown'}; bundled platform release {platform}")
    gate.tests_ok = ok
    return gate


def gate_observe(spec_path: str, org: str, state: str | None, input_path: str | None) -> Gate:
    gate = Gate("observe")
    metric = parse_success_metric(text_of(spec_path))
    if metric is None:
        gate.warn("M1", "could not parse `## Success Metric` "
                        "(expected e.g. `70% ... without transfer` and `first 30 days`)")
        return gate
    target_pct, window_days = metric
    if not state:
        gate.warn("M5", "pass `--state` so the window can start from the release recorded "
                        "in `releases.json`, or pass `--input` with manual counts")
        return gate
    started = activation_time(Path(state), org)
    if started is None:
        gate.warn("M5", f"no release for org `{org}` in releases.json — cannot anchor the "
                        f"{window_days}-day window")
        return gate
    end = started + timedelta(days=window_days)
    now_utc = datetime.now(timezone.utc)
    if now_utc < started:
        gate.note(f"measurement window has not started (release {started.date().isoformat()})")
        return gate
    since = min(now_utc, end)
    if now_utc < end:
        gate.note(f"day {(now_utc - started).days + 1} of {window_days} — provisional check only")

    total = without = 0
    source = ""
    if input_path:
        try:
            manual = json.loads(Path(input_path).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise CannotRun(f"cannot read --input: {exc}") from exc
        total = int(manual["total_sessions"])
        without = int(manual.get("sessions_without_transfer",
                                 manual.get("without_transfer", 0)))
        source = manual.get("source", "JSON file passed to --input")
        gate.note(f"using documented input: {source}")
    else:
        counts = session_transfer_counts(org, started.isoformat(timespec="seconds"))
        if counts is None:
            gate.warn("M5", "MessagingSession is not queryable in this org (permissions or "
                            "channel). Pass `--input` with "
                            '`{"total_sessions": N, "sessions_without_transfer": M, '
                            '"source": "..."}` from Tableau Next / observability exports')
            return gate
        total, without, source = counts
        gate.note(source)

    if total == 0:
        gate.warn("M5", "no bot MessagingSession rows in the window — metric stays unmeasured")
        return gate
    actual = round(100 * without / total, 1)
    gate.note(f"{without}/{total} sessions without transfer proxy ({actual}%) "
              f"vs target {target_pct}%")
    if actual < target_pct:
        gate.warn("M4", f"actual {actual}% is below the {target_pct}% Success Metric "
                        f"for the first {window_days} days (does not block release)")
    return gate


def record_release(state: str, org: str, version: int) -> str:
    """Append to releases.json, next to the state file, which inputs shipped as `version` to
    `org`: the files every recorded gate checked, hashed now, with the commit. Diffing the
    commits of two releases shows what changed in the spec, the agent and the tests."""
    path = Path(state)
    log = path.with_name("releases.json")
    with locked(path):
        files = sorted({p for entry in json_of(path).values() for p in entry.get("inputs", {})
                        if (path.parent / p).exists()})
    entry = {"org": org, "version": version,
             "inputs": {p: digest(path.parent / p) for p in files}}
    commit, dirty = git_head(path.parent, files)
    with locked(log):
        releases = json_of(log).get("releases", [])
        if any(all(old.get(k) == v for k, v in entry.items()) for old in releases):
            return f"v{version} to {org} with these files is already in {log.name}"
        releases.append({**entry, "commit": commit, "uncommitted": dirty, "at": now()})
        write_json(log, {"releases": releases})
    return (f"recorded v{version} to {org} in {log.name}"
            + (", with uncommitted changes: the commit alone will not reproduce it" if dirty else ""))


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
    env_body = found.get("environments", (0, []))[1]
    data_body = found.get("data & privacy", (0, []))[1]
    messy_lines = [l for l in env_body + data_body if MESSY_SOURCE.search(l)]
    if not messy_lines:
        gate.error("S8", "declare where messy real records come from: a line "
                         "`**Messy records:**` or `**Realistic test data:**` under "
                         "`## Environments` or `## Data & Privacy`")
    elif not any(MESSY_STRUCTURED.search(l) for l in messy_lines):
        gate.error("S8", "structure messy records as "
                         "`**Messy records:** org `ORG_ALIAS`; criterion: stale "
                         "LastModifiedDate, empty `Object.Field`, …` "
                         "(machine-checkable tokens after `criterion:`)")
    _, actions_body = found.get("actions", (0, []))
    has_actions = any(l.strip().startswith("### ") for l in actions_body)
    read_obj, read_fld, write_obj, write_fld = spec_data_access_from_body(
        sections(spec).get("salesforce data access", (0, []))[1])
    data_access_empty = not (read_obj or read_fld or write_obj or write_fld)
    if has_actions and data_access_empty:
        (gate.error if final else gate.warn)(
            "S9", "the spec has actions but `## Salesforce Data Access` is missing or "
                  "empty: object and field permissions are unchecked until you fill the table")
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


def messy_data_tests(tests: str) -> list[dict]:
    """Test cases whose comment block immediately above includes `# messy-data`."""
    cases: list[dict] = []
    pending_messy = False
    active: dict | None = None
    for line in tests.splitlines():
        text = line.strip()
        if text.startswith("#") and "utterance" not in text:
            pending_messy = "messy-data" in text.lower()
        elif re.match(r"-\s*utterance\s*:", text):
            if pending_messy:
                active = {"recordIds": []}
                cases.append(active)
            else:
                active = None
            pending_messy = False
        elif active is not None and re.match(r"recordIds?\s*:", text, re.I):
            active["recordIds"].extend(RECORD_ID.findall(line))
        elif text and not text.startswith("#") and not text.startswith("-"):
            if active is not None and re.match(r"-\s", line) is None:
                active = None
    return cases


def out_of_scope_items(spec: str) -> list[tuple[str, str]]:
    """Numbered refuse items (`OOS-01`, …) from `## Out of Scope` prose or bullets."""
    _, body = sections(spec).get("out of scope", (0, []))
    if not body:
        return []
    items: list[str] = []
    for line in body:
        text = line.strip()
        if not text or text.startswith("<!--"):
            continue
        bullet = re.match(r"^(?:[-*]|\d+[.)])\s+(.+)", text)
        if bullet:
            items.append(bullet.group(1).rstrip(".").strip())
            continue
    if not items:
        prose = " ".join(l.strip() for l in body if l.strip() and not l.strip().startswith("<!--"))
        first = re.split(r"\.\s+(?=[A-Z(])", prose, maxsplit=1)[0]
        for part in re.split(r",\s*(?:and\s+)?|;\s*", first):
            part = part.strip().rstrip(".")
            if part and not part.lower().startswith("the agent"):
                items.append(part)
    return [(f"OOS-{i:02d}", text) for i, text in enumerate(items, 1)]


def tagged_oos_ids(tests: str) -> set[str]:
    """`# OOS-nn` on comment lines above a test case, same rules as `tagged_ids`."""
    tagged, pending = set(), set()
    for line in tests.splitlines():
        text = line.strip()
        if text.startswith("#") and "utterance" not in text:
            pending |= set(OOS_TAG.findall(text))
        elif re.match(r"-\s*utterance\s*:", text):
            tagged |= pending
            pending = set()
        elif text:
            pending = set()
    return tagged


def security_tagged_tests(tests: str) -> bool:
    pending = False
    for line in tests.splitlines():
        text = line.strip()
        if text.startswith("#") and "utterance" not in text:
            pending = bool(SECURITY_TAG.search(text))
        elif re.match(r"-\s*utterance\s*:", text):
            if pending:
                return True
            pending = False
        elif text:
            pending = False
    return False


def customer_facing_agent(spec: str, agent: str | None) -> bool:
    if agent:
        if re.search(r"AgentforceServiceAgent|ServiceAgent", agent, re.I):
            return True
    _, cfg = sections(spec).get("agent configuration", (0, []))
    return any("service" in l.lower() and "agent" in l.lower() for l in cfg)


def test_cases_with_meta(tests: str) -> list[dict]:
    """Each live test case: UC tags, optional OOS tags, and `expectedActions` if present."""
    cases: list[dict] = []
    pending_uc, pending_oos = set(), set()
    active: dict | None = None
    for line in tests.splitlines():
        text = line.strip()
        if text.startswith("#") and "utterance" not in text:
            if active is not None:
                active = None
            pending_uc |= set(UC.findall(text))
            pending_oos |= set(OOS_TAG.findall(text))
        elif m := re.match(r"-\s*utterance\s*:\s*\"(.+)\"", text):
            active = {"ucs": set(pending_uc) | set(UC.findall(line)),
                      "oos": set(pending_oos), "expected_actions": [],
                      "utterance": m.group(1)}
            cases.append(active)
            pending_uc, pending_oos = set(), set()
        elif re.match(r"-\s*utterance\s*:", text):
            active = {"ucs": set(pending_uc) | set(UC.findall(line)),
                      "oos": set(pending_oos), "expected_actions": [], "utterance": ""}
            cases.append(active)
            pending_uc = pending_oos = set()
        elif active is not None and re.match(r"expectedActions\s*:", text, re.I):
            active["collecting_actions"] = True
        elif active is not None and active.get("collecting_actions"):
            item = re.match(r"-\s*(\S+)", text)
            if item:
                active["expected_actions"].append(item.group(1))
            elif text and not text.startswith("#"):
                active["collecting_actions"] = False
        elif active is not None and text and not text.startswith("#"):
            if not text.startswith("-") and not re.match(r"[\w]+:", text):
                active = None
    return cases


def escalation_targets_in_agent(agent: str) -> set[str]:
    refs = set()
    for match in ESCALATE_IN_AGENT.findall(agent):
        refs.add(match)
    for flow in re.findall(r"flow://(\w+)", agent):
        if ESCALATE_ACTION_NAME.search(flow) or re.search(
                r"(?i)escalat|handoff|transfer", agent[agent.find(f"flow://{flow}") - 80:
                                                   agent.find(f"flow://{flow}") + 80]):
            refs.add(f"flow://{flow}")
    for name in re.findall(r"^\s+(\w+)\s*:\s*@utils\.escalate", agent, re.M):
        refs.add(name)
    return refs


def escalation_target_from_spec(handoff: str) -> str | None:
    match = ESCALATION_TARGET.search(handoff)
    if not match:
        return None
    raw = match.group(1).strip().strip("`").strip()
    return raw or None


def escalation_action_names(agent: str) -> set[str]:
    names = {m.group(1) for m in re.finditer(r"^\s+(\w+)\s*:\s*@utils\.escalate", agent, re.M)}
    names |= {"escalate", "escalate_to_human", "utils_escalate"}
    return names


def normalize_escalation_ref(text: str) -> str:
    return re.sub(r"\s+", "", text.lower().strip("`"))


def agent_target_matches_declared(agent: str, declared: str) -> bool:
    declared_n = normalize_escalation_ref(declared)
    if "@utils.escalate" in declared_n and ESCALATE_IN_AGENT.search(agent):
        return True
    flow = re.search(r"flow://(\w+)", declared, re.I)
    if flow and f"flow://{flow.group(1)}" in agent:
        return True
    apex = re.search(r"apex://([\w$]+)", declared, re.I)
    if apex and f"apex://{apex.group(1)}" in agent:
        return True
    bare = declared.strip("`").strip()
    if re.fullmatch(r"[\w_]+", bare):
        return bool(re.search(rf"^\s+{re.escape(bare)}\s*:", agent, re.M))
    return declared_n in {normalize_escalation_ref(r) for r in escalation_targets_in_agent(agent)}


def agent_instruction_text(agent: str) -> str:
    lines = agent.splitlines()
    chunks: list[str] = []
    i = 0
    while i < len(lines):
        if re.search(r"\binstructions\s*:", lines[i]) and "|" in lines[i]:
            if lines[i].rstrip().endswith("|"):
                indent = len(lines[i]) - len(lines[i].lstrip()) + 4
                i += 1
                while i < len(lines) and (not lines[i].strip()
                                          or len(lines[i]) - len(lines[i].lstrip()) >= indent):
                    chunks.append(lines[i])
                    i += 1
                continue
        if re.match(r"\s+instructions\s*:\s*->", lines[i]):
            i += 1
            if i < len(lines) and "|" in lines[i]:
                match = LITERAL_BLOCK.match(lines[i])
                if match:
                    indent = len(match.group(1))
                    i += 1
                    while i < len(lines) and (not lines[i].strip()
                                              or len(lines[i]) - len(lines[i].lstrip()) > indent):
                        chunks.append(lines[i])
                        i += 1
                    continue
        if re.match(r"\s+instructions\s*:\s*\|", lines[i]):
            match = LITERAL_BLOCK.match(lines[i])
            if match:
                indent = len(match.group(1))
                i += 1
                while i < len(lines) and (not lines[i].strip()
                                        or len(lines[i]) - len(lines[i].lstrip()) > indent):
                    chunks.append(lines[i])
                    i += 1
                continue
        i += 1
    return "\n".join(chunks)


def test_expects_escalation(case: dict, agent: str) -> bool:
    actions = {a.lower() for a in case.get("expected_actions", [])}
    names = {n.lower() for n in escalation_action_names(agent)}
    if actions & names:
        return True
    return any(ESCALATE_ACTION_NAME.search(a) for a in actions)


def parse_agent_test_results(path: str) -> list[dict]:
    """Normalise a few JSON shapes from `sf agent test` / Testing Center exports."""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    rows: list[dict] = []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        for key in ("testCases", "testCaseResults", "results", "cases"):
            if isinstance(data.get(key), list):
                rows = data[key]
                break
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        actions = row.get("actionsExecuted") or row.get("actionsRan") or row.get(
            "expectedActions") or row.get("actualActions") or []
        if isinstance(actions, str):
            actions = [actions]
        out.append({"actions": actions,
                    "utterance": row.get("utterance") or row.get("input") or "",
                    "passed": row.get("passed", row.get("status") == "passed")})
    return out


def gate_coverage(spec_path: str, tests_path: str, org: str | None = None,
                  final: bool = False, agent_path: str | None = None) -> Gate:
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
    spec_text = text_of(spec_path)
    env = sections(spec_text).get("environments", (0, []))[1]
    data = sections(spec_text).get("data & privacy", (0, []))[1]
    if any(MESSY_SOURCE.search(l) for l in env + data):
        messy = messy_data_tests(tests)
        if not messy:
            gate.error("C6", "the spec names a messy-record source but no test case "
                             "is tagged `# messy-data` on the line above `- utterance:`",
                       tests_path)
        elif not any(m.get("recordIds") for m in messy):
            msg = (f"{len(messy)} `# messy-data` test(s) have no `recordId` or `recordIds`: "
                   "name the sandbox record(s) the case runs against")
            (gate.error if final else gate.warn)("C7", msg, tests_path)
        decl = messy_declaration(spec_text)
        if org and decl:
            alias, criteria = decl
            if not org_matches_declared(org, alias):
                gate.error("C8", f"tests run against org `{org}` but the spec declares messy "
                                 f"records in `{alias}`", tests_path)
            sobject = messy_default_sobject(spec_path, criteria)
            if not sobject:
                gate.error("C9", "cannot resolve an object for messy-record checks: add "
                                 r"`empty `Object.Field`` in the criterion or rows in "
                                 "`## Salesforce Data Access`", spec_path)
            elif not criteria:
                gate.warn("C10", "messy-record criterion has no `stale …` or `empty …` "
                                 "tokens; the gate cannot verify messiness in the org", spec_path)
            else:
                for case in messy:
                    for record_id in case.get("recordIds", []):
                        for reason in check_messy_record(org, record_id, criteria, sobject):
                            gate.error("C11", f"`{record_id}`: {reason}", tests_path)
    refuse = out_of_scope_items(spec_text)
    if refuse:
        covered = tagged_oos_ids(tests)
        for oos_id, label in refuse:
            if oos_id not in covered:
                short = label if len(label) <= 60 else label[:57] + "..."
                gate.error("C12", f"refuse item `{oos_id}` ({short}) has no test. "
                                 f"Tag `# {oos_id}` above a `- utterance:`",
                           tests_path)
        for case in test_cases_with_meta(tests):
            for oos_id in case.get("oos", ()):
                if oos_id not in {oid for oid, _ in refuse}:
                    gate.warn("C14", f"`{oos_id}` is tagged on a test but is not in "
                                     "`## Out of Scope`", tests_path)
    agent_text = text_of(agent_path) if agent_path else None
    if customer_facing_agent(spec_text, agent_text):
        if not security_tagged_tests(tests):
            gate.error("C13", "customer-facing agent needs at least one live test tagged "
                             "`# security` or `# prompt-injection` above `- utterance:`",
                       tests_path)
    return gate


# ---------------------------------------------------------------- handoff

def handoff_section_body(spec: str) -> str:
    return "\n".join(sections(spec).get("human handoff", (0, []))[1])


def handoff_use_case_lines(spec: str) -> list[str]:
    _, body = sections(spec).get("use cases", (0, []))
    return [line for line in body if UC.search(line)
            and re.search(r"\|\s*handoff\s*\|", line, re.I)]


def handoff_must_column(row: str) -> str:
    parts = [p.strip() for p in row.split("|")]
    return parts[4] if len(parts) >= 5 else row


def handoff_uc_requires_live(row: str) -> bool:
    if not re.search(r"\|\s*handoff\s*\|", row, re.I):
        return False
    must = handoff_must_column(row)
    if INFORMATIONAL_HANDOFF.search(must) or TRANSFER_DENIAL.search(must):
        return False
    return promises_live_transfer(must) or promises_live_transfer(row)


def live_handoff_use_case_lines(spec: str) -> list[str]:
    return [row for row in handoff_use_case_lines(spec) if handoff_uc_requires_live(row)]


def handoff_uc_ids(spec: str) -> list[str]:
    return [UC.search(row).group(0) for row in live_handoff_use_case_lines(spec) if UC.search(row)]


def promises_live_transfer(text: str) -> bool:
    flat = re.sub(r"\s+", " ", HANDOFF_MODE.sub("", text))
    if TRANSFER_DENIAL.search(flat):
        return False
    return bool(TRANSFER_PROMISE.search(flat))


def agent_can_escalate(agent: str) -> bool:
    return bool(ESCALATE_IN_AGENT.search(agent))


def agent_has_declared_escalation(agent: str, handoff: str) -> bool:
    target = escalation_target_from_spec(handoff)
    if target:
        return agent_target_matches_declared(agent, target)
    return bool(ESCALATE_IN_AGENT.search(agent))


def gate_handoff(spec_path: str, agent_path: str,
                 tests_path: str | None = None,
                 results_path: str | None = None) -> Gate:
    gate = Gate("handoff")
    spec, agent = text_of(spec_path), text_of(agent_path)
    handoff = handoff_section_body(spec)
    offline = bool(HANDOFF_MODE.search(handoff))
    declared = escalation_target_from_spec(handoff)
    wired = agent_has_declared_escalation(agent, handoff)
    live_rows = live_handoff_use_case_lines(spec)
    all_handoff_rows = handoff_use_case_lines(spec)
    spec_promises = promises_live_transfer(handoff) or bool(live_rows)
    prompt = agent_instruction_text(agent)
    prompt_promises = bool(prompt and promises_live_transfer(prompt))

    if declared and not offline and not agent_target_matches_declared(agent, declared):
        gate.error("H6", f"spec declares `**Escalation target:** {declared}` but the "
                         f"`.agent` has no matching wire-up",
                   agent_path)

    if offline:
        if ESCALATE_IN_AGENT.search(agent):
            gate.error("H3", "spec declares `**Handoff mode:** support-channels-only` "
                             "but the `.agent` still references `@utils.escalate`",
                       agent_path)
        if declared and declared != "@utils.escalate" and agent_target_matches_declared(
                agent, declared):
            gate.error("H3", "support-channels-only must not wire a live escalation "
                             "target in the `.agent`",
                       agent_path)
        scope_promise = promises_live_transfer(handoff) or any(
            handoff_uc_requires_live(row) for row in all_handoff_rows)
        if scope_promise:
            gate.error("H4", "support-channels-only handoff must not promise a live "
                             "transfer in `## Human Handoff` or a `handoff` use case",
                       spec_path)
        if prompt and promises_live_transfer(prompt):
            gate.error("H5", "support-channels-only but `.agent` instructions contain "
                             "live-transfer phrasing (heuristic keyword scan)",
                       agent_path)
            gate.note("H5 uses a keyword heuristic on reasoning/system text, not a "
                      "semantic read of the prompt")
        elif prompt and TRANSFER_PROMISE.search(prompt):
            gate.warn("H5", "`.agent` instructions may promise a transfer; review "
                            "wording (heuristic)", agent_path)
        gate.note("support-channels-only: no live escalation path is required")
        return gate

    needs_path = spec_promises or bool(live_rows) or prompt_promises
    if needs_path and not wired:
        gate.error("H1", "the spec or `.agent` instructions promise or test a live "
                         "handoff but the `.agent` has no escalation path. Wire "
                         f"`{declared or '@utils.escalate'}` or declare "
                         "`**Handoff mode:** support-channels-only`", agent_path)
    if spec_promises and not wired:
        gate.error("H2", "`## Human Handoff` or a live `handoff` use case promises a "
                         "transfer the agent cannot perform", spec_path)
    if prompt_promises and not wired:
        gate.error("H9", "`.agent` instructions promise a live transfer (reasoning or "
                         "system text) but no escalation target is wired",
                   agent_path)

    if tests_path:
        tests = text_of(tests_path)
        live_ucs = set(handoff_uc_ids(spec))
        if live_ucs:
            cases = test_cases_with_meta(tests)
            covered = {uc for case in cases for uc in case["ucs"]}
            for uc in sorted(live_ucs - covered):
                gate.error("H7", f"live `handoff` use case `{uc}` has no test tagged "
                                 "on `- utterance:`. The suite must expect escalation "
                                 "in the trace", tests_path)
            reported: set[str] = set()
            for case in cases:
                missing = sorted(case["ucs"] & live_ucs)
                if not missing:
                    continue
                if test_expects_escalation(case, agent):
                    continue
                for uc in missing:
                    if uc in reported:
                        continue
                    reported.add(uc)
                    gate.error("H8", f"test for `{uc}` does not list an escalation "
                                     "action in `expectedActions` (trace must show "
                                     "the handoff ran)", tests_path)

    if results_path:
        try:
            rows = parse_agent_test_results(results_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            gate.error("H10", f"could not read test results: {exc}", results_path)
            rows = []
        names = {n.lower() for n in escalation_action_names(agent)}
        live_uc_set = set(handoff_uc_ids(spec))
        handoff_utterances = [
            case["utterance"].lower() for case in (
                test_cases_with_meta(text_of(tests_path)) if tests_path else [])
            if case.get("utterance") and case["ucs"] & live_uc_set]
        for row in rows:
            if row.get("passed") is False:
                continue
            utter = (row.get("utterance") or "").lower()
            if handoff_utterances and not any(u in utter or utter in u for u in handoff_utterances):
                continue
            ran = {str(a).lower() for a in row.get("actions", [])}
            escalated = bool(ran & names) or any(
                ESCALATE_ACTION_NAME.search(str(a)) for a in row.get("actions", []))
            if not escalated:
                gate.error("H10", "org test result passed but the trace shows no "
                                 "escalation action (heuristic name match)",
                           results_path)
            elif not ran:
                gate.warn("H11", "result row has no action list; could not verify "
                                 "escalation on the trace", results_path)
    return gate


# ---------------------------------------------------------------- org gates

def spec_data_access_from_body(body: list[str]) -> tuple[set[str], set[tuple[str, str]], set[str], set[tuple[str, str]]]:
    read_obj, read_fld, write_obj, write_fld = set(), set(), set(), set()
    for line in body:
        if not line.strip().startswith("|") or line.count("|") < 5:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5 or cells[0].lower() in ("action", "---", "----"):
            continue
        for token in SALESFORCE_OBJECT.findall(cells[1]):
            read_obj.add(token)
        for token in SALESFORCE_OBJECT.findall(cells[3] if len(cells) > 3 else ""):
            write_obj.add(token)
        for obj, fld in SALESFORCE_FIELD.findall(cells[2]):
            read_fld.add((obj, f"{obj}.{fld}"))
        if len(cells) > 4:
            for obj, fld in SALESFORCE_FIELD.findall(cells[4]):
                write_fld.add((obj, f"{obj}.{fld}"))
    return read_obj, read_fld, write_obj, write_fld


def spec_data_access(spec_path: str) -> tuple[set[str], set[tuple[str, str]], set[str], set[tuple[str, str]]]:
    """Objects and fields the spec says actions read or write (## Salesforce Data Access table)."""
    _, body = sections(text_of(spec_path)).get("salesforce data access", (0, []))
    return spec_data_access_from_body(body)


def messy_declaration(spec: str) -> tuple[str, list[dict]] | None:
    """Declared sandbox alias and parsed machine-checkable criteria, or None."""
    env = sections(spec).get("environments", (0, []))[1]
    data = sections(spec).get("data & privacy", (0, []))[1]
    for line in env + data:
        match = MESSY_STRUCTURED.search(line)
        if not match:
            continue
        org_alias, criterion_text = match.group(1).strip(), match.group(2).strip()
        criteria: list[dict] = []
        for part in re.split(r",\s*(?=(?:stale|empty)\s)", criterion_text):
            part = part.strip().rstrip(".")
            stale = CRITERION_STALE.match(part)
            if stale:
                days = int(stale.group(2)) if stale.group(2) else STALE_DEFAULT_DAYS
                criteria.append({"kind": "stale", "field": stale.group(1), "days": days})
                continue
            empty = CRITERION_EMPTY.match(part)
            if empty:
                criteria.append({"kind": "empty", "field": empty.group(1).strip()})
        return org_alias, criteria
    return None


def org_auth_target(org: str) -> dict | None:
    """The org the `-o` flag points at: alias, username, connected."""
    listing = sf("org", "list")
    for group in ("nonScratchOrgs", "scratchOrgs"):
        for row in listing.get(group, []):
            if org in (row.get("alias"), row.get("username")):
                return row
    return None


def org_matches_declared(org: str, declared_alias: str) -> bool:
    if declared_alias.lower() == org.lower():
        return True
    try:
        target = org_auth_target(org)
    except CannotRun:
        return False
    if not target:
        return False
    return (target.get("alias") or "").lower() == declared_alias.lower()


def messy_default_sobject(spec_path: str, criteria: list[dict]) -> str | None:
    for item in criteria:
        if item["kind"] == "empty" and "." in item["field"]:
            obj = item["field"].split(".", 1)[0]
            if SOBJECT_NAME.match(obj):
                return obj
    read_obj, *_ = spec_data_access(spec_path)
    return next(iter(sorted(read_obj)), None) if read_obj else None


def record_ids_in_tests(tests: str) -> list[str]:
    ids: list[str] = []
    for line in tests.splitlines():
        if re.search(r"recordIds?\s*:", line, re.I):
            ids.extend(RECORD_ID.findall(line))
    return ids


def check_messy_record(org: str, record_id: str, criteria: list[dict], sobject: str) -> list[str]:
    """Reasons the row fails the declared criterion. Empty list means it passes."""
    if not SOBJECT_NAME.match(sobject):
        return [f"cannot check `{record_id}`: object `{sobject}` is not a valid API name"]
    fields = {"Id", "LastModifiedDate", "CreatedDate"}
    for item in criteria:
        if item["kind"] == "stale":
            fields.add(item["field"])
        elif item["kind"] == "empty":
            token = item["field"]
            fld = token.split(".", 1)[-1]
            if SOBJECT_NAME.match(fld) or FIELD_TOKEN.match(fld):
                fields.add(fld)
    query = f"SELECT {', '.join(sorted(fields))} FROM {sobject} WHERE Id = '{record_id}'"
    rows = soql(org, query)
    if not rows:
        return [f"`{record_id}` is not in org `{org}` on `{sobject}`"]
    row = rows[0]
    reasons: list[str] = []
    now = datetime.now(timezone.utc)
    for item in criteria:
        if item["kind"] == "stale":
            raw = row.get(item["field"])
            if not raw:
                reasons.append(f"`{item['field']}` is empty")
                continue
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            age = (now - stamp).days
            if age < item["days"]:
                reasons.append(f"`{item['field']}` is {age}d old, criterion needs >{item['days']}d")
        elif item["kind"] == "empty":
            fld = item["field"].split(".", 1)[-1]
            val = row.get(fld)
            if val not in (None, ""):
                reasons.append(f"`{fld}` is filled (`{val}`), criterion expects empty")
    if criteria and not reasons:
        return []
    if not criteria:
        return ["no machine-checkable criterion to verify messiness"]
    return reasons


def check_record_visibility(org: str, user_id: str, record_ids: list[str]) -> dict[str, bool]:
    """Map record Id -> HasReadAccess for the agent user."""
    if not record_ids:
        return {}
    rows = soql(org, "SELECT RecordId, HasReadAccess FROM UserRecordAccess "
                   f"WHERE UserId = '{user_id}' AND RecordId IN ({quoted(record_ids)})")
    return {row["RecordId"]: bool(row.get("HasReadAccess")) for row in rows}


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


def gate_perms(path: str, org: str, spec_path: str | None = None,
               tests_path: str | None = None) -> Gate:
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
    checked_objects = checked_fields = 0
    if spec_path:
        read_obj, read_fld, write_obj, write_fld = spec_data_access(spec_path)
        if read_obj or read_fld or write_obj or write_fld:
            parents = quoted(set_ids)
            if read_obj:
                rows = {row["SobjectType"] for row in soql(
                    org, "SELECT SobjectType FROM ObjectPermissions "
                         f"WHERE ParentId IN ({parents}) AND PermissionsRead = true "
                         f"AND SobjectType IN ({quoted(read_obj)})")}
                for obj in sorted(read_obj - rows):
                    gate.error("P9", f"the agent user cannot read object `{obj}` "
                                     "(declared in `## Salesforce Data Access`)")
                checked_objects += len(read_obj)
            if write_obj:
                rows = {row["SobjectType"] for row in soql(
                    org, "SELECT SobjectType FROM ObjectPermissions "
                         f"WHERE ParentId IN ({parents}) AND PermissionsEdit = true "
                         f"AND SobjectType IN ({quoted(write_obj)})")}
                for obj in sorted(write_obj - rows):
                    gate.error("P10", f"the agent user cannot edit object `{obj}` "
                                      "(declared in `## Salesforce Data Access`)")
                checked_objects += len(write_obj)
            if read_fld:
                fields = sorted({f for _, f in read_fld})
                granted = {row["Field"] for row in soql(
                    org, "SELECT Field FROM FieldPermissions "
                         f"WHERE ParentId IN ({parents}) AND PermissionsRead = true "
                         f"AND Field IN ({quoted(fields)})")}
                for _, field in sorted(read_fld):
                    if field not in granted:
                        gate.error("P11", f"the agent user cannot read field `{field}` "
                                          "(declared in `## Salesforce Data Access`)")
                checked_fields += len(read_fld)
            if write_fld:
                fields = sorted({f for _, f in write_fld})
                granted = {row["Field"] for row in soql(
                    org, "SELECT Field FROM FieldPermissions "
                         f"WHERE ParentId IN ({parents}) AND PermissionsEdit = true "
                         f"AND Field IN ({quoted(fields)})")}
                for _, field in sorted(write_fld):
                    if field not in granted:
                        gate.error("P12", f"the agent user cannot edit field `{field}` "
                                          "(declared in `## Salesforce Data Access`)")
                checked_fields += len(write_fld)
    record_ids = record_ids_in_tests(text_of(tests_path)) if tests_path else []
    if record_ids:
        visibility = check_record_visibility(org, person["Id"], record_ids)
        for record_id in sorted(set(record_ids)):
            if record_id not in visibility:
                gate.error("P13", f"record `{record_id}` was not returned by UserRecordAccess "
                                  "(missing or not shared to the agent user)")
            elif not visibility[record_id]:
                gate.error("P13", f"record `{record_id}` is not readable by the agent user "
                                  "(OWD or sharing rules hide it even when field access exists)")
    if checked_objects or checked_fields:
        gate.note(f"checked {len(apex)} Apex and {len(flows)} Flow target(s), "
                  f"{checked_objects} object(s) and {checked_fields} field(s) from the spec "
                  f"against {len(set_ids)} permission set(s)")
    else:
        gate.note(f"checked {len(apex)} Apex and {len(flows)} Flow target(s) against "
                  f"{len(set_ids)} permission set(s). Add `## Salesforce Data Access` to the "
                  "spec to gate object and field reads/writes")
    if record_ids:
        gate.note(f"checked read access to {len(set(record_ids))} test record(s) via "
                  "UserRecordAccess")
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
        gate.live = version if version is not None else live[0]
        gate.note(f"`{agent}` v{gate.live} is Active in the org "
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


def gate_doctor(state: str | None = None, org: str | None = None) -> Gate:
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
    platform, ga = platform_release_on(datetime.now(timezone.utc))
    gate.note(f"bundled latest platform release: {platform} GA {ga}")
    if state:
        for verdict, code, msg in retest_reasons(Path(state), org):
            gate.warn(code, msg)
    elif org:
        gate.warn("T1", "pass `--state` with `-o` to check whether a SHIPPED agent needs "
                        "its test suite re-run after a platform or API change")
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


def status(state: str, require: list[str], org: str | None = None) -> int:
    path = Path(state)
    with locked(path):
        data = json_of(path)
    ready = True
    for gate in require + [g for g in data if g not in require and g not in STATE_META]:
        entry = data.get(gate)
        if entry is None:
            verdict = "MISSING"
        elif changed_inputs(path.parent, entry["inputs"]):
            verdict = "STALE"
        else:
            verdict = "PASS" if entry["ok"] else "FAIL"
        if gate in require and verdict != "PASS":
            ready = False
        print(f"{verdict:8}{gate:10}{entry['at'] if entry else 'never run'}")
    log = path.with_name("releases.json")
    with locked(log):
        releases = json_of(log).get("releases", [])
    latest = {release["org"]: release for release in releases}  # the log is in time order
    for ship_org, release in latest.items():  # informational: drift is not a failed gate
        changed = changed_inputs(path.parent, release["inputs"])
        print(f"{'DRIFT' if changed else 'SHIPPED':8}{ship_org:10}v{release['version']} "
              f"{release['at']}" + (f"  changed since: {', '.join(changed)}" if changed else ""))
    for verdict, code, msg in retest_reasons(path, org):
        print(f"{verdict:8}{code:10}{msg}")
    print("READY: every required gate passed on the current files" if ready else
          "NOT READY: fix the gates above, then run them again")
    return 0 if ready else 1


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="gates.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="gate", required=True)
    p = sub.add_parser("doctor")
    p.add_argument("--state", help="state file with test_run evidence and releases.json")
    p.add_argument("-o", "--org", help="org alias to check platform/API re-test hints")
    p = sub.add_parser("recon"); p.add_argument("repo", nargs="?", default=".")
    p.add_argument("--no-orgs", action="store_true")
    p = sub.add_parser("spec"); p.add_argument("spec"); p.add_argument("--final", action="store_true")
    p = sub.add_parser("lint"); p.add_argument("agent"); p.add_argument("--final", action="store_true")
    p = sub.add_parser("coverage"); p.add_argument("spec"); p.add_argument("tests")
    p.add_argument("-o", "--org", help="org where tests run: messy rows must exist there")
    p.add_argument("--final", action="store_true")
    p.add_argument("--agent", help="`.agent` file; needed for customer-facing security rule C13")
    p = sub.add_parser("handoff"); p.add_argument("spec"); p.add_argument("agent")
    p.add_argument("--tests", help="tests.yaml; require escalation in expectedActions for live handoff")
    p.add_argument("--results", help="JSON org test results; verify escalation ran on the trace")
    p = sub.add_parser("perms"); p.add_argument("agent"); p.add_argument("-o", "--org", required=True)
    p.add_argument("--spec", help="spec with optional ## Salesforce Data Access for object/field checks")
    p.add_argument("--tests", help="tests.yaml: UserRecordAccess on recordId / recordIds rows")
    p = sub.add_parser("active"); p.add_argument("agent"); p.add_argument("-o", "--org", required=True)
    p.add_argument("--version", type=int)
    p = sub.add_parser("testrun"); p.add_argument("tests")
    p.add_argument("-o", "--org", required=True)
    p.add_argument("--result-json", help="JSON from `sf agent test run --result-format json`")
    p.add_argument("--pass", dest="record_pass", action="store_true",
                   help="record success without reading JSON")
    p = sub.add_parser("observe"); p.add_argument("spec")
    p.add_argument("-o", "--org", required=True)
    p.add_argument("--input", help="manual counts JSON when session SOQL is unavailable")
    p = sub.add_parser("status"); p.add_argument("state")
    p.add_argument("--require", default="spec,lint,coverage")
    p.add_argument("-o", "--org", help="limit platform/API re-test hints to one org")
    for name in ("spec", "lint", "coverage", "handoff", "perms", "active", "testrun", "observe"):
        sub.choices[name].add_argument("--state", help="JSON file to record the result in")
    a = parser.parse_args(argv)
    try:
        if a.gate == "doctor":
            return gate_doctor(getattr(a, "state", None), getattr(a, "org", None)).finish()
        if a.gate == "recon":
            print(json.dumps(recon(a.repo, not a.no_orgs), indent=2))
            return 0
        if a.gate == "spec":
            return gate_spec(a.spec, a.final).finish(a.state, [a.spec])
        if a.gate == "lint":
            return gate_lint(a.agent, a.final).finish(a.state, [a.agent])
        if a.gate == "coverage":
            inputs = [a.spec, a.tests] + ([a.agent] if a.agent else [])
            return gate_coverage(a.spec, a.tests, a.org, a.final, a.agent).finish(a.state, inputs)
        if a.gate == "handoff":
            inputs = [a.spec, a.agent] + ([a.tests] if a.tests else [])
            if a.results:
                inputs.append(a.results)
            return gate_handoff(a.spec, a.agent, a.tests, a.results).finish(a.state, inputs)
        if a.gate == "perms":
            inputs = [a.agent] + ([a.spec] if a.spec else []) + ([a.tests] if a.tests else [])
            return gate_perms(a.agent, a.org, a.spec, a.tests).finish(a.state, inputs)
        if a.gate == "active":
            gate = gate_active(a.agent, a.org, a.version)
            code = gate.finish(a.state, [])
            if code == 0 and a.state:
                print(f"NOTE  active  {record_release(a.state, a.org, gate.live)}")
            return code
        if a.gate == "testrun":
            gate = gate_testrun(a.tests, a.org, a.result_json, a.record_pass)
            code = gate.finish(a.state, [a.tests])
            if code == 0 and a.state and getattr(gate, "tests_ok", False):
                version = org_api_version(a.org)
                record_test_run(a.state, a.org, a.tests, True, version)
            return code
        if a.gate == "observe":
            return gate_observe(a.spec, a.org, a.state, a.input).finish(a.state, [a.spec])
        return status(a.state, [g for g in a.require.split(",") if g], getattr(a, "org", None))
    except CannotRun as exc:
        print(f"CANNOT RUN  {a.gate}  {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
