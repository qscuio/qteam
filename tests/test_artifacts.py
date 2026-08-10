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
