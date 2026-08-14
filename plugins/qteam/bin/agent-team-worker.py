#!/usr/bin/env python3
"""Launch writable QTeam roles as isolated ``codex exec`` processes.

The native subagent API is intentionally not used for writable work: it has no
per-agent cwd boundary. Each worker is pinned to the task record's Git
worktree, uses argv execution (never a shell), and persists its own lifecycle.
"""

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True

from agent_team_policy import (
    DEFAULT_PROJECT_POLICY, core_policy_digest, effective_execution,
    project_policy_digest, safe_identifier,
)
from agent_team_artifact import ArtifactError, locked_regular, safe_regular
from agent_team_eval import (
    codex_version, parse_codex_trace, regular_output, validate_eval_case,
    validate_learning_manifest, wait_capped_process,
)


ROLES = {
    "developer", "debugger", "frontend-debugger", "system-debugger",
    "test-writer", "integration-tester", "fixer", "knowledge-distiller",
}
TASK_ENV_KEYS = {"TMPDIR", "PORT_BASE", "TEST_DB_NAME", "COMPOSE_PROJECT_NAME", "BUILD_DIR"}


def safe_task_id(value):
    return safe_identifier(value)


@contextmanager
def worker_lock(workers):
    if workers.is_symlink() or (workers.exists() and not workers.is_dir()):
        raise SystemExit("error: workers root must be a real directory")
    workers.mkdir(parents=True, exist_ok=True)
    try:
        with locked_regular(workers / ".worker.lock", "worker lifecycle lock"):
            yield
    except ArtifactError as exc:
        raise SystemExit(f"error: {exc}")


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def git_root(cwd=None):
    res = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd,
                         text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode:
        raise SystemExit("error: not inside a Git repository")
    return Path(res.stdout.strip()).resolve()


def resolve_run(repo, value):
    path = Path(value)
    if not path.is_absolute():
        path = repo / (path if "/" in value else Path(".agents/runs") / value)
    path = path.resolve()
    root = (repo / ".agents/runs").resolve()
    if path.parent != root:
        raise SystemExit(f"error: run must be a direct child of {root}")
    return path


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"error: missing {path}")
    except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
        raise SystemExit(f"error: invalid JSON in {path}: {exc}")


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, indent=2, sort_keys=True)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        fd_dir = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd_dir)
        finally:
            os.close(fd_dir)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_text(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def proc_start(pid):
    if type(pid) is not int or pid <= 1:
        return None
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[21]
    except (OSError, IndexError):
        pass
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)], text=True, timeout=1,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = " ".join(result.stdout.split())
    return value if result.returncode == 0 and value else None


def process_identity_matches(record, pid_key, start_key):
    pid = record.get(pid_key)
    expected = record.get(start_key)
    if type(pid) is not int or pid <= 1:
        return False
    return (isinstance(expected, str) and bool(expected)
            and proc_start(pid) == expected)


def alive(record):
    return process_identity_matches(record, "pid", "proc_start")


def parse_worker_digest(path):
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    values = {}
    for field, label in (
        ("validation_scope", "Validation scope:"),
        ("claim_boundary", "Claim boundary:"),
    ):
        matches = [line[len(label):].strip() for line in text.splitlines()
                   if line.startswith(label)]
        if len(matches) != 1 or not matches[0] or len(matches[0]) > 1000:
            raise ValueError(
                f"worker digest requires exactly one non-empty bounded '{label}' line"
            )
        values[field] = matches[0]
    return values


def launching_active(record):
    return process_identity_matches(
        record, "launch_owner_pid", "launch_owner_start"
    )


def validate_worktree(repo, task):
    raw = task.get("worktree")
    if not raw:
        raise SystemExit("error: task record has no worktree")
    worktree = Path(raw)
    if not worktree.is_absolute():
        worktree = repo / worktree
    worktree = worktree.resolve()
    if not worktree.is_dir():
        raise SystemExit(f"error: task worktree does not exist: {worktree}")
    actual_root = git_root(worktree)
    if actual_root != worktree:
        raise SystemExit(f"error: task worktree must be its Git toplevel: {worktree}")
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=worktree, text=True
    ).strip()
    if branch != task.get("branch"):
        raise SystemExit(
            f"error: worktree branch is {branch!r}, expected {task.get('branch')!r}"
        )
    return worktree


