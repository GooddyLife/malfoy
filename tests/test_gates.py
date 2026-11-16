"""Run with: python -m unittest discover -s tests

Each test breaks the passing example in one way and checks that the right gate
says so. Org gates (perms, active) need a Salesforce org and are not run here.
"""
import json
import sys
import tempfile
import unittest
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
                     gates.gate_coverage(spec, tests)):
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
        gate = gates.gate_coverage(spec, self.file("tests.yaml", TESTS.replace("# UC-04", "#")))
        self.assertEqual([msg.split("`")[1] for _, _, msg in gate.errors], ["UC-04"])

    def test_status_goes_stale_when_an_input_changes(self):
        spec, state = self.file("spec.md", SPEC), str(Path(self.dir.name) / "state.json")
        gates.gate_spec(spec).finish(state, [spec])
        self.assertEqual(gates.status(state, ["spec"]), 0)
        Path(spec).write_text(SPEC + "\nchanged\n", encoding="utf-8")
        self.assertEqual(gates.status(state, ["spec"]), 1)
        self.assertEqual(gates.status(state, ["spec", "lint"]), 1)  # lint never ran

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


if __name__ == "__main__":
    unittest.main()
