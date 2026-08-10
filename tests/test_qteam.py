import json
import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace


SOURCE = Path(__file__).resolve().parents[1]
PLUGIN = SOURCE / "plugins/qteam"
STATE = PLUGIN / "bin/agent-team-state.py"
WORKER = PLUGIN / "bin/agent-team-worker.py"
CHECK = PLUGIN / "bin/agent-team-check-task.py"
REVIEW = PLUGIN / "bin/agent-team-review.py"
ARTIFACT = PLUGIN / "bin/agent_team_artifact.py"
POLICY = PLUGIN / "bin/agent_team_policy.py"
EVAL = PLUGIN / "bin/agent_team_eval.py"
FINISH = PLUGIN / "bin/agent-team-finish.py"
IMPORT = PLUGIN / "bin/import-agent-learning.py"
WAKE = PLUGIN / "bin/wake-agent-team.sh"
PROJECT_SETUP = PLUGIN / "scripts/project-setup.py"
PROJECT_UNINSTALL = PLUGIN / "scripts/project-uninstall.py"
QTEAM = SOURCE / "qteam"


class RepoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.run_git("init", "-b", "main")
        self.run_git("config", "user.email", "qteam@example.invalid")
        self.run_git("config", "user.name", "QTeam Test")
        (self.repo / "README.md").write_text("base\n", encoding="utf-8")
        self.run_git("add", "README.md")
        self.run_git("commit", "-m", "base")

    def tearDown(self):
        self.tmp.cleanup()

    def run_git(self, *args, cwd=None, check=True):
        return subprocess.run(["git", *args], cwd=cwd or self.repo, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              check=check)

    def run_tool(self, tool, *args, check=False, env=None):
        return subprocess.run([sys.executable, str(tool), *args], cwd=self.repo,
                              text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, check=check, env=env)

    def init_run(self, run="test-run", *init_args):
        result = self.run_tool(STATE, "--run", run, "init", "--goal", "test goal",
                               *init_args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.repo / ".agents/runs" / run

    def make_task_record(self, run, task, wave, write_set, depends_on):
        branch = f"agent/{run.name}/{task}"
        worktree = run / "worktrees" / task
        worktree.parent.mkdir(parents=True, exist_ok=True)
        self.run_git("worktree", "add", "-b", branch, str(worktree), "HEAD")
        return {
            "id": task, "title": f"change {task}", "status": "pending",
            "attempt": 1, "branch": branch, "worktree": str(worktree),
            "base_commit": self.run_git("rev-parse", "HEAD").stdout.strip(),
            "parallel_group": f"wave-{wave}", "wave": wave,
            "depends_on": depends_on, "write_set": write_set,
            "read_set": ["README.md"], "forbidden_paths": ["README.md"],
            "verification": "true", "work_kind": "test", "risk_flags": [],
        }

    def make_task(self, run, task="T01", artifact_kind=None, work_kind=None,
                  risk_flags=None, write_set=None, verification="test -f app.txt",
                  test_seams=None, diagnosis_command=None, failure_pattern=None,
                  base_commit=None, wave=1, parallel_group="wave-1",
                  finding_ids=None, experiment=None, required_decisions=None,
                  handoff=None, depends_on=None, reversibility=None):
        branch = f"agent/{run.name}/{task}"
        worktree = run / "worktrees" / task
        worktree.parent.mkdir(parents=True, exist_ok=True)
        start = base_commit or self.run_git("rev-parse", "HEAD").stdout.strip()
        self.run_git("worktree", "add", "-b", branch, str(worktree), start)
        record = {
            "id": task, "title": "change app", "status": "pending",
            "attempt": 1, "branch": branch, "worktree": str(worktree),
            "base_commit": start,
            "parallel_group": parallel_group,
            "wave": wave,
            "depends_on": depends_on or [],
            "write_set": write_set or ["app.txt"], "read_set": ["README.md"],
            "forbidden_paths": ["README.md"], "verification": verification,
            "verification_evidence": [],
            "work_kind": work_kind or ("learning" if artifact_kind else "test"),
            "risk_flags": risk_flags or [],
        }
        if reversibility is not None:
            record["reversibility"] = reversibility
        if test_seams is not None:
            record["test_seams"] = test_seams
            first = test_seams[0]["id"]
            record["scenario_coverage"] = [
                {
                    "dimension": dimension,
                    "applicability": "applicable" if dimension == "happy-path" else "not-applicable",
                    "scenario": "happy-path: caller observes the approved behavior" if dimension == "happy-path" else "",
                    "seam_ids": [first] if dimension == "happy-path" else [],
                    "rationale": ("primary behavior contract" if dimension == "happy-path"
                                  else "not material to this focused fixture"),
                }
                for dimension in (
                    "happy-path", "error-path", "boundary", "abuse-security", "scale",
                    "concurrency", "temporal", "data-variation", "permissions",
                    "integrations", "recovery", "state-transitions",
                )
            ]
        if diagnosis_command is not None:
            record["diagnosis_command"] = diagnosis_command
        if failure_pattern is not None:
            record["failure_pattern"] = failure_pattern
        if artifact_kind:
            record["artifact_kind"] = artifact_kind
        if finding_ids is not None:
            record["finding_ids"] = finding_ids
        if experiment is not None:
            record["experiment"] = experiment
        if required_decisions is not None:
            record["required_decisions"] = required_decisions
        if handoff is not None:
            record["handoff_required"] = True
            record["handoff"] = handoff
        staging = self.repo / f"{task}.json"
        staging.write_text(json.dumps(record), encoding="utf-8")
        result = self.run_tool(STATE, "--run", str(run), "task-put", "--file", str(staging))
        self.assertEqual(result.returncode, 0, result.stderr)
        staging.unlink()
        return worktree, record

    def start_wave(self, run):
        self.run_tool(STATE, "--run", str(run), "phase", "SPEC_READY", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "PLAN_READY", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "WAVE_RUNNING",
                      "--wave", "1", check=True)

    def complete_review(self, run, ledger, axis, reviewer, session_id,
                        result_payload=None, check=True, complete_env=None,
                        run_env_extra=None):
        fake_bin = Path(self.tmp.name) / "review-bin"
        fake_bin.mkdir(exist_ok=True)
        fake = fake_bin / "codex"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "args=sys.argv[1:]\n"
            "if '--version' in args:\n"
            "    print(os.environ.get('FAKE_CODEX_VERSION','codex-cli test-review'))\n"
            "    raise SystemExit(0)\n"
            "out=Path(args[args.index('--output-last-message')+1])\n"
            "payload=os.environ.get('FAKE_REVIEW_RESULT')\n"
            "if payload is None:\n"
            "    ids=json.loads(os.environ['QTEAM_REVIEW_CALIBRATION_CASE_IDS'])\n"
            "    payload=json.dumps({'axis':os.environ['QTEAM_REVIEW_AXIS'],"
            "'verdict':'pass','trajectory_verdict':'pass',"
            "'calibration_results':{ids[0]:'pass',ids[1]:'needs-fix'},"
            "'findings':[],"
            "'resolved_ids':json.loads(os.environ['QTEAM_REVIEW_RESOLVED_IDS']),"
            "'invalid_ids':json.loads(os.environ['QTEAM_REVIEW_INVALID_IDS']),"
            "'upheld_ids':json.loads(os.environ['QTEAM_REVIEW_UPHELD_IDS']),"
            "'invalid_evidence':json.loads(os.environ['QTEAM_REVIEW_INVALID_EVIDENCE'])})\n"
            "out.write_text(payload)\n"
            "print(json.dumps({'type':'thread.started','thread_id':'test-review'}))\n"
            "print(json.dumps({'type':'turn.started'}))\n"
            "for index in range(int(os.environ.get('FAKE_REVIEW_FAILED_CALLS','0'))):\n"
            " print(json.dumps({'type':'item.completed','item':{'type':'command_execution','command':'false '+str(index),'exit_code':1,'aggregated_output':'failed'}}))\n"
            "print(json.dumps({'type':'item.completed','item':{'id':'final','type':'agent_message','text':payload}}))\n"
            "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}))\n"
            "trace=os.environ.get('FAKE_REVIEW_TRACE')\n"
            "if trace: Path(trace).write_text(json.dumps({'args':args,'cwd':os.getcwd()}))\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        trace = Path(self.tmp.name) / f"review-{session_id}.trace.json"
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        packet_runner = json.loads(Path(ledger).read_text(encoding="utf-8"))[
            "packet"
        ]["runner"]["version"]
        env["FAKE_CODEX_VERSION"] = packet_runner
        env["FAKE_REVIEW_TRACE"] = str(trace)
        if run_env_extra:
            env.update(run_env_extra)
        if result_payload is not None:
            result_payload.setdefault("trajectory_verdict", "pass")
            ids = [f"cal-{axis}-01", f"cal-{axis}-02"]
            result_payload.setdefault(
                "calibration_results", {ids[0]: "pass", ids[1]: "needs-fix"}
            )
            env["FAKE_REVIEW_RESULT"] = json.dumps(result_payload)
        launched = self.run_tool(
            REVIEW, "run", "--ledger", str(ledger), "--reviewer", reviewer,
            "--session-id", session_id, env=env,
        )
        if launched.returncode:
            if check:
                self.fail(launched.stderr)
            return launched
        receipt = launched.stdout.strip()
        completed = self.run_tool(
            REVIEW, "complete", "--ledger", str(ledger),
            "--receipt", receipt, check=False, env=complete_env,
        )
        if check and completed.returncode:
            self.fail(completed.stderr)
        return completed

    def prepare_ready_run(self, run):
        integration = f"agent/{run.name}/integration"
        worktree = run / "worktrees/integration"
        worktree.parent.mkdir(parents=True, exist_ok=True)
        self.run_git("worktree", "add", "-b", integration, str(worktree), "HEAD")
        for phase in ("SPEC_READY", "PLAN_READY", "LEARNING_EXPORT"):
            self.run_tool(STATE, "--run", str(run), "phase", phase, check=True)
        for axis, source_flag in (("spec", "--spec-source"),
                                  ("standards", "--standards-source")):
            self.run_tool(
                REVIEW, "--run", str(run), "create", "--wave", "1", "--axis", axis,
                "--base", "HEAD", "--head", integration, source_flag, "README.md",
                check=True,
            )
            ledger = run / f"reviews/wave-1-{axis}.json"
            self.complete_review(run, ledger, axis, f"{axis}-reviewer",
                                 f"{axis}-session-1")
        self.run_tool(REVIEW, "--run", str(run), "check", "--wave", "1",
                      "--head", integration, check=True)
        self.run_tool(STATE, "--run", str(run), "verify-final", "--command", "true",
                      check=True)
        self.run_tool(STATE, "--run", str(run), "boundary-check", check=True)
        self.run_tool(STATE, "--run", str(run), "gate", "learning", "skipped",
                      "--evidence", "unit test", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "READY_TO_FINISH", check=True)
        return integration, worktree


class StateTests(RepoCase):
    def test_init_rejects_empty_model_before_creating_run(self):
        run = self.repo / ".agents/runs/empty-model"
        rejected = self.run_tool(
            STATE, "--run", str(run), "init", "--goal", "invalid model",
            "--model-economy", "",
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("model must be a non-empty string", rejected.stderr)
        self.assertNotIn("Traceback", rejected.stderr)
        self.assertFalse(run.exists())

    def test_state_wal_lock_and_events_reject_symlinks(self):
        run = self.init_run()
        outside_events = Path(self.tmp.name) / "outside-events.jsonl"
        outside_events.write_text("sentinel\n", encoding="utf-8")
        events = run / "events.jsonl"
        events.unlink()
        os.symlink(outside_events, events)
        decision = self.repo / "unsafe-event-decision.json"
        decision.write_text(json.dumps({
            "schema_version": 1, "id": "D-unsafe", "status": "open",
            "question": "Unsafe?", "authority": "user",
            "scope": {"kind": "action", "targets": ["finish"]},
        }), encoding="utf-8")
        rejected_event = self.run_tool(
            STATE, "--run", str(run), "decision-put", "--file", str(decision)
        )
        self.assertNotEqual(rejected_event.returncode, 0)
        self.assertIn("symlink", rejected_event.stderr)
        self.assertEqual(outside_events.read_text(encoding="utf-8"), "sentinel\n")
        self.assertNotIn(
            "D-unsafe",
            json.loads((run / "state.json").read_text(encoding="utf-8"))["decisions"],
        )

        events.unlink()
        events.write_text("[]\n", encoding="utf-8")
        rejected_value = self.run_tool(
            STATE, "--run", str(run), "decision-put", "--file", str(decision)
        )
        self.assertNotEqual(rejected_value.returncode, 0)
        self.assertNotIn("Traceback", rejected_value.stderr)
        self.assertNotIn(
            "D-unsafe",
            json.loads((run / "state.json").read_text(encoding="utf-8"))["decisions"],
        )

        events.write_text("", encoding="utf-8")
        lock = run / ".state.lock"
        lock.unlink()
        outside_lock = Path(self.tmp.name) / "outside-lock"
        outside_lock.write_text("", encoding="utf-8")
        os.symlink(outside_lock, lock)
        rejected_lock = self.run_tool(STATE, "--run", str(run), "status")
        self.assertNotEqual(rejected_lock.returncode, 0)
        self.assertIn("symlink", rejected_lock.stderr)

    def test_dependency_graph_requires_registered_strictly_earlier_predecessors(self):
        run = self.init_run()
        self.make_task(run, task="T01", wave=1)
        self.make_task(
            run, task="T02", wave=2, write_set=["next.txt"], depends_on=["T01"]
        )
        unknown = self.make_task_record(
            run, task="T03", wave=3, write_set=["third.txt"],
            depends_on=["MISSING"],
        )
        source = self.repo / "unknown-dependency.json"
        source.write_text(json.dumps(unknown), encoding="utf-8")
        rejected = self.run_tool(
            STATE, "--run", str(run), "task-put", "--file", str(source)
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("unknown dependency MISSING", rejected.stderr)

        backward = dict(unknown)
        backward["wave"] = 1
        backward["parallel_group"] = "wave-1"
        backward["depends_on"] = ["T02"]
        source.write_text(json.dumps(backward), encoding="utf-8")
        rejected = self.run_tool(
            STATE, "--run", str(run), "task-put", "--file", str(source)
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("must be in a strictly earlier wave", rejected.stderr)

    def test_dependency_ids_fail_cleanly_for_unhashable_json_values(self):
        run = self.init_run()
        for index, malformed in enumerate(([{}], [[]]), start=1):
            task_id = f"T0{index}"
            record = self.make_task_record(
                run, task=task_id, wave=1,
                write_set=[f"malformed-{index}.txt"], depends_on=malformed,
            )
            source = self.repo / f"malformed-dependency-{index}.json"
            source.write_text(json.dumps(record), encoding="utf-8")
            rejected = self.run_tool(
                STATE, "--run", str(run), "task-put", "--file", str(source)
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("depends_on must be an array of unique safe task ids",
                          rejected.stderr)
            self.assertNotIn("Traceback", rejected.stderr)

    def test_dependency_gate_blocks_wave_and_rechecks_task_start(self):
        run = self.init_run()
        self.make_task(run, task="T01", wave=1)
        self.make_task(
            run, task="T02", wave=2, write_set=["next.txt"], depends_on=["T01"]
        )
        self.run_tool(STATE, "--run", str(run), "phase", "SPEC_READY", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "PLAN_READY", check=True)
        blocked_wave = self.run_tool(
            STATE, "--run", str(run), "phase", "WAVE_RUNNING", "--wave", "2"
        )
        self.assertNotEqual(blocked_wave.returncode, 0)
        self.assertIn("T02 blocked by dependencies: T01=pending", blocked_wave.stderr)

        dependency_path = run / "tasks/T01.json"
        dependency = json.loads(dependency_path.read_text(encoding="utf-8"))
        dependency["status"] = "merged"
        dependency_path.write_text(json.dumps(dependency), encoding="utf-8")
        state_path = run / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["tasks"]["T01"]["status"] = "merged"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        self.run_tool(
            STATE, "--run", str(run), "phase", "WAVE_RUNNING", "--wave", "2",
            check=True,
        )

        dependency["status"] = "failed"
        dependency_path.write_text(json.dumps(dependency), encoding="utf-8")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["tasks"]["T01"]["status"] = "failed"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        blocked_task = self.run_tool(
            STATE, "--run", str(run), "task-status", "T02", "running"
        )
        self.assertNotEqual(blocked_task.returncode, 0)
        self.assertIn("T02 blocked by dependencies: T01=failed", blocked_task.stderr)

        dependency["status"] = "artifact_complete"
        dependency_path.write_text(json.dumps(dependency), encoding="utf-8")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["tasks"]["T01"]["status"] = "artifact_complete"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        self.run_tool(
            STATE, "--run", str(run), "task-status", "T02", "running",
            check=True,
        )

    def test_dependency_gate_rejects_task_state_status_projection_tampering(self):
        run = self.init_run()
        self.make_task(run, task="T01", wave=1)
        self.make_task(
            run, task="T02", wave=2, write_set=["next.txt"], depends_on=["T01"]
        )
        self.run_tool(STATE, "--run", str(run), "phase", "SPEC_READY", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "PLAN_READY", check=True)

        dependency_path = run / "tasks/T01.json"
        dependency = json.loads(dependency_path.read_text(encoding="utf-8"))
        dependency["status"] = "merged"
        dependency_path.write_text(json.dumps(dependency), encoding="utf-8")
        rejected = self.run_tool(
            STATE, "--run", str(run), "phase", "WAVE_RUNNING", "--wave", "2"
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("task T01 status mismatch", rejected.stderr)

    def test_status_reports_dependency_ready_tasks_and_exact_blockers(self):
        run = self.init_run()
        self.make_task(run, task="T01", wave=1)
        self.make_task(
            run, task="T02", wave=2, write_set=["next.txt"], depends_on=["T01"]
        )
        packet = json.loads(self.run_tool(
            STATE, "--run", str(run), "status", check=True
        ).stdout)
        self.assertEqual(packet["ready_tasks"], ["T01"])
        self.assertEqual(packet["dependency_blockers"], {
            "T02": [{"task": "T01", "status": "pending"}],
        })

    def test_running_wave_rejects_future_wave_task_and_status_does_not_suggest_it(self):
        run = self.init_run()
        self.make_task(run, task="T01", wave=1)
        self.make_task(run, task="T02", wave=2, write_set=["next.txt"])
        self.run_tool(STATE, "--run", str(run), "phase", "SPEC_READY", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "PLAN_READY", check=True)
        self.run_tool(
            STATE, "--run", str(run), "phase", "WAVE_RUNNING", "--wave", "1",
            check=True,
        )

        rejected = self.run_tool(
            STATE, "--run", str(run), "task-status", "T02", "running"
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("T02 belongs to wave 2, not current wave 1", rejected.stderr)

        task_path = run / "tasks/T01.json"
        task = json.loads(task_path.read_text(encoding="utf-8"))
        task["status"] = "merged"
        task_path.write_text(json.dumps(task), encoding="utf-8")
        state_path = run / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["tasks"]["T01"]["status"] = "merged"
        state_path.write_text(json.dumps(state), encoding="utf-8")

        packet = json.loads(self.run_tool(
            STATE, "--run", str(run), "status", check=True
        ).stdout)
        self.assertEqual(packet["ready_tasks"], ["T02"])
        self.assertNotEqual(packet["next_action"], "start pending task T02")

    def test_dependency_and_wave_replacement_requires_replanning(self):
        run = self.init_run()
        self.make_task(run, task="T01", wave=1)
        self.make_task(
            run, task="T02", wave=2, write_set=["next.txt"], depends_on=["T01"]
        )
        self.run_tool(STATE, "--run", str(run), "phase", "SPEC_READY", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "PLAN_READY", check=True)
        task_path = run / "tasks/T02.json"
        original = json.loads(task_path.read_text(encoding="utf-8"))
        source = self.repo / "replace-dependencies.json"

        changed_dependencies = dict(original)
        changed_dependencies["depends_on"] = []
        source.write_text(json.dumps(changed_dependencies), encoding="utf-8")
        rejected = self.run_tool(
            STATE, "--run", str(run), "task-put", "--replace", "--file",
            str(source),
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("task dependencies are immutable outside REPLANNING",
                      rejected.stderr)

        changed_wave = dict(original)
        changed_wave.update({"wave": 3, "parallel_group": "wave-3"})
        source.write_text(json.dumps(changed_wave), encoding="utf-8")
        rejected = self.run_tool(
            STATE, "--run", str(run), "task-put", "--replace", "--file",
            str(source),
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("task wave is immutable outside REPLANNING", rejected.stderr)

        self.run_tool(
            STATE, "--run", str(run), "phase", "REPLANNING",
            "--reason", "dependency contract changed", check=True,
        )
        source.write_text(json.dumps(changed_dependencies), encoding="utf-8")
        self.run_tool(
            STATE, "--run", str(run), "task-put", "--replace", "--file",
            str(source), check=True,
        )

    def test_generic_task_status_cannot_forge_artifact_completion(self):
        run = self.init_run()
        self.make_task(run, task="T01", wave=1)
        self.start_wave(run)
        self.run_tool(
            STATE, "--run", str(run), "task-status", "T01", "running",
            check=True,
        )
        rejected = self.run_tool(
            STATE, "--run", str(run), "task-status", "T01",
            "artifact_complete",
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("artifact-complete command", rejected.stderr)
        task = json.loads((run / "tasks/T01.json").read_text(encoding="utf-8"))
        state = json.loads((run / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(task["status"], "running")
        self.assertEqual(state["tasks"]["T01"]["status"], "running")

    def test_phase_machine_and_atomic_finish(self):
        run = self.init_run()
        bad = self.run_tool(STATE, "--run", str(run), "phase", "WAVE_RUNNING")
        self.assertNotEqual(bad.returncode, 0)
        self.prepare_ready_run(run)
        status = self.run_tool(STATE, "--run", str(run), "status", check=True)
        packet = json.loads(status.stdout)
        self.assertEqual(packet["next_action"], "finish the reviewed run")
        self.assertTrue(all(
            gate["fresh"] for gate in packet["evidence_freshness"].values()
        ))
        result = self.run_tool(STATE, "--run", str(run), "finish")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads((run / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["phase"], "DONE")
        self.assertTrue(state["finished"])
        events = (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertGreaterEqual(len(events), 6)

    def test_task_transition_is_written_to_state_and_record(self):
        run = self.init_run()
        self.make_task(run)
        self.start_wave(run)
        result = self.run_tool(STATE, "--run", str(run), "task-status", "T01", "running")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads((run / "state.json").read_text(encoding="utf-8"))
        task = json.loads((run / "tasks/T01.json").read_text(encoding="utf-8"))
        self.assertEqual(state["tasks"]["T01"]["status"], "running")
        self.assertEqual(task["status"], "running")

    def test_scoped_decision_blocks_only_covered_task_and_status_asks_exact_question(self):
        run = self.init_run()
        gate = {
            "schema_version": 1, "id": "D01", "status": "open",
            "question": "Use the compatibility encoding?", "authority": "user",
            "scope": {"kind": "task", "targets": ["T01"]},
        }
        source = self.repo / "decision.json"
        source.write_text(json.dumps(gate), encoding="utf-8")
        opened = self.run_tool(
            STATE, "--run", str(run), "decision-put", "--file", str(source)
        )
        self.assertEqual(opened.returncode, 0, opened.stderr)
        self.make_task(run, task="T01", required_decisions=["D01"])
        self.make_task(run, task="T02", write_set=["other.txt"])
        self.start_wave(run)

        allowed = self.run_tool(
            STATE, "--run", str(run), "task-status", "T02", "running"
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        blocked = self.run_tool(
            STATE, "--run", str(run), "task-status", "T01", "running"
        )
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("D01: Use the compatibility encoding?", blocked.stderr)

        status = self.run_tool(STATE, "--run", str(run), "status")
        self.assertEqual(status.returncode, 0, status.stderr)
        packet = json.loads(status.stdout)
        self.assertEqual(packet["open_decisions"][0]["question"], gate["question"])
        self.assertIn("answer decision D01", packet["next_action"])

        resolved = self.run_tool(
            STATE, "--run", str(run), "decision-resolve", "D01",
            "--outcome", "allow", "--choice", "yes",
            "--evidence", "user approved in session",
        )
        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        started = self.run_tool(
            STATE, "--run", str(run), "task-status", "T01", "running"
        )
        self.assertEqual(started.returncode, 0, started.stderr)

    def test_typed_handoff_rejects_ambiguous_continuation(self):
        run = self.init_run()
        branch = "agent/test-run/T01"
        worktree = run / "worktrees/T01"
        worktree.parent.mkdir(parents=True, exist_ok=True)
        self.run_git("worktree", "add", "-b", branch, str(worktree), "HEAD")
        record = {
            "id": "T01", "status": "pending", "branch": branch,
            "worktree": str(worktree),
            "base_commit": self.run_git("rev-parse", "HEAD").stdout.strip(),
            "parallel_group": "wave-1", "wave": 1, "depends_on": [],
            "write_set": ["app.txt"],
            "forbidden_paths": [], "verification": "true", "work_kind": "test",
            "risk_flags": [], "handoff_required": True,
            "handoff": {"kind": "successor", "rationale": "consumer must continue"},
        }
        source = self.repo / "invalid-handoff.json"
        source.write_text(json.dumps(record), encoding="utf-8")
        rejected = self.run_tool(
            STATE, "--run", str(run), "task-put", "--file", str(source)
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("successor handoff needs target_task", rejected.stderr)
        record["handoff"] = {"kind": "no-followup", "rationale": "   "}
        source.write_text(json.dumps(record), encoding="utf-8")
        blank = self.run_tool(
            STATE, "--run", str(run), "task-put", "--file", str(source)
        )
        self.assertNotEqual(blank.returncode, 0)
        self.assertIn("needs kind and rationale", blank.stderr)

    def test_post_task_decision_rejects_self_deadlocking_scope(self):
        run = self.init_run()
        gate = {
            "schema_version": 1, "id": "D-dead", "status": "open",
            "question": "Can T01 start?", "authority": "user",
            "scope": {"kind": "task", "targets": ["T01"]},
        }
        source = self.repo / "dead-decision.json"
        source.write_text(json.dumps(gate), encoding="utf-8")
        self.run_tool(STATE, "--run", str(run), "decision-put", "--file",
                      str(source), check=True)
        branch = "agent/test-run/T01"
        worktree = run / "worktrees/T01"
        worktree.parent.mkdir(parents=True, exist_ok=True)
        self.run_git("worktree", "add", "-b", branch, str(worktree), "HEAD")
        record = {
            "id": "T01", "status": "pending", "branch": branch,
            "worktree": str(worktree),
            "base_commit": self.run_git("rev-parse", "HEAD").stdout.strip(),
            "parallel_group": "wave-1", "wave": 1, "depends_on": [],
            "write_set": ["app.txt"],
            "forbidden_paths": [], "verification": "true", "work_kind": "test",
            "risk_flags": [], "handoff_required": True,
            "handoff": {"kind": "user-decision", "decision_id": "D-dead",
                        "rationale": "consume the result"},
        }
        source.write_text(json.dumps(record), encoding="utf-8")
        rejected = self.run_tool(
            STATE, "--run", str(run), "task-put", "--file", str(source)
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("blocks its source task start", rejected.stderr)

    def test_post_task_decision_cannot_also_be_required_before_task(self):
        run = self.init_run()
        gate_file = self.repo / "overlap-decision.json"
        gate_file.write_text(json.dumps({
            "schema_version": 1, "id": "D-overlap", "status": "open",
            "question": "Finish after consuming T01?", "authority": "user",
            "scope": {"kind": "action", "targets": ["finish"]},
        }), encoding="utf-8")
        self.run_tool(STATE, "--run", str(run), "decision-put", "--file",
                      str(gate_file), check=True)
        branch = "agent/test-run/T01"
        worktree = run / "worktrees/T01"
        worktree.parent.mkdir(parents=True, exist_ok=True)
        self.run_git("worktree", "add", "-b", branch, str(worktree), "HEAD")
        record = {
            "id": "T01", "status": "pending", "branch": branch,
            "worktree": str(worktree),
            "base_commit": self.run_git("rev-parse", "HEAD").stdout.strip(),
            "parallel_group": "wave-1", "wave": 1, "depends_on": [],
            "write_set": ["app.txt"],
            "forbidden_paths": [], "verification": "true", "work_kind": "test",
            "risk_flags": [], "required_decisions": ["D-overlap"],
            "handoff_required": True,
            "handoff": {"kind": "user-decision", "decision_id": "D-overlap",
                        "rationale": "consume the completed result"},
        }
        gate_file.write_text(json.dumps(record), encoding="utf-8")
        rejected = self.run_tool(
            STATE, "--run", str(run), "task-put", "--file", str(gate_file)
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("cannot also be a required_decision", rejected.stderr)

    def test_successor_handoff_rejects_target_in_same_or_earlier_wave(self):
        run = self.init_run()
        self.make_task(run, task="T02", wave=1, write_set=["next.txt"])
        branch = "agent/test-run/T01"
        worktree = run / "worktrees/T01"
        worktree.parent.mkdir(parents=True, exist_ok=True)
        self.run_git("worktree", "add", "-b", branch, str(worktree), "HEAD")
        record = {
            "id": "T01", "status": "pending", "branch": branch,
            "worktree": str(worktree),
            "base_commit": self.run_git("rev-parse", "HEAD").stdout.strip(),
            "parallel_group": "wave-2", "wave": 2, "depends_on": [],
            "write_set": ["app.txt"],
            "forbidden_paths": [], "verification": "true", "work_kind": "test",
            "risk_flags": [], "handoff_required": True,
            "handoff": {"kind": "successor", "target_task": "T02",
                        "rationale": "T02 consumes T01 output"},
        }
        source = self.repo / "backward-successor.json"
        source.write_text(json.dumps(record), encoding="utf-8")
        rejected = self.run_tool(
            STATE, "--run", str(run), "task-put", "--file", str(source)
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("must be in a later wave", rejected.stderr)

    def test_tdd_task_requires_complete_scenario_matrix(self):
        run = self.init_run()
        branch = "agent/test-run/T01"
        worktree = run / "worktrees/T01"
        worktree.parent.mkdir(parents=True, exist_ok=True)
        self.run_git("worktree", "add", "-b", branch, str(worktree), "HEAD")
        record = {
            "id": "T01", "status": "pending", "branch": branch,
            "worktree": str(worktree),
            "base_commit": self.run_git("rev-parse", "HEAD").stdout.strip(),
            "parallel_group": "wave-1", "wave": 1, "depends_on": [],
            "write_set": ["app.txt", "tests/check.txt"], "forbidden_paths": [],
            "verification": "true", "work_kind": "feature", "risk_flags": [],
            "test_seams": [{
                "id": "public-behavior", "behavior": "caller sees result",
                "test_paths": ["tests/check.txt"], "command": "true",
                "red_pattern": "EXPECTED_RED",
            }],
        }
        source = self.repo / "missing-scenarios.json"
        source.write_text(json.dumps(record), encoding="utf-8")
        rejected = self.run_tool(
            STATE, "--run", str(run), "task-put", "--file", str(source)
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("all 12 scenario dimensions", rejected.stderr)
        record["scenario_coverage"] = [
            {
                "dimension": dimension,
                "applicability": "applicable" if dimension in {
                    "happy-path", "error-path",
                } else "not-applicable",
                "scenario": "duplicate scenario" if dimension in {
                    "happy-path", "error-path",
                } else "",
                "seam_ids": ["public-behavior"] if dimension in {
                    "happy-path", "error-path",
                } else [],
                "rationale": "focused fixture",
            }
            for dimension in (
                "happy-path", "error-path", "boundary", "abuse-security", "scale",
                "concurrency", "temporal", "data-variation", "permissions",
                "integrations", "recovery", "state-transitions",
            )
        ]
        source.write_text(json.dumps(record), encoding="utf-8")
        rejected_prefix = self.run_tool(
            STATE, "--run", str(run), "task-put", "--file", str(source)
        )
        self.assertNotEqual(rejected_prefix.returncode, 0)
        self.assertIn("must start with '<dimension>: '", rejected_prefix.stderr)
        for row in record["scenario_coverage"]:
            row["applicability"] = "not-applicable"
            row["scenario"] = ""
            row["seam_ids"] = []
        record["scenario_coverage"][0].update({
            "applicability": "applicable",
            "scenario": "happy-path: caller sees result",
            "seam_ids": ["public-behavior"],
        })
        record["scenario_coverage"][1]["rationale"] = "   "
        source.write_text(json.dumps(record), encoding="utf-8")
        blank_rationale = self.run_tool(
            STATE, "--run", str(run), "task-put", "--file", str(source)
        )
        self.assertNotEqual(blank_rationale.returncode, 0)
        self.assertIn("malformed scenario_coverage row", blank_rationale.stderr)

    def test_post_task_user_decision_handoff_does_not_block_task_start(self):
        run = self.init_run()
        gate = {
            "schema_version": 1, "id": "D-after", "status": "open",
            "question": "Publish the generated compatibility report?",
            "authority": "user", "scope": {"kind": "action", "targets": ["finish"]},
        }
        source = self.repo / "post-task-decision.json"
        source.write_text(json.dumps(gate), encoding="utf-8")
        self.run_tool(
            STATE, "--run", str(run), "decision-put", "--file", str(source),
            check=True,
        )
        self.make_task(
            run, handoff={
                "kind": "user-decision", "decision_id": "D-after",
                "rationale": "the completed report is the decision input",
            },
        )
        self.start_wave(run)
        status = self.run_tool(STATE, "--run", str(run), "status")
        packet = json.loads(status.stdout)
        self.assertEqual(packet["blocking_decisions"], [])
        self.assertEqual(packet["next_action"], "start pending task T01")
        started = self.run_tool(
            STATE, "--run", str(run), "task-status", "T01", "running"
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        premature = self.run_tool(
            STATE, "--run", str(run), "decision-resolve", "D-after",
            "--outcome", "allow", "--choice", "publish",
            "--evidence", "asked before report exists",
        )
        self.assertNotEqual(premature.returncode, 0)
        self.assertIn("requires completed source tasks: T01", premature.stderr)
        worktree = run / "worktrees/T01"
        (worktree / "app.txt").write_text("report ready\n", encoding="utf-8")
        self.run_git("add", "app.txt", cwd=worktree)
        self.run_git("commit", "-m", "complete report", cwd=worktree)
        self.run_tool(STATE, "--run", str(run), "verify-task", "T01", check=True)
        self.run_tool(
            STATE, "--run", str(run), "phase", "WAVE_VALIDATING", check=True
        )
        self.run_tool(CHECK, "--run", str(run), "--task", "T01", check=True)
        resolved = self.run_tool(
            STATE, "--run", str(run), "decision-resolve", "D-after",
            "--outcome", "allow", "--choice", "publish",
            "--evidence", "reviewed completed report",
        )
        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        status = self.run_tool(STATE, "--run", str(run), "status", check=True)
        packet = json.loads(status.stdout)
        self.assertEqual(packet["tasks"]["ready_to_merge"], ["T01"])
        self.assertEqual(packet["blocking_handoffs"], [])
        self.assertEqual(packet["next_action"], "advance to merge completed task T01")

    def test_boundary_gate_rejects_machine_specific_public_delta(self):
        run = self.init_run()
        integration = "agent/test-run/integration"
        worktree = run / "worktrees/integration"
        worktree.parent.mkdir(parents=True, exist_ok=True)
        self.run_git("worktree", "add", "-b", integration, str(worktree), "HEAD")
        for phase in ("SPEC_READY", "PLAN_READY", "LEARNING_EXPORT"):
            self.run_tool(STATE, "--run", str(run), "phase", phase, check=True)
        (worktree / "leak.md").write_text(
            "read /home/alice/private/session.log\n", encoding="utf-8"
        )
        (worktree / "windows-leak.md").write_text(
            "read C:\\Users\\alice\\private\\session.log\n", encoding="utf-8"
        )
        (worktree / "lower-windows-leak.md").write_text(
            "read c:\\users\\alice\\private\\session.log\n", encoding="utf-8"
        )
        (worktree / "root-leak.md").write_text(
            "read /root/private/session.log\n", encoding="utf-8"
        )
        (worktree / "secret.env").write_text(
            'OPENAI_API_KEY="sk-proj-abcdefghijklmnopqrstuvwxyz"\n'
            'PASSWORD=unquoted-production-secret\n', encoding="utf-8"
        )
        (worktree / "opaque.bin").write_bytes(b"\x00\x01not-scannable")
        runtime_copy = worktree / ".agents/runs/copied/state.json"
        runtime_copy.parent.mkdir(parents=True)
        runtime_copy.write_text("{}\n", encoding="utf-8")
        self.run_git(
            "add", "-f", "leak.md", "windows-leak.md", "lower-windows-leak.md",
            "root-leak.md", "secret.env", "opaque.bin",
            ".agents/runs/copied/state.json", cwd=worktree,
        )
        self.run_git("commit", "-m", "add leaked path", cwd=worktree)
        checked = self.run_tool(STATE, "--run", str(run), "boundary-check")
        self.assertNotEqual(checked.returncode, 0)
        report = json.loads(checked.stdout)
        self.assertEqual(report["status"], "failed")
        self.assertIn(
            {"kind": "machine-local-path", "path": "leak.md"}, report["findings"]
        )
        self.assertIn(
            {"kind": "machine-local-path", "path": "windows-leak.md"},
            report["findings"],
        )
        self.assertIn(
            {"kind": "machine-local-path", "path": "lower-windows-leak.md"},
            report["findings"],
        )
        self.assertIn(
            {"kind": "machine-local-path", "path": "root-leak.md"},
            report["findings"],
        )
        self.assertIn(
            {"kind": "openai-key", "path": "secret.env"}, report["findings"]
        )
        self.assertIn(
            {"kind": "secret-assignment", "path": "secret.env"}, report["findings"]
        )
        self.assertIn(
            {"kind": "unscannable-binary", "path": "opaque.bin"},
            report["findings"],
        )
        self.assertIn(
            {"kind": "private-runtime-path",
             "path": ".agents/runs/copied/state.json"},
            report["findings"],
        )

    def test_external_publish_action_checks_its_exact_decision_scope(self):
        run = self.init_run()
        gate = {
            "schema_version": 1, "id": "D-publish", "status": "open",
            "question": "Push the reviewed result?", "authority": "user",
            "scope": {"kind": "action", "targets": ["publish"]},
        }
        source = self.repo / "publish-decision.json"
        source.write_text(json.dumps(gate), encoding="utf-8")
        self.run_tool(
            STATE, "--run", str(run), "decision-put", "--file", str(source),
            check=True,
        )
        blocked = self.run_tool(
            STATE, "--run", str(run), "decision-check", "--action", "publish"
        )
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("D-publish: Push the reviewed result?", blocked.stderr)
        unrelated = self.run_tool(
            STATE, "--run", str(run), "decision-check", "--action", "merge"
        )
        self.assertEqual(unrelated.returncode, 0, unrelated.stderr)
        self.run_tool(
            STATE, "--run", str(run), "decision-resolve", "D-publish",
            "--outcome", "allow", "--choice", "push",
            "--evidence", "explicit user approval", check=True,
        )
        clear = self.run_tool(
            STATE, "--run", str(run), "decision-check", "--action", "publish"
        )
        self.assertEqual(clear.returncode, 0, clear.stderr)
        self.assertEqual(clear.stdout.strip(), "clear")

    def test_resolved_deny_continues_to_block_covered_action(self):
        run = self.init_run()
        source = self.repo / "deny-decision.json"
        source.write_text(json.dumps({
            "schema_version": 1, "id": "D-deny", "status": "open",
            "question": "Push the reviewed result?", "authority": "user",
            "scope": {"kind": "action", "targets": ["publish"]},
        }), encoding="utf-8")
        self.run_tool(STATE, "--run", str(run), "decision-put", "--file",
                      str(source), check=True)
        self.run_tool(
            STATE, "--run", str(run), "decision-resolve", "D-deny",
            "--outcome", "deny", "--choice", "do not push",
            "--evidence", "explicit user refusal", check=True,
        )
        blocked = self.run_tool(
            STATE, "--run", str(run), "decision-check", "--action", "publish"
        )
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("D-deny: Push the reviewed result? [resolved]", blocked.stderr)

    def test_boundary_gate_ignores_deleted_private_runtime_path(self):
        private = self.repo / ".agents/runs/legacy/state.json"
        private.parent.mkdir(parents=True)
        private.write_text("{}\n", encoding="utf-8")
        self.run_git("add", "-f", ".agents/runs/legacy/state.json")
        self.run_git("commit", "-m", "legacy private runtime")
        run = self.init_run()
        integration = "agent/test-run/integration"
        worktree = run / "worktrees/integration"
        worktree.parent.mkdir(parents=True, exist_ok=True)
        self.run_git("worktree", "add", "-b", integration, str(worktree), "HEAD")
        for phase in ("SPEC_READY", "PLAN_READY", "LEARNING_EXPORT"):
            self.run_tool(STATE, "--run", str(run), "phase", phase, check=True)
        (worktree / ".agents/runs/legacy/state.json").unlink()
        self.run_git("add", "-u", cwd=worktree)
        self.run_git("commit", "-m", "remove legacy private runtime", cwd=worktree)
        checked = self.run_tool(STATE, "--run", str(run), "boundary-check")
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_replan_handoff_requires_explicit_audited_closure(self):
        run = self.init_run()
        worktree, _ = self.make_task(run, handoff={
            "kind": "replan", "rationale": "contract cannot be satisfied",
        })
        self.start_wave(run)
        self.run_tool(STATE, "--run", str(run), "task-status", "T01", "running",
                      check=True)
        (worktree / "app.txt").write_text("discovery\n", encoding="utf-8")
        self.run_git("add", "app.txt", cwd=worktree)
        self.run_git("commit", "-m", "record discovery", cwd=worktree)
        self.run_tool(STATE, "--run", str(run), "verify-task", "T01", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "WAVE_VALIDATING",
                      check=True)
        self.run_tool(CHECK, "--run", str(run), "--task", "T01", check=True)
        status = json.loads(self.run_tool(
            STATE, "--run", str(run), "status", check=True
        ).stdout)
        self.assertEqual(
            status["next_action"], "enter REPLANNING to close replan handoff"
        )
        rejected = self.run_tool(
            STATE, "--run", str(run), "phase", "WAVE_MERGING"
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("replan handoffs require REPLANNING", rejected.stderr)
        self.run_tool(STATE, "--run", str(run), "phase", "REPLANNING",
                      "--reason", "contract discovery", check=True)
        closed = self.run_tool(
            STATE, "--run", str(run), "handoff-close", "T01",
            "--reason", "discard incompatible implementation",
        )
        self.assertEqual(closed.returncode, 0, closed.stderr)
        task = json.loads((run / "tasks/T01.json").read_text(encoding="utf-8"))
        self.assertEqual(task["status"], "superseded")
        self.assertEqual(task["handoff_resolution"]["kind"], "replan")

    def test_superseded_successor_does_not_falsely_close_delivery(self):
        run = self.init_run()
        self.make_task(run, task="T02", wave=2, write_set=["next.txt"])
        worktree, _ = self.make_task(run, task="T01", handoff={
            "kind": "successor", "target_task": "T02",
            "rationale": "T02 must deliver the consumer",
        })
        self.start_wave(run)
        self.run_tool(STATE, "--run", str(run), "task-status", "T01", "running",
                      check=True)
        (worktree / "app.txt").write_text("producer\n", encoding="utf-8")
        self.run_git("add", "app.txt", cwd=worktree)
        self.run_git("commit", "-m", "producer", cwd=worktree)
        self.run_tool(STATE, "--run", str(run), "verify-task", "T01", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "WAVE_VALIDATING",
                      check=True)
        self.run_tool(CHECK, "--run", str(run), "--task", "T01", check=True)
        self.run_tool(STATE, "--run", str(run), "task-status", "T02",
                      "superseded", check=True)
        status = json.loads(self.run_tool(
            STATE, "--run", str(run), "status", check=True
        ).stdout)
        self.assertEqual(status["blocking_handoffs"][0]["kind"], "successor")
        self.assertEqual(status["blocking_handoffs"][0]["target_status"], "superseded")
        self.assertEqual(status["next_action"], "advance to merge completed task T01")

    def test_superseding_undelivered_source_supersedes_post_task_decision(self):
        run = self.init_run()
        gate_file = self.repo / "source-decision.json"
        gate_file.write_text(json.dumps({
            "schema_version": 1, "id": "D-source", "status": "open",
            "question": "Use the source result?", "authority": "user",
            "scope": {"kind": "action", "targets": ["finish"]},
        }), encoding="utf-8")
        self.run_tool(STATE, "--run", str(run), "decision-put", "--file",
                      str(gate_file), check=True)
        self.make_task(run, handoff={
            "kind": "user-decision", "decision_id": "D-source",
            "rationale": "consume delivered output",
        })
        self.run_tool(STATE, "--run", str(run), "task-status", "T01",
                      "superseded", check=True)
        decision = json.loads((run / "decisions/D-source.json").read_text(
            encoding="utf-8"
        ))
        self.assertEqual(decision["status"], "superseded")
        self.assertIn("superseded before delivery", decision["superseded_reason"])

    def test_status_never_recommends_task_start_before_wave_phase(self):
        run = self.init_run()
        self.make_task(run)
        status = self.run_tool(STATE, "--run", str(run), "status", check=True)
        packet = json.loads(status.stdout)
        self.assertEqual(packet["next_action"], "advance phase from INIT")

    def test_status_surfaces_wave_start_blocker_before_task_start(self):
        run = self.init_run()
        decision_file = self.repo / "wave-decision.json"
        decision_file.write_text(json.dumps({
            "schema_version": 1, "id": "D-wave", "status": "open",
            "question": "Start wave one?", "authority": "user",
            "scope": {"kind": "action", "targets": ["wave-start"]},
        }), encoding="utf-8")
        self.run_tool(STATE, "--run", str(run), "decision-put", "--file",
                      str(decision_file), check=True)
        self.make_task(run)
        self.run_tool(STATE, "--run", str(run), "phase", "SPEC_READY", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "PLAN_READY", check=True)
        packet = json.loads(self.run_tool(
            STATE, "--run", str(run), "status", check=True
        ).stdout)
        self.assertEqual(packet["blocking_decisions"][0]["id"], "D-wave")
        self.assertIn("answer decision D-wave", packet["next_action"])

    def test_status_does_not_treat_fresh_failed_gate_as_passed(self):
        run = self.init_run()
        self.run_git("branch", "agent/test-run/integration", "HEAD")
        head = self.run_git("rev-parse", "HEAD").stdout.strip()
        state_path = run / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["phase"] = "LEARNING_EXPORT"
        state["gates"] = {
            "reviews": {"status": "passed", "head_sha": head},
            "final_verification": {"status": "failed", "head_sha": head},
            "learning": {"status": "skipped"},
            "public_boundary": {"status": "passed", "head_sha": head},
        }
        state_path.write_text(json.dumps(state), encoding="utf-8")
        packet = json.loads(self.run_tool(
            STATE, "--run", str(run), "status", check=True
        ).stdout)
        self.assertEqual(packet["next_action"], "run final integration verification")

    def test_status_in_validation_recommends_mechanical_task_check(self):
        run = self.init_run()
        self.make_task(run)
        self.start_wave(run)
        self.run_tool(STATE, "--run", str(run), "task-status", "T01", "running",
                      check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "WAVE_VALIDATING",
                      check=True)
        packet = json.loads(self.run_tool(
            STATE, "--run", str(run), "status", check=True
        ).stdout)
        self.assertEqual(packet["next_action"], "run mechanical task check for T01")

    def test_v2_migration_is_atomic_and_requires_each_active_task_to_be_replanned(self):
        run = self.init_run()
        self.make_task(run, task="T01", work_kind="test")
        self.make_task(run, task="T02", work_kind="test", wave=2)
        for task_id in ("T01", "T02"):
            path = run / f"tasks/{task_id}.json"
            task = json.loads(path.read_text(encoding="utf-8"))
            for field in ("work_kind", "risk_flags", "policy", "tdd_required",
                          "diagnosis_required"):
                task.pop(field, None)
            path.write_text(json.dumps(task), encoding="utf-8")
        state_path = run / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["schema_version"] = 2
        for field in ("risk_forced", "model_profiles", "waves",
                      "policy_migration_pending", "integration_provenance_head",
                      "integration_provenance"):
            state.pop(field, None)
        state_path.write_text(json.dumps(state), encoding="utf-8")

        migrated = self.run_tool(STATE, "--run", str(run), "migrate-v2")
        self.assertEqual(migrated.returncode, 0, migrated.stderr)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["schema_version"], 6)
        self.assertEqual(state["policy_migration_pending"], ["T01", "T02"])
        for task_id in ("T01", "T02"):
            task = json.loads((run / f"tasks/{task_id}.json").read_text(
                encoding="utf-8"))
            self.assertEqual(task["policy"]["execution_tier"], "standard")
            self.assertTrue(task["policy_migration"]["requires_replan"])

        blocked = self.run_tool(STATE, "--run", str(run), "phase", "SPEC_READY")
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("require REPLANNING", blocked.stderr)
        task = json.loads((run / "tasks/T01.json").read_text(encoding="utf-8"))
        for field in ("policy", "policy_migration", "tdd_required",
                      "diagnosis_required"):
            task.pop(field, None)
        task["work_kind"] = "test"
        task["risk_flags"] = []
        bypass = self.repo / "bypass-replan.json"
        bypass.write_text(json.dumps(task), encoding="utf-8")
        rejected = self.run_tool(
            STATE, "--run", str(run), "task-put", "--replace", "--file",
            str(bypass),
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("requires REPLANNING phase", rejected.stderr)
        self.run_tool(STATE, "--run", str(run), "phase", "REPLANNING", check=True)

        for task_id in ("T01", "T02"):
            path = run / f"tasks/{task_id}.json"
            task = json.loads(path.read_text(encoding="utf-8"))
            for field in ("policy", "policy_migration", "tdd_required",
                          "diagnosis_required"):
                task.pop(field, None)
            task["work_kind"] = "test"
            task["risk_flags"] = []
            source = self.repo / f"replan-{task_id}.json"
            source.write_text(json.dumps(task), encoding="utf-8")
            replaced = self.run_tool(
                STATE, "--run", str(run), "task-put", "--replace", "--file",
                str(source),
            )
            self.assertEqual(replaced.returncode, 0, replaced.stderr)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["policy_migration_pending"], [])
        ready = self.run_tool(STATE, "--run", str(run), "phase", "PLAN_READY")
        self.assertEqual(ready.returncode, 0, ready.stderr)

    def test_v3_active_run_cannot_bypass_new_task_contracts(self):
        run = self.init_run()
        seams = [{
            "id": "S01", "behavior": "caller sees result",
            "test_paths": ["tests/check.txt"], "command": "true",
            "red_pattern": "EXPECTED_RED",
        }]
        self.make_task(
            run, work_kind="feature", write_set=["app.txt", "tests/check.txt"],
            test_seams=seams,
        )
        task_path = run / "tasks/T01.json"
        task = json.loads(task_path.read_text(encoding="utf-8"))
        for field in ("scenario_coverage", "handoff_required", "required_decisions"):
            task.pop(field, None)
        task_path.write_text(json.dumps(task), encoding="utf-8")
        state_path = run / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["schema_version"] = 3
        state_path.write_text(json.dumps(state), encoding="utf-8")

        bypass = self.run_tool(STATE, "--run", str(run), "phase", "SPEC_READY")
        self.assertNotEqual(bypass.returncode, 0)
        self.assertIn("run migrate-run first", bypass.stderr)
        migrated = self.run_tool(STATE, "--run", str(run), "migrate-run")
        self.assertEqual(migrated.returncode, 0, migrated.stderr)
        current = json.loads(state_path.read_text(encoding="utf-8"))
        migrated_task = json.loads(task_path.read_text(encoding="utf-8"))
        self.assertEqual(current["schema_version"], 6)
        self.assertEqual(current["policy_migration_pending"], ["T01"])
        self.assertFalse(migrated_task["handoff_required"])
        self.assertEqual(migrated_task["required_decisions"], [])
        self.assertTrue(migrated_task["policy_migration"]["requires_replan"])
        self.assertEqual(migrated_task["depends_on"], [])
        blocked = self.run_tool(STATE, "--run", str(run), "phase", "SPEC_READY")
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("require REPLANNING", blocked.stderr)

    def test_v4_dependency_migration_requires_active_tasks_to_be_replanned(self):
        run = self.init_run()
        self.make_task(run, task="T01", wave=1)
        task_path = run / "tasks/T01.json"
        task = json.loads(task_path.read_text(encoding="utf-8"))
        task.pop("depends_on")
        task_path.write_text(json.dumps(task), encoding="utf-8")
        state_path = run / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["schema_version"] = 4
        state_path.write_text(json.dumps(state), encoding="utf-8")

        migrated = self.run_tool(STATE, "--run", str(run), "migrate-run")
        self.assertEqual(migrated.returncode, 0, migrated.stderr)
        current = json.loads(state_path.read_text(encoding="utf-8"))
        migrated_task = json.loads(task_path.read_text(encoding="utf-8"))
        self.assertEqual(current["schema_version"], 6)
        self.assertEqual(current["policy_migration_pending"], ["T01"])
        self.assertEqual(migrated_task["depends_on"], [])
        self.assertTrue(migrated_task["policy_migration"]["requires_replan"])
        blocked = self.run_tool(STATE, "--run", str(run), "phase", "SPEC_READY")
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("require REPLANNING", blocked.stderr)

    def test_v4_running_wave_migration_cannot_start_task_before_replan(self):
        run = self.init_run()
        self.make_task(run, task="T01", wave=1)
        task_path = run / "tasks/T01.json"
        task = json.loads(task_path.read_text(encoding="utf-8"))
        task.pop("depends_on")
        task_path.write_text(json.dumps(task), encoding="utf-8")
        state_path = run / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.update({
            "schema_version": 4,
            "phase": "WAVE_RUNNING",
            "current_wave": 1,
        })
        state_path.write_text(json.dumps(state), encoding="utf-8")

        self.run_tool(STATE, "--run", str(run), "migrate-run", check=True)
        packet = json.loads(self.run_tool(
            STATE, "--run", str(run), "status", check=True
        ).stdout)
        self.assertEqual(packet["ready_tasks"], [])
        self.assertEqual(
            packet["next_action"],
            "enter REPLANNING to restore migrated task dependencies",
        )

        blocked = self.run_tool(
            STATE, "--run", str(run), "task-status", "T01", "running"
        )
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("migrated task T01 requires REPLANNING", blocked.stderr)

    def test_v4_migration_rejects_tampered_task_projection_atomically(self):
        cases = (
            ("identity", lambda task: task.update({"id": "OTHER"}),
             "task record identity mismatch T01"),
            ("status", lambda task: task.update({"status": "blocked"}),
             "task T01 status mismatch"),
            ("dependencies", lambda task: task.update({"depends_on": [{}]}),
             "task T01 depends_on must be unique safe task ids"),
        )
        for name, tamper, expected in cases:
            with self.subTest(case=name):
                run = self.init_run(run=f"legacy-{name}")
                self.make_task(run, task="T01", wave=1)
                task_path = run / "tasks/T01.json"
                task = json.loads(task_path.read_text(encoding="utf-8"))
                tamper(task)
                task_path.write_text(json.dumps(task), encoding="utf-8")
                state_path = run / "state.json"
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["schema_version"] = 4
                state_path.write_text(json.dumps(state), encoding="utf-8")
                events_path = run / "events.jsonl"
                before = {
                    state_path: state_path.read_bytes(),
                    task_path: task_path.read_bytes(),
                    events_path: events_path.read_bytes(),
                }

                rejected = self.run_tool(
                    STATE, "--run", str(run), "migrate-run"
                )
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn(expected, rejected.stderr)
                for path, content in before.items():
                    self.assertEqual(path.read_bytes(), content)

    def test_sealed_v4_run_can_migrate_and_invalidates_old_seal(self):
        run = self.init_run()
        state_path = run / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["schema_version"] = 4
        state["publication_seal"] = {
            "purpose": "integrate", "head_sha": state["base_commit"],
        }
        state_path.write_text(json.dumps(state), encoding="utf-8")

        migrated = self.run_tool(STATE, "--run", str(run), "migrate-run")
        self.assertEqual(migrated.returncode, 0, migrated.stderr)
        current = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(current["schema_version"], 6)
        self.assertNotIn("publication_seal", current)

    def test_v3_untyped_resolved_decision_reopens_instead_of_assuming_allow(self):
        run = self.init_run()
        source = self.repo / "legacy-decision.json"
        source.write_text(json.dumps({
            "schema_version": 1, "id": "D-legacy", "status": "open",
            "question": "Publish the result?", "authority": "user",
            "scope": {"kind": "action", "targets": ["publish"]},
        }), encoding="utf-8")
        self.run_tool(STATE, "--run", str(run), "decision-put", "--file",
                      str(source), check=True)
        self.run_tool(
            STATE, "--run", str(run), "decision-resolve", "D-legacy",
            "--outcome", "allow", "--choice", "do not publish",
            "--evidence", "legacy untyped fixture", check=True,
        )
        decision_path = run / "decisions/D-legacy.json"
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        decision["resolution"].pop("outcome")
        decision_path.write_text(json.dumps(decision), encoding="utf-8")
        state_path = run / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["schema_version"] = 3
        state_path.write_text(json.dumps(state), encoding="utf-8")

        migrated = self.run_tool(STATE, "--run", str(run), "migrate-run")
        self.assertEqual(migrated.returncode, 0, migrated.stderr)
        report = json.loads(migrated.stdout)
        self.assertEqual(report["reopened_decisions"], ["D-legacy"])
        reopened = json.loads(decision_path.read_text(encoding="utf-8"))
        self.assertEqual(reopened["status"], "open")
        self.assertNotIn("resolution", reopened)
        blocked = self.run_tool(
            STATE, "--run", str(run), "decision-check", "--action", "publish"
        )
        self.assertNotEqual(blocked.returncode, 0)

    def test_v3_completed_task_can_be_replaced_during_required_replan(self):
        run = self.init_run()
        worktree, _ = self.make_task(run)
        self.start_wave(run)
        self.run_tool(STATE, "--run", str(run), "task-status", "T01", "running",
                      check=True)
        (worktree / "app.txt").write_text("legacy result\n", encoding="utf-8")
        self.run_git("add", "app.txt", cwd=worktree)
        self.run_git("commit", "-m", "legacy completed task", cwd=worktree)
        self.run_tool(STATE, "--run", str(run), "verify-task", "T01", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "WAVE_VALIDATING",
                      check=True)
        self.run_tool(CHECK, "--run", str(run), "--task", "T01", check=True)
        state_path = run / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["schema_version"] = 3
        state_path.write_text(json.dumps(state), encoding="utf-8")
        self.run_tool(STATE, "--run", str(run), "migrate-run", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "REPLANNING",
                      "--reason", "upgrade contracts", check=True)

        task_path = run / "tasks/T01.json"
        replacement = json.loads(task_path.read_text(encoding="utf-8"))
        replacement["status"] = "pending"
        for field in (
            "check_result", "verification_evidence", "policy", "tdd_required",
            "diagnosis_required", "policy_migration", "updated_at",
        ):
            replacement.pop(field, None)
        replacement_file = self.repo / "replace-completed.json"
        replacement_file.write_text(json.dumps(replacement), encoding="utf-8")
        replaced = self.run_tool(
            STATE, "--run", str(run), "task-put", "--replace", "--file",
            str(replacement_file),
        )
        self.assertEqual(replaced.returncode, 0, replaced.stderr)
        current = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(current["tasks"]["T01"]["status"], "pending")
        self.assertEqual(current["policy_migration_pending"], [])

    def test_v2_migration_rebuilds_verified_merged_provenance(self):
        run = self.init_run()
        worktree, _ = self.make_task(run, task="T01", work_kind="test")
        integration = "agent/test-run/integration"
        integration_worktree = run / "worktrees/integration"
        self.run_git("worktree", "add", "-b", integration,
                     str(integration_worktree), "HEAD")
        self.start_wave(run)
        self.run_tool(STATE, "--run", str(run), "task-status", "T01", "running",
                      check=True)
        (worktree / "app.txt").write_text("merged\n", encoding="utf-8")
        self.run_git("add", "app.txt", cwd=worktree)
        self.run_git("commit", "-m", "task", cwd=worktree)
        task_head = self.run_git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        self.run_tool(STATE, "--run", str(run), "verify-task", "T01", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "WAVE_VALIDATING", check=True)
        self.run_tool(CHECK, "--run", str(run), "--task", "T01", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "WAVE_MERGING", check=True)
        self.run_git("cherry-pick", task_head, cwd=integration_worktree)
        merged_head = self.run_git(
            "rev-parse", "HEAD", cwd=integration_worktree).stdout.strip()
        self.run_tool(STATE, "--run", str(run), "task-status", "T01", "merged",
                      "--commit", merged_head, check=True)

        task_path = run / "tasks/T01.json"
        task = json.loads(task_path.read_text(encoding="utf-8"))
        for field in ("work_kind", "risk_flags", "policy", "tdd_required",
                      "diagnosis_required"):
            task.pop(field, None)
        task_path.write_text(json.dumps(task), encoding="utf-8")
        state_path = run / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["schema_version"] = 2
        for field in ("risk_forced", "model_profiles", "waves",
                      "policy_migration_pending", "integration_provenance_head",
                      "integration_provenance"):
            state.pop(field, None)
        state_path.write_text(json.dumps(state), encoding="utf-8")

        migrated = self.run_tool(STATE, "--run", str(run), "migrate-v2")
        self.assertEqual(migrated.returncode, 0, migrated.stderr)
        rebuilt = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(rebuilt["integration_provenance_head"], merged_head)
        self.assertEqual([item["task"] for item in rebuilt["integration_provenance"]],
                         ["T01"])


class EvaluationContractTests(RepoCase):
    def load_eval_module(self):
        spec = importlib.util.spec_from_file_location("qteam_eval_test", EVAL)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_cross_family_claim_requires_complete_worker_visibility(self):
        module = self.load_eval_module()
        partial = {
            "tasks": ["T01", "T02"],
            "worker_trajectories": [{
                "execution": {"family": "generator-family"},
            }],
            "tool_visibility_unavailable": ["T02"],
        }
        self.assertEqual(
            module.trajectory_independence(partial, "judge-family"),
            "identity-only",
        )
        complete = {
            **partial,
            "worker_trajectories": [
                {"execution": {"family": "generator-family"}},
                {"execution": {"family": "other-generator-family"}},
            ],
            "tool_visibility_unavailable": [],
        }
        self.assertEqual(
            module.trajectory_independence(complete, "judge-family"),
            "cross-family",
        )
        complete["worker_trajectories"][1]["execution"]["family"] = "judge-family"
        self.assertEqual(
            module.trajectory_independence(complete, "judge-family"),
            "identity-only",
        )

    def test_passing_review_schema_requires_passing_trajectory(self):
        schema = json.loads(
            (PLUGIN / "schemas/review-result.schema.json").read_text(
                encoding="utf-8"
            )
        )
        passing = next(
            rule for rule in schema["allOf"]
            if rule["if"]["properties"]["verdict"].get("const") == "pass"
        )
        self.assertEqual(
            passing["then"]["properties"]["trajectory_verdict"],
            {"const": "pass"},
        )

    def test_learning_manifest_rejects_malformed_optional_types(self):
        module = self.load_eval_module()
        base = {
            "schema_version": 1, "run_id": "R1", "project": "repo",
            "items": [{
                "id": "K1", "title": "Knowledge", "category": "knowledge",
                "status": "proposed",
            }],
        }
        with self.assertRaisesRegex(ValueError, "typed run/project/items"):
            module.validate_learning_manifest({**base, "source_commits": 1})
        with self.assertRaisesRegex(ValueError, "typed run/project/items"):
            module.validate_learning_manifest({**base, "source_commits": [{}]})
        malformed_skill = json.loads(json.dumps(base))
        malformed_skill["items"][0].update({
            "category": "skill", "skill_name": 1,
        })
        with self.assertRaisesRegex(ValueError, "id/title/category/status"):
            module.validate_learning_manifest(malformed_skill)

    def test_trajectory_summary_redacts_payloads_and_surfaces_repeated_calls(self):
        module = self.load_eval_module()
        trace = self.repo / "private-trace.jsonl"
        secret = "do-not-copy-this-token"
        events = [
            {"type": "thread.started", "thread_id": "trace-test"},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {
                "type": "command_execution", "command": f"tool --token {secret}",
                "aggregated_output": secret, "exit_code": 0,
            }},
            {"type": "item.completed", "item": {
                "type": "command_execution", "command": "test -f README.md",
                "aggregated_output": "", "exit_code": 0,
            }},
            {"type": "item.completed", "item": {
                "type": "file_change", "changes": [{"path": "one.py"}],
            }},
            {"type": "item.completed", "item": {
                "type": "file_change", "changes": [{"path": "two.py"}],
            }},
            {"type": "item.completed", "item": {
                "type": "command_execution", "command": f"tool --token {secret}",
                "aggregated_output": secret, "exit_code": 0,
            }},
            {"type": "item.completed", "item": {
                "type": "agent_message", "text": "bounded result",
            }},
            {"type": "turn.completed", "usage": {
                "input_tokens": 12, "output_tokens": 3,
            }},
        ]
        trace.write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )
        execution = {
            "tier": "economy",
            **module.execution_profile("gpt-5.6-terra", "low"),
        }
        summary = module.parse_codex_trace(
            trace, "worker", "T01", execution, "codex-cli test",
        )
        module.validate_trajectory(
            summary, "worker", "T01", execution, trace
        )
        serialized = json.dumps(summary, sort_keys=True)
        self.assertNotIn(secret, serialized)
        self.assertEqual(summary["counts"]["duplicate_calls"], 1)
        self.assertEqual(summary["counts"]["empty_result_calls"], 0)
        self.assertEqual(summary["counts"]["tool_calls"], 3)
        self.assertEqual(summary["disposition"], "pass")
        self.assertEqual(summary["counts"]["input_tokens"], 12)
        forged = json.loads(json.dumps(summary))
        forged["counts"]["tool_calls"] = 0
        forged["counts"]["command_calls"] = 0
        forged["counts"]["duplicate_calls"] = 0
        forged["anomalies"] = []
        with self.assertRaisesRegex(ValueError, "summary does not match"):
            module.validate_trajectory(
                forged, "worker", "T01", execution, trace
            )

    def test_calibration_and_eval_evidence_fail_closed(self):
        module = self.load_eval_module()
        suite = module.calibration_suite("standards")
        with self.assertRaisesRegex(ValueError, "failed.*calibration"):
            module.validate_calibration(
                "standards", suite["sha256"],
                {"cal-standards-01": "pass", "cal-standards-02": "pass"},
            )

        run = self.repo / ".agents/runs/eval-run"
        evidence = run / "workers/T01.result.json"
        evidence.parent.mkdir(parents=True)
        evidence.write_text('{"status":"failed"}\n', encoding="utf-8")
        case = {
            "schema_version": 1, "id": "eval-tool-failure", "status": "approved",
            "decision": {
                "authority": "coordinator", "outcome": "approved",
                "evidence": "confirmed by the frozen worker failure",
                "decided_at": "2026-08-10T00:00:00+00:00",
            },
            "source": {
                "kind": "tool-failure", "run_id": "eval-run",
                "evidence": "workers/T01.result.json",
                "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
            },
            "observation": "The runner accepted an empty result.",
            "attribution": "agent", "capability": "tool result validation",
            "expected_outcome": "Reject empty successful tool results.",
            "validation_scope": "the frozen worker result",
            "claim_boundary": "does not cover unrelated tools",
        }
        self.assertEqual(module.validate_eval_case(case, run), case)
        case["source"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "digest does not match"):
            module.validate_eval_case(case, run)

    def test_trace_and_event_readers_fail_closed_on_unbounded_or_typed_input(self):
        module = self.load_eval_module()
        oversized = self.repo / "oversized.jsonl"
        oversized.write_bytes(b"x" * (4 * 1024 * 1024 + 1) + b"\n")
        with self.assertRaisesRegex(ValueError, "bounded JSONL size"):
            module.parse_codex_trace(
                oversized, "worker", "T01",
                {"tier": "economy", **module.execution_profile(
                    "gpt-5.6-terra", "low"
                )},
                "codex-cli test",
            )
        event_log = self.repo / "events.jsonl"
        event_log.write_bytes(b"\xff\n")
        with self.assertRaisesRegex(ValueError, "event encoding"):
            module.read_event_log(event_log)
        array = self.repo / "worker-result.json"
        array.write_text("[]\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "must be a JSON object"):
            module.read_bounded_json_object(array, "worker result")
        schema = json.loads((PLUGIN / "schemas/eval-case.schema.json").read_text(
            encoding="utf-8"
        ))
        pattern = schema["properties"]["source"]["properties"]["evidence"][
            "pattern"
        ]
        for invalid in (".", "./a", "a/./b", "a//b", "a/", "../a"):
            self.assertIsNone(re.fullmatch(pattern, invalid), invalid)
        deeply_nested = self.repo / "deep.jsonl"
        deeply_nested.write_text("[" * 2000 + "0" + "]" * 2000 + "\n",
                                 encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "invalid Codex JSONL"):
            module.parse_codex_trace(
                deeply_nested, "worker", "T01",
                {"tier": "economy", **module.execution_profile(
                    "gpt-5.6-terra", "low"
                )},
                "codex-cli test",
            )
        capped_stdout = self.repo / "capped.stdout"
        capped_stderr = self.repo / "capped.stderr"
        process = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdout.write('x'*8192)"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        with module.regular_output(capped_stdout, "test stdout") as stdout, \
                module.regular_output(capped_stderr, "test stderr") as stderr:
            _code, overflow = module.wait_capped_process(
                process, stdout, stderr, limit=1024
            )
        self.assertTrue(overflow)
        self.assertLessEqual(capped_stdout.stat().st_size, 1024)

        stubborn_stdout = self.repo / "stubborn.stdout"
        stubborn_stderr = self.repo / "stubborn.stderr"
        stubborn = subprocess.Popen(
            [sys.executable, "-c", (
                "import signal,sys,time;"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
                "sys.stdout.write('x'*8192);sys.stdout.flush();time.sleep(30)"
            )],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        started = time.monotonic()
        with module.regular_output(stubborn_stdout, "stubborn stdout") as stdout, \
                module.regular_output(stubborn_stderr, "stubborn stderr") as stderr:
            _code, overflow = module.wait_capped_process(
                stubborn, stdout, stderr, limit=1024
            )
        self.assertTrue(overflow)
        self.assertLess(time.monotonic() - started, 5)


class PolicyTests(RepoCase):
    def test_review_contract_digest_covers_closure_semantics(self):
        spec = importlib.util.spec_from_file_location("qteam_policy_test", POLICY)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(POLICY.parent))
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.pop(0)
        original = module.review_contract_digest("spec", "full")
        module.REVIEW_CLOSURE_INSTRUCTIONS += " Contract revision."
        self.assertNotEqual(
            original, module.review_contract_digest("spec", "full")
        )

    def test_wide_low_risk_wave_upgrades_review_not_workers(self):
        run = self.init_run("wide-run")
        for index in range(1, 5):
            self.make_task(run, task=f"T0{index}", work_kind="docs",
                           write_set=[f"docs/{index}.md"])
        state = json.loads((run / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["waves"]["1"]["execution_tier"], "standard")
        self.assertEqual(state["waves"]["1"]["review_intensity"], "full")
        for index in range(1, 5):
            task = json.loads((run / f"tasks/T0{index}.json").read_text(
                encoding="utf-8"))
            self.assertEqual(task["policy"]["execution_tier"], "economy")

    def test_recursive_write_glob_raises_reversibility_and_review_scope(self):
        run = self.init_run("broad-glob")
        self.make_task(
            run, task="T01", work_kind="docs", write_set=["docs/**"]
        )
        task = json.loads((run / "tasks/T01.json").read_text(encoding="utf-8"))
        self.assertEqual(task["reversibility"], "wide-reversible")
        self.assertEqual(task["policy"]["integration_lane"], "reviewed")
        self.assertEqual(task["policy"]["execution_tier"], "standard")
        self.assertEqual(task["policy"]["review_intensity"], "full")
        self.make_task(
            run, task="T02", work_kind="docs", write_set=["src/**/*.py"]
        )
        nested = json.loads((run / "tasks/T02.json").read_text(encoding="utf-8"))
        self.assertEqual(nested["reversibility"], "wide-reversible")
        self.assertEqual(nested["policy"]["integration_lane"], "reviewed")
        for task_id, pattern in (("T04", "src/*"),
                                 ("T05", "src/[ab].py")):
            self.make_task(
                run, task=task_id, work_kind="docs", write_set=[pattern]
            )
            wildcard = json.loads((run / f"tasks/{task_id}.json").read_text(
                encoding="utf-8"
            ))
            self.assertEqual(wildcard["reversibility"], "wide-reversible")
        sys.path.insert(0, str(POLICY.parent))
        try:
            spec = importlib.util.spec_from_file_location(
                "qteam_wildcard_policy_test", POLICY
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            root_wildcard = module.derive_task_policy({
                "work_kind": "docs", "risk_flags": [], "write_set": ["*"],
            })
        finally:
            sys.path.pop(0)
        self.assertEqual(root_wildcard["reversibility"], "wide-reversible")

    def test_task_facts_derive_model_and_review_policy(self):
        run = self.init_run()
        self.make_task(run, task="T01", work_kind="test")
        self.make_task(run, task="T02", work_kind="config",
                       write_set=["src/auth/**"])
        self.make_task(run, task="T03", work_kind="refactor")
        low = json.loads((run / "tasks/T01.json").read_text(encoding="utf-8"))
        high = json.loads((run / "tasks/T02.json").read_text(encoding="utf-8"))
        refactor = json.loads((run / "tasks/T03.json").read_text(encoding="utf-8"))
        state = json.loads((run / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(low["policy"]["execution_tier"], "economy")
        self.assertEqual(low["policy"]["review_intensity"], "compact")
        self.assertEqual(high["policy"]["execution_tier"], "deep")
        self.assertEqual(high["policy"]["review_intensity"], "risk")
        self.assertIn("authentication", high["policy"]["inferred_risk_flags"])
        self.assertEqual(refactor["policy"]["execution_tier"], "standard")
        self.assertFalse(refactor["tdd_required"])
        self.assertTrue(state["risk_required"])
        self.assertTrue(state["waves"]["1"]["require_risk_review"])

    def test_reversibility_is_inferred_and_cannot_be_declared_downward(self):
        run = self.init_run("reversible-run")
        self.make_task(
            run, task="T01", work_kind="config", risk_flags=["migration"],
            reversibility="contained-reversible",
        )
        task = json.loads((run / "tasks/T01.json").read_text(encoding="utf-8"))
        state = json.loads((run / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(task["policy"]["declared_reversibility"],
                         "contained-reversible")
        self.assertEqual(task["reversibility"], "hard-to-reverse")
        self.assertEqual(task["policy"]["integration_lane"], "human-only")
        self.assertTrue(task["policy"]["require_user_finish_decision"])
        self.assertTrue(state["hard_to_reverse"])

        integration = "agent/reversible-run/integration"
        integration_worktree = run / "worktrees/integration"
        self.run_git(
            "worktree", "add", "-b", integration,
            str(integration_worktree), "HEAD",
        )
        subject_result = self.run_tool(
            STATE, "--run", str(run), "reversibility-subject"
        )
        self.assertEqual(subject_result.returncode, 0, subject_result.stderr)
        subject = json.loads(subject_result.stdout)["subject"]
        decision = {
            "schema_version": 1, "id": "D-HARD", "status": "open",
            "question": "May this exact hard-to-reverse run finish?",
            "authority": "user",
            "scope": {"kind": "action", "targets": ["finish"]},
            "subject": {"kind": "hard-to-reverse-run", "sha256": "0" * 64},
        }
        source = self.repo / "hard-decision.json"
        source.write_text(json.dumps(decision), encoding="utf-8")
        stale = self.run_tool(
            STATE, "--run", str(run), "decision-put", "--file", str(source)
        )
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn("subject is stale", stale.stderr)
        decision["subject"] = subject
        source.write_text(json.dumps(decision), encoding="utf-8")
        accepted = self.run_tool(
            STATE, "--run", str(run), "decision-put", "--file", str(source)
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        resolved = self.run_tool(
            STATE, "--run", str(run), "decision-resolve", "D-HARD",
            "--outcome", "allow", "--choice", "finish exact subject",
            "--evidence", "user approved this frozen run",
        )
        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        for phase in ("SPEC_READY", "PLAN_READY", "LEARNING_EXPORT"):
            self.run_tool(STATE, "--run", str(run), "phase", phase, check=True)
        (integration_worktree / "late.txt").write_text("late\n", encoding="utf-8")
        self.run_git("add", "late.txt", cwd=integration_worktree)
        self.run_git("commit", "-m", "advance hard-to-reverse head",
                     cwd=integration_worktree)
        integration_head = self.run_git(
            "rev-parse", "HEAD", cwd=integration_worktree
        ).stdout.strip()
        task_path = run / "tasks/T01.json"
        task = json.loads(task_path.read_text(encoding="utf-8"))
        task["status"] = "merged"
        task["merge_commit"] = integration_head
        task_path.write_text(json.dumps(task), encoding="utf-8")
        state_path = run / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["tasks"]["T01"]["status"] = "merged"
        for gate in ("final_verification", "reviews", "learning", "public_boundary"):
            state["gates"][gate] = {"status": "passed"}
        state_path.write_text(json.dumps(state), encoding="utf-8")
        stale_after_change = self.run_tool(
            STATE, "--run", str(run), "phase", "READY_TO_FINISH"
        )
        self.assertNotEqual(stale_after_change.returncode, 0)
        self.assertIn("hard-to-reverse", stale_after_change.stderr)

        decision["id"] = "D-MIXED"
        decision["scope"]["targets"] = ["finish", "publish"]
        decision["subject"] = json.loads(self.run_tool(
            STATE, "--run", str(run), "reversibility-subject", check=True
        ).stdout)["subject"]
        source.write_text(json.dumps(decision), encoding="utf-8")
        mixed = self.run_tool(
            STATE, "--run", str(run), "decision-put", "--file", str(source)
        )
        self.assertNotEqual(mixed.returncode, 0)
        self.assertIn("user action decision", mixed.stderr)

        decision["id"] = "D-HARD-FRESH"
        decision["scope"]["targets"] = ["finish"]
        decision["subject"] = json.loads(self.run_tool(
            STATE, "--run", str(run), "reversibility-subject", check=True
        ).stdout)["subject"]
        source.write_text(json.dumps(decision), encoding="utf-8")
        self.run_tool(
            STATE, "--run", str(run), "decision-put", "--file", str(source),
            check=True,
        )
        self.run_tool(
            STATE, "--run", str(run), "decision-resolve", "D-HARD-FRESH",
            "--outcome", "allow", "--choice", "finish refreshed subject",
            "--evidence", "user approved the refreshed subject", check=True,
        )
        sys.path.insert(0, str(PLUGIN / "bin"))
        try:
            spec = importlib.util.spec_from_file_location(
                "qteam_reversibility_authorization_test", STATE
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            current_state = json.loads(state_path.read_text(encoding="utf-8"))
            module.require_hard_to_reverse_authorization(
                self.repo, run, current_state
            )
        finally:
            sys.path.pop(0)

    def test_experiment_is_standard_without_implicit_tdd(self):
        run = self.init_run()
        contract = {
            "goal": "raise score", "metric": {
                "name": "score", "direction": "higher_is_better",
                "command": "printf '1\\n'", "baseline": None,
                "minimum_delta": 1,
            },
            "guard_command": "true", "holdout_command": "true",
            "max_attempts": 3, "plateau_window": 2,
        }
        self.make_task(run, work_kind="experiment", experiment=contract)
        task = json.loads((run / "tasks/T01.json").read_text(encoding="utf-8"))
        self.assertEqual(task["policy"]["execution_tier"], "standard")
        self.assertFalse(task["tdd_required"])

    def test_experiment_contract_is_required_and_not_ignored_elsewhere(self):
        run = self.init_run()
        branch = "agent/test-run/T01"
        worktree = run / "worktrees/T01"
        worktree.parent.mkdir(parents=True, exist_ok=True)
        self.run_git("worktree", "add", "-b", branch, str(worktree), "HEAD")
        base = {
            "id": "T01", "status": "pending", "branch": branch,
            "worktree": str(worktree),
            "base_commit": self.run_git("rev-parse", "HEAD").stdout.strip(),
            "parallel_group": "wave-1", "wave": 1, "depends_on": [],
            "write_set": ["app.txt"], "forbidden_paths": [],
            "verification": "true", "risk_flags": [],
        }
        source = self.repo / "experiment-task.json"
        for work_kind, extra, expected in (
            ("experiment", {}, "exact frozen experiment fields"),
            ("test", {"experiment": {}}, "requires work_kind=experiment"),
            ("test", {"experiment": None}, "requires work_kind=experiment"),
        ):
            source.write_text(json.dumps({**base, "work_kind": work_kind, **extra}),
                              encoding="utf-8")
            result = self.run_tool(STATE, "--run", str(run), "task-put", "--file",
                                   str(source))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(expected, result.stderr)

    def test_duplicate_risk_flag_is_rejected(self):
        run = self.init_run()
        branch = "agent/test-run/T01"
        worktree = run / "worktrees/T01"
        worktree.parent.mkdir(parents=True, exist_ok=True)
        self.run_git("worktree", "add", "-b", branch, str(worktree), "HEAD")
        record = {
            "id": "T01", "status": "pending", "branch": branch,
            "worktree": str(worktree),
            "base_commit": self.run_git("rev-parse", "HEAD").stdout.strip(),
            "parallel_group": "wave-1", "wave": 1, "depends_on": [],
            "write_set": ["app.txt"], "forbidden_paths": [],
            "verification": "true", "work_kind": "test",
            "risk_flags": ["security", "security"],
        }
        source = self.repo / "duplicate-risk.json"
        source.write_text(json.dumps(record), encoding="utf-8")
        result = self.run_tool(STATE, "--run", str(run), "task-put", "--file",
                               str(source))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not contain duplicates", result.stderr)

    def test_boolean_wave_is_rejected_even_though_bool_is_an_int_in_python(self):
        run = self.init_run()
        branch = "agent/test-run/T01"
        worktree = run / "worktrees/T01"
        worktree.parent.mkdir(parents=True, exist_ok=True)
        self.run_git("worktree", "add", "-b", branch, str(worktree), "HEAD")
        record = {
            "id": "T01", "status": "pending", "branch": branch,
            "worktree": str(worktree),
            "base_commit": self.run_git("rev-parse", "HEAD").stdout.strip(),
            "parallel_group": "wave-1", "wave": True, "depends_on": [],
            "write_set": ["app.txt"], "forbidden_paths": [],
            "verification": "true", "work_kind": "test", "risk_flags": [],
        }
        source = self.repo / "boolean-wave.json"
        source.write_text(json.dumps(record), encoding="utf-8")
        result = self.run_tool(STATE, "--run", str(run), "task-put", "--file",
                               str(source))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("positive integer", result.stderr)

    def test_tdd_seams_require_exact_owned_test_files_not_broad_globs(self):
        run = self.init_run()
        branch = "agent/test-run/T01"
        worktree = run / "worktrees/T01"
        worktree.parent.mkdir(parents=True, exist_ok=True)
        self.run_git("worktree", "add", "-b", branch, str(worktree), "HEAD")
        record = {
            "id": "T01", "status": "pending", "branch": branch,
            "worktree": str(worktree),
            "base_commit": self.run_git("rev-parse", "HEAD").stdout.strip(),
            "parallel_group": "wave-1", "wave": 1, "depends_on": [],
            "write_set": ["app.txt", "tests/**"], "forbidden_paths": [],
            "verification": "true", "work_kind": "feature", "risk_flags": [],
            "test_seams": [{
                "id": "public-behavior", "behavior": "public behavior",
                "test_paths": ["tests/**"], "command": "true",
                "red_pattern": "EXPECTED_RED",
            }],
        }
        source = self.repo / "broad-seam.json"
        source.write_text(json.dumps(record), encoding="utf-8")
        result = self.run_tool(STATE, "--run", str(run), "task-put", "--file",
                               str(source))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exact test_paths", result.stderr)


    def test_coordinator_decision_binds_harvested_eval_candidate(self):
        run = self.init_run("eval-decision")
        for phase in ("SPEC_READY", "PLAN_READY", "LEARNING_EXPORT"):
            self.run_tool(STATE, "--run", str(run), "phase", phase, check=True)
        evidence = run / "evidence/tool-failure.json"
        evidence.parent.mkdir(parents=True)
        evidence.write_text('{"confirmed":true}\n', encoding="utf-8")
        outbox = run / "learning-outbox"
        cases = outbox / "eval-cases"
        cases.mkdir(parents=True)
        case = {
            "schema_version": 1, "id": "tool-empty-result",
            "status": "candidate", "source": {
                "kind": "tool-failure", "run_id": run.name,
                "evidence": "evidence/tool-failure.json",
                "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
            },
            "observation": "A tool returned an unusable success payload.",
            "attribution": "agent", "capability": "result validation",
            "expected_outcome": "Reject unusable success payloads.",
            "validation_scope": "the frozen tool failure",
            "claim_boundary": "does not cover unrelated tools",
        }
        case_path = cases / f"{case['id']}.json"
        case_path.write_text(json.dumps(case), encoding="utf-8")
        manifest = {
            "schema_version": 1, "run_id": run.name, "project": "repo",
            "source_commits": [], "items": [{
                "id": "E1", "title": "Empty tool result", "category": "eval",
                "file": f"eval-cases/{case['id']}.json", "status": "proposed",
                "validation_scope": case["validation_scope"],
                "claim_boundary": case["claim_boundary"],
            }],
        }
        manifest_path = outbox / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        decided = self.run_tool(
            STATE, "--run", str(run), "learning-item-decision", "E1",
            "--outcome", "approved", "--evidence", "reviewed by coordinator",
        )
        self.assertEqual(decided.returncode, 0, decided.stderr)
        approved = json.loads(case_path.read_text(encoding="utf-8"))
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["decision"]["authority"], "coordinator")
        approved_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(approved_manifest["items"][0]["decision"],
                         approved["decision"])
        events = [json.loads(line) for line in
                  (run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        decision_event = next(item for item in events
                              if item.get("event") == "learning_item_decided")
        encoded = json.dumps(approved, sort_keys=True,
                             separators=(",", ":")).encode()
        self.assertEqual(decision_event["case_sha256"],
                         hashlib.sha256(encoded).hexdigest())

    def test_coordinator_decision_supports_non_eval_learning_items(self):
        run = self.init_run("knowledge-decision")
        for phase in ("SPEC_READY", "PLAN_READY", "LEARNING_EXPORT"):
            self.run_tool(STATE, "--run", str(run), "phase", phase, check=True)
        outbox = run / "learning-outbox"
        outbox.mkdir(parents=True)
        manifest_path = outbox / "manifest.json"
        manifest_path.write_text(json.dumps({
            "schema_version": 1, "run_id": run.name, "project": "repo",
            "source_commits": [], "items": [{
                "id": "K1", "title": "Durable knowledge",
                "category": "knowledge", "file": "knowledge.md",
                "section": "Durable knowledge", "status": "proposed",
                "validation_scope": "the tested repository behavior",
                "claim_boundary": "does not cover unrelated repositories",
            }],
        }), encoding="utf-8")

        decided = self.run_tool(
            STATE, "--run", str(run), "learning-item-decision", "K1",
            "--outcome", "approved", "--evidence", "coordinator checked source",
        )
        self.assertEqual(decided.returncode, 0, decided.stderr)
        item = json.loads(manifest_path.read_text(encoding="utf-8"))["items"][0]
        self.assertEqual(item["status"], "approved")
        events = [
            json.loads(line)
            for line in (run / "events.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        event = next(
            record for record in events
            if record.get("event") == "learning_item_decided"
            and record.get("item") == "K1"
        )
        self.assertEqual(event["category"], "knowledge")
        encoded = json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(event["item_sha256"], hashlib.sha256(encoded).hexdigest())


class WorkerTests(RepoCase):
    def test_worker_rejects_symlinked_workers_root_before_writing(self):
        run = self.init_run("worker-root-symlink")
        self.make_task(run)
        self.start_wave(run)
        workers = run / "workers"
        if workers.exists():
            shutil.rmtree(workers)
        outside = Path(self.tmp.name) / "outside-workers"
        outside.mkdir()
        workers.symlink_to(outside, target_is_directory=True)
        rejected = self.run_tool(
            WORKER, "spawn", "--run", str(run), "--task", "T01",
            "--role", "developer",
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertFalse(list(outside.iterdir()))

    def test_schema_gate_runs_before_any_writable_worker_process(self):
        run = self.init_run()
        self.make_task(run)
        self.start_wave(run)
        state_path = run / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["schema_version"] = 3
        state_path.write_text(json.dumps(state), encoding="utf-8")
        fake_bin = Path(self.tmp.name) / "bin-schema-gate"
        fake_bin.mkdir()
        trace = Path(self.tmp.name) / "should-not-run"
        fake = fake_bin / "codex"
        fake.write_text(
            "#!/usr/bin/env python3\nfrom pathlib import Path\n"
            f"Path({str(trace)!r}).write_text('ran')\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        rejected = self.run_tool(
            WORKER, "spawn", "--run", str(run), "--task", "T01",
            "--role", "developer", env=env,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("migrate-run first", rejected.stderr)
        self.assertFalse(trace.exists())

    def test_experiment_contract_reaches_developer_packet(self):
        run = self.init_run()
        contract = {
            "goal": "raise score", "metric": {
                "name": "score", "direction": "higher_is_better",
                "command": "printf '1\\n'", "baseline": None,
                "minimum_delta": 1,
            },
            "guard_command": "true", "holdout_command": "true",
            "max_attempts": 3, "plateau_window": 2,
        }
        self.make_task(run, work_kind="experiment", experiment=contract)
        spec = importlib.util.spec_from_file_location("qteam_worker_test", WORKER)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(PLUGIN / "bin"))
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.pop(0)
        task = json.loads((run / "tasks/T01.json").read_text(encoding="utf-8"))
        packet = module.build_packet(self.repo, run, task, "developer", "")
        self.assertIn('"max_attempts": 3', packet)
        self.assertIn('"holdout_command": "true"', packet)
        self.assertIn(".qteam-experiment.json", packet)

    def test_tdd_scenario_matrix_and_claim_boundaries_reach_worker_packet(self):
        run = self.init_run()
        seams = [{
            "id": "S01", "behavior": "caller sees result",
            "test_paths": ["tests/check.txt"], "command": "true",
            "red_pattern": "EXPECTED_RED",
        }]
        self.make_task(
            run, work_kind="feature", write_set=["app.txt", "tests/check.txt"],
            test_seams=seams,
        )
        spec = importlib.util.spec_from_file_location("qteam_worker_tdd_test", WORKER)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(PLUGIN / "bin"))
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.pop(0)
        task = json.loads((run / "tasks/T01.json").read_text(encoding="utf-8"))
        packet = module.build_packet(self.repo, run, task, "developer", "")
        self.assertIn('"scenario_coverage"', packet)
        self.assertIn('"dimension": "abuse-security"', packet)
        self.assertIn("Validation scope:", packet)
        self.assertIn("Claim boundary:", packet)

    def test_worker_success_requires_machine_validated_digest_boundaries(self):
        run = self.init_run()
        self.make_task(run)
        self.start_wave(run)
        fake_bin = Path(self.tmp.name) / "bin-invalid-digest"
        fake_bin.mkdir()
        fake = fake_bin / "codex"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\nfrom pathlib import Path\n"
            "if '--version' in sys.argv:\n print('codex-cli test-worker'); raise SystemExit\n"
            "args=sys.argv[1:]; msg='digest without required fields'\n"
            "Path(args[args.index('--output-last-message')+1]).write_text(msg)\n"
            "print(json.dumps({'type':'thread.started','thread_id':'worker'}))\n"
            "print(json.dumps({'type':'turn.started'}))\n"
            "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':msg}}))\n"
            "print(json.dumps({'type':'turn.completed','usage':{}}))\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        self.run_tool(WORKER, "spawn", "--run", str(run), "--task", "T01",
                      "--role", "developer", env=env, check=True)
        waited = self.run_tool(
            WORKER, "wait", "--run", str(run), "--task", "T01",
            "--timeout", "10", env=env,
        )
        self.assertNotEqual(waited.returncode, 0)
        packet = json.loads(waited.stdout)
        self.assertEqual(packet["status"], "failed")
        self.assertEqual(packet["result"]["exit_code"], 65)
        self.assertIn("Validation scope:", packet["result"]["error"])

    def test_worker_uses_shared_ascii_safe_task_id_contract(self):
        run = self.init_run()
        for task_id in ("É-1", "-T01", "T..01"):
            rejected = self.run_tool(
                WORKER, "spawn", "--run", str(run), f"--task={task_id}",
                "--role", "developer",
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("unsafe task id", rejected.stderr)

    def test_worker_is_pinned_to_exact_task_worktree(self):
        run = self.init_run()
        worktree, _ = self.make_task(run)
        self.start_wave(run)
        fake_bin = Path(self.tmp.name) / "bin"
        fake_bin.mkdir()
        trace = Path(self.tmp.name) / "trace.json"
        fake = fake_bin / "codex"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "if '--version' in sys.argv:\n print('codex-cli test-worker'); raise SystemExit\n"
            "json.dump({'cwd': os.getcwd(), 'args': sys.argv[1:]}, open(os.environ['FAKE_CODEX_TRACE'], 'w'))\n"
            "args=sys.argv[1:]; msg='Validation scope: fake worker execution\\nClaim boundary: fake does not validate production behavior'\n"
            "Path(args[args.index('--output-last-message')+1]).write_text(msg)\n"
            "print(json.dumps({'type':'thread.started','thread_id':'worker'}))\nprint(json.dumps({'type':'turn.started'}))\n"
            "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':msg}}))\nprint(json.dumps({'type':'turn.completed','usage':{}}))\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        env["FAKE_CODEX_TRACE"] = str(trace)
        spawned = self.run_tool(
            WORKER, "spawn", "--run", str(run), "--task", "T01",
            "--role", "developer", env=env,
        )
        self.assertEqual(spawned.returncode, 0, spawned.stderr)
        waited = self.run_tool(
            WORKER, "wait", "--run", str(run), "--task", "T01",
            "--timeout", "10", env=env,
        )
        self.assertEqual(waited.returncode, 0, waited.stderr)
        seen = json.loads(trace.read_text(encoding="utf-8"))
        self.assertEqual(Path(seen["cwd"]), worktree)
        self.assertEqual(seen["args"][0:4], ["exec", "-C", str(worktree), "--sandbox"])
        self.assertEqual(seen["args"][4], "workspace-write")
        self.assertEqual(seen["args"][5:7], ["--model", "gpt-5.6-terra"])
        self.assertIn('model_reasoning_effort="low"', seen["args"])
        self.assertIn('model_provider="openai"', seen["args"])
        record = json.loads((run / "workers/T01.json").read_text(encoding="utf-8"))
        result = json.loads((run / "workers/T01.result.json").read_text(encoding="utf-8"))
        self.assertEqual(record["cwd"], str(worktree))
        self.assertEqual(record["execution"]["tier"], "economy")
        self.assertEqual(result["exit_code"], 0)

    def test_high_risk_worker_uses_deep_profile(self):
        run = self.init_run()
        self.make_task(run, work_kind="config", write_set=["src/auth/**"])
        self.start_wave(run)
        fake_bin = Path(self.tmp.name) / "bin-deep"
        fake_bin.mkdir()
        trace = Path(self.tmp.name) / "trace-deep.json"
        fake = fake_bin / "codex"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "if '--version' in sys.argv:\n print('codex-cli test-worker'); raise SystemExit\n"
            "json.dump({'args': sys.argv[1:]}, open(os.environ['FAKE_CODEX_TRACE'], 'w'))\n"
            "args=sys.argv[1:]; msg='Validation scope: fake worker execution\\nClaim boundary: fake does not validate production behavior'\n"
            "Path(args[args.index('--output-last-message')+1]).write_text(msg)\n"
            "print(json.dumps({'type':'thread.started','thread_id':'worker'}))\nprint(json.dumps({'type':'turn.started'}))\n"
            "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':msg}}))\nprint(json.dumps({'type':'turn.completed','usage':{}}))\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        env["FAKE_CODEX_TRACE"] = str(trace)
        self.run_tool(WORKER, "spawn", "--run", str(run), "--task", "T01",
                      "--role", "developer", env=env, check=True)
        self.run_tool(WORKER, "wait", "--run", str(run), "--task", "T01",
                      "--timeout", "10", env=env, check=True)
        args = json.loads(trace.read_text(encoding="utf-8"))["args"]
        self.assertEqual(args[args.index("--model") + 1], "gpt-5.6-sol")
        self.assertIn('model_reasoning_effort="high"', args)

    def test_concurrent_spawn_has_single_owner(self):
        run = self.init_run()
        self.make_task(run)
        self.start_wave(run)
        fake_bin = Path(self.tmp.name) / "bin-concurrent"
        fake_bin.mkdir()
        fake = fake_bin / "codex"
        fake.write_text(
            "#!/usr/bin/env python3\nimport json, sys, time\nfrom pathlib import Path\n"
            "if '--version' in sys.argv:\n print('codex-cli test-worker'); raise SystemExit\n"
            "time.sleep(0.8)\nargs=sys.argv[1:]; msg='Validation scope: fake worker execution\\nClaim boundary: fake does not validate production behavior'\n"
            "Path(args[args.index('--output-last-message')+1]).write_text(msg)\n"
            "print(json.dumps({'type':'thread.started','thread_id':'worker'}))\nprint(json.dumps({'type':'turn.started'}))\n"
            "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':msg}}))\nprint(json.dumps({'type':'turn.completed','usage':{}}))\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        command = [sys.executable, str(WORKER), "spawn", "--run", str(run),
                   "--task", "T01", "--role", "developer"]
        first = subprocess.Popen(command, cwd=self.repo, env=env, text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        second = subprocess.Popen(command, cwd=self.repo, env=env, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        results = [first.communicate(timeout=10), second.communicate(timeout=10)]
        codes = sorted([first.returncode, second.returncode])
        self.assertEqual(codes[0], 0, results)
        self.assertNotEqual(codes[1], 0, results)
        self.run_tool(WORKER, "wait", "--run", str(run), "--task", "T01",
                      "--timeout", "10", env=env)

    def test_cancel_persists_result_and_kills_process_group(self):
        run = self.init_run()
        self.make_task(run)
        self.start_wave(run)
        fake_bin = Path(self.tmp.name) / "bin-cancel"
        fake_bin.mkdir()
        fake = fake_bin / "codex"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import signal, sys, time\n"
            "if '--version' in sys.argv:\n print('codex-cli test-worker'); raise SystemExit\n"
            "signal.signal(signal.SIGTERM, lambda *_: None)\n"
            "while True: time.sleep(0.1)\n", encoding="utf-8")
        fake.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        spawned = self.run_tool(WORKER, "spawn", "--run", str(run), "--task", "T01",
                                "--role", "developer", env=env)
        self.assertEqual(spawned.returncode, 0, spawned.stderr)
        record = json.loads((run / "workers/T01.json").read_text(encoding="utf-8"))
        cancelled = self.run_tool(WORKER, "cancel", "--run", str(run), "--task", "T01", env=env)
        self.assertEqual(cancelled.returncode, 0, cancelled.stderr)
        result = json.loads((run / "workers/T01.result.json").read_text(encoding="utf-8"))
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["exit_code"], 130)
        with self.assertRaises(ProcessLookupError):
            os.killpg(record["pgid"], 0)

    def test_graceful_child_exit_after_cancel_is_normalized_to_exit_130(self):
        run = self.init_run()
        self.make_task(run)
        self.start_wave(run)
        fake_bin = Path(self.tmp.name) / "bin-graceful-cancel"
        fake_bin.mkdir()
        fake = fake_bin / "codex"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import signal, sys, time\n"
            "if '--version' in sys.argv:\n print('codex-cli test-worker'); raise SystemExit\n"
            "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
            "while True: time.sleep(0.1)\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        self.run_tool(WORKER, "spawn", "--run", str(run), "--task", "T01",
                      "--role", "developer", env=env, check=True)
        record_path = run / "workers/T01.json"
        for _ in range(50):
            if json.loads(record_path.read_text(encoding="utf-8")).get("child_pid"):
                break
            time.sleep(0.02)
        time.sleep(0.1)
        cancelled = self.run_tool(
            WORKER, "cancel", "--run", str(run), "--task", "T01", env=env
        )
        self.assertEqual(cancelled.returncode, 0, cancelled.stderr)
        result = json.loads((run / "workers/T01.result.json").read_text(
            encoding="utf-8"
        ))
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["exit_code"], 130)
        self.assertNotIn("validation_scope", result)

    def test_knowledge_outbox_is_harvested_from_worker_worktree(self):
        run = self.init_run()
        self.make_task(run, artifact_kind="learning")
        self.run_tool(STATE, "--run", str(run), "phase", "SPEC_READY", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "PLAN_READY", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "LEARNING_EXPORT", check=True)
        fake_bin = Path(self.tmp.name) / "bin-harvest"
        fake_bin.mkdir()
        evidence = run / "evidence/eval-source.txt"
        evidence.parent.mkdir(parents=True)
        evidence.write_text("confirmed tool failure\n", encoding="utf-8")
        eval_case = {
            "schema_version": 1, "id": "eval-confirmed-tool-failure",
            "status": "candidate", "source": {
                "kind": "tool-failure", "run_id": run.name,
                "evidence": "evidence/eval-source.txt",
                "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
            },
            "observation": "A successful tool call returned no usable result.",
            "attribution": "agent", "capability": "tool result validation",
            "expected_outcome": "Escalate an empty successful tool result.",
            "validation_scope": "the frozen tool-failure evidence",
            "claim_boundary": "does not cover other tool adapters",
        }
        fake = fake_bin / "codex"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\nfrom pathlib import Path\n"
            "if '--version' in sys.argv:\n print('codex-cli test-worker'); raise SystemExit\n"
            "p=Path('.qteam-learning-outbox'); p.mkdir(); "
            "(p/'manifest.json').write_text(json.dumps({'schema_version':1,'run_id':'test-run','project':'repo','items':[{'id':'E1','title':'Confirmed tool failure','category':'eval','file':'eval-cases/eval-confirmed-tool-failure.json','status':'proposed','validation_scope':'the frozen tool-failure evidence','claim_boundary':'does not cover other tool adapters'}]}))\n"
            "e=p/'eval-cases'; e.mkdir(); "
            f"(e/'eval-confirmed-tool-failure.json').write_text({json.dumps(json.dumps(eval_case))})\n"
            "args=sys.argv[1:]; msg='Validation scope: fake learning outbox\\nClaim boundary: fake does not validate durable knowledge'\n"
            "Path(args[args.index('--output-last-message')+1]).write_text(msg)\n"
            "print(json.dumps({'type':'thread.started','thread_id':'worker'}))\nprint(json.dumps({'type':'turn.started'}))\n"
            "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':msg}}))\nprint(json.dumps({'type':'turn.completed','usage':{}}))\n",
            encoding="utf-8")
        fake.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        self.run_tool(WORKER, "spawn", "--run", str(run), "--task", "T01",
                      "--role", "knowledge-distiller", env=env, check=True)
        self.run_tool(WORKER, "wait", "--run", str(run), "--task", "T01",
                      "--timeout", "10", env=env, check=True)
        task_before_harvest = json.loads(
            (run / "tasks/T01.json").read_text(encoding="utf-8")
        )
        worker_manifest_path = (
            Path(task_before_harvest["worktree"])
            / ".qteam-learning-outbox/manifest.json"
        )
        worker_manifest = json.loads(
            worker_manifest_path.read_text(encoding="utf-8")
        )
        worker_manifest["items"][0]["status"] = "approved"
        worker_manifest_path.write_text(json.dumps(worker_manifest), encoding="utf-8")
        forged = self.run_tool(
            WORKER, "harvest", "--run", str(run), "--task", "T01"
        )
        self.assertNotEqual(forged.returncode, 0)
        self.assertIn("only proposed learning items", forged.stderr)
        self.assertFalse((run / "learning-outbox").exists())
        worker_manifest["items"][0]["status"] = "proposed"
        worker_manifest_path.write_text(json.dumps(worker_manifest), encoding="utf-8")
        harvested = self.run_tool(WORKER, "harvest", "--run", str(run), "--task", "T01")
        self.assertEqual(harvested.returncode, 0, harvested.stderr)
        self.assertTrue((run / "learning-outbox/manifest.json").is_file())
        self.assertTrue((run / "learning-outbox/eval-cases/"
                         "eval-confirmed-tool-failure.json").is_file())
        task = json.loads((run / "tasks/T01.json").read_text(encoding="utf-8"))
        self.assertEqual(task["status"], "artifact_complete")
        self.assertFalse((Path(task["worktree"]) / ".qteam-learning-outbox").exists())

    def test_stale_launch_requires_explicit_restart(self):
        run = self.init_run()
        self.make_task(run)
        self.start_wave(run)
        workers = run / "workers"
        workers.mkdir(parents=True, exist_ok=True)
        (workers / "T01.json").write_text(json.dumps({
            "schema_version": 1, "task": "T01", "role": "developer",
            "state": "launching", "launch_owner_pid": 999999999,
            "launch_owner_start": "stale", "result": "workers/T01.result.json",
            "started_at": "test", "stdout": "workers/T01.stdout.log",
            "stderr": "workers/T01.stderr.log",
        }), encoding="utf-8")
        rejected = self.run_tool(WORKER, "spawn", "--run", str(run), "--task", "T01",
                                 "--role", "developer")
        self.assertNotEqual(rejected.returncode, 0)
        fake_bin = Path(self.tmp.name) / "bin-restart"
        fake_bin.mkdir()
        fake = fake_bin / "codex"
        fake.write_text(
            "#!/usr/bin/env python3\nimport json, sys\nfrom pathlib import Path\n"
            "if '--version' in sys.argv:\n print('codex-cli test-worker'); raise SystemExit\n"
            "args=sys.argv[1:]; msg='Validation scope: fake worker execution\\nClaim boundary: fake does not validate production behavior'\n"
            "Path(args[args.index('--output-last-message')+1]).write_text(msg)\n"
            "print(json.dumps({'type':'thread.started','thread_id':'worker'}))\nprint(json.dumps({'type':'turn.started'}))\n"
            "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':msg}}))\nprint(json.dumps({'type':'turn.completed','usage':{}}))\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        restarted = self.run_tool(WORKER, "spawn", "--run", str(run), "--task", "T01",
                                  "--role", "developer", "--restart", env=env)
        self.assertEqual(restarted.returncode, 0, restarted.stderr)
        self.run_tool(WORKER, "wait", "--run", str(run), "--task", "T01",
                      "--timeout", "10", env=env, check=True)

    def test_cancel_stale_launch_persists_cancelled_result(self):
        run = self.init_run()
        workers = run / "workers"
        workers.mkdir(parents=True, exist_ok=True)
        (workers / "T01.json").write_text(json.dumps({
            "schema_version": 1, "task": "T01", "role": "developer",
            "state": "launching", "launch_owner_pid": 999999999,
            "launch_owner_start": "stale", "result": "workers/T01.result.json",
            "started_at": "test", "stdout": "workers/T01.stdout.log",
            "stderr": "workers/T01.stderr.log",
        }), encoding="utf-8")
        cancelled = self.run_tool(WORKER, "cancel", "--run", str(run), "--task", "T01")
        self.assertEqual(cancelled.returncode, 0, cancelled.stderr)
        status = self.run_tool(WORKER, "status", "--run", str(run), "--task", "T01")
        self.assertNotEqual(status.returncode, 0)
        self.assertEqual(json.loads(status.stdout)["status"], "cancelled")
        result = json.loads((workers / "T01.result.json").read_text(encoding="utf-8"))
        self.assertTrue(result["cancelled"])

    def test_cancel_rereads_identity_after_wrapper_claim(self):
        run = self.init_run()
        workers = run / "workers"
        workers.mkdir(parents=True, exist_ok=True)
        record_path = workers / "T01.json"
        claimed = {
            "schema_version": 1, "task": "T01", "role": "developer",
            "state": "running", "pid": os.getpid(), "pgid": os.getpgrp(),
            "proc_start": "claimed-after-first-read", "result": "workers/T01.result.json",
            "started_at": "test", "stdout": "workers/T01.stdout.log",
            "stderr": "workers/T01.stderr.log",
        }
        record_path.write_text(json.dumps(claimed), encoding="utf-8")
        spec = importlib.util.spec_from_file_location("qteam_worker_test", WORKER)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(WORKER.parent))
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.pop(0)
        stale = dict(claimed)
        for key in ("pid", "pgid", "proc_start"):
            stale.pop(key, None)
        original_current = module.current
        module.current = lambda _run, _task: (stale, None, "lost")
        try:
            with self.assertRaisesRegex(SystemExit, "refusing to signal reused PGID"):
                module.cmd_cancel(type("Args", (), {"task": "T01"})(), self.repo, run)
        finally:
            module.current = original_current
        self.assertFalse((workers / "T01.result.json").exists())
        self.assertEqual(json.loads(record_path.read_text(encoding="utf-8"))["state"], "running")

    def test_cancel_refuses_stale_reused_process_group(self):
        run = self.init_run()
        workers = run / "workers"
        workers.mkdir(parents=True, exist_ok=True)
        (workers / "T01.json").write_text(json.dumps({
            "schema_version": 1, "task": "T01", "role": "developer",
            "state": "running", "pid": os.getpid(), "pgid": os.getpgrp(),
            "proc_start": "definitely-wrong", "result": "workers/T01.result.json",
            "started_at": "test", "stdout": "workers/T01.stdout.log",
            "stderr": "workers/T01.stderr.log",
        }), encoding="utf-8")
        cancelled = self.run_tool(WORKER, "cancel", "--run", str(run), "--task", "T01")
        self.assertNotEqual(cancelled.returncode, 0)
        self.assertIn("refusing to signal reused PGID", cancelled.stderr)

    def test_harvest_rejects_outbox_root_symlink(self):
        run = self.init_run()
        self.make_task(run, artifact_kind="learning")
        for phase in ("SPEC_READY", "PLAN_READY", "LEARNING_EXPORT"):
            self.run_tool(STATE, "--run", str(run), "phase", phase, check=True)
        fake_bin = Path(self.tmp.name) / "bin-symlink"
        fake_bin.mkdir()
        fake = fake_bin / "codex"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\nfrom pathlib import Path\n"
            "if '--version' in sys.argv:\n print('codex-cli test-worker'); raise SystemExit\n"
            "p=Path('real-outbox'); p.mkdir(); (p/'manifest.json').write_text('{}')\n"
            "Path('.qteam-learning-outbox').symlink_to(p, target_is_directory=True)\n"
            "args=sys.argv[1:]; msg='Validation scope: fake learning outbox\\nClaim boundary: fake does not validate durable knowledge'\n"
            "Path(args[args.index('--output-last-message')+1]).write_text(msg)\n"
            "print(json.dumps({'type':'thread.started','thread_id':'worker'}))\nprint(json.dumps({'type':'turn.started'}))\n"
            "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':msg}}))\nprint(json.dumps({'type':'turn.completed','usage':{}}))\n",
            encoding="utf-8")
        fake.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        self.run_tool(WORKER, "spawn", "--run", str(run), "--task", "T01",
                      "--role", "knowledge-distiller", env=env, check=True)
        self.run_tool(WORKER, "wait", "--run", str(run), "--task", "T01",
                      "--timeout", "10", env=env, check=True)
        harvested = self.run_tool(WORKER, "harvest", "--run", str(run), "--task", "T01")
        self.assertNotEqual(harvested.returncode, 0)
        self.assertIn("root symlink is forbidden", harvested.stderr)


class MethodEvidenceTests(RepoCase):
    def test_experiment_establishes_pending_baseline_and_replays_final_controls(self):
        (self.repo / "score.txt").write_text("1\n", encoding="utf-8")
        self.run_git("add", "score.txt")
        self.run_git("commit", "-m", "add baseline score")
        run = self.init_run()
        contract = {
            "goal": "raise score", "metric": {
                "name": "score", "direction": "higher_is_better",
                "command": "cat score.txt", "baseline": None,
                "minimum_delta": 1,
            },
            "guard_command": "test -s score.txt",
            "holdout_command": "grep -qx '2' score.txt",
            "max_attempts": 3, "plateau_window": 2,
        }
        worktree, _record = self.make_task(
            run, work_kind="experiment", experiment=contract,
            write_set=["score.txt"], verification="grep -qx '2' score.txt",
        )
        self.start_wave(run)
        self.run_tool(STATE, "--run", str(run), "task-status", "T01", "running",
                      check=True)
        (worktree / "score.txt").write_text("2\n", encoding="utf-8")
        self.run_git("add", "score.txt", cwd=worktree)
        self.run_git("commit", "-m", "experiment: raise score", cwd=worktree)
        head = self.run_git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        report = {
            "schema_version": 1, "task": "T01", "goal": "raise score",
            "metric": {
                "name": "score", "direction": "higher_is_better",
                "command": "cat score.txt", "baseline": 1, "final": 2,
                "minimum_delta": 1,
            },
            "guard_command": "test -s score.txt",
            "holdout_command": "grep -qx '2' score.txt",
            "max_attempts": 3, "plateau_window": 2,
            "attempts": [{
                "number": 1, "hypothesis": "replace score with two",
                "status": "kept", "commit": head, "metric": 2,
                "delta": 1, "guard_exit_code": 0,
                "evidence": "metric increased and guard passed",
            }],
            "stop_reason": "goal-met", "final_head": head,
        }
        report_path = worktree / ".qteam-experiment.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        missing = self.run_tool(STATE, "--run", str(run), "verify-task", "T01")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("no independently replayed evidence", missing.stderr)
        ingested = self.run_tool(
            STATE, "--run", str(run), "experiment-put", "T01",
            "--file", str(report_path),
        )
        self.assertEqual(ingested.returncode, 0, ingested.stderr)
        self.assertFalse(report_path.exists())
        task = json.loads((run / "tasks/T01.json").read_text(encoding="utf-8"))
        self.assertEqual(task["experiment_verification"]["baseline"], 1)
        self.assertEqual(task["experiment_verification"]["final"], 2)
        verified = self.run_tool(STATE, "--run", str(run), "verify-task", "T01")
        self.assertEqual(verified.returncode, 0, verified.stderr)

    def test_experiment_rejects_failed_held_out_acceptance_without_consuming_report(self):
        (self.repo / "score.txt").write_text("1\n", encoding="utf-8")
        self.run_git("add", "score.txt")
        self.run_git("commit", "-m", "add baseline score")
        run = self.init_run()
        contract = {
            "goal": "raise score", "metric": {
                "name": "score", "direction": "higher_is_better",
                "command": "cat score.txt", "baseline": 1,
                "minimum_delta": 1,
            },
            "guard_command": "test -s score.txt",
            "holdout_command": "grep -qx '3' score.txt",
            "max_attempts": 4, "plateau_window": 2,
        }
        worktree, _record = self.make_task(
            run, work_kind="experiment", experiment=contract,
            write_set=["score.txt"], verification="true",
        )
        self.start_wave(run)
        self.run_tool(STATE, "--run", str(run), "task-status", "T01", "running",
                      check=True)
        (worktree / "score.txt").write_text("2\n", encoding="utf-8")
        self.run_git("add", "score.txt", cwd=worktree)
        self.run_git("commit", "-m", "experiment: candidate", cwd=worktree)
        head = self.run_git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        report = {
            "schema_version": 1, "task": "T01", "goal": "raise score",
            "metric": {
                "name": "score", "direction": "higher_is_better",
                "command": "cat score.txt", "baseline": 1, "final": 2,
                "minimum_delta": 1,
            },
            "guard_command": "test -s score.txt",
            "holdout_command": "grep -qx '3' score.txt",
            "max_attempts": 4, "plateau_window": 2,
            "attempts": [{
                "number": 1, "hypothesis": "raise to two", "status": "kept",
                "commit": head, "metric": 2, "delta": 1,
                "guard_exit_code": 0, "evidence": "working metric passed",
            }],
            "stop_reason": "budget", "final_head": head,
        }
        report_path = worktree / ".qteam-experiment.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        no_op = {
            "hypothesis": "bounded candidate produced no change", "status": "no-op",
            "commit": None, "metric": None, "delta": None,
            "guard_exit_code": None, "evidence": "no candidate change",
        }
        report["attempts"].extend([
            {"number": 2, **no_op}, {"number": 3, **no_op},
            {"number": 4, **no_op},
        ])
        report_path.write_text(json.dumps(report), encoding="utf-8")
        continued = self.run_tool(
            STATE, "--run", str(run), "experiment-put", "T01",
            "--file", str(report_path),
        )
        self.assertNotEqual(continued.returncode, 0)
        self.assertIn("continued after frozen plateau", continued.stderr)
        report["attempts"] = report["attempts"][:3]
        report_path.write_text(json.dumps(report), encoding="utf-8")
        disguised = self.run_tool(
            STATE, "--run", str(run), "experiment-put", "T01",
            "--file", str(report_path),
        )
        self.assertNotEqual(disguised.returncode, 0)
        self.assertIn("requires stop_reason=plateau", disguised.stderr)
        report["attempts"] = report["attempts"][:1]
        report["stop_reason"] = "budget-exhausted"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        invalid_reason = self.run_tool(
            STATE, "--run", str(run), "experiment-put", "T01",
            "--file", str(report_path),
        )
        self.assertNotEqual(invalid_reason.returncode, 0)
        self.assertIn("invalid experiment stop_reason", invalid_reason.stderr)
        report["stop_reason"] = "goal-met"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        rejected = self.run_tool(
            STATE, "--run", str(run), "experiment-put", "T01",
            "--file", str(report_path),
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("held-out experiment acceptance failed", rejected.stderr)
        self.assertTrue(report_path.is_file())
        task = json.loads((run / "tasks/T01.json").read_text(encoding="utf-8"))
        self.assertNotIn("experiment_evidence", task)

    def test_tdd_rejects_red_commit_that_changes_production(self):
        run = self.init_run()
        seam = {
            "id": "app-exists", "behavior": "app exists",
            "test_paths": ["tests/check.txt"], "command": "false",
            "red_pattern": "EXPECTED_RED",
        }
        worktree, _ = self.make_task(
            run, work_kind="feature", write_set=["app.txt", "tests/**"],
            test_seams=[seam],
        )
        self.start_wave(run)
        self.run_tool(STATE, "--run", str(run), "task-status", "T01", "running",
                      check=True)
        (worktree / "app.txt").write_text("premature production\n", encoding="utf-8")
        self.run_git("add", "app.txt", cwd=worktree)
        self.run_git("commit", "-m", "bad red", cwd=worktree)
        red = self.run_git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        (worktree / "tests").mkdir()
        (worktree / "tests/check.txt").write_text("later test\n", encoding="utf-8")
        self.run_git("add", "tests/check.txt", cwd=worktree)
        self.run_git("commit", "-m", "bad green", cwd=worktree)
        green = self.run_git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        replay = self.run_tool(
            STATE, "--run", str(run), "verify-tdd-cycle", "T01",
            "--seam", "app-exists", "--red-commit", red,
            "--green-commit", green,
        )
        self.assertNotEqual(replay.returncode, 0)
        self.assertIn("must change only its test_paths", replay.stderr)

    def test_tdd_rejects_green_commit_that_only_weakens_the_test(self):
        run = self.init_run()
        seam = {
            "id": "app-exists", "behavior": "app exists",
            "test_paths": ["tests/check.sh"], "command": "sh tests/check.sh",
            "red_pattern": "APP_MISSING",
        }
        worktree, _ = self.make_task(
            run, work_kind="feature", write_set=["app.txt", "tests/**"],
            test_seams=[seam],
        )
        self.start_wave(run)
        self.run_tool(STATE, "--run", str(run), "task-status", "T01", "running",
                      check=True)
        (worktree / "tests").mkdir()
        check = worktree / "tests/check.sh"
        check.write_text("#!/bin/sh\necho APP_MISSING\nexit 1\n", encoding="utf-8")
        self.run_git("add", "tests/check.sh", cwd=worktree)
        self.run_git("commit", "-m", "test: prove missing app", cwd=worktree)
        red = self.run_git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        check.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.run_git("add", "tests/check.sh", cwd=worktree)
        self.run_git("commit", "-m", "test: weaken assertion", cwd=worktree)
        green = self.run_git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        replay = self.run_tool(
            STATE, "--run", str(run), "verify-tdd-cycle", "T01",
            "--seam", "app-exists", "--red-commit", red,
            "--green-commit", green,
        )
        self.assertNotEqual(replay.returncode, 0)
        self.assertIn("GREEN commit must not change declared test paths", replay.stderr)

    def test_tdd_rejects_test_changes_after_a_proven_green(self):
        run = self.init_run()
        seam = {
            "id": "app-exists", "behavior": "app exists",
            "test_paths": ["tests/check.sh"], "command": "sh tests/check.sh",
            "red_pattern": "APP_MISSING",
        }
        worktree, _ = self.make_task(
            run, work_kind="feature", write_set=["app.txt", "tests/**"],
            verification="sh tests/check.sh", test_seams=[seam],
        )
        self.start_wave(run)
        self.run_tool(STATE, "--run", str(run), "task-status", "T01", "running",
                      check=True)
        (worktree / "tests").mkdir()
        check = worktree / "tests/check.sh"
        check.write_text(
            "#!/bin/sh\nif test -f app.txt; then exit 0; fi\n"
            "echo APP_MISSING\nexit 1\n",
            encoding="utf-8",
        )
        self.run_git("add", "tests/check.sh", cwd=worktree)
        self.run_git("commit", "-m", "test: prove missing app", cwd=worktree)
        red = self.run_git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        (worktree / "app.txt").write_text("implemented\n", encoding="utf-8")
        self.run_git("add", "app.txt", cwd=worktree)
        self.run_git("commit", "-m", "feat: implement app", cwd=worktree)
        green = self.run_git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        self.run_tool(
            STATE, "--run", str(run), "verify-tdd-cycle", "T01",
            "--seam", "app-exists", "--red-commit", red,
            "--green-commit", green, check=True,
        )
        check.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.run_git("add", "tests/check.sh", cwd=worktree)
        self.run_git("commit", "-m", "test: weaken after green", cwd=worktree)
        verified = self.run_tool(STATE, "--run", str(run), "verify-task", "T01")
        self.assertNotEqual(verified.returncode, 0)
        self.assertIn("changed after GREEN", verified.stderr)

    def test_all_approved_tdd_seams_are_replayed_red_then_green(self):
        run = self.init_run()
        seam = {
            "id": "app-exists",
            "behavior": "the public check reports the implemented app",
            "test_paths": ["tests/check.sh"],
            "command": "sh tests/check.sh",
            "red_pattern": "APP_MISSING",
        }
        abbreviated_base = self.run_git("rev-parse", "--short=8", "HEAD").stdout.strip()
        worktree, _ = self.make_task(
            run, work_kind="feature", write_set=["app.txt", "tests/**"],
            verification="sh tests/check.sh", test_seams=[seam],
            base_commit=abbreviated_base,
        )
        self.start_wave(run)
        self.run_tool(STATE, "--run", str(run), "task-status", "T01", "running",
                      check=True)
        (worktree / "tests").mkdir()
        (worktree / "tests/check.sh").write_text(
            "#!/bin/sh\n"
            "if test -f app.txt; then exit 0; fi\n"
            "echo APP_MISSING\n"
            "exit 1\n",
            encoding="utf-8",
        )
        self.run_git("add", "tests/check.sh", cwd=worktree)
        self.run_git("commit", "-m", "test: prove missing app", cwd=worktree)
        red = self.run_git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        (worktree / "app.txt").write_text("implemented\n", encoding="utf-8")
        self.run_git("add", "app.txt", cwd=worktree)
        self.run_git("commit", "-m", "feat: implement app", cwd=worktree)
        green = self.run_git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        replay = self.run_tool(
            STATE, "--run", str(run), "verify-tdd-cycle", "T01",
            "--seam", "app-exists", "--red-commit", red,
            "--green-commit", green,
        )
        self.assertEqual(replay.returncode, 0, replay.stderr)
        evidence = json.loads(replay.stdout)
        self.assertEqual(evidence["seam_id"], "app-exists")
        self.assertNotEqual(evidence["red_exit_code"], 0)
        self.assertEqual(evidence["green_exit_code"], 0)
        verified = self.run_tool(STATE, "--run", str(run), "verify-task", "T01")
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.run_tool(STATE, "--run", str(run), "phase", "WAVE_VALIDATING",
                      check=True)
        gated = self.run_tool(CHECK, "--run", str(run), "--task", "T01")
        self.assertEqual(gated.returncode, 0, gated.stdout + gated.stderr)

    def test_diagnosis_replays_frozen_repro_and_records_ranked_chain(self):
        run = self.init_run()
        command = ("if test -f app.txt; then exit 0; fi; "
                   "echo BUG_REPRO; exit 1")
        worktree, record = self.make_task(
            run, work_kind="debug", verification="test -f app.txt",
            diagnosis_command=command, failure_pattern="BUG_REPRO",
        )
        self.start_wave(run)
        self.run_tool(STATE, "--run", str(run), "task-status", "T01", "running",
                      check=True)
        report = {
            "schema_version": 1,
            "repro_commit": record["base_commit"],
            "feedback_loop": command,
            "observed_red": "BUG_REPRO at the public command boundary",
            "minimized_repro": "repository without app.txt",
            "hypotheses": [
                {"rank": 1, "statement": "file is never created",
                 "prediction": "app.txt is absent", "check": "test -f app.txt",
                 "outcome": "confirmed absent"},
                {"rank": 2, "statement": "wrong directory",
                 "prediction": "pwd differs", "check": "inspect cwd",
                 "outcome": "falsified"},
                {"rank": 3, "statement": "permission denial",
                 "prediction": "create returns EACCES", "check": "inspect permissions",
                 "outcome": "falsified"},
            ],
            "root_cause": "the implementation never creates app.txt",
            "causal_chain": ["missing writer", "public file boundary", "BUG_REPRO"],
            "fix_boundary": "create app.txt in the task implementation",
            "cleanup": "remove probes and rerun the original command",
            "preventive_lesson": "cover the public file contract before implementation",
        }
        report_path = worktree / ".qteam-diagnosis.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        ingested = self.run_tool(
            STATE, "--run", str(run), "diagnosis-put", "T01",
            "--file", str(report_path),
        )
        self.assertEqual(ingested.returncode, 0, ingested.stderr)
        self.assertFalse(report_path.exists())
        task = json.loads((run / "tasks/T01.json").read_text(encoding="utf-8"))
        self.assertNotEqual(task["diagnosis_evidence"]["repro_exit_code"], 0)
        self.assertEqual(task["diagnosis_evidence"]["failure_pattern"], "BUG_REPRO")
        (worktree / "app.txt").write_text("fixed\n", encoding="utf-8")
        self.run_git("add", "app.txt", cwd=worktree)
        self.run_git("commit", "-m", "fix: create app", cwd=worktree)
        verified = self.run_tool(STATE, "--run", str(run), "verify-task", "T01")
        self.assertEqual(verified.returncode, 0, verified.stderr)
        task = json.loads((run / "tasks/T01.json").read_text(encoding="utf-8"))
        self.assertEqual(task["diagnosis_evidence"]["green_exit_code"], 0)
        self.assertEqual(task["diagnosis_evidence"]["green_head"], self.run_git(
            "rev-parse", "HEAD", cwd=worktree).stdout.strip())

    def test_generic_verification_cannot_hide_original_loop_still_red(self):
        run = self.init_run()
        command = "echo STILL_BROKEN; exit 7"
        worktree, record = self.make_task(
            run, work_kind="debug", verification="true",
            diagnosis_command=command, failure_pattern="STILL_BROKEN",
        )
        self.start_wave(run)
        self.run_tool(STATE, "--run", str(run), "task-status", "T01", "running",
                      check=True)
        report = {
            "schema_version": 1, "repro_commit": record["base_commit"],
            "feedback_loop": command, "observed_red": "STILL_BROKEN",
            "minimized_repro": "one command",
            "hypotheses": [
                {"rank": index, "statement": f"cause {index}",
                 "prediction": "loop remains red", "check": "run command",
                 "outcome": "observed"}
                for index in range(1, 4)
            ],
            "root_cause": "unfixed source", "causal_chain": ["source", "symptom"],
            "fix_boundary": "source", "cleanup": "no debug probes",
            "preventive_lesson": "keep the original feedback command in verification",
        }
        report_path = worktree / ".qteam-diagnosis.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        self.run_tool(STATE, "--run", str(run), "diagnosis-put", "T01",
                      "--file", str(report_path), check=True)
        verified = self.run_tool(STATE, "--run", str(run), "verify-task", "T01")
        self.assertEqual(verified.returncode, 7)
        self.assertIn("STILL_BROKEN", (run / "verifications/T01-diagnosis-green.log").read_text(
            encoding="utf-8"))
        task = json.loads((run / "tasks/T01.json").read_text(encoding="utf-8"))
        self.assertEqual(task["diagnosis_evidence"]["green_exit_code"], 7)
        self.assertEqual(task["verification_evidence"], [])

    def test_leftover_marked_debug_probe_blocks_gate(self):
        run = self.init_run()
        worktree, _ = self.make_task(run)
        self.start_wave(run)
        self.run_tool(STATE, "--run", str(run), "task-status", "T01", "running",
                      check=True)
        (worktree / "app.txt").write_text("[QTEAM-DEBUG-probe.1]\n",
                                          encoding="utf-8")
        self.run_git("add", "app.txt", cwd=worktree)
        self.run_git("commit", "-m", "leave probe", cwd=worktree)
        self.run_tool(STATE, "--run", str(run), "verify-task", "T01", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "WAVE_VALIDATING",
                      check=True)
        gated = self.run_tool(CHECK, "--run", str(run), "--task", "T01")
        self.assertEqual(gated.returncode, 1)
        self.assertIn("DEBUG instrumentation remains", gated.stdout)


class GateTests(RepoCase):
    def test_empty_diff_and_undeclared_shared_surface_fail(self):
        empty = self.run_tool(
            CHECK, "--base", "HEAD", "--head", "HEAD", "--write-set", "**"
        )
        self.assertEqual(empty.returncode, 1)
        self.assertIn("EMPTY diff", empty.stdout)
        (self.repo / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
        self.run_git("add", "Makefile")
        self.run_git("commit", "-m", "shared")
        shared = self.run_tool(
            CHECK, "--base", "HEAD^", "--head", "HEAD", "--write-set", "Makefile"
        )
        self.assertEqual(shared.returncode, 1)
        self.assertIn("UNDECLARED shared surfaces", shared.stdout)
        allowed = self.run_tool(
            CHECK, "--base", "HEAD^", "--head", "HEAD", "--write-set", "Makefile",
            "--allow-shared-surface", "Makefile",
        )
        self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)

    def test_task_mode_requires_verification_evidence(self):
        run = self.init_run()
        worktree, record = self.make_task(run)
        self.start_wave(run)
        self.run_tool(STATE, "--run", str(run), "task-status", "T01", "running", check=True)
        (worktree / "app.txt").write_text("done\n", encoding="utf-8")
        self.run_git("add", "app.txt", cwd=worktree)
        self.run_git("commit", "-m", "task", cwd=worktree)
        self.run_tool(STATE, "--run", str(run), "phase", "WAVE_VALIDATING", check=True)
        missing = self.run_tool(CHECK, "--run", str(run), "--task", "T01")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("VERIFICATION evidence missing", missing.stdout)
        verified = self.run_tool(STATE, "--run", str(run), "verify-task", "T01")
        self.assertEqual(verified.returncode, 0, verified.stderr)
        passed = self.run_tool(CHECK, "--run", str(run), "--task", "T01")
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)


class ReviewTests(RepoCase):
    def test_review_packets_pin_judge_profile_trajectory_and_calibration(self):
        run = self.init_run(
            "judge-run", "--review-model-standard", "gpt-5.6-sol"
        )
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "spec", "--base", "HEAD", "--head", "HEAD",
            "--spec-source", "README.md", check=True,
        )
        ledger_path = run / "reviews/wave-1-spec.json"
        packet = json.loads(ledger_path.read_text(encoding="utf-8"))["packet"]
        self.assertEqual(packet["schema_version"], 3)
        self.assertEqual(packet["review_execution"]["model"], "gpt-5.6-sol")
        self.assertEqual(packet["review_execution"]["provider"], "openai")
        self.assertEqual(packet["calibration"]["axis"], "spec")
        self.assertEqual(packet["trajectory"]["tasks"], [])
        self.assertNotIn("stdout", json.dumps(packet["trajectory"]))
        self.complete_review(
            run, ledger_path, "spec", "spec-reviewer", "judge-session"
        )
        review_trace = json.loads((
            Path(self.tmp.name) / "review-judge-session.trace.json"
        ).read_text(encoding="utf-8"))
        self.assertIn('model_provider="openai"', review_trace["args"])
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        receipt_path = run / ledger["attestation"]["receipt"]
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(
            receipt["runner"]["version"], packet["runner"]["version"]
        )
        self.assertEqual(
            receipt["calibration_sha256"], packet["calibration"]["sha256"]
        )
        stdout_log = run / receipt["stdout_log"]
        original_trace = stdout_log.read_bytes()
        stdout_log.write_bytes(original_trace + b"{}\n")
        tampered = self.run_tool(
            REVIEW, "--run", str(run), "check", "--wave", "1", "--head", "HEAD"
        )
        self.assertNotEqual(tampered.returncode, 0)
        self.assertIn("completed review evaluation failure",
                      tampered.stdout + tampered.stderr)
        stdout_log.write_bytes(original_trace)

        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "standards", "--base", "HEAD", "--head", "HEAD",
            "--standards-source", "README.md", check=True,
        )
        wrong = self.complete_review(
            run, run / "reviews/wave-1-standards.json", "standards",
            "standards-reviewer", "bad-calibration",
            result_payload={
                "axis": "standards", "verdict": "pass", "findings": [],
                "resolved_ids": [], "invalid_ids": [], "upheld_ids": [],
                "invalid_evidence": {},
                "calibration_results": {
                    "cal-standards-01": "pass",
                    "cal-standards-02": "pass",
                },
            },
            check=False,
        )
        self.assertNotEqual(wrong.returncode, 0)
        self.assertIn("calibration", wrong.stderr)

    def test_review_commit_rechecks_publication_seal_inside_lock(self):
        run = self.init_run()
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "spec", "--base", "HEAD", "--head", "HEAD",
            "--spec-source", "README.md", check=True,
        )
        ledger = run / "reviews/wave-1-spec.json"
        before = ledger.read_bytes()
        state_path = run / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["publication_seal"] = {
            "status": "sealed", "purpose": "publish",
            "head_sha": self.run_git("rev-parse", "HEAD").stdout.strip(),
            "decision_sha256": "0" * 64,
            "authorization_sha256": "1" * 64, "sealed_at": "test",
        }
        state_path.write_text(json.dumps(state), encoding="utf-8")

        sys.path.insert(0, str(PLUGIN / "bin"))
        try:
            spec = importlib.util.spec_from_file_location(
                "qteam_review_commit_guard_test", REVIEW
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            sys.path.pop(0)
        args = SimpleNamespace(**{
            "ledger": str(ledger), "severity": "P1", "id": "F-LATE",
            "title": "late finding", "body": "late evidence",
            "impact": "sealed authorization would change",
            "fix_direction": "reject the write", "file": None, "line": None,
            "owner": "fixer", "reviewer": "late-reviewer",
        })
        with self.assertRaisesRegex(SystemExit, "publication seal freezes review"):
            module.cmd_add(args, self.repo, None)
        self.assertEqual(ledger.read_bytes(), before)

    def test_unstarted_review_packet_can_refresh_runner_once(self):
        run = self.init_run("runner-refresh")
        args = (
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "spec", "--base", "HEAD", "--head", "HEAD",
            "--spec-source", "README.md",
        )
        self.run_tool(*args, check=True)
        ledger = run / "reviews/wave-1-spec.json"
        original = json.loads(ledger.read_text(encoding="utf-8"))["packet"]["runner"]
        fake_bin = Path(self.tmp.name) / "runner-refresh-bin"
        fake_bin.mkdir()
        fake = fake_bin / "codex"
        fake.write_text(
            "#!/usr/bin/env python3\nimport os, sys\n"
            "if '--version' in sys.argv: print(os.environ['RUNNER_VERSION'])\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        env["RUNNER_VERSION"] = original["version"] + "-new"
        stale = self.run_tool(*args, env=env)
        self.assertNotEqual(stale.returncode, 0)
        refreshed = self.run_tool(*args, "--refresh-runner", env=env)
        self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
        packet = json.loads(ledger.read_text(encoding="utf-8"))["packet"]
        self.assertEqual(packet["runner"]["version"], env["RUNNER_VERSION"])

        receipts = run / "reviews/receipts"
        receipts.mkdir()
        (receipts / "attempt.json").write_text(json.dumps({
            "ledger": "reviews/wave-1-spec.json", "status": "failed",
        }), encoding="utf-8")
        env["RUNNER_VERSION"] += "-later"
        blocked = self.run_tool(*args, "--refresh-runner", env=env)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("immutable inputs", blocked.stderr)

    def test_review_rejects_wave_absent_from_derived_run_policy(self):
        run = self.init_run()
        self.make_task(run, work_kind="config", write_set=["src/auth/**"])
        self.run_git("branch", "agent/test-run/integration", "HEAD")
        created = self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "999",
            "--axis", "spec", "--base", "HEAD", "--head", "HEAD",
            "--spec-source", "README.md",
        )
        self.assertNotEqual(created.returncode, 0)
        self.assertIn("absent from run policy", created.stderr)

    def test_reviewer_runner_rejects_malformed_finding_ids(self):
        run = self.init_run()
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "spec", "--base", "HEAD", "--head", "HEAD",
            "--spec-source", "README.md", check=True,
        )
        ledger = run / "reviews/wave-1-spec.json"
        malformed = self.complete_review(
            run, ledger, "spec", "spec-reviewer", "malformed-session",
            result_payload={"axis": "spec", "verdict": "pass", "findings": [{}],
                            "resolved_ids": [], "invalid_ids": [],
                            "invalid_evidence": {}, "upheld_ids": []},
            check=False,
        )
        self.assertNotEqual(malformed.returncode, 0)
        self.assertIn("non-empty id", malformed.stderr)

    def test_reviewer_result_rejects_unbounded_extra_payload_fields(self):
        run = self.init_run("review-redaction")
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "spec", "--base", "HEAD", "--head", "HEAD",
            "--spec-source", "README.md", check=True,
        )
        ledger = run / "reviews/wave-1-spec.json"
        blocked = self.complete_review(
            run, ledger, "spec", "spec-reviewer", "extra-payload",
            result_payload={
                "axis": "spec", "verdict": "pass", "findings": [],
                "resolved_ids": [], "invalid_ids": [], "upheld_ids": [],
                "invalid_evidence": {}, "raw_tool_output": "TOP-SECRET-RAW",
            }, check=False,
        )
        self.assertNotEqual(blocked.returncode, 0)
        saved = json.loads(ledger.read_text(encoding="utf-8"))
        self.assertNotIn("attestation", saved)

    def test_deep_reviewer_json_fails_with_durable_receipt(self):
        run = self.init_run("deep-review-result")
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "spec", "--base", "HEAD", "--head", "HEAD",
            "--spec-source", "README.md", check=True,
        )
        ledger = run / "reviews/wave-1-spec.json"
        blocked = self.complete_review(
            run, ledger, "spec", "spec-reviewer", "deep-result",
            check=False,
            run_env_extra={
                "FAKE_REVIEW_RESULT": "[" * 2000 + "0" + "]" * 2000,
            },
        )
        self.assertNotEqual(blocked.returncode, 0)
        self.assertNotIn("Traceback", blocked.stderr)
        receipt = json.loads((
            run / "reviews/receipts/deep-result.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["exit_code"], 65)

    def test_reviewer_with_escalated_trajectory_cannot_attest(self):
        run = self.init_run("review-escalation")
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "spec", "--base", "HEAD", "--head", "HEAD",
            "--spec-source", "README.md", check=True,
        )
        ledger = run / "reviews/wave-1-spec.json"
        blocked = self.complete_review(
            run, ledger, "spec", "spec-reviewer", "escalated-review",
            check=False, run_env_extra={"FAKE_REVIEW_FAILED_CALLS": "5"},
        )
        self.assertNotEqual(blocked.returncode, 0)
        receipt = json.loads((
            run / "reviews/receipts/escalated-review.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["trajectory"]["disposition"], "escalate")

    def test_reviewer_runner_rejects_finding_id_unusable_by_fix_tasks(self):
        run = self.init_run()
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "spec", "--base", "HEAD", "--head", "HEAD",
            "--spec-source", "README.md", check=True,
        )
        ledger = run / "reviews/wave-1-spec.json"
        for index, finding_id in enumerate(("spec:F-1", "F..1", "É-1"), 1):
            blocked = self.complete_review(
                run, ledger, "spec", "spec-reviewer", f"unsafe-finding-{index}",
                result_payload={
                    "axis": "spec", "verdict": "needs-fix", "resolved_ids": [],
                    "invalid_ids": [], "invalid_evidence": {}, "upheld_ids": [],
                    "findings": [{
                        "id": finding_id, "severity": "P1", "title": "unsafe id",
                        "review_evidence": "evidence", "impact": "impact",
                        "fix_direction": "fix", "owner": "fixer",
                    }],
                }, check=False,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("safe identifiers", blocked.stderr)
        self.assertEqual(json.loads(ledger.read_text(encoding="utf-8"))["findings"], [])

    def test_reviewer_runner_rejects_schema_invalid_finding_location(self):
        run = self.init_run()
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "spec", "--base", "HEAD", "--head", "HEAD",
            "--spec-source", "README.md", check=True,
        )
        ledger = run / "reviews/wave-1-spec.json"
        blocked = self.complete_review(
            run, ledger, "spec", "spec-reviewer", "invalid-location",
            result_payload={
                "axis": "spec", "verdict": "needs-fix", "resolved_ids": [],
                "invalid_ids": [], "upheld_ids": [], "invalid_evidence": {},
                "findings": [{
                    "id": "F-LOCATION", "severity": "P1", "title": "bad location",
                    "review_evidence": "evidence", "impact": "impact",
                    "fix_direction": "fix", "owner": "fixer",
                    "file": 123, "line": "bad",
                }],
            }, check=False,
        )
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("file/line", blocked.stderr)
        self.assertEqual(json.loads(ledger.read_text(encoding="utf-8"))["findings"], [])

    def test_manual_finding_add_rejects_nonpositive_line(self):
        run = self.init_run()
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "spec", "--base", "HEAD", "--head", "HEAD",
            "--spec-source", "README.md", check=True,
        )
        ledger = run / "reviews/wave-1-spec.json"
        for line in ("0", "-1"):
            blocked = self.run_tool(
                REVIEW, "add", "--ledger", str(ledger), "--id", f"F-LINE-{line}",
                "--severity", "P1", "--title", "bad line", "--body", "evidence",
                "--impact", "impact", "--fix-direction", "fix", "--file", "app.py",
                "--line", line, "--owner", "fixer", "--reviewer", "spec-reviewer",
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("file/line", blocked.stderr)
        self.assertEqual(json.loads(ledger.read_text(encoding="utf-8"))["findings"], [])

    def test_manual_finding_add_rejects_empty_schema_fields(self):
        run = self.init_run()
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "spec", "--base", "HEAD", "--head", "HEAD",
            "--spec-source", "README.md", check=True,
        )
        ledger = run / "reviews/wave-1-spec.json"
        flag_indexes = {
            "--title": 8, "--body": 10, "--impact": 12,
            "--fix-direction": 14, "--owner": 16, "--reviewer": 18,
        }
        base_args = [
            "add", "--ledger", str(ledger), "--id", "F-EMPTY",
            "--severity", "P1", "--title", "title", "--body", "evidence",
            "--impact", "impact", "--fix-direction", "fix", "--owner", "fixer",
            "--reviewer", "spec-reviewer",
        ]
        for flag, value_index in flag_indexes.items():
            args = list(base_args)
            args[value_index] = ""
            blocked = self.run_tool(REVIEW, *args)
            self.assertNotEqual(blocked.returncode, 0, flag)
            self.assertIn("non-empty strings", blocked.stderr)
        self.assertEqual(json.loads(ledger.read_text(encoding="utf-8"))["findings"], [])

    def test_needs_fix_runner_receipt_cannot_complete_a_ledger(self):
        run = self.init_run()
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "spec", "--base", "HEAD", "--head", "HEAD",
            "--spec-source", "README.md", check=True,
        )
        ledger = run / "reviews/wave-1-spec.json"
        completed = self.complete_review(
            run, ledger, "spec", "spec-reviewer", "needs-fix-session",
            result_payload={
                "axis": "spec", "verdict": "needs-fix",
                "findings": [{"id": "F-NEEDS-FIX", "severity": "P1",
                              "title": "needs fix", "review_evidence": "evidence",
                              "impact": "behavior is wrong",
                              "fix_direction": "correct the behavior", "owner": "fixer"}],
                "resolved_ids": [],
                "invalid_ids": [], "invalid_evidence": {}, "upheld_ids": [],
            },
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        receipt = json.loads((run / "reviews/receipts/needs-fix-session.json").read_text(
            encoding="utf-8"))
        self.assertEqual(receipt["status"], "needs-fix")
        self.assertFalse(json.loads(ledger.read_text(encoding="utf-8"))["completed_at"])

    def test_interrupted_needs_fix_recording_recovers_as_one_transaction(self):
        run = self.init_run()
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "spec", "--base", "HEAD", "--head", "HEAD",
            "--spec-source", "README.md", check=True,
        )
        ledger = run / "reviews/wave-1-spec.json"
        payload = {
            "axis": "spec", "verdict": "needs-fix", "resolved_ids": [],
            "invalid_ids": [], "invalid_evidence": {}, "upheld_ids": [],
            "findings": [{
                "id": "F-ATOMIC", "severity": "P1", "title": "atomic finding",
                "review_evidence": "evidence", "impact": "impact",
                "fix_direction": "fix it", "owner": "fixer",
            }],
        }
        interrupted = self.complete_review(
            run, ledger, "spec", "spec-reviewer", "atomic-needs-fix",
            result_payload=payload, check=False,
            run_env_extra={"QTEAM_FAULT_AFTER_REVIEW_WRITES": "1"},
        )
        self.assertNotEqual(interrupted.returncode, 0)
        self.assertTrue((run / "reviews/.closure-transaction.json").exists())
        retried = self.complete_review(
            run, ledger, "spec", "spec-reviewer", "atomic-needs-fix",
            result_payload=payload, check=False,
        )
        self.assertNotEqual(retried.returncode, 0)
        self.assertFalse((run / "reviews/.closure-transaction.json").exists())
        receipt = json.loads((run / "reviews/receipts/atomic-needs-fix.json").read_text(
            encoding="utf-8"))
        saved = json.loads(ledger.read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "needs-fix")
        self.assertEqual([item["id"] for item in saved["findings"]], ["F-ATOMIC"])
        self.assertEqual(saved["review_attempts"][0]["session_id"],
                         "atomic-needs-fix")

    def test_review_wal_rejects_unbound_finding_injection(self):
        run = self.init_run("review-wal-forgery")
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "spec", "--base", "HEAD", "--head", "HEAD",
            "--spec-source", "README.md", check=True,
        )
        ledger_path = run / "reviews/wave-1-spec.json"
        original = json.loads(ledger_path.read_text(encoding="utf-8"))
        forged = json.loads(json.dumps(original))
        forged["findings"].append({"id": "FORGED", "status": "open"})
        forged["review_attempts"] = [{
            "status": "needs-fix", "finding_ids": ["FORGED"],
        }]
        intent = run / "reviews/.closure-transaction.json"
        intent.write_text(json.dumps({
            "schema_version": 1, "txid": "a" * 32,
            "writes": {"wave-1-spec.json": forged},
        }), encoding="utf-8")
        rejected = self.run_tool(
            REVIEW, "add", "--ledger", str(ledger_path), "--id", "F-REAL",
            "--severity", "P1", "--title", "real", "--body", "evidence",
            "--impact", "impact", "--fix-direction", "fix", "--owner", "fixer",
            "--reviewer", "reviewer",
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn("Traceback", rejected.stderr)
        self.assertEqual(json.loads(ledger_path.read_text(encoding="utf-8")), original)
        self.assertTrue(intent.exists())

    def test_review_wal_recovers_after_final_completion_write(self):
        run = self.init_run("review-final-write-recovery")
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "spec", "--base", "HEAD", "--head", "HEAD",
            "--spec-source", "README.md", check=True,
        )
        ledger = run / "reviews/wave-1-spec.json"
        fault = os.environ.copy()
        fault["QTEAM_FAULT_AFTER_REVIEW_WRITES"] = "1"
        interrupted = self.complete_review(
            run, ledger, "spec", "spec-reviewer", "final-write-crash",
            check=False, complete_env=fault,
        )
        self.assertNotEqual(interrupted.returncode, 0)
        receipt = run / "reviews/receipts/final-write-crash.json"
        recovered = self.run_tool(
            REVIEW, "complete", "--ledger", str(ledger),
            "--receipt", str(receipt),
        )
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertFalse((run / "reviews/.closure-transaction.json").exists())

    def test_review_create_rejects_symlinked_nested_artifact_directory(self):
        run = self.init_run("review-dir-symlink")
        outside = Path(self.tmp.name) / "outside-review-sources"
        outside.mkdir()
        (run / "reviews").mkdir()
        (run / "reviews/sources").symlink_to(outside, target_is_directory=True)
        rejected = self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "spec", "--base", "HEAD", "--head", "HEAD",
            "--spec-source", "README.md",
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertFalse(list(outside.iterdir()))

    def test_reviewer_rejects_non_object_packet_trajectory_without_traceback(self):
        run = self.init_run("review-malformed-trajectory")
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "spec", "--base", "HEAD", "--head", "HEAD",
            "--spec-source", "README.md", check=True,
        )
        ledger = run / "reviews/wave-1-spec.json"
        value = json.loads(ledger.read_text(encoding="utf-8"))
        value["packet"]["trajectory"] = []
        ledger.write_text(json.dumps(value), encoding="utf-8")
        rejected = self.run_tool(
            REVIEW, "run", "--ledger", str(ledger), "--reviewer", "reviewer",
            "--session-id", "malformed-trajectory",
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn("Traceback", rejected.stderr)

    def test_tampered_frozen_source_blocks_reviewer_run(self):
        run = self.init_run()
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "spec", "--base", "HEAD", "--head", "HEAD",
            "--spec-source", "README.md", check=True,
        )
        ledger = run / "reviews/wave-1-spec.json"
        packet = json.loads(ledger.read_text(encoding="utf-8"))["packet"]
        (run / packet["spec_sources"][0]["snapshot"]).write_text(
            "tampered\n", encoding="utf-8"
        )
        blocked = self.complete_review(
            run, ledger, "spec", "spec-reviewer", "tampered-source", check=False
        )
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("snapshot integrity", blocked.stderr)

    def test_completed_review_rejects_tampered_artifact_lint_packet(self):
        run = self.init_run()
        integration, _worktree = self.prepare_ready_run(run)
        ledger_path = run / "reviews/wave-1-spec.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger["packet"]["artifact_lint"]["warnings"].append({
            "code": "SPEC999", "severity": "warning", "message": "tampered",
        })
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
        blocked = self.run_tool(
            REVIEW, "--run", str(run), "check", "--wave", "1",
            "--head", integration,
        )
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("ledger integrity failure", blocked.stdout)

    def test_interrupted_multi_ledger_closure_recovers_idempotently(self):
        run = self.init_run()
        original = self.run_git("rev-parse", "HEAD").stdout.strip()
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1", "--axis", "spec",
            "--base", original, "--head", original, "--spec-source", "README.md",
            check=True,
        )
        first = run / "reviews/wave-1-spec.json"
        self.run_tool(
            REVIEW, "add", "--ledger", str(first), "--id", "F-CRASH",
            "--severity", "P1", "--title", "crash closure", "--body", "evidence",
            "--impact", "wrong behavior", "--fix-direction", "fix behavior",
            "--owner", "fixer", "--reviewer", "spec-reviewer", check=True,
        )
        (self.repo / "app.txt").write_text("fixed\n", encoding="utf-8")
        self.run_git("add", "app.txt")
        self.run_git("commit", "-m", "fix crash finding")
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1", "--axis", "spec",
            "--iteration", "2", "--scope", "fix", "--base", original,
            "--head", "HEAD", "--spec-source", "README.md", check=True,
        )
        second = run / "reviews/wave-1-spec-r2.json"
        fault_env = os.environ.copy()
        fault_env["QTEAM_FAULT_AFTER_REVIEW_WRITES"] = "1"
        interrupted = self.complete_review(
            run, second, "spec", "spec-rereviewer", "closure-crash",
            check=False, complete_env=fault_env,
        )
        self.assertNotEqual(interrupted.returncode, 0)
        self.assertTrue((run / "reviews/.closure-transaction.json").exists())
        receipt = run / "reviews/receipts/closure-crash.json"
        retried = self.run_tool(
            REVIEW, "complete", "--ledger", str(second), "--receipt", str(receipt)
        )
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertFalse((run / "reviews/.closure-transaction.json").exists())
        self.assertEqual(
            json.loads(first.read_text(encoding="utf-8"))["findings"][0]["status"],
            "resolved",
        )
        self.assertTrue(json.loads(second.read_text(encoding="utf-8"))["completed_at"])

    def test_false_positive_is_invalidated_only_by_fresh_dispute_receipt(self):
        run = self.init_run()
        self.run_git("branch", "agent/test-run/integration", "HEAD")
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1", "--axis", "spec",
            "--base", "HEAD", "--head", "HEAD", "--spec-source", "README.md",
            check=True,
        )
        first = run / "reviews/wave-1-spec.json"
        reported = self.complete_review(
            run, first, "spec", "spec-reviewer", "false-positive",
            result_payload={
                "axis": "spec", "verdict": "needs-fix", "resolved_ids": [],
                "invalid_ids": [], "invalid_evidence": {}, "upheld_ids": [],
                "findings": [{
                    "id": "F-FALSE", "severity": "P2", "title": "false alarm",
                    "review_evidence": "suspected issue", "impact": "none if disproved",
                    "fix_direction": "verify the claimed path", "owner": "architect",
                }],
            }, check=False,
        )
        self.assertNotEqual(reported.returncode, 0)
        manual = self.run_tool(REVIEW, "resolve", "--ledger", str(first))
        self.assertNotEqual(manual.returncode, 0)
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1", "--axis", "spec",
            "--iteration", "2", "--scope", "dispute", "--base", "HEAD",
            "--head", "HEAD", "--spec-source", "README.md", check=True,
        )
        second = run / "reviews/wave-1-spec-r2.json"
        self.complete_review(run, second, "spec", "spec-rereviewer", "dispute-pass")
        finding = json.loads(first.read_text(encoding="utf-8"))["findings"][0]
        self.assertEqual(finding["status"], "invalid")
        self.assertIn("independently disproved", finding["resolution"])
        self.assertIn("receipts/dispute-pass.json", finding["evidence"])
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "standards", "--base", "HEAD", "--head", "HEAD",
            "--standards-source", "README.md", check=True,
        )
        self.complete_review(
            run, run / "reviews/wave-1-standards.json", "standards",
            "standards-reviewer", "standards-clean",
        )
        checked = self.run_tool(
            REVIEW, "--run", str(run), "check", "--wave", "1", "--head", "HEAD"
        )
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_dispute_can_uphold_finding_and_return_to_fix_path(self):
        run = self.init_run()
        original = self.run_git("rev-parse", "HEAD").stdout.strip()
        integration = "agent/test-run/integration"
        self.run_git("branch", integration, original)
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1", "--axis", "spec",
            "--base", original, "--head", original, "--spec-source", "README.md",
            check=True,
        )
        first = run / "reviews/wave-1-spec.json"
        finding_payload = {
            "axis": "spec", "verdict": "needs-fix", "resolved_ids": [],
            "invalid_ids": [], "upheld_ids": [], "invalid_evidence": {},
            "findings": [{
                "id": "F-UPHELD", "severity": "P1", "title": "real defect",
                "review_evidence": "observable failure", "impact": "wrong result",
                "fix_direction": "correct result", "owner": "fixer",
            }],
        }
        self.complete_review(
            run, first, "spec", "spec-reviewer", "upheld-original",
            result_payload=finding_payload, check=False,
        )
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1", "--axis", "spec",
            "--iteration", "2", "--scope", "dispute", "--base", original,
            "--head", original, "--spec-source", "README.md", check=True,
        )
        dispute = run / "reviews/wave-1-spec-r2.json"
        upheld = self.complete_review(
            run, dispute, "spec", "spec-rereviewer", "upheld-dispute",
            result_payload={
                "axis": "spec", "verdict": "needs-fix", "findings": [],
                "resolved_ids": [], "invalid_ids": [], "upheld_ids": ["F-UPHELD"],
                "invalid_evidence": {},
            }, check=False,
        )
        self.assertNotEqual(upheld.returncode, 0)
        self.assertEqual(
            json.loads(first.read_text(encoding="utf-8"))["findings"][0]["status"],
            "open",
        )
        (self.repo / "app.txt").write_text("fixed\n", encoding="utf-8")
        self.run_git("add", "app.txt")
        self.run_git("commit", "-m", "fix upheld finding")
        self.run_git("branch", "-f", integration, "HEAD")
        created = self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1", "--axis", "spec",
            "--iteration", "3", "--scope", "fix", "--base", original,
            "--head", "HEAD", "--spec-source", "README.md",
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        packet = json.loads((run / "reviews/wave-1-spec-r3.json").read_text(
            encoding="utf-8"))["packet"]
        self.assertEqual([item["id"] for item in packet["closure_findings"]],
                         ["F-UPHELD"])

    def test_failed_fix_can_uphold_same_id_and_retry_next_iteration(self):
        run = self.init_run()
        original = self.run_git("rev-parse", "HEAD").stdout.strip()
        integration = "agent/test-run/integration"
        self.run_git("branch", integration, original)
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1", "--axis", "spec",
            "--base", original, "--head", original, "--spec-source", "README.md",
            check=True,
        )
        first = run / "reviews/wave-1-spec.json"
        self.complete_review(
            run, first, "spec", "spec-reviewer", "fix-uphold-original",
            result_payload={
                "axis": "spec", "verdict": "needs-fix", "resolved_ids": [],
                "invalid_ids": [], "upheld_ids": [], "invalid_evidence": {},
                "findings": [{
                    "id": "F-RETRY", "severity": "P1", "title": "real defect",
                    "review_evidence": "failure", "impact": "wrong result",
                    "fix_direction": "fix result", "owner": "fixer",
                }],
            }, check=False,
        )
        (self.repo / "app.txt").write_text("first fix\n", encoding="utf-8")
        self.run_git("add", "app.txt")
        self.run_git("commit", "-m", "first insufficient fix")
        first_fix = self.run_git("rev-parse", "HEAD").stdout.strip()
        self.run_git("branch", "-f", integration, first_fix)
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1", "--axis", "spec",
            "--iteration", "2", "--scope", "fix", "--base", original,
            "--head", first_fix, "--spec-source", "README.md", check=True,
        )
        second = run / "reviews/wave-1-spec-r2.json"
        upheld = self.complete_review(
            run, second, "spec", "spec-rereviewer", "fix-still-fails",
            result_payload={
                "axis": "spec", "verdict": "needs-fix", "findings": [],
                "resolved_ids": [], "invalid_ids": [], "upheld_ids": ["F-RETRY"],
                "invalid_evidence": {},
            }, check=False,
        )
        self.assertNotEqual(upheld.returncode, 0)
        (self.repo / "app.txt").write_text("second fix\n", encoding="utf-8")
        self.run_git("add", "app.txt")
        self.run_git("commit", "-m", "second fix")
        self.run_git("branch", "-f", integration, "HEAD")
        created = self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1", "--axis", "spec",
            "--iteration", "3", "--scope", "fix", "--base", first_fix,
            "--head", "HEAD", "--spec-source", "README.md",
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        closure = json.loads((run / "reviews/wave-1-spec-r3.json").read_text(
            encoding="utf-8"))["packet"]["closure_findings"]
        self.assertEqual([item["id"] for item in closure], ["F-RETRY"])

    def test_finding_fix_task_and_scoped_rereview_close_the_loop(self):
        run = self.init_run()
        worktree, _ = self.make_task(run, task="T01", work_kind="test")
        integration = "agent/test-run/integration"
        integration_worktree = run / "worktrees/integration"
        self.run_git("worktree", "add", "-b", integration,
                     str(integration_worktree), "HEAD")
        self.start_wave(run)
        self.run_tool(STATE, "--run", str(run), "task-status", "T01", "running",
                      check=True)
        (worktree / "app.txt").write_text("defect\n", encoding="utf-8")
        self.run_git("add", "app.txt", cwd=worktree)
        self.run_git("commit", "-m", "initial task", cwd=worktree)
        first_task_head = self.run_git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        self.run_tool(STATE, "--run", str(run), "verify-task", "T01", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "WAVE_VALIDATING", check=True)
        self.run_tool(CHECK, "--run", str(run), "--task", "T01", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "WAVE_MERGING", check=True)
        self.run_git("cherry-pick", first_task_head, cwd=integration_worktree)
        first_integration = self.run_git(
            "rev-parse", "HEAD", cwd=integration_worktree).stdout.strip()
        self.run_tool(STATE, "--run", str(run), "task-status", "T01", "merged",
                      "--commit", first_integration, check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "INTEGRATION_TESTING",
                      check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "REVIEWING", check=True)

        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1", "--axis", "spec",
            "--base", "main", "--head", integration, "--spec-source", "README.md",
            check=True,
        )
        first_ledger = run / "reviews/wave-1-spec.json"
        needs_fix = self.complete_review(
            run, first_ledger, "spec", "spec-reviewer", "spec-needs-fix",
            result_payload={
                "axis": "spec", "verdict": "needs-fix", "resolved_ids": [],
                "invalid_ids": [], "invalid_evidence": {}, "upheld_ids": [],
                "findings": [{"id": "F-FIX", "severity": "P1",
                              "title": "wrong behavior",
                              "review_evidence": "app is defective",
                              "impact": "required behavior fails",
                              "fix_direction": "replace defective output", "owner": "fixer"}],
            }, check=False,
        )
        self.assertNotEqual(needs_fix.returncode, 0)
        recorded = json.loads(first_ledger.read_text(encoding="utf-8"))["findings"]
        self.assertEqual([item["id"] for item in recorded], ["F-FIX"])
        self.run_tool(STATE, "--run", str(run), "phase", "FIXING", check=True)
        fix_worktree, _ = self.make_task(
            run, task="FIX01", work_kind="test", base_commit=first_integration,
            parallel_group="serial", finding_ids=["F-FIX"],
        )
        self.run_tool(STATE, "--run", str(run), "task-status", "FIX01", "running",
                      check=True)
        (fix_worktree / "app.txt").write_text("fixed\n", encoding="utf-8")
        self.run_git("add", "app.txt", cwd=fix_worktree)
        self.run_git("commit", "-m", "fix review finding", cwd=fix_worktree)
        fix_head = self.run_git("rev-parse", "HEAD", cwd=fix_worktree).stdout.strip()
        self.run_tool(STATE, "--run", str(run), "verify-task", "FIX01", check=True)
        self.run_tool(CHECK, "--run", str(run), "--task", "FIX01", check=True)
        self.run_git("cherry-pick", fix_head, cwd=integration_worktree)
        fixed_integration = self.run_git(
            "rev-parse", "HEAD", cwd=integration_worktree).stdout.strip()
        self.run_tool(STATE, "--run", str(run), "task-status", "FIX01", "merged",
                      "--commit", fixed_integration, check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "RE_REVIEWING", check=True)

        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1", "--axis", "spec",
            "--iteration", "2", "--scope", "fix", "--base", first_integration,
            "--head", integration, "--spec-source", "README.md", check=True,
        )
        self.complete_review(
            run, run / "reviews/wave-1-spec-r2.json", "spec", "spec-rereviewer",
            "spec-fix-pass",
        )
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "standards", "--base", "main", "--head", integration,
            "--standards-source", "README.md", check=True,
        )
        self.complete_review(
            run, run / "reviews/wave-1-standards.json", "standards",
            "standards-reviewer", "standards-pass",
        )
        checked = self.run_tool(
            REVIEW, "--run", str(run), "check", "--wave", "1", "--head", integration
        )
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        finding = json.loads(first_ledger.read_text(encoding="utf-8"))["findings"][0]
        self.assertEqual(finding["status"], "resolved")

    def test_empty_review_range_cannot_cover_a_merged_wave(self):
        run = self.init_run()
        self.make_task(run, work_kind="test")
        integration = "agent/test-run/integration"
        integration_worktree = run / "worktrees/integration"
        self.run_git("worktree", "add", "-b", integration,
                     str(integration_worktree), "HEAD")
        (integration_worktree / "app.txt").write_text("merged\n", encoding="utf-8")
        self.run_git("add", "app.txt", cwd=integration_worktree)
        self.run_git("commit", "-m", "merge task", cwd=integration_worktree)
        merge_commit = self.run_git("rev-parse", "HEAD", cwd=integration_worktree).stdout.strip()
        task_path = run / "tasks/T01.json"
        task = json.loads(task_path.read_text(encoding="utf-8"))
        task["status"] = "merged"
        task["merge_commit"] = merge_commit
        task_path.write_text(json.dumps(task), encoding="utf-8")
        state_path = run / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["tasks"]["T01"]["status"] = "merged"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        created = self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "spec", "--base", integration, "--head", integration,
            "--spec-source", "README.md",
        )
        self.assertNotEqual(created.returncode, 0)
        self.assertIn("empty review range", created.stderr)

    def test_old_risk_receipt_cannot_cover_later_high_risk_merge(self):
        run = self.init_run()
        self.make_task(run, work_kind="config", write_set=["src/auth/**"])
        integration = "agent/test-run/integration"
        integration_worktree = run / "worktrees/integration"
        self.run_git("worktree", "add", "-b", integration,
                     str(integration_worktree), "HEAD")
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "risk", "--base", "HEAD", "--head", "HEAD",
            "--standards-source", "README.md", check=True,
        )
        self.complete_review(run, run / "reviews/wave-1-risk.json", "risk",
                             "risk-reviewer", "risk-old-session")
        (integration_worktree / "app.txt").write_text("high risk merge\n",
                                                       encoding="utf-8")
        self.run_git("add", "app.txt", cwd=integration_worktree)
        self.run_git("commit", "-m", "merge high risk task", cwd=integration_worktree)
        merge_commit = self.run_git("rev-parse", "HEAD", cwd=integration_worktree).stdout.strip()
        task_path = run / "tasks/T01.json"
        task = json.loads(task_path.read_text(encoding="utf-8"))
        task["status"] = "merged"
        task["merge_commit"] = merge_commit
        task_path.write_text(json.dumps(task), encoding="utf-8")
        state_path = run / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["tasks"]["T01"]["status"] = "merged"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        for axis, flag in (("spec", "--spec-source"),
                           ("standards", "--standards-source")):
            self.run_tool(
                REVIEW, "--run", str(run), "create", "--wave", "1",
                "--axis", axis, "--base", "main", "--head", integration,
                flag, "README.md", check=True,
            )
            self.complete_review(
                run, run / f"reviews/wave-1-{axis}.json", axis,
                f"{axis}-reviewer", f"{axis}-current-session",
            )
        checked = self.run_tool(
            STATE, "--run", str(run), "reviews-checked", "--wave", "1",
            "--head", integration,
        )
        self.assertNotEqual(checked.returncode, 0)
        self.assertIn("risk review range does not cover merged wave", checked.stderr)

    def test_fix_rereview_range_combines_with_original_wave_coverage(self):
        run = self.init_run()
        self.make_task(run, task="T01", work_kind="test", write_set=["app1.txt"])
        self.make_task(run, task="T02", work_kind="test", write_set=["app2.txt"])
        integration = "agent/test-run/integration"
        integration_worktree = run / "worktrees/integration"
        self.run_git("worktree", "add", "-b", integration,
                     str(integration_worktree), "HEAD")

        def commit_and_mark(task_id, path):
            (integration_worktree / path).write_text(f"{task_id}\n", encoding="utf-8")
            self.run_git("add", path, cwd=integration_worktree)
            self.run_git("commit", "-m", f"merge {task_id}", cwd=integration_worktree)
            commit = self.run_git("rev-parse", "HEAD", cwd=integration_worktree).stdout.strip()
            task_path = run / f"tasks/{task_id}.json"
            task = json.loads(task_path.read_text(encoding="utf-8"))
            task["status"] = "merged"
            task["merge_commit"] = commit
            task_path.write_text(json.dumps(task), encoding="utf-8")
            state_path = run / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["tasks"][task_id]["status"] = "merged"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            return commit

        first = commit_and_mark("T01", "app1.txt")
        for axis, flag in (("spec", "--spec-source"),
                           ("standards", "--standards-source")):
            self.run_tool(
                REVIEW, "--run", str(run), "create", "--wave", "1",
                "--axis", axis, "--base", "main", "--head", first,
                flag, "README.md", check=True,
            )
            self.complete_review(run, run / f"reviews/wave-1-{axis}.json", axis,
                                 f"{axis}-reviewer", f"{axis}-original-session")
        second = commit_and_mark("T02", "app2.txt")
        for axis, flag in (("spec", "--spec-source"),
                           ("standards", "--standards-source")):
            self.run_tool(
                REVIEW, "--run", str(run), "create", "--wave", "1",
                "--axis", axis, "--iteration", "2", "--scope", "fix",
                "--base", first, "--head", second, flag, "README.md", check=True,
            )
            self.complete_review(run, run / f"reviews/wave-1-{axis}-r2.json", axis,
                                 f"{axis}-rereviewer", f"{axis}-fix-session")
        checked = self.run_tool(
            REVIEW, "--run", str(run), "check", "--wave", "1",
            "--head", integration,
        )
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_high_risk_wave_does_not_force_risk_axis_on_later_low_risk_wave(self):
        run = self.init_run()
        self.make_task(run, task="T01", work_kind="config",
                       write_set=["src/auth/**"], wave=1)
        self.make_task(run, task="T02", work_kind="docs",
                       write_set=["docs/guide.md"], wave=2)
        integration = "agent/test-run/integration"
        self.run_git("branch", integration, "HEAD")
        for wave, axes in ((1, ("spec", "standards", "risk")),
                           (2, ("spec", "standards"))):
            for axis in axes:
                source_flag = ("--spec-source" if axis == "spec"
                               else "--standards-source")
                self.run_tool(
                    REVIEW, "--run", str(run), "create", "--wave", str(wave),
                    "--axis", axis, "--base", "HEAD", "--head", integration,
                    source_flag, "README.md", check=True,
                )
                ledger = run / f"reviews/wave-{wave}-{axis}.json"
                self.complete_review(run, ledger, axis, f"{axis}-reviewer-{wave}",
                                     f"{axis}-session-{wave}")
            checked = self.run_tool(
                REVIEW, "--run", str(run), "check", "--wave", str(wave),
                "--head", integration,
            )
            self.assertEqual(checked.returncode, 0,
                             checked.stdout + checked.stderr)
        self.assertFalse((run / "reviews/wave-2-risk.json").exists())

    def test_high_risk_policy_adds_risk_axis_without_dropping_quality(self):
        run = self.init_run()
        self.make_task(run, work_kind="config", write_set=["src/auth/**"])
        integration = "agent/test-run/integration"
        self.run_git("branch", integration, "HEAD")
        for axis, source_flag, reviewer in (
            ("spec", "--spec-source", "spec-reviewer"),
            ("standards", "--standards-source", "standards-reviewer"),
        ):
            self.run_tool(
                REVIEW, "--run", str(run), "create", "--wave", "1",
                "--axis", axis, "--base", "HEAD", "--head", integration,
                source_flag, "README.md", check=True,
            )
            ledger = run / f"reviews/wave-1-{axis}.json"
            packet = json.loads(ledger.read_text(encoding="utf-8"))["packet"]
            self.assertEqual(packet["execution_tier"], "deep")
            self.assertEqual(packet["review_intensity"], "risk")
            self.complete_review(run, ledger, axis, reviewer, f"{axis}-session")
            if axis == "spec":
                trace = json.loads((Path(self.tmp.name) /
                                    "review-spec-session.trace.json").read_text(
                                        encoding="utf-8"))["args"]
                self.assertEqual(trace[trace.index("--model") + 1], "gpt-5.6-sol")
                self.assertIn('model_reasoning_effort="high"', trace)
                trace_record = json.loads((Path(self.tmp.name) /
                                           "review-spec-session.trace.json").read_text(
                                               encoding="utf-8"))
                self.assertNotEqual(Path(trace_record["cwd"]), self.repo)
                self.assertFalse(Path(trace_record["cwd"]).exists())
                self.assertIn("Check the changed behavior", trace[-1])
                self.assertIn("named risk and rollback", trace[-1])
                self.assertIn("invalid_evidence must map every and only", trace[-1])
        blocked = self.run_tool(
            REVIEW, "--run", str(run), "check", "--wave", "1",
            "--head", integration,
        )
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("missing risk ledger", blocked.stdout)
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "risk", "--base", "HEAD", "--head", integration,
            "--standards-source", "README.md", check=True,
        )
        risk_ledger = run / "reviews/wave-1-risk.json"
        self.complete_review(run, risk_ledger, "risk", "risk-reviewer",
                             "risk-session")
        passed = self.run_tool(
            REVIEW, "--run", str(run), "check", "--wave", "1",
            "--head", integration,
        )
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)

    def test_fixed_packet_and_finding_gate(self):
        run = self.init_run()
        (self.repo / "app.txt").write_text("change\n", encoding="utf-8")
        self.run_git("add", "app.txt")
        self.run_git("commit", "-m", "change")
        created = self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1", "--axis", "spec",
            "--base", "HEAD^", "--head", "HEAD", "--spec-source", "README.md",
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        ledger = run / "reviews/wave-1-spec.json"
        packet = json.loads(ledger.read_text(encoding="utf-8"))["packet"]
        snapshot = run / packet["spec_sources"][0]["snapshot"]
        self.assertEqual(snapshot.read_text(encoding="utf-8"), "base\n")
        (self.repo / "README.md").write_text("mutated after packet\n", encoding="utf-8")
        self.assertEqual(snapshot.read_text(encoding="utf-8"), "base\n")
        add = self.run_tool(
            REVIEW, "add", "--ledger", str(ledger), "--id", "F-1",
            "--severity", "P1", "--title", "Missing case", "--body", "Evidence",
            "--impact", "case fails", "--fix-direction", "cover missing case",
            "--owner", "fixer", "--reviewer", "spec-reviewer",
        )
        self.assertEqual(add.returncode, 0, add.stderr)
        blocked = self.run_tool(REVIEW, "complete", "--ledger", str(ledger))
        self.assertNotEqual(blocked.returncode, 0)
        (self.repo / "app.txt").write_text("fixed\n", encoding="utf-8")
        self.run_git("add", "app.txt")
        self.run_git("commit", "-m", "fix review finding")
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1", "--axis", "spec",
            "--iteration", "2", "--scope", "fix", "--base", packet["head_sha"],
            "--head", "HEAD", "--spec-source", "README.md", check=True,
        )
        fix_ledger = run / "reviews/wave-1-spec-r2.json"
        completed = self.complete_review(run, fix_ledger, "spec", "spec-rereviewer",
                                         "spec-session-2")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        closed = json.loads(ledger.read_text(encoding="utf-8"))["findings"][0]
        self.assertEqual(closed["status"], "resolved")
        self.assertIn("receipts/spec-session-2.json", closed["evidence"])
        # Standards is also mandatory for the combined gate.
        self.run_git("branch", "agent/test-run/integration", "HEAD")
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1", "--axis", "standards",
            "--base", "HEAD^^", "--head", "HEAD", "--standards-source", "README.md",
        )
        standards = run / "reviews/wave-1-standards.json"
        self.complete_review(run, standards, "standards", "standards-reviewer",
                             "standards-session-1")
        gate = self.run_tool(REVIEW, "--run", str(run), "check", "--wave", "1", "--head", "HEAD")
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)

    def test_concurrent_findings_are_not_lost(self):
        run = self.init_run()
        created = self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1", "--axis", "spec",
            "--base", "HEAD", "--head", "HEAD", "--spec-source", "README.md",
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        ledger = run / "reviews/wave-1-spec.json"
        procs = []
        for index in range(20):
            procs.append(subprocess.Popen(
                [sys.executable, str(REVIEW), "add", "--ledger", str(ledger),
                 "--id", f"F-{index}", "--severity", "P2", "--title", f"finding {index}",
                 "--body", "evidence", "--impact", "impact",
                 "--fix-direction", "fix it", "--owner", "fixer",
                 "--reviewer", "spec-reviewer"],
                cwd=self.repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ))
        results = [proc.communicate(timeout=10) for proc in procs]
        self.assertTrue(all(proc.returncode == 0 for proc in procs), results)
        saved = json.loads(ledger.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["findings"]), 20)

    def test_fix_rereview_closes_frozen_old_finding(self):
        run = self.init_run()
        original = self.run_git("rev-parse", "HEAD").stdout.strip()
        self.run_tool(REVIEW, "--run", str(run), "create", "--wave", "1",
                      "--axis", "spec", "--base", original, "--head", original,
                      "--spec-source", "README.md", check=True)
        first = run / "reviews/wave-1-spec.json"
        self.run_tool(REVIEW, "add", "--ledger", str(first), "--id", "F-OLD",
                      "--severity", "P1", "--title", "old", "--body", "still open",
                      "--impact", "broken", "--fix-direction", "fix old",
                      "--owner", "fixer", "--reviewer", "spec-reviewer", check=True)
        (self.repo / "app.txt").write_text("fixed\n", encoding="utf-8")
        self.run_git("add", "app.txt")
        self.run_git("commit", "-m", "fix old finding")
        self.run_git("branch", "agent/test-run/integration", "HEAD")
        self.run_tool(REVIEW, "--run", str(run), "create", "--wave", "1",
                      "--axis", "spec", "--iteration", "2", "--scope", "fix",
                      "--base", original, "--head", "HEAD",
                      "--spec-source", "README.md", check=True)
        second = run / "reviews/wave-1-spec-r2.json"
        self.complete_review(run, second, "spec", "spec-rereviewer", "spec-session-2")
        self.run_tool(REVIEW, "--run", str(run), "create", "--wave", "1",
                      "--axis", "standards", "--base", "HEAD", "--head", "HEAD",
                      "--standards-source", "README.md", check=True)
        self.complete_review(run, run / "reviews/wave-1-standards.json", "standards",
                             "standards-reviewer", "standards-session-1")
        gate = self.run_tool(REVIEW, "--run", str(run), "check", "--wave", "1",
                             "--head", "HEAD")
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)

    def test_mandatory_review_axes_require_distinct_sessions(self):
        run = self.init_run()
        self.run_git("branch", "agent/test-run/integration", "HEAD")
        ledgers = {}
        for axis, flag, reviewer in (
            ("spec", "--spec-source", "spec-reviewer"),
            ("standards", "--standards-source", "standards-reviewer"),
        ):
            self.run_tool(REVIEW, "--run", str(run), "create", "--wave", "1",
                          "--axis", axis, "--base", "HEAD", "--head", "HEAD",
                          flag, "README.md", check=True)
            ledgers[axis] = (run / f"reviews/wave-1-{axis}.json", reviewer)
        self.complete_review(run, ledgers["spec"][0], "spec",
                             ledgers["spec"][1], "same-session")
        reused = self.complete_review(
            run, ledgers["standards"][0], "standards",
            ledgers["standards"][1], "same-session", check=False,
        )
        self.assertNotEqual(reused.returncode, 0)
        self.assertIn("session/result already exists", reused.stderr)


