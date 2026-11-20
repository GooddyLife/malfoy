"""Run with: python -m unittest discover -s tests

Each test breaks the passing example in one way and checks that the right gate
says so. The org gates (perms, active) get faked SOQL answers: no org is needed.
"""
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "build-agent" / "scripts"))
import gates  # noqa: E402

EXAMPLE = ROOT / "examples" / "order-status"
SPEC = (EXAMPLE / "spec.md").read_text(encoding="utf-8")
AGENT = (EXAMPLE / "OrderStatus.agent").read_text(encoding="utf-8")
TESTS = (EXAMPLE / "tests.yaml").read_text(encoding="utf-8")


class GatesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def file(self, name: str, content: str) -> str:
        path = Path(self.dir.name) / name
        path.write_text(content, encoding="utf-8")
        return str(path)

    def codes(self, gate) -> set:
        return {code for code, _, _ in gate.errors + gate.warns}

    def test_example_passes_every_local_gate(self):
        spec, agent, tests = (self.file("spec.md", SPEC), self.file("a.agent", AGENT),
                              self.file("tests.yaml", TESTS))
        for gate in (gates.gate_spec(spec), gates.gate_lint(agent, final=True),
                     gates.gate_handoff(spec, agent, tests),
                     gates.gate_coverage(spec, tests, agent_path=agent)):
            self.assertEqual(gate.errors + gate.warns, [], gate.name)

    def test_spec_missing_section_placeholder_and_guardrail(self):
        broken = SPEC.replace("## Human Handoff", "## Handoff").replace("guardrail", "other")
        broken = broken.replace("`OrderStatus`", "`Agent_API_Name`", 1)
        self.assertEqual(self.codes(gates.gate_spec(self.file("spec.md", broken))),
                         {"S1", "S3", "S6"})

    def test_spec_final_rejects_actions_not_built(self):
        path = self.file("spec.md", SPEC)
        self.assertEqual(gates.gate_spec(path).errors, [])
        self.assertEqual(self.codes(gates.gate_spec(path, final=True)), {"S7"})

    def test_lint_language_user_id_and_service_without_user(self):
        no_language = AGENT[:AGENT.index("language:")] + AGENT[AGENT.index("start_agent"):]
        self.assertIn("L1", self.codes(gates.gate_lint(self.file("a.agent", no_language))))
        user_id = AGENT.replace("order.agent@acme-outdoor.example", "005000000000001AAA")
        self.assertIn("L4", self.codes(gates.gate_lint(self.file("b.agent", user_id))))
        no_user = AGENT.replace('    default_agent_user: "order.agent@acme-outdoor.example"\n', "")
        self.assertIn("L5", self.codes(gates.gate_lint(self.file("c.agent", no_user))))

    def test_lint_hash_inside_literal_block_is_prompt_text(self):
        noisy = AGENT.replace("        You are the order assistant",
                              "        # note to self: reword this\n        You are the order assistant")
        gate = gates.gate_lint(self.file("a.agent", noisy))
        self.assertEqual([code for code, _, _ in gate.warns], ["L3"])
        # the same `#` outside the block is a real comment and stays silent
        self.assertEqual(gates.gate_lint(self.file("b.agent", "# note\n" + AGENT)).warns, [])

    def test_lint_record_id_and_unfinished_marker(self):
        dirty = AGENT + '\n# TODO wire the queue\n            queue: "00G5f000004XyZaEAK"\n'
        self.assertEqual(self.codes(gates.gate_lint(self.file("a.agent", dirty))), {"L6", "L7"})
        self.assertTrue(gates.gate_lint(self.file("a.agent", dirty), final=True).errors)

    def test_coverage_reports_use_case_without_test(self):
        spec = self.file("spec.md", SPEC)
        gate = gates.gate_coverage(spec, self.file("tests.yaml", TESTS.replace("# UC-04", "#")),
                                   agent_path=self.file("a.agent", AGENT))
        self.assertEqual([msg.split("`")[1] for _, _, msg in gate.errors], ["UC-04"])

    def test_spec_requires_messy_record_source(self):
        stripped = SPEC.replace("**Messy records:**", "**Neat records:**", 1)
        self.assertIn("S8", self.codes(gates.gate_spec(self.file("spec.md", stripped))))

    def test_handoff_requires_escalate_or_offline_mode(self):
        spec, agent, tests = (self.file("spec.md", SPEC), self.file("a.agent", AGENT),
                              self.file("tests.yaml", TESTS))
        self.assertEqual(gates.gate_handoff(spec, agent, tests).errors, [])
        no_esc = AGENT.replace("escalate_to_human: @utils.escalate", "# removed")
        self.assertEqual(self.codes(gates.gate_handoff(spec, self.file("b.agent", no_esc))),
                         {"H1", "H2", "H6", "H9"})
        offline = SPEC.replace(
            "## Human Handoff",
            "## Human Handoff\n\n**Handoff mode:** support-channels-only", 1)
        offline = re.sub(r"\| UC-04 \| handoff \|.*\n", "", offline)
        offline = offline.replace(
            "transfer right away, without trying to solve it first",
            "say how to reach support, without promising a transfer", 1)
        offline = offline.replace(
            "On request, at any point. Also after two lookups in a row that find nothing. "
            "The chat goes\n to the Customer Care queue with the conversation attached.",
            "On request, point to the support phone line. No live transfer.", 1)
        offline = offline.replace("**Escalation target:** `@utils.escalate`\n", "")
        offline_agent = no_esc.replace("transfer right away.", "point to support channels.")
        no_esc_path = self.file("d.agent", offline_agent)
        self.assertEqual(
            self.codes(gates.gate_handoff(self.file("s.md", offline), no_esc_path)), set())
        bad_agent = self.file("c.agent", AGENT)
        self.assertIn("H3", self.codes(gates.gate_handoff(
            self.file("s2.md", offline), bad_agent)))
        promise_spec = re.sub(
            r"## Human Handoff\n.*?(?=\n## )",
            ("## Human Handoff\n\n**Handoff mode:** support-channels-only\n\n"
             "Transfers to the Customer Care queue on request.\n\n"),
            SPEC, count=1, flags=re.S)
        no_esc_only = self.file("e.agent", no_esc)
        self.assertIn("H4", self.codes(gates.gate_handoff(
            self.file("s3.md", promise_spec), no_esc_only)))

    def test_handoff_detects_instruction_promise_without_wire(self):
        agent = AGENT.replace("@utils.escalate", "# no escalate")
        no_wire = self.file("nw.agent", agent)
        self.assertIn("H9", self.codes(gates.gate_handoff(self.file("spec.md", SPEC), no_wire)))

    def test_handoff_informational_use_case_does_not_require_escalate(self):
        spec = SPEC.replace(
            "| UC-03 | adjacent | asks to change the delivery address | say it only checks order status and offers to transfer to a person |",
            "| UC-03 | adjacent | asks to change the delivery address | refuse address changes in one sentence |",
            1)
        spec = spec.replace(
            "| UC-04 | handoff | asks for a person | transfer right away, without trying to solve it first |",
            "| UC-04 | handoff | asks for a person | say how to reach support, without promising a transfer |",
            1)
        spec = spec.replace(
            "On request, at any point. Also after two lookups in a row that find nothing. The chat goes\n"
            "to the Customer Care queue with the conversation attached.\n\n**Escalation target:** `@utils.escalate`",
            "On request, point to the support phone line. No live transfer.\n",
            1)
        agent = AGENT.replace("escalate_to_human: @utils.escalate", "# removed")
        agent = agent.replace("transfer right away.", "point to support channels.")
        self.assertEqual(self.codes(gates.gate_handoff(
            self.file("s.md", spec), self.file("b.agent", agent))), set())

    def test_handoff_custom_escalation_target_mismatch(self):
        spec = SPEC.replace("**Escalation target:** `@utils.escalate`",
                            "**Escalation target:** `flow://Wrong_Queue`", 1)
        self.assertIn("H6", self.codes(gates.gate_handoff(
            self.file("s.md", spec), self.file("a.agent", AGENT))))

    def test_handoff_tests_must_expect_escalation(self):
        spec, agent = self.file("spec.md", SPEC), self.file("a.agent", AGENT)
        bad = TESTS.replace("escalate_to_human", "lookup_order", 1)
        self.assertIn("H8", self.codes(gates.gate_handoff(spec, agent, self.file("t.yaml", bad))))

    def test_handoff_org_results_without_escalation(self):
        spec, agent, tests = (self.file("spec.md", SPEC), self.file("a.agent", AGENT),
                              self.file("tests.yaml", TESTS))
        results = self.file("r.json", json.dumps([{
            "utterance": "I want to talk to a person",
            "passed": True,
            "actionsExecuted": ["lookup_order"],
        }]))
        self.assertIn("H10", self.codes(gates.gate_handoff(spec, agent, tests, results)))

    def test_handoff_support_channels_heuristic_on_instructions(self):
        offline = re.sub(
            r"## Human Handoff\n.*?(?=\n## Data)",
            ("## Human Handoff\n\n**Handoff mode:** support-channels-only\n\n"
             "On request, point to the support phone line. No live transfer.\n"),
            SPEC, count=1, flags=re.S)
        offline = re.sub(r"\| UC-04 \| handoff \|.*\n", "", offline)
        agent = self.file("a.agent", AGENT)  # still says transfer in system instructions
        self.assertIn("H5", self.codes(gates.gate_handoff(self.file("s.md", offline), agent)))

    def test_coverage_out_of_scope_and_security(self):
        spec, agent = self.file("spec.md", SPEC), self.file("a.agent", AGENT)
        tests = TESTS.replace("# OOS-01\n  # OOS-02\n", "# ")
        self.assertIn("C12", self.codes(gates.gate_coverage(
            spec, self.file("t.yaml", tests), agent_path=agent)))
        lines, drop, no_sec = TESTS.splitlines(), False, []
        for line in lines:
            if line.strip() == "# security":
                drop = True
                continue
            if drop:
                if line.strip().startswith("# ") and "security" not in line:
                    drop = False
                    no_sec.append(line)
                continue
            no_sec.append(line)
        no_sec = "\n".join(no_sec) + "\n"
        self.assertIn("C13", self.codes(gates.gate_coverage(
            spec, self.file("t2.yaml", no_sec), agent_path=agent)))

    def test_coverage_requires_messy_data_test(self):
        spec, tests = self.file("spec.md", SPEC), self.file("tests.yaml", TESTS)
        agent = self.file("a.agent", AGENT)
        self.assertEqual(gates.gate_coverage(spec, tests, agent_path=agent).errors, [])
        no_messy = TESTS.replace("# messy-data\n", "")
        self.assertIn("C6", self.codes(gates.gate_coverage(
            spec, self.file("t.yaml", no_messy), agent_path=agent)))

    def test_spec_structured_messy_records_and_data_access_warning(self):
        prose = SPEC.replace(
            "**Messy records:** org `Acme_Staging`; criterion:",
            "**Messy records:** partial-copy sandbox", 1)
        self.assertIn("S8", self.codes(gates.gate_spec(self.file("spec.md", prose))))
        no_table = SPEC.replace("## Salesforce Data Access\n\n", "## Salesforce Data Access\n\n<!--\n", 1)
        no_table = no_table.replace(
            "| lookup_order | `Order__c` |", "| lookup_order |", 1).split("## Agent Configuration")[0] + "## Agent Configuration" + SPEC.split("## Agent Configuration", 1)[1]
        # simpler: empty the table rows
        stripped_access = re.sub(
            r"(## Salesforce Data Access\n\n\| Action.*?\n\|--------.*?\n)\|[^\n]+\n",
            r"\1", SPEC, count=1, flags=re.S)
        gate = gates.gate_spec(self.file("spec.md", stripped_access))
        self.assertIn("S9", self.codes(gate))
        self.assertIn("S9", self.codes(gates.gate_spec(self.file("s.md", stripped_access), final=True)))

    def test_coverage_final_and_org_messy_checks(self):
        spec, tests = self.file("spec.md", SPEC), self.file("tests.yaml", TESTS)
        no_id = TESTS.replace('recordId: "801000000000001AAA"\n', "")
        self.assertIn("C7", self.codes(gates.gate_coverage(spec, self.file("t.yaml", no_id), final=True)))
        self.assertIn("C7", {c for c, _, _ in gates.gate_coverage(
            spec, self.file("t.yaml", no_id)).warns})
        old_soql = gates.soql
        gates.soql = FakeOrg(Order__c=[{
            "Id": "801000000000001AAA", "LastModifiedDate": "2020-01-01T00:00:00.000+0000",
            "CreatedDate": "2020-01-01T00:00:00.000+0000", "Customer_Email__c": None}])
        self.addCleanup(setattr, gates, "soql", old_soql)
        self.assertEqual(gates.gate_coverage(spec, tests, org="Acme_Staging").errors, [])
        self.assertIn("C8", self.codes(gates.gate_coverage(spec, tests, org="wrong_org")))
        gates.soql = FakeOrg(Order__c=[{
            "Id": "801000000000001AAA", "LastModifiedDate": "2025-01-01T00:00:00.000+0000",
            "CreatedDate": "2025-01-01T00:00:00.000+0000", "Customer_Email__c": "a@b.c"}])
        self.assertIn("C11", self.codes(gates.gate_coverage(spec, tests, org="Acme_Staging")))

    def test_coverage_ignores_tags_not_on_a_live_test_case(self):
        spec, untagged = self.file("spec.md", SPEC), TESTS.replace("# UC-04", "#")
        for where in ("\n  # - utterance: later  # UC-04\n",   # commented-out test case
                      "\n# UC-04 still to write\nnotes: none\n"):  # tag on no test case
            gate = gates.gate_coverage(spec, self.file("tests.yaml", untagged + where),
                                       agent_path=self.file("a.agent", AGENT))
            self.assertIn("C3", self.codes(gate), where)
        trailing = untagged.replace('"I want to talk to a person"',
                                    '"I want to talk to a person"  # UC-04')
        self.assertEqual(gates.gate_coverage(
            spec, self.file("t.yaml", trailing), agent_path=self.file("b.agent", AGENT)).errors, [])

    def test_bom_and_corrupt_json_cannot_run(self):
        root = Path(self.dir.name)
        (root / "sfdx-project.json").write_bytes(
            b'\xef\xbb\xbf{"packageDirectories": [{"path": "force-app"}]}')
        self.assertEqual(gates.recon(str(root), orgs=False)["packageDirectories"], ["force-app"])
        state = self.file("state.json", "{not json")
        with self.assertRaises(gates.CannotRun):
            gates.status(state, ["spec"])
        self.assertEqual(gates.main(["status", state]), 2)

    def test_status_goes_stale_when_an_input_changes(self):
        spec, state = self.file("spec.md", SPEC), str(Path(self.dir.name) / "state.json")
        gates.gate_spec(spec).finish(state, [spec])
        self.assertEqual(gates.status(state, ["spec"]), 0)
        Path(spec).write_text(SPEC + "\nchanged\n", encoding="utf-8")
        self.assertEqual(gates.status(state, ["spec"]), 1)
        self.assertEqual(gates.status(state, ["spec", "lint"]), 1)  # lint never ran

    def test_state_survives_a_move_and_a_line_ending_change(self):
        folder = Path(self.dir.name) / "repo" / ".malfoy" / "OrderStatus"
        folder.mkdir(parents=True)
        spec, state = folder / "spec.md", folder / "state.json"
        spec.write_bytes(SPEC.encode("utf-8"))
        gates.gate_spec(str(spec)).finish(str(state), [str(spec)])
        self.assertEqual(list(json.loads(state.read_text())["spec"]["inputs"]), ["spec.md"])
        moved = Path(self.dir.name) / "clone"  # another machine, another checkout path
        shutil.copytree(Path(self.dir.name) / "repo", moved)
        copy = moved / ".malfoy" / "OrderStatus"
        (copy / "spec.md").write_bytes(SPEC.replace("\n", "\r\n").encode("utf-8"))  # autocrlf
        self.assertEqual(gates.status(str(copy / "state.json"), ["spec"]), 0)

    def test_parallel_gates_do_not_lose_results(self):
        spec, state = self.file("spec.md", SPEC), str(Path(self.dir.name) / "state.json")
        def run(n):
            gate = gates.gate_spec(spec)
            gate.name = f"g{n}"
            gate.finish(state, [spec])
        with ThreadPoolExecutor(16) as pool:
            list(pool.map(run, range(32)))
        self.assertEqual(len(json.loads(Path(state).read_text())), 32)

    def test_recon_reads_a_dx_project(self):
        root = Path(self.dir.name)
        (root / "sfdx-project.json").write_text(json.dumps(
            {"packageDirectories": [{"path": "force-app"}], "sourceApiVersion": "66.0"}))
        bundle = root / "force-app" / "main" / "default" / "aiAuthoringBundles" / "OrderStatus"
        bundle.mkdir(parents=True)
        (root / "AGENTS.md").write_text("rules")
        profile = gates.recon(str(root), orgs=False)
        self.assertEqual(profile["agents"]["aiAuthoringBundles"], ["OrderStatus"])
        self.assertEqual(profile["rulesToRead"], ["AGENTS.md"])
        with self.assertRaises(gates.CannotRun):
            gates.recon(str(root / "force-app"), orgs=False)


