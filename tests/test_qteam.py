import json
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1]
STATE = SOURCE / "bin/agent-team-state.py"
WORKER = SOURCE / "bin/agent-team-worker.py"
CHECK = SOURCE / "bin/agent-team-check-task.py"
REVIEW = SOURCE / "bin/agent-team-review.py"
FINISH = SOURCE / "bin/agent-team-finish.py"
WAKE = SOURCE / "bin/wake-agent-team.sh"


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

    def init_run(self, run="test-run"):
        result = self.run_tool(STATE, "--run", run, "init", "--goal", "test goal")
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.repo / ".agents/runs" / run

    def make_task(self, run, task="T01", artifact_kind=None):
        branch = f"agent/{run.name}/{task}"
        worktree = run / "worktrees" / task
        worktree.parent.mkdir(parents=True, exist_ok=True)
        self.run_git("worktree", "add", "-b", branch, str(worktree), "HEAD")
        record = {
            "id": task, "title": "change app", "status": "pending",
            "attempt": 1, "branch": branch, "worktree": str(worktree),
            "base_commit": self.run_git("rev-parse", "HEAD").stdout.strip(),
            "parallel_group": "wave-1",
            "wave": 1,
            "write_set": ["app.txt"], "read_set": ["README.md"],
            "forbidden_paths": ["README.md"], "verification": "test -f app.txt",
            "verification_evidence": [],
        }
        if artifact_kind:
            record["artifact_kind"] = artifact_kind
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

    def complete_review(self, run, ledger, axis, reviewer, session_id):
        result = run / "reviews/results" / f"{session_id}.json"
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text(json.dumps({
            "axis": axis, "verdict": "pass", "findings": []
        }), encoding="utf-8")
        return self.run_tool(
            REVIEW, "complete", "--ledger", str(ledger),
            "--reviewer", reviewer, "--session-id", session_id,
            "--result", str(result), check=True,
        )

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
        self.run_tool(STATE, "--run", str(run), "gate", "learning", "skipped",
                      "--evidence", "unit test", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "READY_TO_FINISH", check=True)
        return integration, worktree


class StateTests(RepoCase):
    def test_phase_machine_and_atomic_finish(self):
        run = self.init_run()
        bad = self.run_tool(STATE, "--run", str(run), "phase", "WAVE_RUNNING")
        self.assertNotEqual(bad.returncode, 0)
        self.prepare_ready_run(run)
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