def role_prompt(repo, role):
    candidates = [
        repo / ".codex/worker-prompts" / f"{role}.md",
        Path(__file__).resolve().parent.parent / "worker-prompts" / f"{role}.md",
    ]
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise SystemExit(f"error: missing worker prompt for role {role}")


def build_packet(repo, run_dir, task, role, extra_prompt):
    allowed = {
        key: task.get(key) for key in (
            "id", "title", "purpose", "behavior", "branch", "worktree",
            "write_set", "read_set", "forbidden_paths", "env", "tests",
            "verification", "stop_rule", "spec_excerpt", "contracts",
            "work_kind", "risk_flags", "policy", "test_seams",
            "scenario_coverage", "test_paths",
            "diagnosis_command", "failure_pattern",
            "experiment",
        ) if key in task
    }
    prompt = role_prompt(repo, role)
    handoff = ("Before exit, leave the outbox uncommitted for controlled harvest and print a bounded digest."
               if role == "knowledge-distiller" else
               ("Before exit, commit only the final kept experiment state locally, leave .qteam-experiment.json uncommitted, and print a bounded attempt digest."
                if task.get("work_kind") == "experiment" else
                "Before exit, commit task changes locally on the assigned task branch and print a bounded digest with commits, files, verification commands, results, validation scope (what the evidence directly covers), and claim boundary (what it does not establish)."))
    return f"""{prompt}

QTeam is the only orchestration authority. Perform exactly this bounded task.
Do not spawn or coordinate other agents. Do not edit run state JSON directly.

Run: {run_dir.name}
Role: {role}
Task packet:
{json.dumps(allowed, indent=2, sort_keys=True)}

Additional coordinator instruction:
{extra_prompt or '(none)'}

{handoff}
Every digest must include non-empty single-line `Validation scope:` and
`Claim boundary:` fields. State what the evidence directly covers and what it
does not establish; never broaden a claim beyond the recorded evidence.
"""