class FakeOrg:
    """Answers the SOQL the org gates send, keyed on the object queried. No org needed."""

    def __init__(self, **tables):
        self.tables = tables

    def __call__(self, org, query):
        table = query.split(" FROM ")[1].split()[0]
        return self.tables.get(table, [])


AGENT_USER = {"Id": "005000000000001AAA", "IsActive": True,
              "Profile": {"UserLicense": {"Name": "Einstein Agent"}}}
ASSIGNED = {"PermissionSetId": "0PS000000000001AAA",
            "PermissionSet": {"PermissionsRunFlow": True}}
FLOW_OK = {"ApiName": "Lookup_Order", "ActiveVersionId": "301000000000001AAA"}


class OrgGatesTest(unittest.TestCase):
    def setUp(self):
        original = gates.soql
        self.addCleanup(setattr, gates, "soql", original)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.agent = str(Path(self.dir.name) / "a.agent")
        Path(self.agent).write_text(AGENT + '\n            target: "apex://OrderService"\n',
                                    encoding="utf-8")

    def perms(self, **tables) -> set:
        gates.soql = FakeOrg(**tables)
        gate = gates.gate_perms(self.agent, "fake")
        return {code for code, _, _ in gate.errors + gate.warns}

    def test_perms_pass_when_user_reaches_every_target(self):
        self.assertEqual(self.perms(
            User=[AGENT_USER], PermissionSetAssignment=[ASSIGNED],
            ApexClass=[{"Id": "01p000000000001AAA", "Name": "OrderService"}],
            SetupEntityAccess=[{"SetupEntityId": "01p000000000001AAA"}],
            FlowDefinitionView=[FLOW_OK]), set())

    def test_perms_checks_declared_object_and_field_access(self):
        spec = Path(self.dir.name) / "spec.md"
        spec.write_text(SPEC, encoding="utf-8")
        gates.soql = FakeOrg(
            User=[AGENT_USER], PermissionSetAssignment=[ASSIGNED],
            ApexClass=[{"Id": "01p000000000001AAA", "Name": "OrderService"}],
            SetupEntityAccess=[{"SetupEntityId": "01p000000000001AAA"}],
            FlowDefinitionView=[FLOW_OK],
            ObjectPermissions=[{"SobjectType": "Order__c"}],
            FieldPermissions=[{"Field": "Order__c.Status__c"}])
        gate = gates.gate_perms(self.agent, "fake", str(spec))
        self.assertEqual({c for c, _, _ in gate.errors}, {"P11"})
        gates.soql = FakeOrg(
            User=[AGENT_USER], PermissionSetAssignment=[ASSIGNED],
            ApexClass=[{"Id": "01p000000000001AAA", "Name": "OrderService"}],
            SetupEntityAccess=[{"SetupEntityId": "01p000000000001AAA"}],
            FlowDefinitionView=[FLOW_OK],
            ObjectPermissions=[{"SobjectType": "Order__c"}],
            FieldPermissions=[{"Field": f} for f in (
                "Order__c.Status__c", "Order__c.Estimated_Delivery__c",
                "Order__c.Customer_Email__c")])
        self.assertEqual(gates.gate_perms(self.agent, "fake", str(spec)).errors, [])

    def test_perms_record_visibility(self):
        spec = Path(self.dir.name) / "spec.md"
        spec.write_text(SPEC, encoding="utf-8")
        tests = Path(self.dir.name) / "tests.yaml"
        tests.write_text(TESTS, encoding="utf-8")
        gates.soql = FakeOrg(
            User=[AGENT_USER], PermissionSetAssignment=[ASSIGNED],
            ApexClass=[{"Id": "01p000000000001AAA", "Name": "OrderService"}],
            SetupEntityAccess=[{"SetupEntityId": "01p000000000001AAA"}],
            FlowDefinitionView=[FLOW_OK],
            UserRecordAccess=[{"RecordId": "801000000000001AAA", "HasReadAccess": False}])
        gate = gates.gate_perms(self.agent, "fake", str(spec), str(tests))
        self.assertIn("P13", {c for c, _, _ in gate.errors})
        gates.soql = FakeOrg(
            User=[AGENT_USER], PermissionSetAssignment=[ASSIGNED],
            ApexClass=[{"Id": "01p000000000001AAA", "Name": "OrderService"}],
            SetupEntityAccess=[{"SetupEntityId": "01p000000000001AAA"}],
            FlowDefinitionView=[FLOW_OK],
            ObjectPermissions=[{"SobjectType": "Order__c"}],
            FieldPermissions=[{"Field": f} for f in (
                "Order__c.Status__c", "Order__c.Estimated_Delivery__c",
                "Order__c.Customer_Email__c")],
            UserRecordAccess=[{"RecordId": "801000000000001AAA", "HasReadAccess": True}])
        errors = {c for c, _, _ in gates.gate_perms(self.agent, "fake", str(spec), str(tests)).errors}
        self.assertFalse(errors & {"P13"}, errors)

    def test_perms_failures(self):
        self.assertEqual(self.perms(), {"P1"})
        inactive = dict(AGENT_USER, IsActive=False, Profile={"UserLicense": {"Name": "Salesforce"}})
        self.assertEqual(self.perms(
            User=[inactive], PermissionSetAssignment=[dict(ASSIGNED, PermissionSet={})],
            ApexClass=[{"Id": "01p000000000001AAA", "Name": "OrderService"}],
            FlowDefinitionView=[dict(FLOW_OK, ActiveVersionId=None)]),
            {"P2", "P3", "P5", "P7", "P8"})
        self.assertEqual(self.perms(User=[AGENT_USER], PermissionSetAssignment=[ASSIGNED]),
                         {"P4", "P6"})

    def test_active(self):
        def codes(rows, version=None):
            gates.soql = FakeOrg(BotVersion=rows)
            return {c for c, _, _ in gates.gate_active("OrderStatus", "fake", version).errors}
        rows = [{"VersionNumber": 3, "Status": "Inactive"}, {"VersionNumber": 2, "Status": "Active"}]
        self.assertEqual(codes(rows), set())
        self.assertEqual(codes(rows, version=2), set())
        self.assertEqual(codes(rows, version=3), {"A3"})
        self.assertEqual(codes([rows[0]]), {"A2"})
        self.assertEqual(codes([]), {"A1"})