class WorkerTests(RepoCase):
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
            "json.dump({'cwd': os.getcwd(), 'args': sys.argv[1:]}, open(os.environ['FAKE_CODEX_TRACE'], 'w'))\n",
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
        record = json.loads((run / "workers/T01.json").read_text(encoding="utf-8"))
        result = json.loads((run / "workers/T01.result.json").read_text(encoding="utf-8"))
        self.assertEqual(record["cwd"], str(worktree))
        self.assertEqual(result["exit_code"], 0)

    def test_concurrent_spawn_has_single_owner(self):
        run = self.init_run()
        self.make_task(run)
        self.start_wave(run)
        fake_bin = Path(self.tmp.name) / "bin-concurrent"
        fake_bin.mkdir()
        fake = fake_bin / "codex"
        fake.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(0.8)\n", encoding="utf-8")
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
            "import signal, time\n"
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
        with self.assertRaises(ProcessLookupError):
            os.killpg(record["pgid"], 0)

    def test_knowledge_outbox_is_harvested_from_worker_worktree(self):
        run = self.init_run()
        self.make_task(run, artifact_kind="learning")
        self.run_tool(STATE, "--run", str(run), "phase", "SPEC_READY", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "PLAN_READY", check=True)
        self.run_tool(STATE, "--run", str(run), "phase", "LEARNING_EXPORT", check=True)
        fake_bin = Path(self.tmp.name) / "bin-harvest"
        fake_bin.mkdir()
        fake = fake_bin / "codex"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "from pathlib import Path\n"
            "p=Path('.qteam-learning-outbox'); p.mkdir(); (p/'manifest.json').write_text('{}')\n",
            encoding="utf-8")
        fake.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        self.run_tool(WORKER, "spawn", "--run", str(run), "--task", "T01",
                      "--role", "knowledge-distiller", env=env, check=True)
        self.run_tool(WORKER, "wait", "--run", str(run), "--task", "T01",
                      "--timeout", "10", env=env, check=True)
        harvested = self.run_tool(WORKER, "harvest", "--run", str(run), "--task", "T01")
        self.assertEqual(harvested.returncode, 0, harvested.stderr)
        self.assertTrue((run / "learning-outbox/manifest.json").is_file())
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
        fake.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
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
        spec.loader.exec_module(module)
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
            "from pathlib import Path\n"
            "p=Path('real-outbox'); p.mkdir(); (p/'manifest.json').write_text('{}')\n"
            "Path('.qteam-learning-outbox').symlink_to(p, target_is_directory=True)\n",
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
            "--reviewer", "spec-reviewer",
        )
        self.assertEqual(add.returncode, 0, add.stderr)
        blocked = self.run_tool(REVIEW, "complete", "--ledger", str(ledger))
        self.assertNotEqual(blocked.returncode, 0)
        resolved = self.run_tool(
            REVIEW, "resolve", "--ledger", str(ledger), "--id", "F-1",
            "--resolution", "fixed", "--evidence", "test passed",
            "--reviewer", "spec-rereviewer",
        )
        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        completed = self.complete_review(run, ledger, "spec", "spec-rereviewer",
                                         "spec-session-1")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        # Standards is also mandatory for the combined gate.
        self.run_git("branch", "agent/test-run/integration", "HEAD")
        self.run_tool(
            REVIEW, "--run", str(run), "create", "--wave", "1", "--axis", "standards",
            "--base", "HEAD^", "--head", "HEAD", "--standards-source", "README.md",
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
                 "--body", "evidence", "--reviewer", "spec-reviewer"],
                cwd=self.repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ))
        results = [proc.communicate(timeout=10) for proc in procs]
        self.assertTrue(all(proc.returncode == 0 for proc in procs), results)
        saved = json.loads(ledger.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["findings"]), 20)

    def test_old_iteration_open_finding_blocks_new_iteration(self):
        run = self.init_run()
        self.run_git("branch", "agent/test-run/integration", "HEAD")
        for iteration in (1, 2):
            extra = [] if iteration == 1 else ["--iteration", "2", "--scope", "fix"]
            self.run_tool(REVIEW, "--run", str(run), "create", "--wave", "1",
                          "--axis", "spec", *extra, "--base", "HEAD", "--head", "HEAD",
                          "--spec-source", "README.md", check=True)
        first = run / "reviews/wave-1-spec.json"
        self.run_tool(REVIEW, "add", "--ledger", str(first), "--id", "F-OLD",
                      "--severity", "P1", "--title", "old", "--body", "still open",
                      "--reviewer", "spec-reviewer", check=True)
        second = run / "reviews/wave-1-spec-r2.json"
        self.complete_review(run, second, "spec", "spec-rereviewer", "spec-session-2")
        self.run_tool(REVIEW, "--run", str(run), "create", "--wave", "1",
                      "--axis", "standards", "--base", "HEAD", "--head", "HEAD",
                      "--standards-source", "README.md", check=True)
        self.complete_review(run, run / "reviews/wave-1-standards.json", "standards",
                             "standards-reviewer", "standards-session-1")
        gate = self.run_tool(REVIEW, "--run", str(run), "check", "--wave", "1",
                             "--head", "HEAD")
        self.assertEqual(gate.returncode, 1)
        self.assertIn("F-OLD", gate.stdout)

    def test_mandatory_review_axes_require_distinct_sessions(self):
        run = self.init_run()
        self.run_git("branch", "agent/test-run/integration", "HEAD")
        for axis, flag, reviewer in (
            ("spec", "--spec-source", "spec-reviewer"),
            ("standards", "--standards-source", "standards-reviewer"),
        ):
            self.run_tool(REVIEW, "--run", str(run), "create", "--wave", "1",
                          "--axis", axis, "--base", "HEAD", "--head", "HEAD",
                          flag, "README.md", check=True)
            self.complete_review(run, run / f"reviews/wave-1-{axis}.json", axis,
                                 reviewer, "same-session")
        gate = self.run_tool(REVIEW, "--run", str(run), "check", "--wave", "1",
                             "--head", "HEAD")
        self.assertNotEqual(gate.returncode, 0)
        self.assertIn("distinct sessions", gate.stdout)