def state_status(repo, run_dir, task, status, failure=None):
    tool = Path(__file__).with_name("agent-team-state.py")
    if not tool.exists():
        tool = repo / ".codex/bin/agent-team-state"
    cmd = [sys.executable, str(tool), "--run", str(run_dir),
           "task-status", task, status]
    if failure:
        cmd.extend(["--failure", failure])
    res = subprocess.run(cmd, cwd=repo, text=True, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    if res.returncode:
        raise SystemExit(res.stderr.strip() or "error: failed to update task state")


def cmd_spawn(args, repo, run_dir):
    if args.role not in ROLES:
        raise SystemExit(f"error: unsupported writable role {args.role}")
    if not safe_task_id(args.task):
        raise SystemExit("error: unsafe task id")
    task = read_json(run_dir / "tasks" / f"{args.task}.json")
    if task.get("id") != args.task:
        raise SystemExit("error: task record identity mismatch")
    state = read_json(run_dir / "state.json")
    if (state.get("schema_version") != 6
            or task.get("policy", {}).get("policy_version") != 3
            or any(field not in state for field in (
                "project_policy", "policy_layers", "quality_lanes", "work_queue",
            ))):
        raise SystemExit(
            "error: run state/policy contract is not current; run migrate-run first"
        )
    core = state.get("policy_layers", [{}])[0]
    if (not isinstance(core, dict)
            or core.get("sha256") != core_policy_digest()
            or core.get("defaults_sha256")
            != project_policy_digest(DEFAULT_PROJECT_POLICY)):
        raise SystemExit(
            "error: frozen core policy differs from this runtime; run migrate-run first"
        )
    allowed_phases = {
        "developer": {"WAVE_RUNNING", "FIXING"},
        "debugger": {"WAVE_RUNNING", "FIXING"},
        "frontend-debugger": {"WAVE_RUNNING", "FIXING"},
        "system-debugger": {"WAVE_RUNNING", "FIXING"},
        "fixer": {"FIXING"},
        "test-writer": {"INTEGRATION_TESTING"},
        "integration-tester": {"INTEGRATION_TESTING"},
        "knowledge-distiller": {"LEARNING_EXPORT"},
    }
    if state.get("phase") not in allowed_phases[args.role]:
        raise SystemExit(f"error: role {args.role} cannot run during phase {state.get('phase')}")
    if args.role == "knowledge-distiller" and task.get("artifact_kind") != "learning":
        raise SystemExit("error: knowledge-distiller task requires artifact_kind=learning")
    if not task.get("policy"):
        raise SystemExit("error: task has no derived execution policy; replan it through task-put")
    if task.get("policy_migration", {}).get("requires_replan"):
        raise SystemExit("error: migrated task must be replaced during REPLANNING")
    try:
        execution = effective_execution(task["policy"], args.role,
                                        state.get("model_profiles"))
        runner_version = codex_version()
    except (KeyError, ValueError) as exc:
        raise SystemExit(f"error: invalid task execution policy: {exc}")
    worktree = validate_worktree(repo, task)
    workers = run_dir / "workers"
    record_path = workers / f"{args.task}.json"
    result_path = workers / f"{args.task}.result.json"
    with worker_lock(workers):
        if record_path.exists():
            old = read_json(record_path)
            active_old = (old.get("state") == "running" and alive(old)) or (
                old.get("state") == "launching" and launching_active(old))
            if active_old or not args.restart or (result_path.exists() and not args.restart):
                raise SystemExit(f"error: worker record already exists for {args.task}")
        extra = Path(args.prompt).read_text(encoding="utf-8") if args.prompt else args.message
        packet_path = workers / f"{args.task}.prompt.txt"
        atomic_text(packet_path, build_packet(
            repo, run_dir, task, args.role, extra
        ))
        stdout_path = workers / f"{args.task}.stdout.log"
        stderr_path = workers / f"{args.task}.stderr.log"
        final_path = workers / f"{args.task}.final.txt"
        result_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        token = uuid.uuid4().hex
        record = {
            "schema_version": 1, "task": args.task, "role": args.role,
            "backend": "codex-exec", "cwd": str(worktree),
            "execution": execution,
            "runner": {"name": "codex-cli", "version": runner_version},
            "launch_token": token, "started_at": now(),
            "launch_owner_pid": os.getpid(),
            "launch_owner_start": proc_start(os.getpid()),
            "stdout": str(stdout_path.relative_to(run_dir)),
            "stderr": str(stderr_path.relative_to(run_dir)),
            "last_message": str(final_path.relative_to(run_dir)),
            "result": str(result_path.relative_to(run_dir)), "state": "launching",
            "env": {key: str(value) for key, value in task.get("env", {}).items()
                    if key in TASK_ENV_KEYS},
        }
        # Establish the schema/version/decision/task lifecycle gate before any
        # writable process exists. A launch failure is compensated to failed.
        state_status(repo, run_dir, args.task, "running")
        try:
            atomic_json(record_path, record)
            wrapper = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "_run",
                 "--record", str(record_path), "--result", str(result_path),
                 "--stdout", str(stdout_path), "--stderr", str(stderr_path),
                 "--final", str(final_path),
                 "--cwd", str(worktree), "--prompt", str(packet_path),
                 "--model", execution["model"], "--thinking", execution["thinking"],
                 "--token", token, "--workers", str(workers)],
                cwd=repo, start_new_session=True, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            failure = f"worker wrapper launch failed: {type(exc).__name__}: {exc}"
            result = {
                "schema_version": 1, "task": args.task, "exit_code": 127,
                "error": failure, "started_at": record["started_at"],
                "finished_at": now(), "stdout": record["stdout"],
                "stderr": record["stderr"],
            }
            atomic_json(result_path, result)
            record["state"] = "failed"
            record["finished_at"] = result["finished_at"]
            atomic_json(record_path, record)
            state_status(repo, run_dir, args.task, "failed", failure)
            raise SystemExit(f"error: {failure}")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        record = read_json(record_path)
        if (record.get("pid") == wrapper.pid
                and record.get("state") in {
                    "running", "succeeded", "failed", "cancelled",
                }):
            break
        time.sleep(0.02)
    else:
        try:
            os.killpg(wrapper.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        state_status(repo, run_dir, args.task, "failed",
                     "worker wrapper did not claim its launch record")
        raise SystemExit("error: worker wrapper did not claim its launch record")
    print(json.dumps(record, indent=2, sort_keys=True))


def cmd_internal_run(args):
    record_path = Path(args.record)
    with worker_lock(Path(args.workers)):
        record = read_json(record_path)
        if record.get("launch_token") != args.token or record.get("state") != "launching":
            return 125
        record["pid"] = os.getpid()
        record["pgid"] = os.getpgrp()
        record["proc_start"] = proc_start(os.getpid())
        record["state"] = "running"
        atomic_json(record_path, record)
    env = os.environ.copy()
    task = record["task"]
    env.update(record.get("env", {}))
    env.setdefault("TMPDIR", str(Path(args.cwd) / ".agents-tmp" / task))
    if not Path(env["TMPDIR"]).is_absolute():
        env["TMPDIR"] = str((Path(args.cwd) / env["TMPDIR"]).resolve())
    Path(env["TMPDIR"]).mkdir(parents=True, exist_ok=True)
    prompt = Path(args.prompt).read_text(encoding="utf-8")
    started = time.monotonic()
    cancelled = False
    child = None
    try:
        runner_version = codex_version()
    except ValueError as exc:
        runner_version = None
        runner_error = str(exc)
    else:
        runner_error = None
    def on_term(_signum, _frame):
        nonlocal cancelled
        cancelled = True
        if child and child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    signal.signal(signal.SIGTERM, on_term)
    err = None
    try:
        with regular_output(args.stdout, "worker stdout") as out, \
             regular_output(args.stderr, "worker stderr") as err, \
             regular_output(args.final, "worker final message", readwrite=True) as final:
            if runner_error:
                raise RuntimeError(runner_error)
            if record.get("runner") != {
                    "name": "codex-cli", "version": runner_version}:
                raise RuntimeError(
                    "Codex runner version changed after worker launch"
                )
            child = subprocess.Popen(
                ["codex", "exec", "-C", args.cwd, "--sandbox", "workspace-write",
                 "--model", args.model, "-c",
                 f'model_reasoning_effort="{args.thinking}"',
                 "-c", f'model_provider="{record["execution"]["provider"]}"',
                 "--json",
                 "--output-last-message", f"/proc/self/fd/{final.fileno()}", prompt],
                cwd=args.cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                pass_fds=(final.fileno(),), start_new_session=True,
            )
            with worker_lock(Path(args.workers)):
                latest = read_json(record_path)
                latest["child_pid"] = child.pid
                latest["child_start"] = proc_start(child.pid)
                atomic_json(record_path, latest)
            code, overflow = wait_capped_process(
                child, out, err, process_group=True
            )
            error = (
                "Codex runner output exceeded the retained trace limit"
                if overflow else None
            )
            if overflow and code == 0:
                code = 65
            digest = {}
            if code == 0 and not cancelled:
                try:
                    digest = parse_worker_digest(args.final)
                    digest["trajectory"] = parse_codex_trace(
                        args.stdout, "worker", task, record["execution"],
                        runner_version,
                    )
                except (OSError, ValueError) as exc:
                    code, error = 65, str(exc)
                    print(error, file=err)
    except Exception as exc:  # persisted infrastructure failure, never hidden
        code, error = 127, f"{type(exc).__name__}: {exc}"
        digest = {}
        if err is not None and not err.closed:
            print(error, file=err)
    result = {
        "schema_version": 1, "task": task,
        "exit_code": code, "error": error,
        "started_at": record["started_at"], "finished_at": now(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout": record["stdout"], "stderr": record["stderr"],
    }
    if cancelled:
        code = 130
        result["exit_code"] = code
        result["error"] = error or "cancelled"
        result["cancelled"] = True
    if code == 0:
        result.update(digest)
    with worker_lock(Path(args.workers)):
        atomic_json(Path(args.result), result)
        record = read_json(record_path)
        record["state"] = "cancelled" if cancelled else ("succeeded" if code == 0 else "failed")
        record["finished_at"] = result["finished_at"]
        atomic_json(record_path, record)
    return code


def current(run_dir, task):
    if not safe_task_id(task):
        raise SystemExit("error: unsafe task id")
    record = read_json(run_dir / "workers" / f"{task}.json")
    result_path = run_dir / record["result"]
    result = read_json(result_path) if result_path.exists() else None
    if result:
        status = "cancelled" if result.get("cancelled") else (
            "succeeded" if result["exit_code"] == 0 else "failed")
    elif (record.get("state") == "launching" and launching_active(record)) or alive(record):
        status = "running"
    else:
        status = "lost"
    return record, result, status


def cmd_status(args, _repo, run_dir):
    record, result, status = current(run_dir, args.task)
    print(json.dumps({"status": status, "worker": record, "result": result},
                     indent=2, sort_keys=True))
    if status in {"failed", "lost", "cancelled"}:
        raise SystemExit(1)


def cmd_wait(args, _repo, run_dir):
    deadline = None if args.timeout is None else time.monotonic() + args.timeout
    while True:
        record, result, status = current(run_dir, args.task)
        if status != "running":
            print(json.dumps({"status": status, "worker": record, "result": result},
                             indent=2, sort_keys=True))
            raise SystemExit(0 if status == "succeeded" else 1)
        if deadline is not None and time.monotonic() >= deadline:
            print(json.dumps({"status": "running", "worker": record}, indent=2,
                             sort_keys=True))
            raise SystemExit(124)
        time.sleep(0.2)


def cancelled_result(record, task, error="cancelled"):
    return {
        "schema_version": 1, "task": task, "exit_code": 130,
        "error": error, "cancelled": True,
        "started_at": record["started_at"], "finished_at": now(),
        "stdout": record["stdout"], "stderr": record["stderr"],
    }


def cmd_cancel(args, _repo, run_dir):
    record, result, status = current(run_dir, args.task)
    if result:
        print(status)
        return
    pgid = record.get("pgid") or record.get("pid")
    if not pgid:
        with worker_lock(run_dir / "workers"):
            # The wrapper may claim the launch between current() and this
            # lock. Re-read while claim/cancel are serialized and decide from
            # the latest process identity, never from the stale snapshot.
            record_path = run_dir / "workers" / f"{args.task}.json"
            record = read_json(record_path)
            result_path = run_dir / record["result"]
            if result_path.exists():
                print("cancelled" if read_json(result_path).get("cancelled") else "finished")
                return
            pgid = record.get("pgid") or record.get("pid")
            if not pgid:
                cancelled = cancelled_result(record, args.task, "cancelled stale launch")
                atomic_json(result_path, cancelled)
                record["state"] = "cancelled"
                record["finished_at"] = cancelled["finished_at"]
                atomic_json(record_path, record)
                print("cancelled_stale_launch")
                return
    leader_valid = (record.get("pid") == pgid and process_identity_matches(
        record, "pid", "proc_start"
    ))
    child_valid = process_identity_matches(
        record, "child_pid", "child_start"
    )
    groups = []
    if leader_valid:
        groups.append(pgid)
    if child_valid:
        groups.append(record["child_pid"])
    if not groups:
        raise SystemExit("error: recorded process identities are stale; refusing to signal reused PGID")
    for group in dict.fromkeys(groups):
        try:
            os.killpg(group, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        alive = []
        for group in dict.fromkeys(groups):
            try:
                os.killpg(group, 0)
                alive.append(group)
            except ProcessLookupError:
                pass
        if not alive:
            break
        time.sleep(0.05)
    else:
        for group in alive:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
    result_path = run_dir / record["result"]
    if not result_path.exists():
        cancelled = cancelled_result(record, args.task)
        with worker_lock(run_dir / "workers"):
            atomic_json(result_path, cancelled)
            record = read_json(run_dir / "workers" / f"{args.task}.json")
            record["state"] = "cancelled"
            record["finished_at"] = cancelled["finished_at"]
            atomic_json(run_dir / "workers" / f"{args.task}.json", record)
    print("cancelled")


def cmd_harvest(args, _repo, run_dir):
    record, _result, status = current(run_dir, args.task)
    if record.get("role") != "knowledge-distiller" or status != "succeeded":
        raise SystemExit("error: harvest requires a successful knowledge-distiller worker")
    source = Path(record["cwd"]) / ".qteam-learning-outbox"
    target = run_dir / "learning-outbox"
    if source.is_symlink():
        raise SystemExit(f"error: worker outbox root symlink is forbidden: {source}")
    if not source.is_dir() and not target.is_dir():
        raise SystemExit(f"error: worker outbox missing: {source}")
    if source.is_dir():
        for path in source.rglob("*"):
            if path.is_symlink():
                raise SystemExit(f"error: outbox symlinks are forbidden: {path}")
        eval_root = source / "eval-cases"
        if eval_root.exists() and not eval_root.is_dir():
            raise SystemExit("error: learning eval-cases must be a directory")
        if eval_root.is_dir():
            for path in sorted(eval_root.iterdir()):
                if not path.is_file() or path.suffix != ".json":
                    raise SystemExit(
                        "error: learning eval-cases accepts only JSON files"
                    )
                try:
                    case = validate_eval_case(read_json(path), run_dir)
                except (SystemExit, ValueError) as exc:
                    raise SystemExit(f"error: invalid eval case {path.name}: {exc}")
                if path.stem != case["id"]:
                    raise SystemExit(
                        f"error: eval case filename must match id {case['id']}"
                    )
                if case["status"] != "candidate":
                    raise SystemExit(
                        "error: knowledge workers may harvest only candidate eval cases"
                    )
        manifest_path = source / "manifest.json"
        try:
            worker_manifest = read_json(manifest_path)
            validate_learning_manifest(worker_manifest, run_dir.name)
        except (SystemExit, ValueError) as exc:
            raise SystemExit(f"error: invalid learning manifest: {exc}")
        if any(
            item.get("status") != "proposed" or "decision" in item
            for item in worker_manifest["items"]
        ):
            raise SystemExit(
                "error: knowledge workers may harvest only proposed learning items"
            )
    if target.is_symlink():
        raise SystemExit("error: run learning-outbox target is a symlink")
    if target.is_dir():
        for path in target.rglob("*"):
            if path.is_symlink():
                raise SystemExit(f"error: harvested outbox contains a symlink: {path}")
    def manifest(root):
        entries = []
        for path in sorted(root.rglob("*")):
            rel = str(path.relative_to(root))
            if path.is_dir():
                entries.append((rel, "dir", None))
            elif path.is_file():
                entries.append((rel, "file", path.read_bytes()))
        return entries
    if source.is_dir() and target.is_dir() and manifest(source) != manifest(target):
        raise SystemExit("error: existing harvested outbox differs from worker outbox; refusing overwrite")
    if not target.exists():
        staging = Path(tempfile.mkdtemp(prefix=".learning-outbox.", dir=run_dir))
        try:
            for path in source.rglob("*"):
                rel = path.relative_to(source)
                destination = staging / rel
                if path.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                elif path.is_file():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, destination)
            os.replace(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    if source.exists():
        shutil.rmtree(source)
    state_tool = Path(__file__).with_name("agent-team-state.py")
    if not state_tool.exists():
        state_tool = _repo / ".codex/bin/agent-team-state"
    completed = subprocess.run(
        [sys.executable, str(state_tool), "--run", str(run_dir),
         "artifact-complete", args.task, "--kind", "learning",
         "--result", str(target.relative_to(run_dir))], cwd=_repo,
    )
    if completed.returncode:
        raise SystemExit("error: outbox harvested but artifact lifecycle update failed")
    print(target)


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("spawn")
    p.add_argument("--run", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--role", required=True)
    prompt = p.add_mutually_exclusive_group()
    prompt.add_argument("--prompt", help="path to extra coordinator instructions")
    prompt.add_argument("--message", default="", help="short extra coordinator instruction")
    p.add_argument("--restart", action="store_true")
    p.set_defaults(func=cmd_spawn)
    for name, func in (("status", cmd_status), ("wait", cmd_wait),
                       ("cancel", cmd_cancel), ("harvest", cmd_harvest)):
        p = sub.add_parser(name)
        p.add_argument("--run", required=True)
        p.add_argument("--task", required=True)
        if name == "wait":
            p.add_argument("--timeout", type=float)
        p.set_defaults(func=func)
    p = sub.add_parser("_run", help=argparse.SUPPRESS)
    for flag in ("record", "result", "stdout", "stderr", "final", "cwd", "prompt", "token",
                 "workers", "model", "thinking"):
        p.add_argument(f"--{flag}", required=True)
    return ap


def main():
    args = parser().parse_args()
    if args.command == "_run":
        raise SystemExit(cmd_internal_run(args))
    repo = git_root()
    run_dir = resolve_run(repo, args.run)
    args.func(args, repo, run_dir)


if __name__ == "__main__":
    main()