class ReleaseLogTest(unittest.TestCase):
    """`active` with --state records which inputs shipped with which org version (#1)."""

    def setUp(self):
        original = gates.soql
        self.addCleanup(setattr, gates, "soql", original)
        gates.soql = FakeOrg(BotVersion=[{"VersionNumber": 2, "Status": "Active"}])
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        folder = Path(self.dir.name)
        for name, text in (("spec.md", SPEC), ("a.agent", AGENT), ("tests.yaml", TESTS)):
            (folder / name).write_text(text, encoding="utf-8")
        self.folder, self.state = folder, str(folder / "state.json")
        quiet = io.StringIO()
        with redirect_stdout(quiet):
            gates.main(["spec", str(folder / "spec.md"), "--state", self.state])
            gates.main(["lint", str(folder / "a.agent"), "--state", self.state])
            gates.main(["coverage", str(folder / "spec.md"), str(folder / "tests.yaml"),
                        "--state", self.state])

    def run_gate(self, *args) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            code = gates.main(list(args))
        return code, out.getvalue()

    def releases(self) -> list:
        log = self.folder / "releases.json"
        return json.loads(log.read_text())["releases"] if log.exists() else []

    def test_pass_records_once_and_fail_records_nothing(self):
        self.assertEqual(self.run_gate("active", "OrderStatus", "-o", "fake", "--state",
                                       self.state)[0], 0)
        entry, = self.releases()
        self.assertEqual((entry["org"], entry["version"]), ("fake", 2))
        self.assertEqual(sorted(entry["inputs"]), ["a.agent", "spec.md", "tests.yaml"])
        self.assertIn("commit", entry)
        self.run_gate("active", "OrderStatus", "-o", "fake", "--state", self.state)
        self.assertEqual(len(self.releases()), 1)  # same org, version and files: no duplicate
        code, _ = self.run_gate("active", "OrderStatus", "-o", "fake", "--version", "3",
                                "--state", self.state)
        self.assertEqual((code, len(self.releases())), (1, 1))

    def test_status_shows_drift_from_the_last_release(self):
        self.run_gate("active", "OrderStatus", "-o", "fake", "--state", self.state)
        self.assertIn("SHIPPED fake      v2", self.run_gate("status", self.state)[1])
        (self.folder / "spec.md").write_text(SPEC + "\nchanged\n", encoding="utf-8")
        _, out = self.run_gate("status", self.state)
        self.assertRegex(out, r"DRIFT\s+fake\s+v2 .*changed since: spec\.md")