class FinishTests(RepoCase):
    def test_push_only_is_rejected_before_git_mutation(self):
        result = self.run_tool(FINISH, "--push", "--yes")
        self.assertEqual(result.returncode, 3)
        self.assertIn("--push requires --commit", result.stderr)

    def test_successful_fast_forward_closes_run(self):
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
            FINISH, "--commit", "integrate", "--allow-default-branch", "--yes"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        state = json.loads((run / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["phase"], "DONE")
        self.assertTrue(state["finished"])
        self.assertEqual(self.run_git("rev-parse", "main").stdout,
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
        result = self.run_tool(FINISH, "--commit", "integrate", "--allow-default-branch", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.run_git("rev-parse", "main").stdout.strip(), before)

    def test_finish_fails_closed_on_corrupt_run_state(self):
        corrupt = self.repo / ".agents/runs/corrupt/state.json"
        corrupt.parent.mkdir(parents=True)
        corrupt.write_text("{broken", encoding="utf-8")
        result = self.run_tool(FINISH)
        self.assertEqual(result.returncode, 3)
        self.assertIn("corrupt state", result.stderr)

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
                  "parallel_group": "wave-1", "wave": 1,
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

    def test_wal_intent_is_recovered_before_read(self):
        run = self.init_run()
        state = json.loads((run / "state.json").read_text(encoding="utf-8"))
        state["goal"] = "recovered goal"
        intent = {"schema_version": 1, "txid": "recovery-test",
                  "writes": {"state.json": state},
                  "event": {"event": "recovered_test"}}
        (run / ".transaction.json").write_text(json.dumps(intent), encoding="utf-8")
        shown = self.run_tool(STATE, "--run", str(run), "show")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(json.loads(shown.stdout)["goal"], "recovered goal")
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

        # Reset the integration branch, then refresh every head-bound gate. The
        # merged-task provenance gate must still detect that T01 disappeared.
        self.run_git("reset", "--hard", "main", cwd=integration_worktree)
        for axis, flag, reviewer in (
            ("spec", "--spec-source", "spec-rereviewer"),
            ("standards", "--standards-source", "standards-rereviewer"),
        ):
            self.run_tool(REVIEW, "--run", str(run), "create", "--wave", "1",
                          "--axis", axis, "--iteration", "2", "--scope", "fix",
                          "--base", "main", "--head", integration,
                          flag, "README.md", check=True)
            self.complete_review(run, run / f"reviews/wave-1-{axis}-r2.json", axis,
                                 reviewer, f"{axis}-reset-session")
        self.run_tool(REVIEW, "--run", str(run), "check", "--wave", "1",
                      "--head", integration, check=True)
        self.run_tool(STATE, "--run", str(run), "verify-final", "--command", "true",
                      check=True)
        self.run_tool(STATE, "--run", str(run), "gate", "learning", "skipped",
                      "--evidence", "unit test", check=True)
        ready = self.run_tool(STATE, "--run", str(run), "phase", "READY_TO_FINISH")
        self.assertNotEqual(ready.returncode, 0)
        self.assertIn("merged task provenance invalid", ready.stderr)


class InstallerTests(RepoCase):
    def test_install_has_read_only_agents_and_isolated_worker_tools(self):
        old_agents = self.repo / ".codex/agents"
        old_agents.mkdir(parents=True)
        (old_agents / "developer.toml").write_text("old writable role\n", encoding="utf-8")
        old_skill = self.repo / ".agents/skills/using-superpowers"
        old_skill.mkdir(parents=True)
        (old_skill / "USER_MARKER").write_text("preserve me\n", encoding="utf-8")
        (old_skill / "SKILL.md").write_text("competing live trigger\n", encoding="utf-8")
        old_qteam = self.repo / ".agents/skills/agent-team-dev"
        old_qteam.mkdir(parents=True)
        (old_qteam / "USER_MARKER").write_text("old qteam\n", encoding="utf-8")
        result = subprocess.run(
            ["bash", str(SOURCE / "install.sh"), str(self.repo)], cwd=SOURCE,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((old_agents / "developer.toml").exists())
        self.assertTrue(list(old_agents.glob("developer.toml.bak.*")))
        self.assertIn("old writable role", list(old_agents.glob("developer.toml.bak.*"))[0].read_text())
        self.assertTrue((old_agents / "test-designer.toml").is_file())
        self.assertTrue((self.repo / ".codex/bin/agent-team-worker").is_file())
        self.assertTrue((self.repo / ".codex/bin/agent-team-state").is_file())
        self.assertTrue((self.repo / ".codex/bin/agent-team-review").is_file())
        self.assertTrue((self.repo / ".agents/skills/qteam-router/SKILL.md").is_file())
        self.assertTrue((self.repo / ".agents/skills/goal-execution-discipline/SKILL.md").is_file())
        self.assertTrue((self.repo / ".agents/skills/grill-with-docs/SKILL.md").is_file())
        self.assertFalse(old_skill.exists())
        backup_root = self.repo / ".codex/qteam-backups/skills"
        skill_backups = list(backup_root.glob("using-superpowers.*"))
        self.assertTrue(skill_backups)
        self.assertTrue((skill_backups[0] / "USER_MARKER").is_file())
        qteam_backups = list(backup_root.glob("agent-team-dev.*"))
        self.assertEqual(len(qteam_backups), 1)
        self.assertEqual((qteam_backups[0] / "USER_MARKER").read_text(), "old qteam\n")
        live_competing = [path for path in (self.repo / ".agents/skills").glob("*/SKILL.md")
                          if path.parent.name == "using-superpowers"]
        self.assertEqual(live_competing, [])
        # Doctor validates every schema, not only run-state.
        (self.repo / ".codex/schemas/finding.schema.json").write_text("{broken", encoding="utf-8")
        doctor = subprocess.run([str(self.repo / ".codex/bin/agent-team-doctor")],
                                cwd=self.repo, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        self.assertNotEqual(doctor.returncode, 0)
        self.assertIn("finding.schema.json", doctor.stdout)


class WakeTests(RepoCase):
    def test_corrupt_run_state_fails_closed(self):
        prompt = self.repo / ".agents/skills/agent-team-dev/references/wake-prompt.md"
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
