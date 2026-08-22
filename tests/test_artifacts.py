import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1]
PLUGIN = SOURCE / "plugins/qteam"
ARTIFACT = PLUGIN / "bin/agent_team_artifact.py"
STATE = PLUGIN / "bin/agent-team-state.py"
REVIEW = PLUGIN / "bin/agent-team-review.py"
PROJECT_SETUP = PLUGIN / "scripts/project-setup.py"


GOOD_SPEC = """<!-- qteam-artifact: spec-v1 -->
# Feature

## Problem statement
The current behavior is not observable.

## User-visible solution
Expose the result through the public command.

## User stories
- US-1: As an operator, I can inspect the result.

## Acceptance criteria
- AC-1: Given a completed run, when status is requested, then the result is shown.

## Testing decisions
Exercise the public CLI seam.

## Implementation decisions
Keep the command output deterministic and machine-readable.

## Constraints and invariants
The result remains deterministic.

## Out of scope
Remote publication.

## Assumptions and unresolved blockers
None.
"""


class ArtifactCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.email", "qteam@example.invalid")
        self.git("config", "user.name", "QTeam Test")
        (self.repo / "README.md").write_text("base\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-m", "base")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args, check=True):
        return subprocess.run(
            ["git", *args], cwd=self.repo, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check,
        )

    def tool(self, tool, *args, check=False):
        return subprocess.run(
            [sys.executable, str(tool), *args], cwd=self.repo, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check,
        )

    def artifact(self, *args, check=False):
        return self.tool(ARTIFACT, *args, check=check)

    def init_run(self, run_id):
        result = self.tool(
            STATE, "--run", run_id, "init", "--goal", f"run {run_id}"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(result.stdout)
        self.git("branch", state["integration_branch"], state["base_commit"])
        return state

    def put_finish_decision(self, run_id, decision_id):
        draft = self.repo / f"{decision_id}.json"
        draft.write_text(json.dumps({
            "schema_version": 1, "id": decision_id, "status": "open",
            "question": f"Approve {decision_id}?", "authority": "user",
            "scope": {"kind": "action", "targets": ["finish"]},
        }), encoding="utf-8")
        result = self.tool(
            STATE, "--run", run_id, "decision-put", "--file", str(draft)
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class ArtifactLintTests(ArtifactCase):
    def test_typed_spec_is_strict_while_legacy_source_is_compatible(self):
        spec = self.repo / "spec.md"
        spec.write_text(GOOD_SPEC, encoding="utf-8")
        passed = self.artifact("lint", "--kind", "spec", "--file", "spec.md")
        self.assertEqual(passed.returncode, 0, passed.stderr)
        report = json.loads(passed.stdout)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["errors"], [])

        spec.write_text(
            GOOD_SPEC.replace("## Acceptance criteria", "## Notes"),
            encoding="utf-8",
        )
        failed = self.artifact("lint", "--kind", "spec", "--file", "spec.md")
        self.assertNotEqual(failed.returncode, 0)
        report = json.loads(failed.stdout)
        self.assertEqual(report["status"], "fail")
        self.assertIn("SPEC002", {item["code"] for item in report["errors"]})

        legacy = self.artifact("lint", "--kind", "spec", "--file", "README.md")
        self.assertEqual(legacy.returncode, 0, legacy.stderr)
        report = json.loads(legacy.stdout)
        self.assertEqual(report["status"], "pass-with-warnings")
        self.assertIn("SPEC000", {item["code"] for item in report["warnings"]})

    def test_typed_spec_requires_implementation_decisions_and_rejects_mixed_markers(self):
        spec = self.repo / "spec.md"
        spec.write_text(
            GOOD_SPEC.replace(
                "## Implementation decisions\n"
                "Keep the command output deterministic and machine-readable.\n\n",
                "",
            ),
            encoding="utf-8",
        )
        missing = self.artifact("lint", "--kind", "spec", "--file", "spec.md")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn(
            "implementation decisions",
            {item["message"].rsplit(": ", 1)[-1]
             for item in json.loads(missing.stdout)["errors"]},
        )

        spec.write_text(
            GOOD_SPEC + "\n<!-- qteam-artifact: spec-v999 -->\n",
            encoding="utf-8",
        )
        mixed = self.artifact("lint", "--kind", "spec", "--file", "spec.md")
        self.assertNotEqual(mixed.returncode, 0)
        self.assertIn("SPEC001", {
            item["code"] for item in json.loads(mixed.stdout)["errors"]
        })

    def test_typed_ticket_fields_are_required_for_every_task(self):
        tickets = self.repo / "tickets.md"
        tickets.write_text("""<!-- qteam-artifact: tickets-v1 -->
# T1: complete
depends_on: []
Requirements: US-1
Done when: behavior works
Verify: run test one

# T2: incomplete
depends_on is unknown; requirements are TBD; we will verify later and be done whenever.
""", encoding="utf-8")
        result = self.artifact(
            "lint", "--kind", "tickets", "--file", "tickets.md"
        )
        self.assertNotEqual(result.returncode, 0)
        errors = json.loads(result.stdout)["errors"]
        self.assertTrue(any(
            item["code"] == "TICKET004" and "T2" in item["message"]
            for item in errors
        ))

    def test_spec_review_freezes_lint_report_and_blocks_typed_errors(self):
        spec = self.repo / "spec.md"
        spec.write_text(GOOD_SPEC, encoding="utf-8")
        initialized = self.tool(
            STATE, "--run", "review-run", "init", "--goal", "review lint"
        )
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        created = self.tool(
            REVIEW, "--run", "review-run", "create", "--wave", "1",
            "--axis", "spec", "--base", "HEAD", "--head", "HEAD",
            "--spec-source", "spec.md",
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        ledger = json.loads(Path(created.stdout.strip()).read_text(encoding="utf-8"))
        packet = ledger["packet"]
        self.assertEqual(packet["artifact_lint"]["status"], "pass")
        expected = hashlib.sha256(
            json.dumps(packet["artifact_lint"], sort_keys=True,
                       separators=(",", ":")).encode()
        ).hexdigest()
        self.assertEqual(packet["artifact_lint_sha256"], expected)

        bad = self.repo / "bad-spec.md"
        bad.write_text(
            GOOD_SPEC.replace("## Acceptance criteria", "## Notes"),
            encoding="utf-8",
        )
        blocked = self.tool(
            REVIEW, "--run", "review-run", "create", "--wave", "2",
            "--axis", "spec", "--base", "HEAD", "--head", "HEAD",
            "--spec-source", "bad-spec.md",
        )
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("artifact lint failed", blocked.stderr)

    def test_pre_010_review_packet_remains_readable_without_claiming_lint(self):
        spec = self.repo / "spec.md"
        spec.write_text(GOOD_SPEC, encoding="utf-8")
        self.tool(STATE, "--run", "legacy-review", "init", "--goal", "legacy",
                  check=True)
        created = self.tool(
            REVIEW, "--run", "legacy-review", "create", "--wave", "1",
            "--axis", "spec", "--base", "HEAD", "--head", "HEAD",
            "--spec-source", "spec.md", check=True,
        )
        ledger_path = Path(created.stdout.strip())
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger["packet"]["schema_version"] = 1
        ledger["packet"].pop("artifact_lint")
        ledger["packet"].pop("artifact_lint_sha256")
        spec_module = importlib.util.spec_from_file_location(
            "qteam_review_legacy", REVIEW
        )
        module = importlib.util.module_from_spec(spec_module)
        sys.path.insert(0, str(PLUGIN / "bin"))
        try:
            spec_module.loader.exec_module(module)
            sources = module.validate_source_snapshots(
                self.repo / ".agents/runs/legacy-review", ledger["packet"]
            )
        finally:
            sys.path.pop(0)
        self.assertEqual(len(sources), 1)


class EpicTests(ArtifactCase):
    def test_epic_dependencies_gate_run_start_and_completion_uses_done_state(self):
        epic_base = self.git("rev-parse", "HEAD").stdout.strip()
        self.assertEqual(
            self.artifact("epic-init", "--epic", "platform", "--goal",
                          "deliver platform").returncode,
            0,
        )
        plan = self.repo / "epic-plan.json"
        plan.write_text(json.dumps({
            "contracts": [{
                "id": "api-v1", "owner_run": "run-a",
                "consumers": ["run-b"], "summary": "stable API",
            }],
            "runs": [
                {"id": "run-a", "title": "foundation", "spec": "a.md",
                 "depends_on": [], "contracts": ["api-v1"]},
                {"id": "run-b", "title": "consumer", "spec": "b.md",
                 "depends_on": ["run-a"], "contracts": ["api-v1"]},
            ],
        }), encoding="utf-8")
        planned = self.artifact(
            "epic-plan", "--epic", "platform", "--file", str(plan)
        )
        self.assertEqual(planned.returncode, 0, planned.stderr)

        blocked = self.tool(
            STATE, "--run", "run-b", "init", "--goal", "consumer",
            "--epic", "platform",
        )
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("blocked by epic dependency run-a", blocked.stderr)
        self.assertFalse((self.repo / ".agents/runs/run-b/state.json").exists())

        predecessor_started = self.tool(
            STATE, "--run", "run-a", "init", "--goal", "foundation",
            "--epic", "platform",
        )
        self.assertEqual(predecessor_started.returncode, 0,
                         predecessor_started.stderr)
        predecessor_state = json.loads(predecessor_started.stdout)
        (self.repo / "foundation.txt").write_text("done\n", encoding="utf-8")
        self.git("add", "foundation.txt")
        self.git("commit", "-m", "finish foundation")
        predecessor_state.update({
            "phase": "DONE", "finished": True,
            "finished_head": self.git("rev-parse", "HEAD").stdout.strip(),
        })
        predecessor = self.repo / ".agents/runs/run-a/state.json"
        predecessor.write_text(json.dumps(predecessor_state), encoding="utf-8")
        completed = self.artifact(
            "epic-complete-run", "--epic", "platform", "--run", "run-a"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

        missing_output = self.tool(
            STATE, "--run", "run-b", "init", "--goal", "consumer",
            "--base-commit", epic_base, "--epic", "platform",
        )
        self.assertNotEqual(missing_output.returncode, 0)
        self.assertIn("finished head", missing_output.stderr)

        started = self.tool(
            STATE, "--run", "run-b", "init", "--goal", "consumer",
            "--epic", "platform",
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        state = json.loads(started.stdout)
        self.assertEqual(state["epic"]["id"], "platform")
        self.assertEqual(state["epic"]["run"], "run-b")
        self.assertEqual(len(state["epic"]["plan_sha256"]), 64)
        self.assertEqual(
            state["epic"]["dependency_heads"]["run-a"],
            predecessor_state["finished_head"],
        )

    def test_epic_plan_cannot_be_replaced_after_a_run_starts(self):
        self.artifact("epic-init", "--epic", "frozen", "--goal", "freeze plan")
        plan = self.repo / "frozen-plan.json"
        plan.write_text(json.dumps({
            "contracts": [],
            "runs": [{"id": "run-a", "title": "a", "spec": "a.md",
                      "depends_on": [], "contracts": []}],
        }), encoding="utf-8")
        self.artifact("epic-plan", "--epic", "frozen", "--file", str(plan),
                      check=True)
        started = self.tool(
            STATE, "--run", "run-a", "init", "--goal", "a",
            "--epic", "frozen",
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        replaced = self.artifact(
            "epic-plan", "--epic", "frozen", "--file", str(plan), "--replace"
        )
        self.assertNotEqual(replaced.returncode, 0)
        self.assertIn("after a run starts", replaced.stderr)

    def test_epic_ready_is_read_only_and_only_state_init_activates(self):
        self.artifact("epic-init", "--epic", "ready", "--goal", "readiness")
        plan = self.repo / "ready-plan.json"
        plan.write_text(json.dumps({
            "contracts": [],
            "runs": [{"id": "run-a", "title": "a", "spec": "a.md",
                      "depends_on": [], "contracts": []}],
        }), encoding="utf-8")
        self.artifact("epic-plan", "--epic", "ready", "--file", str(plan),
                      check=True)
        checked = self.artifact(
            "epic-ready", "--epic", "ready", "--run", "run-a"
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(json.loads(checked.stdout)["status"], "ready")
        manifest = json.loads(
            (self.repo / ".agents/epics/ready/epic.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["runs"]["run-a"]["status"], "planned")
        replaced = self.artifact(
            "epic-plan", "--epic", "ready", "--file", str(plan), "--replace"
        )
        self.assertEqual(replaced.returncode, 0, replaced.stderr)

    def test_epic_plan_rejects_cycles_and_unknown_contracts(self):
        self.artifact("epic-init", "--epic", "bad", "--goal", "bad plan")
        plan = self.repo / "bad-plan.json"
        plan.write_text(json.dumps({
            "contracts": [],
            "runs": [
                {"id": "a", "title": "a", "spec": "a.md",
                 "depends_on": ["b"], "contracts": ["missing"]},
                {"id": "b", "title": "b", "spec": "b.md",
                 "depends_on": ["a"], "contracts": []},
            ],
        }), encoding="utf-8")
        failed = self.artifact(
            "epic-plan", "--epic", "bad", "--file", str(plan)
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("unknown", failed.stderr)
        payload = json.loads(plan.read_text(encoding="utf-8"))
        payload["runs"][0]["contracts"] = []
        plan.write_text(json.dumps(payload), encoding="utf-8")
        cycled = self.artifact(
            "epic-plan", "--epic", "bad", "--file", str(plan)
        )
        self.assertNotEqual(cycled.returncode, 0)
        self.assertIn("cycle", cycled.stderr)

    def test_epic_contract_membership_matches_owner_and_consumers(self):
        self.artifact("epic-init", "--epic", "contracts", "--goal", "contracts")
        plan = self.repo / "contract-plan.json"
        plan.write_text(json.dumps({
            "contracts": [{"id": "api", "owner_run": "owner",
                           "consumers": ["consumer"], "summary": "API"}],
            "runs": [
                {"id": "owner", "title": "owner", "spec": "a.md",
                 "depends_on": [], "contracts": []},
                {"id": "consumer", "title": "consumer", "spec": "b.md",
                 "depends_on": ["owner"], "contracts": ["api"]},
            ],
        }), encoding="utf-8")
        result = self.artifact(
            "epic-plan", "--epic", "contracts", "--file", str(plan)
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be declared", result.stderr)

    def test_epic_completion_requires_original_mechanical_binding(self):
        self.artifact("epic-init", "--epic", "bound", "--goal", "bound run")
        plan = self.repo / "bound-plan.json"
        plan.write_text(json.dumps({
            "contracts": [],
            "runs": [{"id": "run-a", "title": "a", "spec": "a.md",
                      "depends_on": [], "contracts": []}],
        }), encoding="utf-8")
        self.artifact("epic-plan", "--epic", "bound", "--file", str(plan),
                      check=True)
        state = self.repo / ".agents/runs/run-a/state.json"
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({
            "schema_version": 6, "run_id": "run-a", "phase": "DONE",
            "finished": True,
            "finished_head": self.git("rev-parse", "HEAD").stdout.strip(),
        }), encoding="utf-8")
        rejected = self.artifact(
            "epic-complete-run", "--epic", "bound", "--run", "run-a"
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("epic binding", rejected.stderr)

    def test_epic_completion_rejects_a_changed_active_plan(self):
        self.artifact("epic-init", "--epic", "changed", "--goal", "changed")
        plan = self.repo / "changed-plan.json"
        plan.write_text(json.dumps({
            "contracts": [],
            "runs": [{"id": "run-a", "title": "a", "spec": "a.md",
                      "depends_on": [], "contracts": []}],
        }), encoding="utf-8")
        self.artifact("epic-plan", "--epic", "changed", "--file", str(plan),
                      check=True)
        started = self.tool(
            STATE, "--run", "run-a", "init", "--goal", "a",
            "--epic", "changed", check=True,
        )
        state_path = self.repo / ".agents/runs/run-a/state.json"
        state = json.loads(started.stdout)
        state.update({"phase": "DONE", "finished": True,
                      "finished_head": state["base_commit"]})
        state_path.write_text(json.dumps(state), encoding="utf-8")
        manifest_path = self.repo / ".agents/epics/changed/epic.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["runs"]["run-a"]["title"] = "silently changed"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        rejected = self.artifact(
            "epic-complete-run", "--epic", "changed", "--run", "run-a"
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("binding changed", rejected.stderr)

    def test_epic_completion_rejects_symlinked_run_state(self):
        outside = Path(self.tmp.name) / "outside-run"
        outside.mkdir()
        runs = self.repo / ".agents/runs"
        runs.mkdir(parents=True)
        os.symlink(outside, runs / "run-a")
        rejected = self.artifact(
            "epic-complete-run", "--epic", "anything", "--run", "run-a"
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("symlink", rejected.stderr)

    def test_epic_plan_rejects_unhashable_dependencies_without_traceback(self):
        self.artifact("epic-init", "--epic", "malformed", "--goal", "bad JSON")
        plan = self.repo / "malformed-plan.json"
        plan.write_text(json.dumps({
            "contracts": [],
            "runs": [{"id": "run-a", "title": "a", "spec": "a.md",
                      "depends_on": [{}], "contracts": []}],
        }), encoding="utf-8")
        rejected = self.artifact(
            "epic-plan", "--epic", "malformed", "--file", str(plan)
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn("Traceback", rejected.stderr)

    def test_epic_init_rejects_symlinked_state_root(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        os.symlink(outside, self.repo / ".agents")
        rejected = self.artifact(
            "epic-init", "--epic", "escape", "--goal", "escape"
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertFalse((outside / "epics").exists())

    def test_epic_wal_files_never_follow_symlinks(self):
        self.artifact("epic-init", "--epic", "safe", "--goal", "safe WAL",
                      check=True)
        events = self.repo / ".agents/epics/safe/events.jsonl"
        events.unlink()
        outside = Path(self.tmp.name) / "outside-events.jsonl"
        outside.write_text("sentinel\n", encoding="utf-8")
        os.symlink(outside, events)
        plan = self.repo / "safe-plan.json"
        plan.write_text(json.dumps({
            "contracts": [],
            "runs": [{"id": "run-a", "title": "a", "spec": "a.md",
                      "depends_on": [], "contracts": []}],
        }), encoding="utf-8")
        rejected = self.artifact(
            "epic-plan", "--epic", "safe", "--file", str(plan)
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("symlink", rejected.stderr)
        self.assertEqual(outside.read_text(encoding="utf-8"), "sentinel\n")

    def test_epic_rejects_non_object_event_entries_before_mutation(self):
        self.artifact("epic-init", "--epic", "malformed-wal", "--goal", "WAL",
                      check=True)
        events = self.repo / ".agents/epics/malformed-wal/events.jsonl"
        events.write_text("[]\n", encoding="utf-8")
        plan = self.repo / "wal-plan.json"
        plan.write_text(json.dumps({
            "contracts": [],
            "runs": [{"id": "run-a", "title": "a", "spec": "a.md",
                      "depends_on": [], "contracts": []}],
        }), encoding="utf-8")
        rejected = self.artifact(
            "epic-plan", "--epic", "malformed-wal", "--file", str(plan)
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn("Traceback", rejected.stderr)
        manifest = json.loads(
            (self.repo / ".agents/epics/malformed-wal/epic.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["runs"], {})

    def test_epic_recovery_validates_wal_manifest_before_overwrite(self):
        self.artifact("epic-init", "--epic", "bad-intent", "--goal", "WAL",
                      check=True)
        manifest_path = self.repo / ".agents/epics/bad-intent/epic.json"
        original = manifest_path.read_text(encoding="utf-8")
        intent = self.repo / ".agents/epics/bad-intent/.transaction.json"
        intent.write_text(json.dumps({
            "schema_version": 1, "manifest": {},
            "event": {
                "event": "tampered", "txid": "a" * 32,
                "recorded_at": "2026-08-10T00:00:00+00:00",
            },
        }), encoding="utf-8")
        rejected = self.artifact("epic-status", "--epic", "bad-intent")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn("Traceback", rejected.stderr)
        self.assertEqual(manifest_path.read_text(encoding="utf-8"), original)
        self.assertTrue(intent.exists())

    def test_epic_runtime_rejects_unknown_top_level_and_status_evidence_mismatch(self):
        self.artifact("epic-init", "--epic", "shape", "--goal", "shape")
        manifest_path = self.repo / ".agents/epics/shape/epic.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["unexpected"] = True
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        invalid = self.artifact("epic-status", "--epic", "shape")
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("unknown or missing", invalid.stderr)

        schema = json.loads(
            (PLUGIN / "schemas/epic.schema.json").read_text(encoding="utf-8")
        )
        self.assertIn("allOf", schema["$defs"]["run"])


class ProductCloseoutTests(ArtifactCase):
    def complete_epic(self, epic_id="product", run_ids=("run-a", "run-b")):
        if not (self.repo / ".codex/qteam-project.json").is_file():
            self.tool(PROJECT_SETUP, str(self.repo), check=True)
        base = self.git("rev-parse", "HEAD").stdout.strip()
        self.artifact(
            "epic-init", "--epic", epic_id, "--goal", "deliver product",
            check=True,
        )
        plan = self.repo / f"{epic_id}-plan.json"
        plan.write_text(json.dumps({
            "contracts": [],
            "runs": [
                {
                    "id": run_id, "title": f"ship product {run_id}",
                    "spec": f"{run_id}.md", "depends_on": [], "contracts": [],
                }
                for run_id in run_ids
            ],
        }), encoding="utf-8")
        self.artifact(
            "epic-plan", "--epic", epic_id, "--file", str(plan), check=True,
        )
        release = None
        for index, run_id in enumerate(run_ids):
            started = self.tool(
                STATE, "--run", run_id, "init", "--goal", f"ship {run_id}",
                "--epic", epic_id, check=True,
            )
            state = json.loads(started.stdout)
            if index == 0:
                (self.repo / "product.txt").write_text(
                    f"{epic_id} released\n", encoding="utf-8"
                )
                self.git("add", "product.txt")
                self.git("commit", "-m", "release product")
                release = self.git("rev-parse", "HEAD").stdout.strip()
            state.update({"phase": "DONE", "finished": True,
                          "finished_head": release})
            state_path = self.repo / f".agents/runs/{run_id}/state.json"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            self.artifact(
                "epic-complete-run", "--epic", epic_id, "--run", run_id,
                check=True,
            )
        return base, release

    def write_draft(self, *, evidence_path="state.json", outcome_id="O1",
                    run_id="run-a"):
        draft = self.repo / "product-closeout-draft.json"
        draft.write_text(json.dumps({
            "summary": "The product shipped, but review found a reusable gap.",
            "retrospectives": [
                {
                    "lens": "product-outcome",
                    "reviewer": "product-review-session",
                    "summary": "The release met its product acceptance boundary.",
                    "evidence": [{"run": run_id, "path": "state.json"}],
                    "validation_scope": "the delivered product outcome",
                    "claim_boundary": "does not assess QTeam process behavior",
                },
                {
                    "lens": "qteam-behavior",
                    "reviewer": "qteam-review-session",
                    "summary": "A reusable process gap caused late detection.",
                    "evidence": [{"run": run_id, "path": "events.jsonl"}],
                    "validation_scope": "the recorded QTeam delivery process",
                    "claim_boundary": "does not assert product-domain correctness",
                },
            ],
            "outcomes": [{
                "id": outcome_id,
                "title": "Late workflow defect",
                "observation": "The defect was detected after implementation.",
                "evidence": [{"run": run_id, "path": evidence_path}],
                "validation_scope": "the completed run-a product delivery",
                "claim_boundary": "does not establish behavior in unrelated domains",
            }],
            "improvements": [{
                "id": "I1", "title": "Detect the defect before implementation",
                "target": "skill", "outcomes": [outcome_id],
                "proposal": "Teach the relevant QTeam skill to check the invariant.",
                "success_criterion": "A held-out replay rejects the defective design.",
                "status": "proposed",
            }],
            "prior_improvements": [],
        }), encoding="utf-8")
        return draft

    def test_product_closeout_seals_evidence_and_exports_only_approved_changes(self):
        _, release = self.complete_epic()
        draft = self.write_draft()
        sealed = self.artifact(
            "product-closeout-seal", "--epic", "product",
            "--release", release, "--file", str(draft),
        )
        self.assertEqual(sealed.returncode, 0, sealed.stderr)
        closeout_path = Path(sealed.stdout.strip())
        closeout = json.loads(closeout_path.read_text(encoding="utf-8"))
        self.assertEqual(closeout["release_commit"], release)
        self.assertEqual(closeout["qteam_runtime"]["version"], "0.18.0")
        self.assertNotIn("source_path", closeout["qteam_runtime"])
        self.assertEqual(
            closeout["qteam_runtime"]["project_manifest"],
            ".codex/qteam-project.json",
        )
        self.assertGreater(closeout["qteam_runtime"]["managed_files"], 0)
        evidence = closeout["outcomes"][0]["evidence"][0]
        self.assertRegex(evidence["sha256"], r"^[0-9a-f]{64}$")

        pending = self.artifact("product-closeout-status", "--epic", "product")
        self.assertEqual(pending.returncode, 0, pending.stderr)
        self.assertEqual(json.loads(pending.stdout)["status"], "pending-decisions")
        blocked = self.artifact("product-closeout-brief", "--epic", "product")
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("pending", blocked.stderr)

        approved = self.artifact(
            "product-closeout-decision", "--epic", "product", "--item", "I1",
            "--outcome", "approved", "--evidence",
            "Coordinator confirmed the defect and replay criterion.",
        )
        self.assertEqual(approved.returncode, 0, approved.stderr)
        status = self.artifact("product-closeout-status", "--epic", "product")
        self.assertEqual(json.loads(status.stdout)["status"], "complete")
        brief = self.artifact("product-closeout-brief", "--epic", "product")
        self.assertEqual(brief.returncode, 0, brief.stderr)
        payload = json.loads(brief.stdout)
        self.assertEqual([item["id"] for item in payload["improvements"]], ["I1"])
        self.assertEqual(payload["source"]["epic_id"], "product")
        self.assertEqual(payload["source"]["release_commit"], release)

    def test_product_closeout_requires_all_runs_and_release_ancestry(self):
        base, _ = self.complete_epic("ancestry")
        rejected = self.artifact(
            "product-closeout-seal", "--epic", "ancestry", "--release", base,
            "--file", str(self.write_draft()),
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("release commit", rejected.stderr)
        self.assertFalse(
            (self.repo / ".agents/epics/ancestry/product-closeout.json").exists()
        )

        self.artifact("epic-init", "--epic", "unfinished", "--goal", "unfinished")
        plan = self.repo / "unfinished-plan.json"
        plan.write_text(json.dumps({
            "contracts": [],
            "runs": [
                {"id": "run-a", "title": "a", "spec": "a.md",
                 "depends_on": [], "contracts": []},
                {"id": "run-b", "title": "b", "spec": "b.md",
                 "depends_on": [], "contracts": []},
            ],
        }), encoding="utf-8")
        self.artifact(
            "epic-plan", "--epic", "unfinished", "--file", str(plan), check=True,
        )
        unfinished = self.artifact(
            "product-closeout-seal", "--epic", "unfinished", "--release", "HEAD",
            "--file", str(self.write_draft()),
        )
        self.assertNotEqual(unfinished.returncode, 0)
        self.assertIn("not durably done", unfinished.stderr.lower())

    def test_product_closeout_rejects_single_run_epic(self):
        _, release = self.complete_epic(
            "single-run", run_ids=("run-a",)
        )
        rejected = self.artifact(
            "product-closeout-seal", "--epic", "single-run",
            "--release", release, "--file", str(self.write_draft()),
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("multiple runs", rejected.stderr)

    def test_product_closeout_requires_independent_retrospective_passes(self):
        _, release = self.complete_epic("independent-passes")
        draft = self.write_draft()
        payload = json.loads(draft.read_text(encoding="utf-8"))
        payload["retrospectives"][1]["reviewer"] = (
            payload["retrospectives"][0]["reviewer"]
        )
        draft.write_text(json.dumps(payload), encoding="utf-8")

        rejected = self.artifact(
            "product-closeout-seal", "--epic", "independent-passes",
            "--release", release, "--file", str(draft),
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("independent retrospective", rejected.stderr)

    def test_product_closeout_rejects_tampered_installed_runtime(self):
        _, release = self.complete_epic("runtime-binding")
        runtime = self.repo / ".codex/bin/agent_team_artifact.py"
        runtime.write_text(
            runtime.read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )

        rejected = self.artifact(
            "product-closeout-seal", "--epic", "runtime-binding",
            "--release", release, "--file", str(self.write_draft()),
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn("Traceback", rejected.stderr)
        self.assertIn("runtime", rejected.stderr.lower())

    def test_product_closeout_check_detects_changed_run_evidence(self):
        _, release = self.complete_epic("stale")
        self.artifact(
            "product-closeout-seal", "--epic", "stale", "--release", release,
            "--file", str(self.write_draft()), check=True,
        )
        state_path = self.repo / ".agents/runs/run-a/state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["tampered"] = True
        state_path.write_text(json.dumps(state), encoding="utf-8")
        refused = self.artifact(
            "product-closeout-decision", "--epic", "stale", "--item", "I1",
            "--outcome", "approved", "--evidence",
            "Stale evidence cannot support a coordinator decision.",
        )
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("stale", refused.stderr)
        closeout = json.loads(
            (self.repo / ".agents/epics/stale/product-closeout.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(closeout["improvements"][0]["status"], "proposed")
        stale = self.artifact("product-closeout-check", "--epic", "stale")
        self.assertNotEqual(stale.returncode, 0)
        report = json.loads(stale.stdout)
        self.assertEqual(report["status"], "stale")
        self.assertIn("state.json", report["stale"][0]["path"])

    def test_product_closeout_check_detects_missing_decision_event(self):
        _, release = self.complete_epic("decision-event")
        self.artifact(
            "product-closeout-seal", "--epic", "decision-event",
            "--release", release, "--file", str(self.write_draft()),
            check=True,
        )
        self.artifact(
            "product-closeout-decision", "--epic", "decision-event",
            "--item", "I1", "--outcome", "approved", "--evidence",
            "Coordinator approved the held-out replay criterion.",
            check=True,
        )
        events_path = (
            self.repo / ".agents/epics/decision-event/"
            "product-closeout-events.jsonl"
        )
        events = events_path.read_text(encoding="utf-8").splitlines()
        events_path.write_text(events[0] + "\n", encoding="utf-8")

        stale = self.artifact(
            "product-closeout-check", "--epic", "decision-event"
        )
        self.assertNotEqual(stale.returncode, 0)
        report = json.loads(stale.stdout)
        self.assertEqual(report["status"], "stale")
        self.assertIn("bound decision event", report["stale"][0]["reason"])

    def test_product_closeout_rejects_duplicate_and_tampered_event_audit_fields(self):
        _, release = self.complete_epic("event-audit")
        self.artifact(
            "product-closeout-seal", "--epic", "event-audit",
            "--release", release, "--file", str(self.write_draft()),
            check=True,
        )
        events_path = (
            self.repo / ".agents/epics/event-audit/"
            "product-closeout-events.jsonl"
        )
        original = events_path.read_text(encoding="utf-8")
        events_path.write_text(original + original, encoding="utf-8")

        duplicate = self.artifact(
            "product-closeout-check", "--epic", "event-audit"
        )
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertNotIn("Traceback", duplicate.stderr)
        self.assertIn("duplicate", duplicate.stderr)

        event = json.loads(original)
        event["txid"] = "c" * 32
        event["recorded_at"] = "2026-08-22T01:02:03+00:00"
        event["unexpected"] = "must not be accepted"
        events_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

        tampered = self.artifact(
            "product-closeout-check", "--epic", "event-audit"
        )
        self.assertNotEqual(tampered.returncode, 0)
        self.assertNotIn("Traceback", tampered.stderr)
        self.assertIn("transaction event", tampered.stderr)

    def test_product_closeout_rejects_structurally_valid_unbound_event(self):
        _, release = self.complete_epic("unbound-event")
        self.artifact(
            "product-closeout-seal", "--epic", "unbound-event",
            "--release", release, "--file", str(self.write_draft()),
            check=True,
        )
        self.artifact(
            "product-closeout-decision", "--epic", "unbound-event",
            "--item", "I1", "--outcome", "approved", "--evidence",
            "Coordinator approved the held-out replay criterion.",
            check=True,
        )
        events_path = (
            self.repo / ".agents/epics/unbound-event/"
            "product-closeout-events.jsonl"
        )
        ghost = {
            "event": "product_improvement_decided",
            "item": "ghost",
            "outcome": "approved",
            "evidence": "Structurally valid but not owned by the closeout.",
            "item_sha256": "d" * 64,
            "txid": "e" * 32,
            "recorded_at": "2026-08-22T01:02:03+00:00",
        }
        with events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(ghost) + "\n")

        rejected = self.artifact(
            "product-closeout-check", "--epic", "unbound-event"
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn("Traceback", rejected.stderr)
        self.assertIn("unbound product closeout event", rejected.stderr)

    def test_product_closeout_check_detects_changed_sealed_proposal(self):
        _, release = self.complete_epic("sealed-proposal")
        self.artifact(
            "product-closeout-seal", "--epic", "sealed-proposal",
            "--release", release, "--file", str(self.write_draft()),
            check=True,
        )
        closeout_path = (
            self.repo / ".agents/epics/sealed-proposal/product-closeout.json"
        )
        closeout = json.loads(closeout_path.read_text(encoding="utf-8"))
        closeout["improvements"][0]["proposal"] = "A coherently edited proposal."
        closeout_path.write_text(json.dumps(closeout), encoding="utf-8")

        stale = self.artifact(
            "product-closeout-check", "--epic", "sealed-proposal"
        )
        self.assertNotEqual(stale.returncode, 0)
        report = json.loads(stale.stdout)
        self.assertEqual(report["status"], "stale")
        self.assertIn("sealed closeout event", report["stale"][0]["reason"])

        refused = self.artifact(
            "product-closeout-decision", "--epic", "sealed-proposal",
            "--item", "I1", "--outcome", "approved", "--evidence",
            "Coordinator evidence must not approve edited content.",
        )
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("sealed closeout event", refused.stderr)

    def test_product_closeout_rejects_symlinked_event_log_without_outside_write(self):
        _, release = self.complete_epic("event-symlink")
        epic_dir = self.repo / ".agents/epics/event-symlink"
        outside = self.repo.parent / "outside-closeout-events"
        outside.write_text("unchanged\n", encoding="utf-8")
        (epic_dir / "product-closeout-events.jsonl").symlink_to(outside)

        rejected = self.artifact(
            "product-closeout-seal", "--epic", "event-symlink",
            "--release", release, "--file", str(self.write_draft()),
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn("Traceback", rejected.stderr)
        self.assertIn("symlink", rejected.stderr)
        self.assertEqual(outside.read_text(encoding="utf-8"), "unchanged\n")
        self.assertFalse((epic_dir / "product-closeout.json").exists())

    def test_product_closeout_recovery_rejects_unbound_wal_before_overwrite(self):
        _, release = self.complete_epic("unbound-wal")
        self.artifact(
            "product-closeout-seal", "--epic", "unbound-wal",
            "--release", release, "--file", str(self.write_draft()),
            check=True,
        )
        epic_dir = self.repo / ".agents/epics/unbound-wal"
        closeout_path = epic_dir / "product-closeout.json"
        original = closeout_path.read_text(encoding="utf-8")
        replacement = json.loads(original)
        replacement["summary"] = "A valid shape that was not a valid transition."
        replacement["improvements"][0]["status"] = "approved"
        replacement["improvements"][0]["decision"] = {
            "authority": "coordinator",
            "outcome": "approved",
            "evidence": "A forged but structurally valid WAL decision.",
            "decided_at": "2026-08-22T00:00:00+00:00",
            "txid": "a" * 32,
        }
        item_sha256 = hashlib.sha256(json.dumps(
            replacement["improvements"][0], sort_keys=True,
            separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        intent = epic_dir / ".product-closeout.transaction.json"
        intent.write_text(json.dumps({
            "schema_version": 1,
            "closeout": replacement,
            "event": {
                "event": "product_improvement_decided",
                "item": "I1",
                "outcome": "approved",
                "evidence": "A forged but structurally valid WAL decision.",
                "item_sha256": item_sha256,
                "txid": "a" * 32,
                "recorded_at": "2026-08-22T00:00:00+00:00",
            },
        }), encoding="utf-8")

        rejected = self.artifact(
            "product-closeout-status", "--epic", "unbound-wal"
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn("Traceback", rejected.stderr)
        self.assertIn("transaction transition", rejected.stderr)
        self.assertEqual(closeout_path.read_text(encoding="utf-8"), original)
        self.assertTrue(intent.exists())

    def test_product_closeout_recovery_preserves_wal_for_tampered_same_txid_event(self):
        _, release = self.complete_epic("tampered-wal-event")
        self.artifact(
            "product-closeout-seal", "--epic", "tampered-wal-event",
            "--release", release, "--file", str(self.write_draft()),
            check=True,
        )
        epic_dir = self.repo / ".agents/epics/tampered-wal-event"
        closeout_path = epic_dir / "product-closeout.json"
        events_path = epic_dir / "product-closeout-events.jsonl"
        closeout = json.loads(closeout_path.read_text(encoding="utf-8"))
        event = json.loads(events_path.read_text(encoding="utf-8"))
        intent = epic_dir / ".product-closeout.transaction.json"
        intent.write_text(json.dumps({
            "schema_version": 1, "closeout": closeout, "event": event,
        }), encoding="utf-8")
        event["unexpected"] = "must block recovery before any write"
        events_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
        closeout_path.unlink()

        rejected = self.artifact(
            "product-closeout-status", "--epic", "tampered-wal-event"
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn("Traceback", rejected.stderr)
        self.assertIn("transaction event", rejected.stderr)
        self.assertFalse(closeout_path.exists())
        self.assertTrue(intent.exists())

    def test_product_closeout_recovery_rejects_epic_run_subset_wal(self):
        _, release = self.complete_epic(
            "subset-wal", run_ids=("run-a", "run-b", "run-c")
        )
        self.artifact(
            "product-closeout-seal", "--epic", "subset-wal",
            "--release", release, "--file", str(self.write_draft()),
            check=True,
        )
        epic_dir = self.repo / ".agents/epics/subset-wal"
        closeout_path = epic_dir / "product-closeout.json"
        events_path = epic_dir / "product-closeout-events.jsonl"
        closeout = json.loads(closeout_path.read_text(encoding="utf-8"))
        closeout["runs"].pop("run-c")
        sealed_view = {
            key: value for key, value in closeout.items()
            if key != "updated_at"
        }
        sealed_view["improvements"] = [
            {
                key: value for key, value in item.items()
                if key not in {"status", "decision"}
            }
            for item in closeout["improvements"]
        ]
        sealed_sha256 = hashlib.sha256(json.dumps(
            sealed_view, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        event = {
            "event": "product_closeout_sealed",
            "epic": "subset-wal",
            "release_commit": release,
            "sealed_sha256": sealed_sha256,
            "txid": closeout["seal"]["txid"],
            "recorded_at": closeout["seal"]["recorded_at"],
        }
        closeout_path.unlink()
        events_path.unlink()
        intent = epic_dir / ".product-closeout.transaction.json"
        intent.write_text(json.dumps({
            "schema_version": 1, "closeout": closeout, "event": event,
        }), encoding="utf-8")

        rejected = self.artifact(
            "product-closeout-status", "--epic", "subset-wal"
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn("Traceback", rejected.stderr)
        self.assertIn("epic binding", rejected.stderr)
        self.assertFalse(closeout_path.exists())
        self.assertTrue(intent.exists())

    def test_product_closeout_recovery_fsyncs_after_removing_valid_wal(self):
        _, release = self.complete_epic("valid-wal")
        self.artifact(
            "product-closeout-seal", "--epic", "valid-wal",
            "--release", release, "--file", str(self.write_draft()),
            check=True,
        )
        epic_dir = self.repo / ".agents/epics/valid-wal"
        closeout = json.loads(
            (epic_dir / "product-closeout.json").read_text(encoding="utf-8")
        )
        event = json.loads(
            (epic_dir / "product-closeout-events.jsonl")
            .read_text(encoding="utf-8").splitlines()[0]
        )
        intent = epic_dir / ".product-closeout.transaction.json"
        intent.write_text(json.dumps({
            "schema_version": 1, "closeout": closeout, "event": event,
        }), encoding="utf-8")

        spec = importlib.util.spec_from_file_location(
            "product_closeout_recovery_test", ARTIFACT,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fsync_states = []
        real_fsync = module.os.fsync

        def recorded_fsync(descriptor):
            fsync_states.append(intent.exists())
            return real_fsync(descriptor)

        module.os.fsync = recorded_fsync
        try:
            with module.locked_product_closeout(
                    self.repo, "valid-wal") as (_directory, _path):
                pass
        finally:
            module.os.fsync = real_fsync
        self.assertFalse(intent.exists())
        self.assertIn(False, fsync_states)

    def test_product_closeout_recovers_valid_seal_wal_at_each_crash_point(self):
        for crash_point in ("before-closeout", "before-event", "before-intent"):
            with self.subTest(crash_point=crash_point):
                epic_id = crash_point
                run_ids = (f"{epic_id}-a", f"{epic_id}-b")
                _, release = self.complete_epic(epic_id, run_ids=run_ids)
                self.artifact(
                    "product-closeout-seal", "--epic", epic_id,
                    "--release", release, "--file",
                    str(self.write_draft(run_id=run_ids[0])),
                    check=True,
                )
                epic_dir = self.repo / f".agents/epics/{epic_id}"
                closeout_path = epic_dir / "product-closeout.json"
                events_path = epic_dir / "product-closeout-events.jsonl"
                closeout = json.loads(
                    closeout_path.read_text(encoding="utf-8")
                )
                event = json.loads(
                    events_path.read_text(encoding="utf-8").splitlines()[0]
                )
                intent = epic_dir / ".product-closeout.transaction.json"
                intent.write_text(json.dumps({
                    "schema_version": 1,
                    "closeout": closeout,
                    "event": event,
                }), encoding="utf-8")
                if crash_point == "before-closeout":
                    closeout_path.unlink()
                    events_path.unlink()
                elif crash_point == "before-event":
                    events_path.unlink()

                recovered = self.artifact(
                    "product-closeout-status", "--epic", epic_id
                )
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                self.assertEqual(
                    json.loads(recovered.stdout)["status"],
                    "pending-decisions",
                )
                self.assertTrue(closeout_path.is_file())
                self.assertEqual(
                    len(events_path.read_text(encoding="utf-8").splitlines()),
                    1,
                )
                self.assertFalse(intent.exists())

    def test_product_closeout_uses_one_validated_run_state_snapshot(self):
        _, release = self.complete_epic("single-snapshot")
        epic_path = self.repo / ".agents/epics/single-snapshot/epic.json"
        manifest = json.loads(epic_path.read_text(encoding="utf-8"))
        expected = manifest["runs"]["run-a"]["run_state_sha256"]
        state_path = self.repo / ".agents/runs/run-a/state.json"

        spec = importlib.util.spec_from_file_location(
            "product_closeout_snapshot_test", ARTIFACT,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        real_digest = module.file_sha256
        real_regular_bytes = module.regular_bytes
        replaced = False
        digest_calls = 0
        snapshot_reads = 0

        def replace_after_validated_digest(path):
            nonlocal digest_calls, replaced
            if Path(path) == state_path:
                digest_calls += 1
            digest = real_digest(path)
            if Path(path) == state_path and not replaced:
                replaced = True
                state_path.write_text(
                    state_path.read_text(encoding="utf-8") + "\n",
                    encoding="utf-8",
                )
            return digest

        def count_snapshot_reads(path, label):
            nonlocal snapshot_reads
            if Path(path) == state_path:
                snapshot_reads += 1
            return real_regular_bytes(path, label)

        module.file_sha256 = replace_after_validated_digest
        module.regular_bytes = count_snapshot_reads
        try:
            snapshots = module._snapshot_product_runs(
                self.repo, manifest, release
            )
        finally:
            module.file_sha256 = real_digest
            module.regular_bytes = real_regular_bytes
        self.assertEqual(snapshot_reads, 1)
        self.assertEqual(digest_calls, 0)
        self.assertFalse(replaced)
        self.assertEqual(snapshots["run-a"]["state_sha256"], expected)

    def test_product_closeout_uses_one_validated_learning_snapshot(self):
        _, release = self.complete_epic("learning-snapshot")
        epic_path = self.repo / ".agents/epics/learning-snapshot/epic.json"
        manifest = json.loads(epic_path.read_text(encoding="utf-8"))
        learning_path = (
            self.repo / ".agents/runs/run-a/learning-outbox/manifest.json"
        )
        learning_path.parent.mkdir(parents=True)
        learning_bytes = json.dumps({
            "schema_version": 1,
            "run_id": "run-a",
            "project": "product",
            "items": [],
        }).encode("utf-8")
        learning_path.write_bytes(learning_bytes)
        expected = hashlib.sha256(learning_bytes).hexdigest()

        spec = importlib.util.spec_from_file_location(
            "product_closeout_learning_snapshot_test", ARTIFACT,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        real_digest = module.file_sha256
        real_regular_bytes = module.regular_bytes
        replaced = False
        digest_calls = 0
        snapshot_reads = 0

        def replace_after_validated_digest(path):
            nonlocal digest_calls, replaced
            if Path(path) == learning_path:
                digest_calls += 1
            digest = real_digest(path)
            if Path(path) == learning_path and not replaced:
                replaced = True
                learning_path.write_text(json.dumps({
                    "schema_version": 1,
                    "run_id": "run-a",
                    "project": "product",
                    "items": [{
                        "id": "late-change",
                        "title": "A later valid learning item",
                        "category": "lesson",
                        "status": "proposed",
                    }],
                }), encoding="utf-8")
            return digest

        def count_snapshot_reads(path, label):
            nonlocal snapshot_reads
            if Path(path) == learning_path:
                snapshot_reads += 1
            return real_regular_bytes(path, label)

        module.file_sha256 = replace_after_validated_digest
        module.regular_bytes = count_snapshot_reads
        try:
            snapshots = module._snapshot_product_runs(
                self.repo, manifest, release
            )
        finally:
            module.file_sha256 = real_digest
            module.regular_bytes = real_regular_bytes
        learning = snapshots["run-a"]["learning"]
        self.assertEqual(snapshot_reads, 1)
        self.assertEqual(digest_calls, 0)
        self.assertFalse(replaced)
        self.assertEqual(learning["sha256"], expected)
        self.assertEqual(learning["items"], 0)

    def test_product_closeout_rejects_invalid_learning_manifest(self):
        _, release = self.complete_epic("invalid-learning")
        learning_path = (
            self.repo / ".agents/runs/run-a/learning-outbox/manifest.json"
        )
        learning_path.parent.mkdir(parents=True)
        learning_path.write_text(json.dumps({"items": []}), encoding="utf-8")

        rejected = self.artifact(
            "product-closeout-seal", "--epic", "invalid-learning",
            "--release", release, "--file", str(self.write_draft()),
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn("Traceback", rejected.stderr)
        self.assertIn("learning manifest", rejected.stderr)

        learning_path.write_text(json.dumps({
            "schema_version": 1,
            "run_id": "another-run",
            "project": "product",
            "items": [],
        }), encoding="utf-8")
        wrong_run = self.artifact(
            "product-closeout-seal", "--epic", "invalid-learning",
            "--release", release, "--file", str(self.write_draft()),
        )
        self.assertNotEqual(wrong_run.returncode, 0)
        self.assertIn("run_id does not match", wrong_run.stderr)

    def test_product_closeout_check_reports_directory_evidence_as_stale(self):
        _, release = self.complete_epic("directory-evidence")
        self.artifact(
            "product-closeout-seal", "--epic", "directory-evidence",
            "--release", release, "--file", str(self.write_draft()),
            check=True,
        )
        state_path = self.repo / ".agents/runs/run-a/state.json"
        state_path.unlink()
        state_path.mkdir()

        stale = self.artifact(
            "product-closeout-check", "--epic", "directory-evidence"
        )
        self.assertNotEqual(stale.returncode, 0)
        self.assertNotIn("Traceback", stale.stderr)
        report = json.loads(stale.stdout)
        self.assertEqual(report["status"], "stale")
        self.assertEqual(report["stale"][0]["path"],
                         ".agents/runs/run-a/state.json")

    def test_product_closeout_rejects_unsafe_or_unbound_draft_evidence(self):
        _, release = self.complete_epic("unsafe")
        unsafe = self.artifact(
            "product-closeout-seal", "--epic", "unsafe", "--release", release,
            "--file", str(self.write_draft(evidence_path="../state.json")),
        )
        self.assertNotEqual(unsafe.returncode, 0)
        self.assertNotIn("Traceback", unsafe.stderr)
        self.assertIn("evidence", unsafe.stderr)

        unknown = self.write_draft(outcome_id="missing")
        payload = json.loads(unknown.read_text(encoding="utf-8"))
        payload["improvements"][0]["outcomes"] = ["O-does-not-exist"]
        unknown.write_text(json.dumps(payload), encoding="utf-8")
        rejected = self.artifact(
            "product-closeout-seal", "--epic", "unsafe", "--release", release,
            "--file", str(unknown),
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn("Traceback", rejected.stderr)
        self.assertIn("unknown outcome", rejected.stderr)

    def test_product_closeout_schema_exposes_the_runtime_contract(self):
        schema_path = PLUGIN / "schemas/product-closeout.schema.json"
        self.assertTrue(schema_path.is_file())
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)
        self.assertIn("improvements", schema["required"])
        self.assertIn("retrospectives", schema["required"])
        self.assertEqual(schema["properties"]["runs"]["minProperties"], 2)
        self.assertEqual(
            schema["properties"]["runs"]["propertyNames"],
            {"$ref": "#/$defs/id"},
        )
        self.assertIn(
            "/state\\.json$",
            schema["$defs"]["run"]["properties"]["state_path"]["pattern"],
        )
        self.assertIn(
            "project_manifest_sha256",
            schema["$defs"]["qteam_runtime"]["required"],
        )
        self.assertEqual(
            set(schema["$defs"]["improvement"]["properties"]["target"]["enum"]),
            {"skill", "worker-prompt", "tool", "policy", "eval"},
        )
        status_rules = {
            rule["if"]["properties"]["status"]["const"]: rule["then"]
            for rule in schema["$defs"]["improvement"]["allOf"]
        }
        for outcome in ("approved", "rejected"):
            self.assertEqual(
                status_rules[outcome]["properties"]["decision"]
                ["properties"]["outcome"]["const"],
                outcome,
            )


class KnowledgeArtifactTests(ArtifactCase):
    def test_code_index_is_bound_to_commit_and_source_blobs(self):
        draft = self.repo / "index-draft.json"
        draft.write_text(json.dumps({
            "components": [{
                "id": "root", "summary": "repository entry point",
                "sources": ["README.md"], "symbols": [], "contracts": [],
            }],
            "external_resources": [],
        }), encoding="utf-8")
        sealed = self.artifact(
            "index-seal", "--file", str(draft),
            "--output", ".agents/index/components.json",
        )
        self.assertEqual(sealed.returncode, 0, sealed.stderr)
        checked = self.artifact(
            "index-check", "--file", ".agents/index/components.json"
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(json.loads(checked.stdout)["status"], "fresh")

        (self.repo / "README.md").write_text("changed\n", encoding="utf-8")
        stale = self.artifact(
            "index-check", "--file", ".agents/index/components.json"
        )
        self.assertNotEqual(stale.returncode, 0)
        self.assertEqual(json.loads(stale.stdout)["status"], "stale")
        self.assertEqual(json.loads(stale.stdout)["stale_sources"][0]["path"],
                         "README.md")

    def test_code_index_rejects_unhashable_sources_without_traceback(self):
        draft = self.repo / "bad-index.json"
        draft.write_text(json.dumps({
            "components": [{
                "id": "root", "summary": "bad source", "sources": [{}],
                "symbols": [], "contracts": [],
            }],
            "external_resources": [],
        }), encoding="utf-8")
        rejected = self.artifact(
            "index-seal", "--file", str(draft), "--output", "index.json"
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn("Traceback", rejected.stderr)

    def test_code_index_external_optional_fields_match_published_schema_types(self):
        draft = self.repo / "typed-external-index.json"
        payload = {
            "components": [{
                "id": "root", "summary": "root", "sources": ["README.md"],
                "symbols": [], "contracts": [],
            }],
            "external_resources": [{
                "id": "docs", "url": "https://example.invalid/docs",
                "evidence": "primary docs", "version": 7, "verified_at": {},
            }],
        }
        draft.write_text(json.dumps(payload), encoding="utf-8")
        rejected = self.artifact(
            "index-seal", "--file", str(draft), "--output", "typed-index.json"
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("must be strings", rejected.stderr)

        payload["external_resources"][0].update({
            "version": "v1", "verified_at": "2026-08-10",
        })
        draft.write_text(json.dumps(payload), encoding="utf-8")
        self.artifact(
            "index-seal", "--file", str(draft), "--output", "typed-index.json",
            check=True,
        )
        sealed = self.repo / "typed-index.json"
        artifact = json.loads(sealed.read_text(encoding="utf-8"))
        artifact["external_resources"][0]["version"] = 7
        sealed.write_text(json.dumps(artifact), encoding="utf-8")
        checked = self.artifact("index-check", "--file", "typed-index.json")
        self.assertNotEqual(checked.returncode, 0)
        self.assertNotIn("Traceback", checked.stderr)

    def test_code_index_does_not_trust_a_coherently_edited_blob_claim(self):
        draft = self.repo / "index.json"
        draft.write_text(json.dumps({
            "components": [{"id": "root", "summary": "root",
                            "sources": ["README.md"], "symbols": [],
                            "contracts": []}],
            "external_resources": [],
        }), encoding="utf-8")
        self.artifact(
            "index-seal", "--file", str(draft), "--output", "sealed-index.json",
            check=True,
        )
        (self.repo / "README.md").write_text("new committed source\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-m", "change source")
        sealed_path = self.repo / "sealed-index.json"
        sealed = json.loads(sealed_path.read_text(encoding="utf-8"))
        new_blob = self.git("rev-parse", "HEAD:README.md").stdout.strip()
        sealed["components"][0]["sources"][0]["blob"] = new_blob
        sealed_path.write_text(json.dumps(sealed), encoding="utf-8")
        checked = self.artifact("index-check", "--file", "sealed-index.json")
        self.assertNotEqual(checked.returncode, 0)
        self.assertIn("base commit blob", checked.stdout)

    def test_spec_drift_is_a_head_bound_proposal_not_an_apply_path(self):
        self.init_run("delivery")
        self.put_finish_decision("delivery", "drift-D1")
        spec = self.repo / "spec.md"
        spec.write_text(GOOD_SPEC, encoding="utf-8")
        draft = self.repo / "drift-draft.json"
        draft.write_text(json.dumps({
            "summary": "implementation clarified one contract",
            "changes": [{
                "id": "D1", "layer": "design", "original": "implicit order",
                "actual": "stable order", "reason": "integration evidence",
                "proposal": "document stable order", "decision_id": "drift-D1",
            }],
        }), encoding="utf-8")
        sealed = self.artifact(
            "drift-seal", "--run", "delivery", "--file", str(draft),
            "--source", "spec.md", "--output",
            ".agents/runs/delivery/spec-drift.json",
        )
        self.assertEqual(sealed.returncode, 0, sealed.stderr)
        report = json.loads(
            (self.repo / ".agents/runs/delivery/spec-drift.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(report["approval_required"])
        self.assertEqual(report["apply_status"], "proposal-only")
        self.assertEqual(report["changes"][0]["status"], "pending")
        decision_record = json.loads(
            (self.repo / ".agents/runs/delivery/decisions/drift-D1.json").read_text(
                encoding="utf-8"
            )
        )
        change = json.loads(draft.read_text(encoding="utf-8"))["changes"][0]
        expected_change = hashlib.sha256(
            json.dumps(change, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.assertEqual(decision_record["subject"], {
            "kind": "spec-drift-change", "sha256": expected_change,
        })

        fresh = self.artifact(
            "drift-check", "--file", ".agents/runs/delivery/spec-drift.json"
        )
        self.assertNotEqual(fresh.returncode, 0)
        self.assertEqual(json.loads(fresh.stdout)["status"], "pending-approval")
        resolved = self.tool(
            STATE, "--run", "delivery", "decision-resolve",
            "drift-D1", "--outcome", "allow",
            "--choice", "accept proposal", "--evidence", "user approved",
        )
        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        fresh = self.artifact(
            "drift-check", "--file", ".agents/runs/delivery/spec-drift.json"
        )
        self.assertEqual(fresh.returncode, 0, fresh.stderr)
        self.assertEqual(json.loads(fresh.stdout)["status"], "approved")
        spec.write_text(GOOD_SPEC + "\nchanged\n", encoding="utf-8")
        stale = self.artifact(
            "drift-check", "--file", ".agents/runs/delivery/spec-drift.json"
        )
        self.assertNotEqual(stale.returncode, 0)
        self.assertEqual(json.loads(stale.stdout)["status"], "stale")

    def test_drift_check_rejects_tampered_approval_contract(self):
        self.init_run("delivery")
        self.put_finish_decision("delivery", "D-D1")
        spec = self.repo / "spec.md"
        spec.write_text(GOOD_SPEC, encoding="utf-8")
        draft = self.repo / "drift.json"
        draft.write_text(json.dumps({
            "summary": "drift", "changes": [{
                "id": "D1", "layer": "tasks", "original": "a", "actual": "b",
                "reason": "evidence", "proposal": "update", "decision_id": "D-D1",
            }],
        }), encoding="utf-8")
        self.artifact(
            "drift-seal", "--run", "delivery", "--file", str(draft),
            "--source", "spec.md", "--output",
            ".agents/runs/delivery/spec-drift.json", check=True,
        )
        path = self.repo / ".agents/runs/delivery/spec-drift.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["approval_required"] = False
        path.write_text(json.dumps(payload), encoding="utf-8")
        rejected = self.artifact(
            "drift-check", "--file", ".agents/runs/delivery/spec-drift.json"
        )
        self.assertNotEqual(rejected.returncode, 0)

    def test_drift_seal_requires_a_real_run_head_and_finish_decision(self):
        spec = self.repo / "spec.md"
        spec.write_text(GOOD_SPEC, encoding="utf-8")
        draft = self.repo / "drift.json"
        draft.write_text(json.dumps({
            "summary": "drift", "changes": [{
                "id": "D1", "layer": "design", "original": "a", "actual": "b",
                "reason": "evidence", "proposal": "update",
                "decision_id": "missing-decision",
            }],
        }), encoding="utf-8")
        missing_run = self.artifact(
            "drift-seal", "--run", "missing", "--file", str(draft),
            "--source", "spec.md", "--output", "drift-report.json",
        )
        self.assertNotEqual(missing_run.returncode, 0)

        state = self.init_run("actual")
        missing_decision = self.artifact(
            "drift-seal", "--run", "actual", "--file", str(draft),
            "--source", "spec.md", "--output", "drift-report.json",
        )
        self.assertNotEqual(missing_decision.returncode, 0)
        self.assertIn("decision", missing_decision.stderr)

        self.put_finish_decision("actual", "missing-decision")
        other = self.repo / "other.txt"
        other.write_text("other\n", encoding="utf-8")
        self.git("add", "other.txt")
        self.git("commit", "-m", "other head")
        wrong_head = self.artifact(
            "drift-seal", "--run", "actual", "--file", str(draft),
            "--source", "spec.md", "--output", "drift-report.json",
            "--head", "HEAD",
        )
        self.assertNotEqual(wrong_head.returncode, 0)
        self.assertIn("integration head", wrong_head.stderr)
        self.assertNotEqual(state["base_commit"], self.git("rev-parse", "HEAD").stdout.strip())

    def test_drift_seal_rejects_a_generic_pre_resolved_finish_approval(self):
        self.init_run("delivery")
        self.put_finish_decision("delivery", "generic-approval")
        resolved = self.tool(
            STATE, "--run", "delivery", "decision-resolve", "generic-approval",
            "--outcome", "allow", "--choice", "generic yes",
            "--evidence", "approved before proposal",
        )
        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        spec = self.repo / "spec.md"
        spec.write_text(GOOD_SPEC, encoding="utf-8")
        draft = self.repo / "pre-resolved-drift.json"
        draft.write_text(json.dumps({
            "summary": "later proposal", "changes": [{
                "id": "D1", "layer": "design", "original": "a", "actual": "b",
                "reason": "new evidence", "proposal": "new proposal",
                "decision_id": "generic-approval",
            }],
        }), encoding="utf-8")
        rejected = self.artifact(
            "drift-seal", "--run", "delivery", "--file", str(draft),
            "--source", "spec.md", "--output",
            ".agents/runs/delivery/spec-drift.json",
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("unbound and open", rejected.stderr)

    def test_drift_registration_never_follows_a_run_event_symlink(self):
        self.init_run("delivery")
        self.put_finish_decision("delivery", "drift-D1")
        events = self.repo / ".agents/runs/delivery/events.jsonl"
        events.unlink()
        outside = Path(self.tmp.name) / "outside-run-events.jsonl"
        outside.write_text("sentinel\n", encoding="utf-8")
        os.symlink(outside, events)
        spec = self.repo / "spec.md"
        spec.write_text(GOOD_SPEC, encoding="utf-8")
        draft = self.repo / "safe-drift.json"
        draft.write_text(json.dumps({
            "summary": "safe", "changes": [{
                "id": "D1", "layer": "design", "original": "a", "actual": "b",
                "reason": "evidence", "proposal": "proposal",
                "decision_id": "drift-D1",
            }],
        }), encoding="utf-8")
        rejected = self.artifact(
            "drift-seal", "--run", "delivery", "--file", str(draft),
            "--source", "spec.md", "--output",
            ".agents/runs/delivery/spec-drift.json",
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("symlink", rejected.stderr)
        self.assertEqual(outside.read_text(encoding="utf-8"), "sentinel\n")

    def test_drift_seal_rejects_duplicate_sources_before_binding_decisions(self):
        self.init_run("delivery")
        self.put_finish_decision("delivery", "drift-D1")
        spec = self.repo / "spec.md"
        spec.write_text(GOOD_SPEC, encoding="utf-8")
        draft = self.repo / "duplicate-source-drift.json"
        draft.write_text(json.dumps({
            "summary": "duplicate source", "changes": [{
                "id": "D1", "layer": "design", "original": "a", "actual": "b",
                "reason": "evidence", "proposal": "proposal",
                "decision_id": "drift-D1",
            }],
        }), encoding="utf-8")
        rejected = self.artifact(
            "drift-seal", "--run", "delivery", "--file", str(draft),
            "--source", "spec.md", "--source", str(spec), "--output",
            ".agents/runs/delivery/spec-drift.json",
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("duplicates", rejected.stderr)
        decision = json.loads(
            (self.repo / ".agents/runs/delivery/decisions/drift-D1.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("subject", decision)

    def test_drift_no_drift_evidence_matches_schema_for_seal_and_check(self):
        self.init_run("delivery")
        self.put_finish_decision("delivery", "drift-D1")
        spec = self.repo / "spec.md"
        spec.write_text(GOOD_SPEC, encoding="utf-8")
        draft = self.repo / "typed-no-drift.json"
        payload = {
            "summary": "typed", "no_drift_evidence": {}, "changes": [{
                "id": "D1", "layer": "design", "original": "a", "actual": "b",
                "reason": "evidence", "proposal": "proposal",
                "decision_id": "drift-D1",
            }],
        }
        draft.write_text(json.dumps(payload), encoding="utf-8")
        rejected = self.artifact(
            "drift-seal", "--run", "delivery", "--file", str(draft),
            "--source", "spec.md", "--output",
            ".agents/runs/delivery/spec-drift.json",
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("string or null", rejected.stderr)

        payload["no_drift_evidence"] = None
        draft.write_text(json.dumps(payload), encoding="utf-8")
        self.artifact(
            "drift-seal", "--run", "delivery", "--file", str(draft),
            "--source", "spec.md", "--output",
            ".agents/runs/delivery/spec-drift.json", check=True,
        )
        report_path = self.repo / ".agents/runs/delivery/spec-drift.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["no_drift_evidence"] = {}
        report_path.write_text(json.dumps(report), encoding="utf-8")
        checked = self.artifact(
            "drift-check", "--file", ".agents/runs/delivery/spec-drift.json"
        )
        self.assertNotEqual(checked.returncode, 0)
        self.assertIn("string or null", checked.stderr)


class ArtifactPackagingTests(unittest.TestCase):
    def test_runtime_contract_and_attribution_include_artifact_capabilities(self):
        sys.path.insert(0, str(PLUGIN / "scripts"))
        try:
            import qteam_project
        finally:
            sys.path.pop(0)
        for binary in ("agent-team-artifact", "agent_team_artifact.py"):
            self.assertIn(binary, qteam_project.BINARIES)
        for schema in ("artifact-lint", "code-index", "epic", "spec-drift"):
            self.assertIn(schema, qteam_project.SCHEMAS)
            json.loads((PLUGIN / f"schemas/{schema}.schema.json").read_text(
                encoding="utf-8"
            ))
        notices = (PLUGIN / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        license_text = (PLUGIN / "LICENSES/Smart-Ralph-MIT.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("Smart Ralph", notices)
        self.assertIn("Copyright (c) 2025 tzachbon", license_text)


if __name__ == "__main__":
    unittest.main()