class ObservabilityTest(unittest.TestCase):
    def setUp(self):
        original = gates.soql
        self.addCleanup(setattr, gates, "soql", original)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.folder = Path(self.dir.name)
        self.state = str(self.folder / "state.json")
        self.tests = str(self.folder / "tests.yaml")
        Path(self.tests).write_text(TESTS, encoding="utf-8")

    def write_release(self, org="fake", at="2025-06-01T00:00:00+00:00"):
        log = self.folder / "releases.json"
        log.write_text(json.dumps({"releases": [{
            "org": org, "version": 1, "at": at, "inputs": {"spec.md": "abc"},
        }]}), encoding="utf-8")

    def test_platform_release_calendar_is_deterministic(self):
        release, ga = gates.platform_release_on(datetime(2025, 8, 1, tzinfo=timezone.utc))
        self.assertEqual((release, ga), ("Summer '25", "2025-06-17"))
        newer = gates.newer_platform_releases("2024-06-01T00:00:00+00:00")
        self.assertIn(("Summer '24", "2024-06-10"), newer)

    def test_testrun_records_api_and_platform_in_state(self):
        gates.soql = FakeOrg(Organization=[{"ApiVersion": "65.0"}])
        code = gates.main(["testrun", self.tests, "-o", "fake", "--pass", "--state", self.state])
        self.assertEqual(code, 0)
        run = json.loads(Path(self.state).read_text())["test_run"]
        self.assertTrue(run["ok"])
        self.assertEqual(run["org_api_version"], "65.0")
        self.assertIn("platform_release", run)

    def test_status_retest_when_platform_moves_on(self):
        gates.soql = FakeOrg(Organization=[{"ApiVersion": "65.0"}])
        self.write_release(at="2025-01-01T00:00:00+00:00")
        Path(self.state).write_text(json.dumps({
            "test_run": {"ok": True, "at": "2025-01-01T00:00:00+00:00", "org": "fake",
                         "org_api_version": "65.0", "platform_release": "Winter '24",
                         "inputs": {"tests.yaml": "abc"}},
        }), encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            gates.status(self.state, ["spec"])
        self.assertRegex(out.getvalue(), r"RETEST\s+T2")

    def test_status_retest_when_api_version_changes(self):
        self.write_release()
        gates.record_test_run(self.state, "fake", self.tests, True, "65.0", "Summer '26")
        gates.soql = FakeOrg(Organization=[{"ApiVersion": "66.0"}])
        out = io.StringIO()
        with redirect_stdout(out):
            gates.status(self.state, ["spec"], org="fake")
        self.assertRegex(out.getvalue(), r"RETEST\s+T3")

    def test_observe_warns_below_target_with_input(self):
        spec = self.file("spec.md", SPEC)
        self.write_release(at="2020-01-01T00:00:00+00:00")
        metrics = self.file("metrics.json", json.dumps({
            "total_sessions": 100, "sessions_without_transfer": 50,
            "source": "Tableau export (example)",
        }))
        gate = gates.gate_observe(spec, "fake", self.state, metrics)
        self.assertIn("M4", self.codes(gate))

    def test_observe_uses_messaging_session_counts(self):
        spec = self.file("spec.md", SPEC)
        self.write_release(at="2020-01-01T00:00:00+00:00")
        gates.soql = FakeOrg(
            MessagingSession=[{"expr0": 10}],
        )
        calls = []

        def counting(org, query):
            calls.append(query)
            if "CaseId" in query:
                return [{"expr0": 3}]
            return [{"expr0": 10}]

        gates.soql = counting
        gate = gates.gate_observe(spec, "fake", self.state, None)
        self.assertNotIn("M4", self.codes(gate))
        self.assertEqual(len(calls), 2)

    def file(self, name: str, content: str) -> str:
        path = self.folder / name
        path.write_text(content, encoding="utf-8")
        return str(path)

    def codes(self, gate) -> set:
        return {code for code, _, _ in gate.errors + gate.warns}


if __name__ == "__main__":
    unittest.main()