class FinishTests(RepoCase):
    def test_bound_spec_drift_is_rechecked_by_finish(self):
        run = self.init_run()
        integration, _ = self.prepare_ready_run(run)
        decision = self.repo / "drift-decision.json"
        decision.write_text(json.dumps({
            "schema_version": 1, "id": "drift-D1", "status": "open",
            "question": "Approve the spec drift?", "authority": "user",
            "scope": {"kind": "action", "targets": ["finish"]},
        }), encoding="utf-8")
        self.run_tool(
            STATE, "--run", str(run), "decision-put", "--file", str(decision),
            check=True,
        )
        draft = self.repo / "drift-draft.json"
        draft.write_text(json.dumps({
            "summary": "document actual behavior", "changes": [{
                "id": "D1", "layer": "design", "original": "implicit",
                "actual": "explicit", "reason": "integration evidence",
                "proposal": "document explicit behavior",
                "decision_id": "drift-D1",
            }],
        }), encoding="utf-8")
        self.run_tool(
            ARTIFACT, "drift-seal", "--run", run.name, "--file", str(draft),
            "--source", "README.md", "--head", integration,
            "--output", f".agents/runs/{run.name}/spec-drift.json", check=True,
        )
        state = json.loads((run / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["spec_drift"]["decision_ids"], ["drift-D1"])
        self.run_tool(
            STATE, "--run", str(run), "decision-resolve", "drift-D1",
            "--outcome", "allow", "--choice", "accept",
            "--evidence", "user approval", check=True,
        )
        ready = self.run_tool(
            STATE, "--run", str(run), "finish", "--check-only"
        )
        self.assertEqual(ready.returncode, 0, ready.stderr)

        (self.repo / "README.md").write_text("changed after approval\n", encoding="utf-8")
        blocked = self.run_tool(
            STATE, "--run", str(run), "finish", "--check-only"
        )
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("spec drift gate is stale", blocked.stderr)

    def test_state_mutation_rechecks_publication_seal_inside_run_lock(self):
        run = self.init_run()
        state_path = run / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["publication_seal"] = {
            "status": "sealed", "purpose": "integrate",
            "head_sha": self.run_git("rev-parse", "HEAD").stdout.strip(),
            "decision_sha256": "0" * 64,
            "authorization_sha256": "1" * 64, "sealed_at": "test",
        }
        state_path.write_text(json.dumps(state), encoding="utf-8")
        sys.path.insert(0, str(PLUGIN / "bin"))
        try:
            spec = importlib.util.spec_from_file_location(
                "qteam_state_commit_guard_test", STATE
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            sys.path.pop(0)
        args = SimpleNamespace(
            name="learning", status="failed", evidence="late mutation"
        )
        with self.assertRaisesRegex(SystemExit, "publication seal freezes READY state"):
            module.cmd_gate(args, self.repo, run)
        unchanged = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(unchanged["gates"]["learning"]["status"], "pending")

    def test_push_only_is_rejected_before_git_mutation(self):
        result = self.run_tool(FINISH, "--push", "--yes")
        self.assertEqual(result.returncode, 3)
        self.assertIn("--push requires --integrate", result.stderr)

    def test_ungated_integration_commit_cannot_close_run(self):
        run = self.init_run()
        integration, integration_worktree = self.prepare_ready_run(run)
        # Add code, then refresh the head-bound review and final verification.
        (integration_worktree / "app.txt").write_text("integrated\n", encoding="utf-8")
        self.run_git("add", "app.txt", cwd=integration_worktree)
        self.run_git("commit", "-m", "integration", cwd=integration_worktree)
        # Existing ledgers are stale, so create re-review iteration 2 at new HEAD.
        for axis, source_flag in (("spec", "--spec-source"),
                                  ("standards", "--standards-source")):
            self.run_tool(REVIEW, "--run", str(run), "create", "--wave", "1",
                          "--axis", axis, "--iteration", "2", "--scope", "fix",
                          "--base", "main", "--head", integration,
                          source_flag, "README.md", check=True)
            ledger = run / f"reviews/wave-1-{axis}-r2.json"
            self.complete_review(run, ledger, axis, f"{axis}-rereviewer",
                                 f"{axis}-session-2")
        self.run_tool(REVIEW, "--run", str(run), "check", "--wave", "1",
                      "--head", integration, check=True)
        self.run_tool(STATE, "--run", str(run), "verify-final", "--command", "true",
                      check=True)
        result = self.run_tool(
            FINISH, "--integrate", "--allow-default-branch", "--yes"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("integration provenance", result.stdout + result.stderr)
        state = json.loads((run / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["phase"], "READY_TO_FINISH")
        self.assertFalse(state["finished"])
        self.assertNotEqual(self.run_git("rev-parse", "main").stdout,
                            self.run_git("rev-parse", integration).stdout)

    def test_finish_preflight_failure_does_not_move_branch(self):
        run = self.init_run()
        integration, _ = self.prepare_ready_run(run)
        before = self.run_git("rev-parse", "main").stdout.strip()
        # Simulate a recoverable corrupted/stale summary from an interrupted old client.
        state_path = run / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["tasks"]["T99"] = {"status": "pending", "attempt": 1}
        state_path.write_text(json.dumps(state), encoding="utf-8")
        result = self.run_tool(
            FINISH, "--integrate", "--allow-default-branch", "--yes"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.run_git("rev-parse", "main").stdout.strip(), before)

    def test_local_integration_seals_authorization_before_git_mutation(self):
        run = self.init_run()
        task_worktree, _ = self.make_task(run)
        integration = "agent/test-run/integration"
        integration_worktree = run / "worktrees/integration"
        self.run_git(
            "worktree", "add", "-b", integration, str(integration_worktree), "HEAD"
        )
        self.start_wave(run)
        self.run_tool(
            STATE, "--run", str(run), "task-status", "T01", "running", check=True
        )
        (task_worktree / "app.txt").write_text("integrated\n", encoding="utf-8")
        self.run_git("add", "app.txt", cwd=task_worktree)
        self.run_git("commit", "-m", "task change", cwd=task_worktree)
        task_head = self.run_git("rev-parse", "HEAD", cwd=task_worktree).stdout.strip()
        self.run_tool(
            STATE, "--run", str(run), "phase", "WAVE_VALIDATING", check=True
        )
        self.run_tool(STATE, "--run", str(run), "verify-task", "T01", check=True)
        self.run_tool(CHECK, "--run", str(run), "--task", "T01", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "WAVE_MERGING", check=True)
        self.run_git("cherry-pick", task_head, cwd=integration_worktree)
        integration_head = self.run_git(
            "rev-parse", "HEAD", cwd=integration_worktree
        ).stdout.strip()
        self.run_tool(
            STATE, "--run", str(run), "task-status", "T01", "merged",
            "--commit", integration_head, check=True,
        )
        self.run_tool(
            STATE, "--run", str(run), "phase", "INTEGRATION_TESTING", check=True
        )
        self.run_tool(STATE, "--run", str(run), "phase", "REVIEWING", check=True)
        for axis, source_flag in (("spec", "--spec-source"),
                                  ("standards", "--standards-source")):
            self.run_tool(
                REVIEW, "--run", str(run), "create", "--wave", "1",
                "--axis", axis, "--base", "main", "--head", integration,
                source_flag, "README.md", check=True,
            )
            self.complete_review(
                run, run / f"reviews/wave-1-{axis}.json", axis,
                f"{axis}-reviewer", f"{axis}-session-1",
            )
        self.run_tool(
            REVIEW, "--run", str(run), "check", "--wave", "1",
            "--head", integration, check=True,
        )
        self.run_tool(
            STATE, "--run", str(run), "phase", "LEARNING_EXPORT", check=True
        )
        self.run_tool(
            STATE, "--run", str(run), "verify-final", "--command", "true",
            check=True,
        )
        self.run_tool(STATE, "--run", str(run), "boundary-check", check=True)
        self.run_tool(
            STATE, "--run", str(run), "gate", "learning", "skipped",
            "--evidence", "unit test", check=True,
        )
        self.run_tool(
            STATE, "--run", str(run), "phase", "READY_TO_FINISH", check=True
        )

        fake_bin = Path(self.tmp.name) / "finish-git-bin"
        fake_bin.mkdir()
        real_git = shutil.which("git")
        marker = Path(self.tmp.name) / "late-gate.json"
        wrapper = fake_bin / "git"
        wrapper.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, subprocess, sys\n"
            "from pathlib import Path\n"
            "args=sys.argv[1:]\n"
            "result=subprocess.run([os.environ['REAL_GIT'], *args])\n"
            "if result.returncode == 0 and args[:2] == ['merge', '--ff-only']:\n"
            "    late=subprocess.run([sys.executable, os.environ['STATE_TOOL'], "
            "'--run', os.environ['RUN_DIR'], 'verify-final', '--command', 'false'], "
            "text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)\n"
            "    Path(os.environ['LATE_GATE_MARKER']).write_text(json.dumps({"
            "'returncode':late.returncode,'stdout':late.stdout,'stderr':late.stderr}))\n"
            "raise SystemExit(result.returncode)\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        env = os.environ.copy()
        env.update({
            "PATH": f"{fake_bin}:{env['PATH']}", "REAL_GIT": real_git,
            "STATE_TOOL": str(STATE), "RUN_DIR": str(run),
            "LATE_GATE_MARKER": str(marker),
        })
        finished = self.run_tool(
            FINISH, "--integrate", "--allow-default-branch", "--yes", env=env
        )
        self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)
        late = json.loads(marker.read_text(encoding="utf-8"))
        self.assertNotEqual(late["returncode"], 0)
        self.assertIn("publication seal freezes READY state", late["stderr"])
        self.assertEqual(
            self.run_git("rev-parse", "main").stdout.strip(), integration_head
        )
        state = json.loads((run / "state.json").read_text(encoding="utf-8"))
        self.assertTrue(state["finished"])
        self.assertEqual(state["phase"], "DONE")
        self.assertEqual(state["publication_seal"]["purpose"], "integrate")

    def test_finish_fails_closed_on_corrupt_run_state(self):
        corrupt = self.repo / ".agents/runs/corrupt/state.json"
        corrupt.parent.mkdir(parents=True)
        corrupt.write_text("{broken", encoding="utf-8")
        result = self.run_tool(FINISH)
        self.assertEqual(result.returncode, 3)
        self.assertIn("corrupt state", result.stderr)

    def test_finish_rechecks_gate_status_after_ready_transition(self):
        run = self.init_run()
        integration, _ = self.prepare_ready_run(run)
        failed_verification = self.run_tool(
            STATE, "--run", str(run), "verify-final", "--command", "false"
        )
        self.assertNotEqual(failed_verification.returncode, 0)
        blocked = self.run_tool(
            STATE, "--run", str(run), "finish", "--check-only"
        )
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("failed final verification", blocked.stderr)
        refused_seal = self.run_tool(
            STATE, "--run", str(run), "decision-check", "--action", "publish",
            "--seal", "--expected-head", integration,
        )
        self.assertNotEqual(refused_seal.returncode, 0)
        self.assertIn("failed final verification", refused_seal.stderr)

        state_path = run / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        head = self.run_git("rev-parse", integration).stdout.strip()
        state["gates"]["final_verification"] = {
            "status": "passed", "head_sha": head,
        }
        state["gates"]["learning"] = {"status": "failed"}
        state_path.write_text(json.dumps(state), encoding="utf-8")
        blocked_learning = self.run_tool(
            STATE, "--run", str(run), "finish", "--check-only"
        )
        self.assertNotEqual(blocked_learning.returncode, 0)
        self.assertIn("learning gate", blocked_learning.stderr)

    def test_publication_seal_detects_direct_authorization_tampering(self):
        run = self.init_run()
        integration, _ = self.prepare_ready_run(run)
        sealed = self.run_tool(
            STATE, "--run", str(run), "decision-check", "--action", "publish",
            "--seal", "--expected-head", integration,
        )
        self.assertEqual(sealed.returncode, 0, sealed.stderr)
        late = self.repo / "late-decision.json"
        late.write_text(json.dumps({
            "schema_version": 1, "id": "D-late", "status": "open",
            "question": "Stop publication?", "authority": "user",
            "scope": {"kind": "action", "targets": ["publish"]},
        }), encoding="utf-8")
        rejected = self.run_tool(
            STATE, "--run", str(run), "decision-put", "--file", str(late)
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("publication seal", rejected.stderr)
        gate_mutation = self.run_tool(
            STATE, "--run", str(run), "verify-final", "--command", "false"
        )
        self.assertNotEqual(gate_mutation.returncode, 0)
        self.assertIn("freezes READY state", gate_mutation.stderr)
        review_mutation = self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "spec", "--base", "HEAD", "--head", "HEAD",
            "--spec-source", "README.md",
        )
        self.assertNotEqual(review_mutation.returncode, 0)
        self.assertIn("publication seal freezes review", review_mutation.stderr)
        state_path = run / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["gates"]["learning"] = {"status": "failed"}
        state_path.write_text(json.dumps(state), encoding="utf-8")
        blocked = self.run_tool(
            STATE, "--run", str(run), "finish", "--check-only"
        )
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("authorization changed after sealing", blocked.stderr)

    def test_frozen_finish_head_excludes_later_integration_advance(self):
        run = self.init_run()
        integration, integration_worktree = self.prepare_ready_run(run)
        preflight = self.run_tool(STATE, "--run", str(run), "finish", "--check-only", check=True)
        frozen = json.loads(preflight.stdout)["integration_head"]
        (integration_worktree / "late.txt").write_text("not reviewed\n", encoding="utf-8")
        self.run_git("add", "late.txt", cwd=integration_worktree)
        self.run_git("commit", "-m", "late integration advance", cwd=integration_worktree)
        advanced = self.run_git("rev-parse", integration).stdout.strip()
        self.assertNotEqual(frozen, advanced)
        closed = self.run_tool(STATE, "--run", str(run), "finish",
                               "--expected-head", frozen)
        self.assertEqual(closed.returncode, 0, closed.stderr)
        state = json.loads((run / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["finished_head"], frozen)
        self.assertNotEqual(state["finished_head"], advanced)


class InvariantTests(RepoCase):
    def test_schema6_wal_cannot_drop_security_and_execution_core(self):
        run = self.init_run("v6-wal-core")
        state_path = run / "state.json"
        original = json.loads(state_path.read_text(encoding="utf-8"))
        weakened = json.loads(json.dumps(original))
        for field in (
            "risk_forced", "hard_to_reverse", "waves", "model_profiles",
            "review_model_profiles", "integration_provenance",
            "integration_provenance_head",
        ):
            weakened.pop(field)
        intent = {
            "schema_version": 1, "txid": "bad-v6-core",
            "writes": {"state.json": weakened},
            "event": {"event": "tampered"},
        }
        intent_path = run / ".transaction.json"
        intent_path.write_text(json.dumps(intent), encoding="utf-8")
        rejected = self.run_tool(STATE, "--run", str(run), "show")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn("Traceback", rejected.stderr)
        self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), original)
        self.assertTrue(intent_path.exists())

    def test_forged_wal_cannot_jump_current_run_directly_to_done(self):
        run = self.init_run("forged-done-wal")
        state_path = run / "state.json"
        original = json.loads(state_path.read_text(encoding="utf-8"))
        forged = json.loads(json.dumps(original))
        forged["phase"] = "DONE"
        forged["finished"] = True
        intent_path = run / ".transaction.json"
        intent_path.write_text(json.dumps({
            "schema_version": 1, "txid": "forged-finish",
            "writes": {"state.json": forged},
            "event": {"event": "phase", "from": "INIT", "to": "DONE"},
        }), encoding="utf-8")
        rejected = self.run_tool(STATE, "--run", str(run), "show")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("legacy run transactions", rejected.stderr)
        self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), original)
        self.assertTrue(intent_path.exists())

    def test_v5_migration_preserves_custom_profiles_and_risk_override(self):
        run = self.init_run("v5-custom")
        state_path = run / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["schema_version"] = 5
        state["risk_required"] = True
        state["risk_forced"] = False
        state["model_profiles"] = {
            "economy": {"model": "custom-e", "thinking": "low"},
            "standard": {"model": "custom-s", "thinking": "high"},
            "deep": {"model": "custom-d", "thinking": "xhigh"},
        }
        state.pop("review_model_profiles", None)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        migrated = self.run_tool(STATE, "--run", str(run), "migrate-run")
        self.assertEqual(migrated.returncode, 0, migrated.stderr)
        updated = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertFalse(updated["risk_forced"])
        self.assertEqual(
            [updated["model_profiles"][tier]["model"]
             for tier in ("economy", "standard", "deep")],
            ["custom-e", "custom-s", "custom-d"],
        )
        self.assertEqual(updated["review_model_profiles"]["standard"]["model"],
                         "custom-s")
        self.assertEqual(updated["model_profiles"]["deep"]["provider"], "openai")

    def test_unsafe_task_id_and_done_mutation_are_rejected(self):
        run = self.init_run()
        unsafe = self.run_tool(STATE, "--run", str(run), "task-status",
                               "../../other/tasks/T01", "running")
        self.assertNotEqual(unsafe.returncode, 0)
        self.prepare_ready_run(run)
        self.run_tool(STATE, "--run", str(run), "finish", check=True)
        record = {"id": "T01", "status": "pending", "branch": "agent/test-run/T01",
                  "worktree": str(run / "worktrees/T01"), "write_set": ["app.txt"],
                  "base_commit": self.run_git("rev-parse", "HEAD").stdout.strip(),
                  "parallel_group": "wave-1", "wave": 1, "depends_on": [],
                  "forbidden_paths": [], "verification": "true"}
        source = self.repo / "task-after-done.json"
        source.write_text(json.dumps(record), encoding="utf-8")
        result = self.run_tool(STATE, "--run", str(run), "task-put", "--file", str(source))
        self.assertNotEqual(result.returncode, 0)

    def test_task_branch_change_after_gate_cannot_be_marked_merged(self):
        run = self.init_run()
        worktree, _ = self.make_task(run)
        self.start_wave(run)
        self.run_tool(STATE, "--run", str(run), "task-status", "T01", "running", check=True)
        (worktree / "app.txt").write_text("ok\n", encoding="utf-8")
        self.run_git("add", "app.txt", cwd=worktree)
        self.run_git("commit", "-m", "checked", cwd=worktree)
        self.run_tool(STATE, "--run", str(run), "verify-task", "T01", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "WAVE_VALIDATING", check=True)
        self.run_tool(CHECK, "--run", str(run), "--task", "T01", check=True)
        checked_head = self.run_git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        self.run_git("branch", "agent/test-run/integration", checked_head)
        (worktree / "README.md").write_text("forbidden after gate\n", encoding="utf-8")
        self.run_git("add", "README.md", cwd=worktree)
        self.run_git("commit", "-m", "stale", cwd=worktree)
        self.run_tool(STATE, "--run", str(run), "phase", "WAVE_MERGING", check=True)
        result = self.run_tool(STATE, "--run", str(run), "task-status", "T01", "merged",
                               "--commit", checked_head)
        self.assertNotEqual(result.returncode, 0)

    def test_add_revert_history_cannot_be_hidden_inside_task_provenance(self):
        run = self.init_run()
        worktree, _ = self.make_task(run)
        integration = "agent/test-run/integration"
        integration_worktree = run / "worktrees/integration"
        self.run_git("worktree", "add", "-b", integration,
                     str(integration_worktree), "HEAD")
        self.start_wave(run)
        self.run_tool(STATE, "--run", str(run), "task-status", "T01", "running",
                      check=True)
        (worktree / "app.txt").write_text("legal\n", encoding="utf-8")
        self.run_git("add", "app.txt", cwd=worktree)
        self.run_git("commit", "-m", "legal task", cwd=worktree)
        task_head = self.run_git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        self.run_tool(STATE, "--run", str(run), "verify-task", "T01", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "WAVE_VALIDATING", check=True)
        self.run_tool(CHECK, "--run", str(run), "--task", "T01", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "WAVE_MERGING", check=True)

        (integration_worktree / "secret.txt").write_text("must not enter history\n",
                                                          encoding="utf-8")
        self.run_git("add", "secret.txt", cwd=integration_worktree)
        self.run_git("commit", "-m", "hidden secret", cwd=integration_worktree)
        self.run_git("revert", "--no-edit", "HEAD", cwd=integration_worktree)
        self.run_git("cherry-pick", task_head, cwd=integration_worktree)
        integration_head = self.run_git(
            "rev-parse", "HEAD", cwd=integration_worktree).stdout.strip()
        rejected = self.run_tool(
            STATE, "--run", str(run), "task-status", "T01", "merged",
            "--commit", integration_head,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("one-to-one", rejected.stderr)

    def test_wal_intent_is_recovered_before_read(self):
        run = self.init_run()
        state = json.loads((run / "state.json").read_text(encoding="utf-8"))
        state["goal"] = "recovered goal"
        intent = {"schema_version": 2, "txid": "recovery-test",
                  "writes": {"state.json": state},
                  "event": {"event": "recovered_test"}}
        digest = hashlib.sha256(json.dumps(
            intent, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        with (run / "events.jsonl").open("a", encoding="utf-8") as events:
            events.write(json.dumps({
                "event": "transaction_prepared", "prepared_txid": "recovery-test",
                "transaction_sha256": digest,
            }) + "\n")
        (run / ".transaction.json").write_text(json.dumps(intent), encoding="utf-8")
        shown = self.run_tool(STATE, "--run", str(run), "show")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(json.loads(shown.stdout)["goal"], "recovered goal")
        self.assertFalse((run / ".transaction.json").exists())

    def test_truncated_final_event_is_repaired_before_public_command(self):
        run = self.init_run()
        with (run / "events.jsonl").open("ab") as events:
            events.write(b"{")

        advanced = self.run_tool(
            STATE, "--run", str(run), "phase", "SPEC_READY"
        )
        self.assertEqual(advanced.returncode, 0, advanced.stderr)
        self.assertFalse((run / ".transaction.json").exists())
        records = [
            json.loads(line)
            for line in (run / "events.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]
        self.assertTrue(all(isinstance(record, dict) for record in records))
        self.assertEqual(
            json.loads((run / "state.json").read_text(encoding="utf-8"))["phase"],
            "SPEC_READY",
        )

    def test_completed_old_wal_cannot_replay_stale_state(self):
        run = self.init_run()
        old_state = json.loads((run / "state.json").read_text(encoding="utf-8"))
        self.run_tool(
            STATE, "--run", str(run), "phase", "SPEC_READY", check=True
        )
        current = (run / "state.json").read_bytes()
        intent = {
            "schema_version": 2,
            "txid": "completed-old-transaction",
            "writes": {"state.json": old_state},
            "event": {
                "event": "phase", "from": "INIT", "to": "SPEC_READY",
            },
        }
        digest = hashlib.sha256(json.dumps(
            intent, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        with (run / "events.jsonl").open("a", encoding="utf-8") as events:
            events.write(json.dumps({
                "event": "transaction_prepared",
                "prepared_txid": intent["txid"],
                "transaction_sha256": digest,
            }) + "\n")
            events.write(json.dumps({
                "event": "phase", "txid": intent["txid"],
                "from": "INIT", "to": "SPEC_READY",
            }) + "\n")
        transaction_path = run / ".transaction.json"
        transaction_path.write_text(json.dumps(intent), encoding="utf-8")

        rejected = self.run_tool(STATE, "--run", str(run), "show")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("cannot replay stale writes", rejected.stderr)
        self.assertEqual((run / "state.json").read_bytes(), current)
        self.assertTrue(transaction_path.exists())

    def test_malformed_wal_never_overwrites_valid_run_state(self):
        run = self.init_run()
        state_path = run / "state.json"
        original = state_path.read_bytes()
        intent = run / ".transaction.json"
        intent.write_text(json.dumps({
            "schema_version": 1, "txid": "malformed-recovery",
            "writes": {"state.json": {}},
            "event": {"event": "tampered"},
        }), encoding="utf-8")
        rejected = self.run_tool(STATE, "--run", str(run), "show")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn("Traceback", rejected.stderr)
        self.assertEqual(state_path.read_bytes(), original)
        self.assertTrue(intent.exists())

    def test_legacy_v2_wal_recovers_before_migration(self):
        run = self.init_run()
        state_path = run / "state.json"
        legacy = json.loads(state_path.read_text(encoding="utf-8"))
        legacy["schema_version"] = 2
        for field in (
            "risk_forced", "model_profiles", "waves", "policy_migration_pending",
            "integration_provenance_head", "integration_provenance",
        ):
            legacy.pop(field, None)
        state_path.write_text(json.dumps(legacy), encoding="utf-8")
        recovered = {**legacy, "goal": "recovered legacy goal"}
        intent = {
            "schema_version": 1, "txid": "legacy-v2-recovery",
            "writes": {"state.json": recovered},
            "event": {"event": "legacy_recovered"},
        }
        (run / ".transaction.json").write_text(json.dumps(intent), encoding="utf-8")
        migrated = self.run_tool(STATE, "--run", str(run), "migrate-v2")
        self.assertEqual(migrated.returncode, 0, migrated.stderr)
        current = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(current["schema_version"], 6)
        self.assertEqual(current["goal"], "recovered legacy goal")
        self.assertFalse((run / ".transaction.json").exists())

    def test_legacy_init_wal_recovers_when_state_file_is_missing(self):
        run = self.init_run()
        state_path = run / "state.json"
        legacy = json.loads(state_path.read_text(encoding="utf-8"))
        legacy["schema_version"] = 2
        for field in (
            "risk_forced", "model_profiles", "review_model_profiles", "runner",
            "waves", "policy_migration_pending", "integration_provenance_head",
            "integration_provenance", "hard_to_reverse",
        ):
            legacy.pop(field, None)
        state_path.unlink()
        intent = {
            "schema_version": 1, "txid": "legacy-init-recovery",
            "writes": {"state.json": legacy},
            "event": {"event": "run_created"},
        }
        (run / ".transaction.json").write_text(
            json.dumps(intent), encoding="utf-8"
        )

        shown = self.run_tool(STATE, "--run", str(run), "show")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(json.loads(shown.stdout)["schema_version"], 2)
        self.assertFalse((run / ".transaction.json").exists())

    def test_reset_integration_cannot_hide_a_previously_merged_task(self):
        run = self.init_run()
        worktree, _ = self.make_task(run)
        integration = "agent/test-run/integration"
        integration_worktree = run / "worktrees/integration"
        self.run_git("worktree", "add", "-b", integration,
                     str(integration_worktree), "HEAD")
        self.start_wave(run)
        self.run_tool(STATE, "--run", str(run), "task-status", "T01", "running", check=True)
        (worktree / "app.txt").write_text("checked\n", encoding="utf-8")
        self.run_git("add", "app.txt", cwd=worktree)
        self.run_git("commit", "-m", "task", cwd=worktree)
        task_head = self.run_git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        self.run_tool(STATE, "--run", str(run), "verify-task", "T01", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "WAVE_VALIDATING", check=True)
        self.run_tool(CHECK, "--run", str(run), "--task", "T01", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "WAVE_MERGING", check=True)
        self.run_git("cherry-pick", task_head, cwd=integration_worktree)
        merged_head = self.run_git("rev-parse", "HEAD", cwd=integration_worktree).stdout.strip()
        self.run_tool(STATE, "--run", str(run), "task-status", "T01", "merged",
                      "--commit", merged_head, check=True)
        for phase in ("INTEGRATION_TESTING", "REVIEWING", "LEARNING_EXPORT"):
            self.run_tool(STATE, "--run", str(run), "phase", phase, check=True)

        # Reset the integration branch, then try to refresh head-bound reviews.
        # Review-range coverage must detect that T01 disappeared before finish.
        self.run_git("reset", "--hard", "main", cwd=integration_worktree)
        reviewed = self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1",
            "--axis", "spec", "--iteration", "2", "--scope", "fix",
            "--base", "main", "--head", integration,
            "--spec-source", "README.md",
        )
        self.assertNotEqual(reviewed.returncode, 0)
        self.assertIn("empty review range", reviewed.stderr)


class LearningImportTests(RepoCase):
    def test_importer_rejects_run_id_traversal(self):
        qnote = Path(self.tmp.name) / "qnote-traversal"
        (qnote / "misc/ai/session-knowledge").mkdir(parents=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=qnote, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        rejected = subprocess.run(
            [sys.executable, str(IMPORT), str(self.repo), "../../escape"],
            cwd=qnote, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("safe direct-child identifier", rejected.stderr)

    def test_importer_rejects_malformed_manifest_items_without_traceback(self):
        run_id = "malformed-learning"
        outbox = self.repo / ".agents/runs" / run_id / "learning-outbox"
        outbox.mkdir(parents=True)
        (outbox / "manifest.json").write_text(json.dumps({
            "schema_version": 1, "run_id": run_id, "project": "repo",
            "items": [[]],
        }), encoding="utf-8")
        qnote = Path(self.tmp.name) / "qnote-malformed"
        (qnote / "misc/ai/session-knowledge").mkdir(parents=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=qnote, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        rejected = subprocess.run(
            [sys.executable, str(IMPORT), str(self.repo), run_id], cwd=qnote,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("invalid learning manifest", rejected.stderr)
        self.assertNotIn("Traceback", rejected.stderr)

    def test_importer_only_promotes_eval_cases_bound_to_frozen_run_evidence(self):
        run_id = "eval-import"
        run = self.repo / ".agents/runs" / run_id
        outbox = run / "learning-outbox"
        cases = outbox / "eval-cases"
        cases.mkdir(parents=True)
        evidence = run / "reviews/wave-1-standards.json"
        evidence.parent.mkdir(parents=True)
        evidence.write_text('{"finding":"confirmed"}\n', encoding="utf-8")
        case = {
            "schema_version": 1, "id": "review-finding-empty-success",
            "status": "approved", "source": {
                "kind": "review-finding", "run_id": run_id,
                "evidence": "reviews/wave-1-standards.json",
                "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
            },
            "observation": "The implementation accepted an empty success result.",
            "attribution": "agent", "capability": "result validation",
            "expected_outcome": "Reject the empty success result.",
            "validation_scope": "the confirmed review finding",
            "claim_boundary": "does not cover unrelated result formats",
            "decision": {
                "authority": "coordinator", "outcome": "approved",
                "evidence": "coordinator confirmed the review finding",
                "decided_at": "2026-08-10T00:00:00+00:00",
            },
        }
        case_path = cases / f"{case['id']}.json"
        case_path.write_text(json.dumps(case), encoding="utf-8")
        manifest = {
            "schema_version": 1, "run_id": run_id, "project": "repo",
            "source_commits": [], "items": [{
                "id": "E1", "title": "Empty success regression",
                "category": "eval", "file": f"eval-cases/{case['id']}.json",
                "status": "approved",
                "decision": case["decision"],
                "validation_scope": "the confirmed review finding",
                "claim_boundary": "does not cover unrelated result formats",
            }],
        }
        (outbox / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        case_sha = hashlib.sha256(json.dumps(
            case, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        item_sha = hashlib.sha256(json.dumps(
            manifest["items"][0], sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        (run / "events.jsonl").write_text(json.dumps({
            "event": "learning_item_decided", "item": "E1",
            "case_id": case["id"], "outcome": "approved",
            "evidence": case["decision"]["evidence"],
            "case_sha256": case_sha, "item_sha256": item_sha,
        }) + "\n", encoding="utf-8")
        qnote = Path(self.tmp.name) / "qnote-eval"
        (qnote / "misc/ai/session-knowledge").mkdir(parents=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=qnote, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        imported = subprocess.run(
            [sys.executable, str(IMPORT), str(self.repo), run_id], cwd=qnote,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(imported.returncode, 0, imported.stderr)
        destination = qnote / "misc/ai/session-knowledge/evals" / (
            "repo-review-finding-empty-success-"
            + hashlib.sha256(case["id"].encode()).hexdigest()[:12] + ".md"
        )
        self.assertIn(
            '"expected_outcome": "Reject the empty success result."',
            destination.read_text(encoding="utf-8"),
        )

        evidence.write_text('{"finding":"tampered"}\n', encoding="utf-8")
        destination.unlink()
        rejected = subprocess.run(
            [sys.executable, str(IMPORT), str(self.repo), run_id], cwd=qnote,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(rejected.returncode, 0, rejected.stderr)
        self.assertIn("evidence digest does not match", rejected.stdout)
        self.assertFalse(destination.exists())

    def test_importer_requires_and_preserves_validation_claim_boundaries(self):
        run_id = "R1"
        outbox = self.repo / ".agents/runs" / run_id / "learning-outbox"
        outbox.mkdir(parents=True)
        (outbox / "knowledge.md").write_text(
            "## Validated fact\n\nThe behavior was observed.\n", encoding="utf-8"
        )
        manifest = {
            "schema_version": 1, "run_id": run_id, "project": "repo",
            "source_commits": [],
            "items": [{
                "id": "K1", "title": "Validated fact", "category": "knowledge",
                "source": "T01", "file": "knowledge.md",
                "section": "Validated fact", "status": "approved",
            }],
        }
        manifest_path = outbox / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        qnote = Path(self.tmp.name) / "qnote"
        (qnote / "misc/ai/session-knowledge").mkdir(parents=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=qnote, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        rejected = subprocess.run(
            [sys.executable, str(IMPORT), str(self.repo), run_id], cwd=qnote,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(rejected.returncode, 0, rejected.stderr)
        self.assertIn("missing or invalid single-line validation_scope", rejected.stdout)
        destination = (
            qnote / "misc/ai/session-knowledge/knowledge/repo-validated-fact.md"
        )
        self.assertFalse(destination.exists())

        manifest["items"][0].update({
            "validation_scope": "focused behavior at the recorded task result",
            "claim_boundary": "does not establish behavior for other integrations",
        })
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        unapproved = subprocess.run(
            [sys.executable, str(IMPORT), str(self.repo), run_id], cwd=qnote,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(unapproved.returncode, 0, unapproved.stderr)
        self.assertIn("missing bound coordinator approval", unapproved.stdout)
        self.assertFalse(destination.exists())

        decision = {
            "authority": "coordinator", "outcome": "approved",
            "evidence": "coordinator verified the frozen source",
            "decided_at": "2026-08-10T00:00:00+00:00",
        }
        manifest["items"][0]["decision"] = decision
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        item_sha = hashlib.sha256(json.dumps(
            manifest["items"][0], sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        (outbox.parent / "events.jsonl").write_text(json.dumps({
            "event": "learning_item_decided", "item": "K1",
            "category": "knowledge", "outcome": "approved",
            "evidence": decision["evidence"], "item_sha256": item_sha,
        }) + "\n", encoding="utf-8")
        imported = subprocess.run(
            [sys.executable, str(IMPORT), str(self.repo), run_id], cwd=qnote,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(imported.returncode, 0, imported.stderr)
        content = destination.read_text(encoding="utf-8")
        self.assertIn(
            "> Validation scope: focused behavior at the recorded task result", content
        )
        self.assertIn(
            "> Claim boundary: does not establish behavior for other integrations", content
        )


class InstallerTests(RepoCase):
    def fake_plugin_env(self):
        fake_bin = Path(self.tmp.name) / "doctor-bin"
        fake_bin.mkdir(exist_ok=True)
        fake = fake_bin / "codex"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "print(json.dumps({'installed':[{'pluginId':'qteam@qteam',"
            "'installed':True,'enabled':True}]}))\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        return env

    def test_project_setup_installs_runtime_without_copying_plugin_skills(self):
        original_config = "[tools]\nmode = 'keep'\n"
        original_ignore = "user-cache/\n"
        config = self.repo / ".codex/config.toml"
        config.parent.mkdir(parents=True)
        config.write_text(original_config, encoding="utf-8")
        (self.repo / ".gitignore").write_text(original_ignore, encoding="utf-8")
        old_agents = self.repo / ".codex/agents"
        old_agents.mkdir(parents=True)
        (old_agents / "developer.toml").write_text("old writable role\n", encoding="utf-8")
        (self.repo / ".codex/agent-team-template.version").write_text(
            "agent-team-template-version: 0.5.0\n", encoding="utf-8"
        )
        old_skill = self.repo / ".agents/skills/using-superpowers"
        old_skill.mkdir(parents=True)
        (old_skill / "USER_MARKER").write_text("preserve me\n", encoding="utf-8")
        (old_skill / "SKILL.md").write_text("competing live trigger\n", encoding="utf-8")
        old_qteam = self.repo / ".agents/skills/agent-team-dev"
        old_qteam.mkdir(parents=True)
        (old_qteam / "USER_MARKER").write_text("old qteam\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(PROJECT_SETUP), str(self.repo)], cwd=SOURCE,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((old_agents / "developer.toml").exists())
        self.assertTrue((old_agents / "test-designer.toml").is_file())
        self.assertTrue((self.repo / ".codex/bin/agent-team-worker").is_file())
        self.assertTrue((self.repo / ".codex/bin/agent-team-state").is_file())
        self.assertTrue((self.repo / ".codex/bin/agent-team-review").is_file())
        self.assertTrue((self.repo / ".codex/bin/agent-team-artifact").is_file())
        self.assertTrue((self.repo / ".codex/bin/agent_team_artifact.py").is_file())
        self.assertTrue((self.repo / ".codex/bin/agent_team_policy.py").is_file())
        self.assertTrue((self.repo / ".codex/bin/agent_team_eval.py").is_file())
        self.assertTrue((self.repo / ".codex/bin/import-agent-learning").is_file())
        self.assertTrue((self.repo /
                         ".codex/schemas/review-receipt.schema.json").is_file())
        self.assertTrue((self.repo /
                         ".codex/schemas/experiment.schema.json").is_file())
        self.assertTrue((self.repo /
                         ".codex/schemas/decision-gate.schema.json").is_file())
        self.assertTrue((self.repo /
                         ".codex/schemas/handoff.schema.json").is_file())
        self.assertTrue((self.repo /
                         ".codex/schemas/scenario-coverage.schema.json").is_file())
        self.assertTrue((self.repo /
                         ".codex/schemas/trajectory.schema.json").is_file())
        self.assertTrue((self.repo /
                         ".codex/schemas/eval-case.schema.json").is_file())
        self.assertTrue((self.repo /
                         ".codex/schemas/artifact-lint.schema.json").is_file())
        self.assertTrue((self.repo / ".codex/schemas/epic.schema.json").is_file())
        self.assertTrue((self.repo /
                         ".codex/schemas/code-index.schema.json").is_file())
        self.assertTrue((self.repo /
                         ".codex/schemas/spec-drift.schema.json").is_file())
        self.assertTrue((self.repo / ".codex/licenses/Superpowers-MIT.txt").is_file())
        self.assertTrue((self.repo / ".codex/licenses/Autoresearch-MIT.txt").is_file())
        self.assertTrue((self.repo / ".codex/licenses/LoopX-MIT.txt").is_file())
        self.assertTrue((self.repo / ".codex/licenses/Smart-Ralph-MIT.txt").is_file())
        self.assertFalse((self.repo / ".agents/skills/qteam-router").exists())
        self.assertFalse(old_qteam.exists())
        self.assertFalse(old_skill.exists())
        self.assertTrue((self.repo / ".codex/qteam-project.json").is_file())
        doctor = subprocess.run([str(self.repo / ".codex/bin/agent-team-doctor")],
                                cwd=self.repo, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=self.fake_plugin_env())
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
        # Doctor validates every schema, not only run-state.
        finding_schema = self.repo / ".codex/schemas/finding.schema.json"
        expected_schema = finding_schema.read_text(encoding="utf-8")
        finding_schema.write_text("{broken", encoding="utf-8")
        doctor = subprocess.run([str(self.repo / ".codex/bin/agent-team-doctor")],
                                cwd=self.repo, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=self.fake_plugin_env())
        self.assertNotEqual(doctor.returncode, 0)
        self.assertIn("finding.schema.json", doctor.stdout)
        finding_schema.write_text(expected_schema, encoding="utf-8")

        conflict = self.repo / ".agents/skills/qteam-explore"
        conflict.mkdir(parents=True)
        (conflict / "SKILL.md").write_text("local discovery\n", encoding="utf-8")
        doctor = subprocess.run([str(self.repo / ".codex/bin/agent-team-doctor")],
                                cwd=self.repo, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=self.fake_plugin_env())
        self.assertNotEqual(doctor.returncode, 0)
        self.assertIn("qteam-explore", doctor.stdout)
        shutil.rmtree(conflict)

        removed = self.run_tool(PROJECT_UNINSTALL, str(self.repo))
        self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
        self.assertEqual(config.read_text(encoding="utf-8"), original_config)
        self.assertEqual((self.repo / ".gitignore").read_text(encoding="utf-8"),
                         original_ignore)
        self.assertEqual((old_agents / "developer.toml").read_text(encoding="utf-8"),
                         "old writable role\n")
        self.assertEqual((old_qteam / "USER_MARKER").read_text(encoding="utf-8"),
                         "old qteam\n")
        self.assertTrue((old_skill / "USER_MARKER").is_file())
        self.assertFalse((self.repo / ".codex/qteam-project.json").exists())

    def test_setup_refuses_user_owned_local_exploration_skill_before_mutation(self):
        local = self.repo / ".agents/skills/qteam-explore"
        local.mkdir(parents=True)
        marker = local / "SKILL.md"
        marker.write_text("user-owned discovery\n", encoding="utf-8")
        blocked = self.run_tool(PROJECT_SETUP, str(self.repo))
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("not QTeam-owned", blocked.stderr)
        self.assertEqual(marker.read_text(encoding="utf-8"), "user-owned discovery\n")
        self.assertFalse((self.repo / ".codex/qteam-project.json").exists())
        self.assertFalse((self.repo / ".codex/config.toml").exists())

    def test_legacy_setup_never_moves_user_owned_local_exploration_skill(self):
        (self.repo / ".codex").mkdir()
        (self.repo / ".codex/agent-team-template.version").write_text(
            "agent-team-template-version: 0.6.0\n", encoding="utf-8"
        )
        local = self.repo / ".agents/skills/qteam-explore"
        local.mkdir(parents=True)
        marker = local / "SKILL.md"
        marker.write_text("user-owned legacy discovery\n", encoding="utf-8")
        blocked = self.run_tool(PROJECT_SETUP, str(self.repo))
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("not QTeam-owned", blocked.stderr)
        self.assertEqual(marker.read_text(encoding="utf-8"),
                         "user-owned legacy discovery\n")
        self.assertFalse((self.repo / ".codex/qteam-project.json").exists())

    def test_project_uninstall_retains_locally_modified_runtime_file(self):
        installed = subprocess.run(
            [sys.executable, str(PROJECT_SETUP), str(self.repo)], cwd=SOURCE,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        managed = self.repo / ".codex/agents/researcher.toml"
        managed.write_text("user modification\n", encoding="utf-8")
        blocked = self.run_tool(PROJECT_UNINSTALL, str(self.repo))
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("locally modified", blocked.stderr)
        self.assertEqual(managed.read_text(encoding="utf-8"), "user modification\n")
        self.assertTrue((self.repo / ".codex/qteam-project.json").is_file())
        copied_uninstaller = self.repo / ".codex/bin/qteam-project-uninstall"
        self.assertTrue(copied_uninstaller.is_file())
        managed.write_text(
            (PLUGIN / "agents/researcher.toml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        retried = self.run_tool(copied_uninstaller, str(self.repo))
        self.assertEqual(retried.returncode, 0, retried.stdout + retried.stderr)
        self.assertFalse((self.repo / ".codex/qteam-project.json").exists())

    def test_setup_rejects_parent_symlink_without_writing_outside_repository(self):
        outside = Path(self.tmp.name) / "outside-bin"
        outside.mkdir()
        codex = self.repo / ".codex"
        codex.mkdir()
        (codex / "bin").symlink_to(outside, target_is_directory=True)
        blocked = self.run_tool(PROJECT_SETUP, str(self.repo))
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("symlink", blocked.stderr)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((codex / "config.toml").exists())
        self.assertFalse((codex / "qteam-project.json").exists())

    def test_interrupted_setup_rolls_back_from_durable_intent(self):
        config = self.repo / ".codex/config.toml"
        config.parent.mkdir()
        config.write_text("[user]\nkeep = true\n", encoding="utf-8")
        ignore = self.repo / ".gitignore"
        ignore.write_text("keep-me/\n", encoding="utf-8")
        env = os.environ.copy()
        env["QTEAM_TEST_FAIL_AFTER_INSTALLS"] = "2"
        blocked = self.run_tool(PROJECT_SETUP, str(self.repo), env=env)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("injected QTeam setup failure", blocked.stderr)
        self.assertEqual(config.read_text(encoding="utf-8"), "[user]\nkeep = true\n")
        self.assertEqual(ignore.read_text(encoding="utf-8"), "keep-me/\n")
        self.assertFalse((self.repo / ".codex/qteam-project.json").exists())
        self.assertFalse((self.repo / ".codex/agents/researcher.toml").exists())

    def test_uninstall_rejects_manifest_target_outside_exact_allowlist(self):
        self.run_tool(PROJECT_SETUP, str(self.repo), check=True)
        manifest_path = self.repo / ".codex/qteam-project.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        readme_before = (self.repo / "README.md").read_bytes()
        manifest["installed_files"][0].update({
            "path": "README.md",
            "sha256": hashlib.sha256(readme_before).hexdigest(),
            "prior": None,
            "prior_sha256": None,
        })
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        blocked = self.run_tool(PROJECT_UNINSTALL, str(self.repo))
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("unexpected or duplicate", blocked.stderr)
        self.assertEqual((self.repo / "README.md").read_bytes(), readme_before)
        self.assertTrue((self.repo / ".codex/agents/researcher.toml").is_file())

    def test_current_manifest_cannot_claim_a_previous_installed_path_set(self):
        self.run_tool(PROJECT_SETUP, str(self.repo), check=True)
        manifest_path = self.repo / ".codex/qteam-project.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        license_path = ".codex/licenses/Autoresearch-MIT.txt"
        manifest["installed_files"] = [
            record for record in manifest["installed_files"]
            if record["path"] != license_path
        ]
        (self.repo / license_path).unlink()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        blocked = self.run_tool(PROJECT_UNINSTALL, str(self.repo))
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("omits installed runtime paths", blocked.stderr)
        self.assertTrue((self.repo / ".codex/agents/researcher.toml").is_file())
        self.assertTrue(manifest_path.is_file())

    def test_uninstall_verifies_backup_digest_before_any_mutation(self):
        researcher = self.repo / ".codex/agents/researcher.toml"
        researcher.parent.mkdir(parents=True)
        researcher.write_text("original researcher\n", encoding="utf-8")
        self.run_tool(PROJECT_SETUP, str(self.repo), check=True)
        manifest = json.loads((self.repo / ".codex/qteam-project.json").read_text(
            encoding="utf-8"))
        record = next(item for item in manifest["installed_files"]
                      if item["path"] == ".codex/agents/researcher.toml")
        (self.repo / record["prior"]).write_text("tampered backup\n", encoding="utf-8")
        installed_bytes = researcher.read_bytes()
        blocked = self.run_tool(PROJECT_UNINSTALL, str(self.repo))
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("integrity check", blocked.stderr)
        self.assertEqual(researcher.read_bytes(), installed_bytes)

    def test_uninstall_rejects_forged_restored_phase(self):
        researcher = self.repo / ".codex/agents/researcher.toml"
        researcher.parent.mkdir(parents=True)
        researcher.write_text("original researcher\n", encoding="utf-8")
        self.run_tool(PROJECT_SETUP, str(self.repo), check=True)
        manifest_path = self.repo / ".codex/qteam-project.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        backup_root = self.repo / manifest["backup_root"]
        manifest["phase"] = "restored"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        blocked = self.run_tool(PROJECT_UNINSTALL, str(self.repo))
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("does not match", blocked.stderr)
        self.assertTrue(manifest_path.is_file())
        self.assertTrue(backup_root.is_dir())
        self.assertNotEqual(researcher.read_text(encoding="utf-8"),
                            "original researcher\n")

    def test_uninstall_verifies_backup_permission_mode(self):
        researcher = self.repo / ".codex/agents/researcher.toml"
        researcher.parent.mkdir(parents=True)
        researcher.write_text("original researcher\n", encoding="utf-8")
        researcher.chmod(0o600)
        self.run_tool(PROJECT_SETUP, str(self.repo), check=True)
        manifest = json.loads((self.repo / ".codex/qteam-project.json").read_text(
            encoding="utf-8"))
        record = next(item for item in manifest["installed_files"]
                      if item["path"] == ".codex/agents/researcher.toml")
        backup = self.repo / record["prior"]
        backup.chmod(0o777)

        blocked = self.run_tool(PROJECT_UNINSTALL, str(self.repo))
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("integrity check", blocked.stderr)
        backup.chmod(0o600)
        self.run_tool(PROJECT_UNINSTALL, str(self.repo), check=True)
        self.assertEqual(researcher.stat().st_mode & 0o777, 0o600)

    def test_config_merge_rejects_quoted_semantic_agents_table(self):
        config = self.repo / ".codex/config.toml"
        config.parent.mkdir()
        original = '["agents"]\nmax_depth = 2\n'
        config.write_text(original, encoding="utf-8")

        blocked = self.run_tool(PROJECT_SETUP, str(self.repo))
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("refuses to rewrite", blocked.stderr)
        self.assertEqual(config.read_text(encoding="utf-8"), original)
        self.assertFalse((self.repo / ".codex/qteam-project.json").exists())
        self.assertFalse((self.repo / ".codex/qteam-backups").exists())

    def test_config_merge_rejects_escaped_semantic_agents_names(self):
        cases = (
            '["a\\u0067ents"]\nmax_depth = 2\n',
            '[agents]\n"max_\\u0064epth" = 2\n',
        )
        for original in cases:
            with self.subTest(config=original):
                config = self.repo / ".codex/config.toml"
                config.parent.mkdir(exist_ok=True)
                config.write_text(original, encoding="utf-8")
                blocked = self.run_tool(PROJECT_SETUP, str(self.repo))
                self.assertNotEqual(blocked.returncode, 0)
                self.assertIn("refuses to rewrite", blocked.stderr)
                self.assertEqual(config.read_text(encoding="utf-8"), original)
                self.assertFalse((self.repo / ".codex/qteam-project.json").exists())
                self.assertFalse((self.repo / ".codex/qteam-backups").exists())

    def test_config_merge_handles_array_table_and_deduplicates_legacy_capacity(self):
        original = (
            "[agents]\n"
            "max_concurrent_threads_per_session = 5\n"
            "max_threads = 9\n"
            "[[hooks]]\n"
            "name = 'keep'\n"
        )
        config = self.repo / ".codex/config.toml"
        config.parent.mkdir()
        config.write_text(original, encoding="utf-8")
        self.run_tool(PROJECT_SETUP, str(self.repo), check=True)
        merged = config.read_text(encoding="utf-8")
        self.assertEqual(merged.count("max_concurrent_threads_per_session"), 1)
        self.assertNotIn("max_threads", merged)
        self.assertLess(merged.index("max_depth"), merged.index("[[hooks]]"))
        self.run_tool(PROJECT_UNINSTALL, str(self.repo), check=True)
        self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_setup_adds_each_sensitive_ignore_even_when_runs_is_already_ignored(self):
        ignore = self.repo / ".gitignore"
        original = ".agents/runs/\n"
        ignore.write_text(original, encoding="utf-8")
        self.run_tool(PROJECT_SETUP, str(self.repo), check=True)
        installed = ignore.read_text(encoding="utf-8").splitlines()
        for required in (
            ".agents/runs/", ".agents/tmp/", "*.bak.*",
            ".codex/qteam-backups/", ".codex/qteam-project.json",
            ".codex/agent-team-template.version",
        ):
            self.assertIn(required, installed)
        backup = next((self.repo / ".codex/qteam-backups/install").iterdir())
        ignored = self.run_git("check-ignore", str(backup), check=False)
        self.assertEqual(ignored.returncode, 0, ignored.stderr)
        self.run_tool(PROJECT_UNINSTALL, str(self.repo), check=True)
        self.assertEqual(ignore.read_text(encoding="utf-8"), original)

    def test_moved_path_restoration_is_restart_safe(self):
        marker = self.repo / ".codex/agent-team-template.version"
        marker.parent.mkdir()
        marker.write_text("agent-team-template-version: 0.5.0\n", encoding="utf-8")
        skill = self.repo / ".agents/skills/using-superpowers"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("legacy\n", encoding="utf-8")
        self.run_tool(PROJECT_SETUP, str(self.repo), check=True)
        manifest = json.loads((self.repo / ".codex/qteam-project.json").read_text(
            encoding="utf-8"))
        record = next(item for item in manifest["moved_paths"]
                      if item["path"] == ".agents/skills/using-superpowers")
        saved = self.repo / record["backup"]
        skill.parent.mkdir(parents=True, exist_ok=True)
        saved.rename(skill)
        self.run_tool(PROJECT_UNINSTALL, str(self.repo), check=True)
        self.assertEqual((skill / "SKILL.md").read_text(encoding="utf-8"), "legacy\n")

    def test_project_only_tools_leave_no_python_bytecode_runtime(self):
        self.run_tool(PROJECT_SETUP, str(self.repo), check=True)
        state = self.repo / ".codex/bin/agent-team-state"
        helped = subprocess.run(
            [str(state), "--help"], cwd=self.repo, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(helped.returncode, 0, helped.stdout + helped.stderr)
        uninstaller = self.repo / ".codex/bin/qteam-project-uninstall"
        removed = subprocess.run(
            [str(uninstaller), str(self.repo)], cwd=self.repo, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
        self.assertFalse((self.repo / ".codex").exists())


class PluginTests(RepoCase):
    def test_marketplace_points_to_valid_versioned_qteam_plugin(self):
        marketplace = json.loads(
            (SOURCE / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
        )
        manifest = json.loads(
            (PLUGIN / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(marketplace["name"], "qteam")
        self.assertEqual(marketplace["plugins"], [{
            "name": "qteam",
            "source": {"source": "local", "path": "./plugins/qteam"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Developer Tools",
        }])
        self.assertEqual(manifest["name"], "qteam")
        self.assertEqual(manifest["version"],
                         (PLUGIN / "VERSION").read_text(encoding="utf-8").strip())

    def fake_codex_env(self):
        fake_bin = Path(self.tmp.name) / "plugin-bin"
        fake_bin.mkdir(exist_ok=True)
        state = Path(self.tmp.name) / "plugin-state.json"
        state.write_text(json.dumps({"marketplace": False, "plugin": False}),
                         encoding="utf-8")
        log = Path(self.tmp.name) / "plugin-calls.jsonl"
        fake = fake_bin / "codex"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "state_path=Path(os.environ['QTEAM_FAKE_STATE'])\n"
            "log=Path(os.environ['QTEAM_FAKE_LOG'])\n"
            "state=json.loads(state_path.read_text())\n"
            "args=sys.argv[1:]\n"
            "with log.open('a') as out: out.write(json.dumps(args)+'\\n')\n"
            "if args==['plugin','marketplace','list','--json']:\n"
            "  items=[]\n"
            "  if state['marketplace']: items=[{'name':'qteam','root':os.environ['QTEAM_ROOT']}]\n"
            "  print(json.dumps({'marketplaces':items}))\n"
            "elif args[:3]==['plugin','marketplace','add']:\n"
            "  state['marketplace']=True\n"
            "elif args==['plugin','add','qteam@qteam']:\n"
            "  state['plugin']=True\n"
            "elif args==['plugin','list','--json']:\n"
            "  items=[{'pluginId':'qteam@qteam'}] if state['plugin'] else []\n"
            "  print(json.dumps({'installed':items}))\n"
            "elif args==['plugin','remove','qteam@qteam']:\n"
            "  state['plugin']=False\n"
            "elif args==['plugin','marketplace','remove','qteam']:\n"
            "  state['marketplace']=False\n"
            "else:\n"
            "  raise SystemExit('unexpected fake codex args: '+repr(args))\n"
            "state_path.write_text(json.dumps(state))\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        env["QTEAM_FAKE_STATE"] = str(state)
        env["QTEAM_FAKE_LOG"] = str(log)
        env["QTEAM_ROOT"] = str(SOURCE.resolve())
        return env, state, log

    def test_simple_plugin_setup_and_uninstall_commands_are_symmetric(self):
        env, state, log = self.fake_codex_env()
        setup = subprocess.run(
            [str(QTEAM), "setup"], cwd=SOURCE, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
        self.assertEqual(json.loads(state.read_text()),
                         {"marketplace": True, "plugin": True})
        removed = subprocess.run(
            [str(QTEAM), "uninstall"], cwd=SOURCE, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
        self.assertEqual(json.loads(state.read_text()),
                         {"marketplace": False, "plugin": False})
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertIn(["plugin", "marketplace", "add", str(SOURCE.resolve())], calls)
        self.assertIn(["plugin", "add", "qteam@qteam"], calls)
        self.assertIn(["plugin", "remove", "qteam@qteam"], calls)
        self.assertIn(["plugin", "marketplace", "remove", "qteam"], calls)

    def test_simple_commands_manage_plugin_and_project_runtime_together(self):
        env, state, _log = self.fake_codex_env()
        stamps = []
        for _attempt in range(2):
            setup = subprocess.run(
                [str(QTEAM), "setup", str(self.repo)], cwd=SOURCE, env=env,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
            manifest_path = self.repo / ".codex/qteam-project.json"
            self.assertTrue(manifest_path.is_file())
            stamps.append(json.loads(manifest_path.read_text(encoding="utf-8"))["stamp"])
            self.assertTrue((self.repo / ".codex/bin/agent-team-state").is_file())
        self.assertNotEqual(stamps[0], stamps[1])
        removed = subprocess.run(
            [str(QTEAM), "uninstall", str(self.repo)], cwd=SOURCE, env=env,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
        self.assertFalse((self.repo / ".codex/qteam-project.json").exists())
        self.assertEqual(json.loads(state.read_text()),
                         {"marketplace": False, "plugin": False})

    def test_setup_recovers_durable_preparing_intent_after_hard_exit(self):
        config = self.repo / ".codex/config.toml"
        config.parent.mkdir()
        original = "[user]\nkeep = true\n"
        config.write_text(original, encoding="utf-8")
        crash_env = os.environ.copy()
        crash_env["QTEAM_TEST_HARD_EXIT_AFTER_BACKUPS"] = "1"
        interrupted = self.run_tool(
            PROJECT_SETUP, str(self.repo), env=crash_env,
        )
        self.assertEqual(interrupted.returncode, 86)
        manifest_path = self.repo / ".codex/qteam-project.json"
        interrupted_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(interrupted_manifest["phase"], "preparing")
        self.assertTrue((self.repo / interrupted_manifest["backup_root"]).is_dir())
        self.assertEqual(config.read_text(encoding="utf-8"), original)

        env, _state, _log = self.fake_codex_env()
        recovered = subprocess.run(
            [str(QTEAM), "setup", str(self.repo)], cwd=SOURCE, env=env,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(recovered.returncode, 0,
                         recovered.stdout + recovered.stderr)
        installed_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(installed_manifest["phase"], "installed")
        self.assertNotEqual(installed_manifest["stamp"], interrupted_manifest["stamp"])

    def test_setup_fails_closed_on_corrupt_existing_manifest(self):
        self.run_tool(PROJECT_SETUP, str(self.repo), check=True)
        manifest_path = self.repo / ".codex/qteam-project.json"
        manifest_path.write_text("{broken", encoding="utf-8")
        env, _state, _log = self.fake_codex_env()
        blocked = subprocess.run(
            [str(QTEAM), "setup", str(self.repo)], cwd=SOURCE, env=env,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertNotEqual(blocked.returncode, 0)
        self.assertNotIn("QTeam setup complete", blocked.stdout)
        self.assertTrue((self.repo / ".codex/agents/researcher.toml").is_file())

    def test_setup_upgrades_schema_v2_and_previous_v3_runtime_contracts(self):
        researcher = self.repo / ".codex/agents/researcher.toml"
        researcher.parent.mkdir(parents=True)
        researcher.write_text("pre-plugin researcher\n", encoding="utf-8")
        researcher.chmod(0o600)
        marker = self.repo / ".codex/agent-team-template.version"
        marker.write_text("agent-team-template-version: 0.5.0\n", encoding="utf-8")
        legacy_skill = self.repo / ".agents/skills/using-superpowers"
        legacy_skill.mkdir(parents=True)
        (legacy_skill / "SKILL.md").write_text("legacy\n", encoding="utf-8")
        for schema_version in (2, 3):
            with self.subTest(schema_version=schema_version):
                self.run_tool(PROJECT_SETUP, str(self.repo), check=True)
                manifest_path = self.repo / ".codex/qteam-project.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                importer_path = ".codex/bin/import-agent-learning"
                autoresearch_license = ".codex/licenses/Autoresearch-MIT.txt"
                experiment_schema = ".codex/schemas/experiment.schema.json"
                previous_paths = {
                    autoresearch_license, experiment_schema,
                    ".codex/bin/agent_team_eval.py",
                    ".codex/schemas/eval-case.schema.json",
                    ".codex/schemas/trajectory.schema.json",
                    ".codex/licenses/LoopX-MIT.txt",
                    ".codex/licenses/Smart-Ralph-MIT.txt",
                    ".codex/bin/agent-team-artifact",
                    ".codex/bin/agent_team_artifact.py",
                    ".codex/schemas/artifact-lint.schema.json",
                    ".codex/schemas/code-index.schema.json",
                    ".codex/schemas/epic.schema.json",
                    ".codex/schemas/spec-drift.schema.json",
                    ".codex/schemas/decision-gate.schema.json",
                    ".codex/schemas/handoff.schema.json",
                    ".codex/schemas/scenario-coverage.schema.json",
                }
                if schema_version == 2:
                    previous_paths.add(importer_path)
                manifest["installed_files"] = [
                    record for record in manifest["installed_files"]
                    if record["path"] not in previous_paths
                ]
                for previous_path in previous_paths:
                    (self.repo / previous_path).unlink()
                manifest["version"] = "0.6.0"
                if schema_version == 2:
                    manifest["schema_version"] = 2
                    for record in (
                        manifest["installed_files"] + manifest["mutable_files"]
                    ):
                        record.pop("mode")
                        record.pop("prior_mode")
                    spec = importlib.util.spec_from_file_location(
                        "qteam_project_legacy_test",
                        PLUGIN / "scripts/qteam_project.py",
                    )
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    for record in manifest["moved_paths"]:
                        record["backup_sha256"] = module.legacy_tree_digest(
                            self.repo / record["backup"]
                        )
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

                env, _state, _log = self.fake_codex_env()
                upgraded = subprocess.run(
                    [str(QTEAM), "setup", str(self.repo)], cwd=SOURCE, env=env,
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                self.assertEqual(upgraded.returncode, 0,
                                 upgraded.stdout + upgraded.stderr)
                current = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertEqual(current["schema_version"], 3)
                self.assertTrue((self.repo / importer_path).is_file())
                self.assertTrue((self.repo / autoresearch_license).is_file())
                self.assertTrue((self.repo / experiment_schema).is_file())
                self.run_tool(PROJECT_UNINSTALL, str(self.repo), check=True)
                self.assertEqual(researcher.read_text(encoding="utf-8"),
                                 "pre-plugin researcher\n")
                self.assertEqual(researcher.stat().st_mode & 0o777, 0o600)
                self.assertEqual((legacy_skill / "SKILL.md").read_text(
                    encoding="utf-8"), "legacy\n")

    def test_setup_upgrades_v07_runtime_with_new_interaction_contracts(self):
        self.run_tool(PROJECT_SETUP, str(self.repo), check=True)
        manifest_path = self.repo / ".codex/qteam-project.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        additions = {
            ".codex/bin/agent_team_eval.py",
            ".codex/schemas/eval-case.schema.json",
            ".codex/schemas/trajectory.schema.json",
            ".codex/licenses/LoopX-MIT.txt",
            ".codex/licenses/Smart-Ralph-MIT.txt",
            ".codex/bin/agent-team-artifact",
            ".codex/bin/agent_team_artifact.py",
            ".codex/schemas/artifact-lint.schema.json",
            ".codex/schemas/code-index.schema.json",
            ".codex/schemas/epic.schema.json",
            ".codex/schemas/spec-drift.schema.json",
            ".codex/schemas/decision-gate.schema.json",
            ".codex/schemas/handoff.schema.json",
            ".codex/schemas/scenario-coverage.schema.json",
        }
        manifest["installed_files"] = [
            record for record in manifest["installed_files"]
            if record["path"] not in additions
        ]
        for relative in additions:
            (self.repo / relative).unlink()
        manifest["version"] = "0.7.0"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        env, _state, _log = self.fake_codex_env()
        upgraded = subprocess.run(
            [str(QTEAM), "setup", str(self.repo)], cwd=SOURCE, env=env,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(upgraded.returncode, 0, upgraded.stdout + upgraded.stderr)
        current = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(current["version"],
                         (PLUGIN / "VERSION").read_text(encoding="utf-8").strip())
        for relative in additions:
            self.assertTrue((self.repo / relative).is_file())

    def test_uninstall_conflict_makes_no_plugin_or_marketplace_mutation(self):
        env, state, log = self.fake_codex_env()
        state.write_text(json.dumps({"marketplace": True, "plugin": True}),
                         encoding="utf-8")
        env["QTEAM_ROOT"] = str(Path(self.tmp.name) / "different-qteam")
        blocked = subprocess.run(
            [str(QTEAM), "uninstall"], cwd=SOURCE, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(blocked.returncode, 3)
        self.assertIn("different source", blocked.stderr)
        self.assertEqual(json.loads(state.read_text()),
                         {"marketplace": True, "plugin": True})
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertNotIn(["plugin", "remove", "qteam@qteam"], calls)
        self.assertNotIn(["plugin", "marketplace", "remove", "qteam"], calls)


class InteractionContractTests(unittest.TestCase):
    def test_existing_roles_own_decisions_handoffs_scenarios_and_publication(self):
        workflow = (PLUGIN / "skills/agent-team-dev/SKILL.md").read_text(
            encoding="utf-8"
        )
        interaction = (PLUGIN /
                       "skills/agent-team-dev/references/interaction-contract.md").read_text(
                           encoding="utf-8")
        scenario = (PLUGIN /
                    "skills/qteam-tdd/references/scenario-coverage.md").read_text(
                        encoding="utf-8")
        test_designer = (PLUGIN / "agents/test-designer.toml").read_text(
            encoding="utf-8"
        )
        planner = (PLUGIN / "agents/parallel-planner.toml").read_text(
            encoding="utf-8"
        )

        self.assertIn("decision-put", workflow)
        self.assertIn("boundary-check", workflow)
        self.assertIn("typed successor/user-decision/replan", workflow)
        self.assertIn("smallest exact scope", interaction)
        self.assertIn(
            "Do not also put a post-task gate",
            " ".join(interaction.split()),
        )
        self.assertIn("compact operator packet", interaction.lower())
        self.assertIn("Validation scope", interaction)
        self.assertIn("Claim boundary", " ".join(interaction.split()))
        for dimension in (
            "happy-path", "error-path", "boundary", "abuse-security", "scale",
            "concurrency", "temporal", "data-variation", "permissions",
            "integrations", "recovery", "state-transitions",
        ):
            self.assertIn(dimension, scenario)
            self.assertIn(dimension, test_designer)
        self.assertIn("deduplicate equivalents", test_designer)
        self.assertIn("handoff_required", planner)
        self.assertFalse((PLUGIN / "agents/operator.toml").exists())
        self.assertFalse((PLUGIN / "agents/decision-manager.toml").exists())

    def test_loopx_attribution_and_new_schemas_are_packaged(self):
        notices = (PLUGIN / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        license_text = (PLUGIN / "LICENSES/LoopX-MIT.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("LoopX", notices)
        self.assertIn("does not import LoopX's token/quota economy", notices)
        self.assertIn("Copyright (c) 2026 LoopX contributors", license_text)
        for schema in (
            "decision-gate", "handoff", "scenario-coverage", "trajectory",
            "eval-case",
        ):
            self.assertTrue((PLUGIN / f"schemas/{schema}.schema.json").is_file())

    def test_published_schemas_encode_runtime_decision_handoff_scenario_rules(self):
        decision = json.loads((PLUGIN / "schemas/decision-gate.schema.json").read_text(
            encoding="utf-8"
        ))
        scope_rules = decision["properties"]["scope"]["allOf"]
        by_scope = {
            rule["if"]["properties"]["kind"].get("const"): rule["then"]
            for rule in scope_rules
        }
        self.assertEqual(by_scope["global"]["properties"]["targets"]["maxItems"], 0)
        self.assertEqual(
            by_scope["wave"]["properties"]["targets"]["items"]["type"], "integer"
        )
        self.assertIn(
            "publish", by_scope["action"]["properties"]["targets"]["items"]["enum"]
        )
        self.assertIn(
            "outcome", decision["properties"]["resolution"]["required"]
        )
        self.assertEqual(decision["properties"]["question"]["pattern"], "\\S")

        handoff = json.loads((PLUGIN / "schemas/handoff.schema.json").read_text(
            encoding="utf-8"
        ))
        successor_rule = handoff["allOf"][0]["then"]
        decision_rule = handoff["allOf"][1]["then"]
        self.assertEqual(successor_rule["not"]["required"], ["decision_id"])
        self.assertEqual(decision_rule["not"]["required"], ["target_task"])

        scenarios = json.loads((
            PLUGIN / "schemas/scenario-coverage.schema.json"
        ).read_text(encoding="utf-8"))
        dimensions = {
            rule["contains"]["properties"]["dimension"]["const"]
            for rule in scenarios["allOf"]
        }
        self.assertEqual(dimensions, {
            "happy-path", "error-path", "boundary", "abuse-security", "scale",
            "concurrency", "temporal", "data-variation", "permissions",
            "integrations", "recovery", "state-transitions",
        })
        self.assertTrue(all(rule["maxContains"] == 1 for rule in scenarios["allOf"]))
        applicable = scenarios["items"]["allOf"][0]["then"]["properties"]
        not_applicable = scenarios["items"]["allOf"][1]["then"]["properties"]
        self.assertEqual(applicable["seam_ids"]["minItems"], 1)
        self.assertEqual(not_applicable["seam_ids"]["maxItems"], 0)
        prefix_rules = scenarios["items"]["allOf"][2:]
        self.assertEqual(len(prefix_rules), 12)
        self.assertTrue(all(
            rule["then"]["properties"]["scenario"]["pattern"].startswith("^")
            for rule in prefix_rules
        ))

        task = json.loads((PLUGIN / "schemas/task.schema.json").read_text(
            encoding="utf-8"
        ))
        self.assertIn("depends_on", task["required"])
        self.assertTrue(task["properties"]["depends_on"]["uniqueItems"])
        run_state = json.loads((
            PLUGIN / "schemas/run-state.schema.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(run_state["properties"]["schema_version"]["const"], 6)


class ExplorationSkillTests(unittest.TestCase):
    def test_exploration_reuses_read_only_roles_and_preserves_qteam_authority(self):
        skill = (PLUGIN / "skills/qteam-explore/SKILL.md").read_text(encoding="utf-8")
        router = (PLUGIN / "skills/qteam-router/SKILL.md").read_text(encoding="utf-8")
        researcher = (PLUGIN / "agents/researcher.toml").read_text(encoding="utf-8")
        architect = (PLUGIN / "agents/architect.toml").read_text(encoding="utf-8")
        review = (PLUGIN / "skills/qteam-review/SKILL.md").read_text(encoding="utf-8")
        skill_flat = " ".join(skill.split())

        self.assertIn('fork_turns="none"', skill)
        self.assertIn("No new role is needed", skill)
        self.assertIn("No unbounded mode is allowed", skill)
        self.assertIn("## Non-negotiable runtime boundary", skill)
        self.assertIn("follow the capability classification, deadline, cancellation, and fallback algorithm", skill_flat)
        self.assertIn("held-out acceptance check", skill)
        self.assertIn("two consecutive probes produce no new/extended result", skill)
        self.assertIn("qteam-explore", router)
        self.assertIn("qteam-explore", researcher)
        self.assertIn("Do not invoke qteam-explore", researcher)
        self.assertIn("qteam-explore", architect)
        self.assertIn("mandatory on every gate", review)
        self.assertFalse((PLUGIN / "agents/experimenter.toml").exists())
        self.assertFalse((PLUGIN / "worker-prompts/experimenter.md").exists())

        expected_agents = {
            "architect.toml", "parallel-planner.toml", "researcher.toml",
            "risk-reviewer.toml", "spec-reviewer.toml",
            "standards-reviewer.toml", "test-designer.toml",
        }
        self.assertEqual({path.name for path in (PLUGIN / "agents").glob("*.toml")},
                         expected_agents)

        plugin_files = [
            path for path in PLUGIN.rglob("*")
            if path.is_file() and "upstream" not in path.relative_to(PLUGIN).parts
        ]
        frontier_surface = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore") for path in plugin_files
        ).lower()
        for forbidden in ("token_budget", "token_limit", "tokens_used", "token_cost"):
            self.assertNotIn(forbidden, frontier_surface)
        self.assertFalse(any("token" in path.name.lower() for path in plugin_files))

    def test_research_frontier_separates_breadth_promotion_depth_and_falsification(self):
        skill = (PLUGIN / "skills/qteam-explore/SKILL.md").read_text(encoding="utf-8")
        rule = (PLUGIN / "skills/qteam-explore/references/research-frontier-rule.md").read_text(
            encoding="utf-8")
        researcher = (PLUGIN / "agents/researcher.toml").read_text(encoding="utf-8")
        architect = (PLUGIN / "agents/architect.toml").read_text(encoding="utf-8")
        router = (PLUGIN / "skills/qteam-router/SKILL.md").read_text(encoding="utf-8")
        rule_flat = " ".join(rule.split())
        researcher_flat = " ".join(researcher.split())
        architect_flat = " ".join(architect.split())

        self.assertIn("BREADTH -> PROMOTE -> DEPTH -> FALSIFY -> HANDOFF", skill)
        self.assertIn("BREADTH -> PROMOTE -> DEPTH -> FALSIFY -> HANDOFF", rule)
        for dimension in ("repo-native", "external-analogs", "adversarial"):
            self.assertIn(dimension, rule)
        self.assertIn("Promote a candidate only when all are true", rule)
        self.assertIn("every promoted candidate has a depth dossier", rule_flat)
        self.assertIn("No mode may exceed three live researcher lanes", rule)
        self.assertIn("status: complete|disproved|blocked", rule)
        self.assertIn("This is a work boundary, not token accounting", rule)
        self.assertIn("blocked dossier satisfies protocol", rule_flat)
        self.assertIn("second consecutive no-progress cycle triggers cancellation", rule_flat)
        self.assertIn("runtime-enforced|coordinator-observed|unsupported", rule)
        self.assertIn("stop_reason: deadline-unenforceable", rule_flat)
        self.assertIn("author: coordinator-fallback", rule_flat)
        self.assertIn("stop_reason: worker-deadline", rule_flat)
        self.assertIn("stop_reason: cancel-failed", rule_flat)
        self.assertIn("unsupported` packet that was never launched needs no cancellation", rule_flat)
        self.assertIn("falsification_status: blocked", rule)
        self.assertIn("frontier_phase=breadth or frontier_phase=depth", researcher)
        self.assertIn("at most two candidate cards without promoting them", researcher_flat)
        self.assertIn("one promoted candidate", researcher)
        self.assertIn("status=complete, disproved, or blocked", researcher)
        self.assertIn("do not restart the search", architect_flat)
        self.assertIn("blocked dossier is insufficient evidence", architect_flat)
        self.assertIn("permit zero survivors or recommendations", architect_flat)
        self.assertIn("Do not invoke qteam-explore, reroute the request, or spawn agents", architect)
        self.assertIn("explicit deep/broad request", router)

    def test_frontier_brief_and_ui_expose_coverage_and_depth_outputs(self):
        template = (PLUGIN /
                    "skills/qteam-explore/references/exploration-brief-template.md").read_text(
                        encoding="utf-8")
        metadata = (PLUGIN / "skills/qteam-explore/agents/openai.yaml").read_text(
            encoding="utf-8")

        self.assertIn("## Frontier coverage", template)
        self.assertIn("Selection | Result | Author / worker state | Capability / deadline / timestamps", template)
        self.assertIn("complete / blocked / operationally-blocked / not-run", template)
        self.assertIn("## Depth dossiers", template)
        self.assertIn("Distinctness from known options", template)
        self.assertIn("Expected decision impact", template)
        self.assertIn("Strongest contradicting evidence", template)
        self.assertIn("Status: complete | disproved | blocked", template)
        self.assertIn("Attempted probes", template)
        self.assertIn("Author: researcher | coordinator-fallback", template)
        self.assertIn("## Falsification", template)
        self.assertIn("Author: architect | coordinator-fallback", template)
        self.assertIn("## Operational blockers", template)
        self.assertIn("Worker state: live / cancel-failed", template)
        self.assertIn("If falsification is `blocked`, omit Recommendation", template)
        self.assertIn("Recommendation (conditional)", template)
        self.assertIn("wayfinder | user | stop", template)
        self.assertIn("Promotion: promoted | duplicate | disproved | insufficient", template)
        self.assertIn("expand beyond my known options", metadata)
        self.assertIn("deeply investigate", metadata)

    def test_exploration_skill_has_template_metadata_and_attribution(self):
        self.assertTrue((PLUGIN /
                         "skills/qteam-explore/references/exploration-brief-template.md").is_file())
        self.assertTrue((PLUGIN /
                         "skills/qteam-explore/references/research-frontier-rule.md").is_file())
        metadata = (PLUGIN / "skills/qteam-explore/agents/openai.yaml").read_text(
            encoding="utf-8")
        self.assertIn("$qteam-explore", metadata)
        self.assertIn("deeply investigate", metadata)
        notices = (PLUGIN / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        license_text = (PLUGIN / "LICENSES/Autoresearch-MIT.txt").read_text(
            encoding="utf-8")
        self.assertIn("Udit Goenka", notices)
        self.assertIn("Copyright (c) 2026 Udit Goenka", license_text)


class WakeTests(RepoCase):
    def test_corrupt_run_state_fails_closed(self):
        prompt = self.repo / ".codex/worker-prompts/wake-prompt.md"
        prompt.parent.mkdir(parents=True)
        prompt.write_text("bounded wake prompt\n", encoding="utf-8")
        corrupt = self.repo / ".agents/runs/corrupt/state.json"
        corrupt.parent.mkdir(parents=True)
        corrupt.write_text("{broken", encoding="utf-8")
        fake_bin = Path(self.tmp.name) / "bin-wake"
        fake_bin.mkdir()
        fake = fake_bin / "codex"
        fake.write_text("#!/usr/bin/env python3\nraise SystemExit(99)\n", encoding="utf-8")
        fake.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        result = subprocess.run(
            ["bash", str(WAKE), "--exec", "--allow-assumptions", "goal"],
            cwd=self.repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env,
        )
        self.assertEqual(result.returncode, 5)
        self.assertIn("invalid run state", result.stderr)


if __name__ == "__main__":
    unittest.main()
