#!/usr/bin/env python3
"""Transactional state manager for QTeam runs.

Coordinators and workers must use this command instead of editing state.json or
task records directly. Writes are locked, atomic, and mirrored to events.jsonl.
"""

import argparse
import fcntl
import fnmatch
import json
import os
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


PHASES = {
    "INIT", "SPEC_READY", "PLAN_READY", "WAVE_RUNNING",
    "WAVE_VALIDATING", "WAVE_MERGING", "INTEGRATION_TESTING",
    "REVIEWING", "FIXING", "RE_REVIEWING", "REPLANNING",
    "LEARNING_EXPORT", "READY_TO_FINISH", "DONE",
}
TRANSITIONS = {
    "INIT": {"SPEC_READY", "REPLANNING"},
    "SPEC_READY": {"PLAN_READY", "REPLANNING"},
    "PLAN_READY": {"WAVE_RUNNING", "LEARNING_EXPORT", "REPLANNING"},
    "WAVE_RUNNING": {"WAVE_VALIDATING", "REPLANNING"},
    "WAVE_VALIDATING": {"WAVE_MERGING", "REPLANNING"},
    "WAVE_MERGING": {"INTEGRATION_TESTING", "REPLANNING"},
    "INTEGRATION_TESTING": {"REVIEWING", "FIXING", "REPLANNING"},
    "REVIEWING": {"FIXING", "LEARNING_EXPORT", "WAVE_RUNNING", "REPLANNING"},
    "FIXING": {"RE_REVIEWING", "REPLANNING"},
    "RE_REVIEWING": {"FIXING", "LEARNING_EXPORT", "WAVE_RUNNING", "REPLANNING"},
    "REPLANNING": {"SPEC_READY", "PLAN_READY"},
    "LEARNING_EXPORT": {"READY_TO_FINISH", "REPLANNING"},
    "READY_TO_FINISH": {"DONE"},
    "DONE": set(),
}
TASK_STATUSES = {
    "pending", "running", "blocked", "completed", "failed",
    "superseded", "merged", "artifact_complete",
}
TASK_TRANSITIONS = {
    "pending": {"running", "blocked", "superseded"},
    "running": {"blocked", "completed", "failed", "artifact_complete"},
    "blocked": {"pending", "running", "failed", "superseded"},
    "completed": {"merged", "running", "failed"},
    "failed": {"pending", "superseded"},
    "superseded": set(),
    "merged": set(),
    "artifact_complete": set(),
}
TASK_ENV_KEYS = {"TMPDIR", "PORT_BASE", "TEST_DB_NAME", "COMPOSE_PROJECT_NAME", "BUILD_DIR"}
DEFAULT_SHARED_SURFACES = [
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "pyproject.toml", "setup.cfg", "tox.ini", "go.mod", "go.sum",
    "Cargo.toml", "Cargo.lock", "requirements*.txt", "Makefile",
    "CMakeLists.txt", "configure.ac", "Dockerfile*", "docker-compose*.yml",
    ".gitignore", ".github/**", "migrations/**", "schemas/**", "generated/**",
    "**/*.proto", "**/openapi*", "**/schemas/**", "**/fixtures/**",
    "**/__snapshots__/**", "**/*.snap",
]


def safe_task_id(value):
    return bool(value) and all(ch.isalnum() or ch in "._-" for ch in value) \
        and value not in {".", ".."} and ".." not in value


def git(args, cwd, check=True):
    res = subprocess.run(["git", *args], cwd=cwd, text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and res.returncode:
        raise SystemExit(res.stderr.strip() or f"error: git {' '.join(args)} failed")
    return res


def glob_match(path, pattern):
    return fnmatch.fnmatch(path, pattern) or (
        pattern.startswith("**/") and fnmatch.fnmatch(path, pattern[3:]))


def static_prefix(pattern):
    positions = [pattern.find(ch) for ch in "*?[" if ch in pattern]
    return pattern[:min(positions)] if positions else pattern


def patterns_overlap(left, right):
    lp, rp = static_prefix(left), static_prefix(right)
    if not lp:
        return True
    if not rp:
        return not any(ch in left for ch in "*?[") and glob_match(left, right)
    return lp.startswith(rp) or rp.startswith(lp) \
        or glob_match(left, right) or glob_match(right, left)


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def git_root():
    res = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if res.returncode:
        raise SystemExit("error: not inside a Git repository")
    return Path(res.stdout.strip()).resolve()


def resolve_run(repo, value):
    path = Path(value)
    if not path.is_absolute():
        path = repo / (path if "/" in value else Path(".agents/runs") / value)
    path = path.resolve()
    runs_root = (repo / ".agents/runs").resolve()
    if path.parent != runs_root:
        raise SystemExit(f"error: run directory must be a direct child of {runs_root}")
    return path


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


def event_recorded(run_dir, txid):
    path = run_dir / "events.jsonl"
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            if json.loads(line).get("txid") == txid:
                return True
        except json.JSONDecodeError:
            continue
    return False


def apply_transaction(run_dir, transaction):
    for rel, value in transaction["writes"].items():
        path = (run_dir / rel).resolve()
        if run_dir.resolve() not in path.parents:
            raise SystemExit("error: transaction path escaped run directory")
        atomic_json(path, value)
    event = transaction.get("event")
    if event and not event_recorded(run_dir, transaction["txid"]):
        append_event(run_dir, {"txid": transaction["txid"], **event})


def recover_transaction(run_dir):
    intent = run_dir / ".transaction.json"
    if not intent.exists():
        return
    transaction = load_json(intent)
    apply_transaction(run_dir, transaction)
    intent.unlink()
    fd_dir = os.open(run_dir, os.O_RDONLY)
    try:
        os.fsync(fd_dir)
    finally:
        os.close(fd_dir)


def commit_transaction(run_dir, writes, event):
    transaction = {
        "schema_version": 1, "txid": uuid.uuid4().hex,
        "writes": {str(Path(path).relative_to(run_dir)): value
                   for path, value in writes.items()},
        "event": event,
    }
    intent = run_dir / ".transaction.json"
    atomic_json(intent, transaction)
    apply_transaction(run_dir, transaction)
    intent.unlink()
    fd_dir = os.open(run_dir, os.O_RDONLY)
    try:
        os.fsync(fd_dir)
    finally:
        os.close(fd_dir)


@contextmanager
def locked(run_dir):
    run_dir.mkdir(parents=True, exist_ok=True)
    lock_path = run_dir / ".state.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        recover_transaction(run_dir)
        yield


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"error: missing {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"error: invalid JSON in {path}: {exc}")


def append_event(run_dir, event):
    event = {"ts": now(), **event}
    path = run_dir / "events.jsonl"
    needs_newline = path.exists() and path.stat().st_size > 0
    if needs_newline:
        with path.open("rb") as check:
            check.seek(-1, os.SEEK_END)
            needs_newline = check.read(1) != b"\n"
    with path.open("a", encoding="utf-8") as out:
        if needs_newline:
            out.write("\n")
        out.write(json.dumps(event, sort_keys=True) + "\n")
        out.flush()
        os.fsync(out.fileno())


def integration_head(repo, state):
    return git(["rev-parse", state["integration_branch"]], repo).stdout.strip()


def validate_reviews(repo, run_dir, state, head_sha=None, require_risk=None):
    head_sha = head_sha or integration_head(repo, state)
    axes = ["spec", "standards"]
    risk = state.get("risk_required", False) if require_risk is None else require_risk
    if risk:
        axes.append("risk")
    errors = []
    attestations = {}
    for axis in axes:
        ledgers = []
        for path in (run_dir / "reviews").glob(f"wave-*-{axis}*.json"):
            ledger = load_json(path)
            packet = ledger.get("packet", {})
            if packet.get("axis") == axis:
                ledgers.append((packet.get("iteration", 1), path, ledger))
        if not ledgers:
            errors.append(f"missing {axis} review")
            continue
        open_ids = [item.get("id") for _, _, ledger in ledgers
                    for item in ledger.get("findings", []) if item.get("status") == "open"]
        if open_ids:
            errors.append(f"{axis} unresolved: {', '.join(open_ids)}")
        latest = max(ledgers, key=lambda item: (item[2]["packet"].get("wave", 0), item[0]))[2]
        if not latest.get("completed_at"):
            errors.append(f"{axis} latest review incomplete")
        if latest.get("packet", {}).get("head_sha") != head_sha:
            errors.append(f"{axis} latest review does not cover integration HEAD")
        attestation = latest.get("attestation", {})
        result = attestation.get("result", {})
        if (not attestation.get("reviewer") or not attestation.get("session_id")
                or result.get("axis") != axis or result.get("verdict") != "pass"):
            errors.append(f"{axis} latest review has no valid attestation")
        else:
            attestations[axis] = attestation
    if all(axis in attestations for axis in ("spec", "standards")):
        if attestations["spec"]["session_id"] == attestations["standards"]["session_id"]:
            errors.append("spec and standards reviews must use distinct sessions")
        if attestations["spec"]["reviewer"] == attestations["standards"]["reviewer"]:
            errors.append("spec and standards reviews must use distinct reviewers")
    if errors:
        raise SystemExit("error: review gate invalid: " + "; ".join(errors))
    return axes


def wave_tasks(run_dir, state, wave):
    records = []
    for task_id in state.get("tasks", {}):
        task = load_json(run_dir / "tasks" / f"{task_id}.json")
        if task.get("wave") == wave:
            records.append(task)
    return records


def validate_merged_tasks(repo, run_dir, state, head_sha):
    """Prove every merged task's checked code is still in the frozen run head."""
    errors = []
    for task_id, summary in state.get("tasks", {}).items():
        if summary.get("status") != "merged":
            continue
        task = load_json(run_dir / "tasks" / f"{task_id}.json")
        checked = task.get("check_result", {}).get("head_sha")
        merge_commit = task.get("merge_commit")
        proof = task.get("merge_proof", {})
        if task.get("check_result", {}).get("status") != "passed" or not checked:
            errors.append(f"{task_id} has no passed mechanical check")
            continue
        if not merge_commit:
            errors.append(f"{task_id} has no recorded merge commit")
            continue
        if (proof.get("checked_head") != checked
                or proof.get("integration_commit") != merge_commit
                or proof.get("mode") not in {"ancestor", "patch-equivalent"}):
            errors.append(f"{task_id} has no valid immutable merge proof")
        if git(["merge-base", "--is-ancestor", merge_commit, head_sha], repo,
               check=False).returncode:
            errors.append(f"{task_id} merge commit is absent from integration")
    if errors:
        raise SystemExit("error: merged task provenance invalid: " + "; ".join(errors))


def cmd_init(args, repo, run_dir):
    state_file = run_dir / "state.json"
    with locked(run_dir):
        if state_file.exists():
            state = load_json(state_file)
            if state.get("finished"):
                raise SystemExit("error: run already exists and is finished")
            print(json.dumps(state, indent=2, sort_keys=True))
            return
        base_branch = args.base_branch or subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=repo, text=True
        ).strip()
        base_commit = args.base_commit or subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()
        state = {
            "schema_version": 2,
            "run_id": run_dir.name,
            "goal": args.goal,
            "base_branch": base_branch,
            "base_commit": base_commit,
            "integration_branch": args.integration_branch or f"agent/{run_dir.name}/integration",
            "phase": "INIT",
            "current_wave": 0,
            "waves": {},
            "tasks": {},
            "gates": {
                "final_verification": {"status": "pending"},
                "reviews": {"status": "pending"},
                "learning": {"status": "pending"},
            },
            "risk_required": args.require_risk,
            "shared_surfaces": list(dict.fromkeys([*DEFAULT_SHARED_SURFACES,
                                                     *args.shared_surface])),
            "plan_file": args.plan_file,
            "finished": False,
            "created_at": now(),
            "updated_at": now(),
        }
        commit_transaction(run_dir, {state_file: state},
                           {"event": "run_created", "phase": "INIT"})
    print(json.dumps(state, indent=2, sort_keys=True))


def cmd_phase(args, _repo, run_dir):
    target = args.phase
    if target not in PHASES or target == "DONE":
        raise SystemExit("error: invalid phase (DONE is set only by finish)")
    with locked(run_dir):
        path = run_dir / "state.json"
        state = load_json(path)
        old = state["phase"]
        if target == old:
            print(old)
            return
        if target not in TRANSITIONS.get(old, set()):
            raise SystemExit(f"error: illegal phase transition {old} -> {target}")
        wave = args.wave if args.wave is not None else state.get("current_wave", 0)
        if target == "WAVE_RUNNING":
            if not wave:
                raise SystemExit("error: WAVE_RUNNING requires --wave")
            records = wave_tasks(run_dir, state, wave)
            if not records or any(task.get("status") not in {"pending", "blocked"}
                                  for task in records):
                raise SystemExit("error: wave must have only pending/blocked tasks before start")
        elif target == "WAVE_VALIDATING":
            records = wave_tasks(run_dir, state, wave)
            if not records or any(task.get("status") != "running" for task in records):
                raise SystemExit("error: all wave tasks must be running before validation")
        elif target == "WAVE_MERGING":
            records = wave_tasks(run_dir, state, wave)
            if not records or any(task.get("status") != "completed" for task in records):
                raise SystemExit("error: all wave tasks must pass checks before merging")
        elif target in {"INTEGRATION_TESTING", "REVIEWING"}:
            records = wave_tasks(run_dir, state, wave)
            if records and any(task.get("status") != "merged" for task in records):
                raise SystemExit("error: all wave tasks must be merged before integration/review")
        if target == "READY_TO_FINISH":
            gates = state.get("gates", {})
            required = {
                "final_verification": {"passed"},
                "reviews": {"passed"},
                "learning": {"passed", "skipped"},
            }
            missing = [name for name, statuses in required.items()
                       if gates.get(name, {}).get("status") not in statuses]
            if missing:
                raise SystemExit(f"error: finish gates not satisfied: {', '.join(missing)}")
            unfinished = [task for task, value in state.get("tasks", {}).items()
                          if value.get("status") not in {"merged", "superseded", "artifact_complete"}]
            if unfinished:
                raise SystemExit("error: unfinished tasks block finish: " + ", ".join(unfinished))
            head = integration_head(_repo, state)
            if gates["final_verification"].get("head_sha") != head:
                raise SystemExit("error: final verification does not cover integration HEAD")
            validate_merged_tasks(_repo, run_dir, state, head)
            validate_reviews(_repo, run_dir, state, head)
        if target in {"WAVE_RUNNING", "FIXING", "REPLANNING"}:
            gates = state.setdefault("gates", {})
            gates["reviews"] = {"status": "pending", "updated_at": now()}
            gates["final_verification"] = {"status": "pending", "updated_at": now()}
            if target == "REPLANNING":
                gates["learning"] = {"status": "pending", "updated_at": now()}
        state["phase"] = target
        if args.wave is not None:
            state["current_wave"] = args.wave
        state["updated_at"] = now()
        commit_transaction(run_dir, {path: state},
                           {"event": "phase", "from": old, "to": target,
                            "reason": args.reason})
    print(target)


def cmd_task_put(args, _repo, run_dir):
    record = load_json(Path(args.file))
    task_id = record.get("id")
    if not safe_task_id(task_id):
        raise SystemExit("error: task record needs a safe non-empty id")
    status = record.setdefault("status", "pending")
    if status != "pending":
        raise SystemExit("error: new/replaced task records must start pending")
    required = ["write_set", "forbidden_paths", "branch", "worktree", "wave",
                "base_commit", "parallel_group", "verification"]
    missing = [key for key in required if key not in record]
    if missing:
        raise SystemExit(f"error: task record missing required fields: {', '.join(missing)}")
    nonempty = [key for key in ("write_set", "branch", "worktree", "base_commit",
                                "parallel_group", "verification") if not record.get(key)]
    if nonempty:
        raise SystemExit(f"error: task record has empty required fields: {', '.join(nonempty)}")
    if not isinstance(record["wave"], int) or record["wave"] < 1:
        raise SystemExit("error: task wave must be a positive integer")
    with locked(run_dir):
        state_path = run_dir / "state.json"
        state = load_json(state_path)
        if state.get("finished") or state.get("phase") == "DONE":
            raise SystemExit("error: finished run is immutable")
        if state.get("phase") not in {"INIT", "SPEC_READY", "PLAN_READY", "REPLANNING"}:
            raise SystemExit("error: tasks may be created/replaced only during planning")
        task_path = run_dir / "tasks" / f"{task_id}.json"
        if task_path.exists() and not args.replace:
            raise SystemExit(f"error: task {task_id} exists; use --replace")
        if task_path.exists():
            existing = load_json(task_path)
            if existing.get("status") not in {"pending", "blocked", "failed"}:
                raise SystemExit(f"error: cannot replace task {task_id} in status "
                                 f"{existing.get('status')}")
        worktree = Path(record["worktree"])
        if not worktree.is_absolute():
            worktree = (_repo / worktree).resolve()
        expected = (run_dir / "worktrees" / task_id).resolve()
        if worktree != expected:
            raise SystemExit(f"error: task worktree must be exactly {expected}")
        expected_branch = f"agent/{run_dir.name}/{task_id}"
        if record["branch"] != expected_branch:
            raise SystemExit(f"error: task branch must be exactly {expected_branch}")
        shared = state.get("shared_surfaces", DEFAULT_SHARED_SURFACES)
        declared_shared = record.get("allow_shared_surfaces", [])
        unknown_shared = [pattern for pattern in declared_shared if pattern not in shared]
        if unknown_shared:
            raise SystemExit("error: task declares shared surfaces not registered in run: "
                             + ", ".join(unknown_shared))
        if declared_shared and record.get("parallel_group") != "serial":
            raise SystemExit("error: tasks allowed to touch shared surfaces must be serial")
        planned_shared = [surface for surface in shared
                          if any(patterns_overlap(write, surface)
                                 for write in record.get("write_set", []))]
        undeclared = [surface for surface in planned_shared if surface not in declared_shared]
        if undeclared:
            raise SystemExit("error: write_set overlaps undeclared shared surfaces: "
                             + ", ".join(undeclared))
        record["updated_at"] = now()
        old = state.setdefault("tasks", {}).get(task_id)
        state["tasks"][task_id] = {"status": status, "attempt": record.get("attempt", 1)}
        state["updated_at"] = now()
        commit_transaction(run_dir, {task_path: record, state_path: state},
                           {"event": "task_put", "task": task_id,
                            "replaced": old is not None, "status": status})
    print(task_id)


def cmd_task_status(args, _repo, run_dir):
    if not safe_task_id(args.task):
        raise SystemExit("error: unsafe task id")
    if args.status not in TASK_STATUSES:
        raise SystemExit(f"error: invalid task status {args.status}")
    with locked(run_dir):
        state_path = run_dir / "state.json"
        task_path = run_dir / "tasks" / f"{args.task}.json"
        state, task = load_json(state_path), load_json(task_path)
        if state.get("finished") or state.get("phase") == "DONE":
            raise SystemExit("error: finished run is immutable")
        if task.get("id") != args.task:
            raise SystemExit("error: task record identity mismatch")
        old = task.get("status", "pending")
        phase = state.get("phase")
        if args.status == "running" and phase not in {
            "WAVE_RUNNING", "FIXING", "INTEGRATION_TESTING", "LEARNING_EXPORT"
        }:
            raise SystemExit(f"error: task cannot run during phase {phase}")
        if args.status == "merged" and phase not in {
            "WAVE_MERGING", "FIXING", "INTEGRATION_TESTING"
        }:
            raise SystemExit(f"error: task cannot merge during phase {phase}")
        if args.status != old and args.status not in TASK_TRANSITIONS.get(old, set()):
            raise SystemExit(f"error: illegal task transition {old} -> {args.status}")
        if args.status == "completed" and task.get("check_result", {}).get("status") != "passed":
            raise SystemExit("error: task cannot complete before mechanical check passes")
        if args.status == "merged" and not (args.commit or task.get("merge_commit")):
            raise SystemExit("error: merged status requires --commit <integration-commit>")
        if args.status == "merged":
            integration = state["integration_branch"]
            integration_head = git(["rev-parse", integration], _repo).stdout.strip()
            commit = git(["rev-parse", args.commit], _repo).stdout.strip()
            checked_head = task.get("check_result", {}).get("head_sha")
            current_task_head = git(["rev-parse", task["branch"]], _repo).stdout.strip()
            if commit != integration_head:
                raise SystemExit("error: merge commit must equal current integration HEAD")
            if not checked_head or current_task_head != checked_head:
                raise SystemExit("error: task branch changed after mechanical check")
            ancestor = not git(["merge-base", "--is-ancestor", checked_head,
                                integration], _repo, check=False).returncode
            proof_mode = "ancestor"
            if not ancestor:
                cherry = git(["cherry", integration, checked_head,
                              task["base_commit"]], _repo, check=False)
                lines = [line for line in cherry.stdout.splitlines() if line.strip()]
                if cherry.returncode or not lines or any(not line.startswith("-") for line in lines):
                    raise SystemExit(
                        "error: checked task commits are neither ancestors nor patch-equivalent "
                        "in integration; create and gate an integration fix task"
                    )
                proof_mode = "patch-equivalent"
        task["status"] = args.status
        if args.status == "running" and old != "running":
            task.pop("check_result", None)
        if args.failure:
            task["failure"] = args.failure
        if args.commit:
            if args.status == "merged":
                task["merge_commit"] = commit
                task["merge_proof"] = {
                    "mode": proof_mode,
                    "checked_head": checked_head,
                    "integration_commit": commit,
                    "recorded_at": now(),
                }
            else:
                task.setdefault("commits", []).append(args.commit)
        task["updated_at"] = now()
        state.setdefault("tasks", {})[args.task] = {
            "status": args.status, "attempt": task.get("attempt", 1)
        }
        state["updated_at"] = now()
        commit_transaction(run_dir, {task_path: task, state_path: state},
                           {"event": "task_status", "task": args.task,
                            "from": old, "to": args.status,
                            "failure": args.failure})
    print(args.status)


def cmd_task_check(args, _repo, run_dir):
    if not safe_task_id(args.task):
        raise SystemExit("error: unsafe task id")
    if args.status not in {"passed", "failed"}:
        raise SystemExit("error: check status must be passed or failed")
    with locked(run_dir):
        state_path = run_dir / "state.json"
        task_path = run_dir / "tasks" / f"{args.task}.json"
        state, task = load_json(state_path), load_json(task_path)
        if state.get("finished") or state.get("phase") == "DONE":
            raise SystemExit("error: finished run is immutable")
        if state.get("phase") not in {"WAVE_VALIDATING", "FIXING", "INTEGRATION_TESTING"}:
            raise SystemExit(f"error: task check is invalid during phase {state.get('phase')}")
        if task.get("id") != args.task:
            raise SystemExit("error: task record identity mismatch")
        old = task.get("status", "pending")
        head_sha = git(["rev-parse", args.head], _repo).stdout.strip()
        branch_head = git(["rev-parse", task["branch"]], _repo).stdout.strip()
        if head_sha != branch_head:
            raise SystemExit("error: check head is not current task branch HEAD")
        task["check_result"] = {
            "status": args.status,
            "base_sha": git(["rev-parse", args.base], _repo).stdout.strip(),
            "head_sha": head_sha,
            "evidence": args.evidence, "checked_at": now(),
        }
        if args.status == "passed":
            if old != "running":
                raise SystemExit(f"error: passing check requires running task, got {old}")
            task["status"] = "completed"
            state.setdefault("tasks", {})[args.task] = {
                "status": "completed", "attempt": task.get("attempt", 1)
            }
        task["updated_at"] = now()
        state["updated_at"] = now()
        commit_transaction(run_dir, {task_path: task, state_path: state},
                           {"event": "task_check", "task": args.task,
                            "result": args.status, "base": args.base,
                            "head": head_sha, "from": old,
                            "to": task.get("status")})
    print(args.status)


def cmd_artifact_complete(args, _repo, run_dir):
    if not safe_task_id(args.task):
        raise SystemExit("error: unsafe task id")
    with locked(run_dir):
        state_path = run_dir / "state.json"
        task_path = run_dir / "tasks" / f"{args.task}.json"
        state, task = load_json(state_path), load_json(task_path)
        if state.get("phase") != "LEARNING_EXPORT" or task.get("status") != "running":
            raise SystemExit("error: artifact completion requires running LEARNING_EXPORT task")
        if task.get("artifact_kind") != args.kind:
            raise SystemExit("error: artifact kind does not match task record")
        task["status"] = "artifact_complete"
        task["artifact_result"] = args.result
        task["updated_at"] = now()
        state["tasks"][args.task] = {
            "status": "artifact_complete", "attempt": task.get("attempt", 1)
        }
        state["updated_at"] = now()
        commit_transaction(run_dir, {task_path: task, state_path: state},
                           {"event": "artifact_complete", "task": args.task,
                            "kind": args.kind, "result": args.result})
    print("artifact_complete")


def run_verification(command, cwd, env, log_path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(["bash", "-lc", command], cwd=cwd, env=env,
                                stdout=log, stderr=subprocess.STDOUT)
    return result.returncode


def cmd_verify_task(args, repo, run_dir):
    if not safe_task_id(args.task):
        raise SystemExit("error: unsafe task id")
    with locked(run_dir):
        state_path = run_dir / "state.json"
        task_path = run_dir / "tasks" / f"{args.task}.json"
        state, task = load_json(state_path), load_json(task_path)
        if state.get("finished") or task.get("status") != "running":
            raise SystemExit("error: task verification requires an active running task")
        if state.get("phase") not in {
            "WAVE_RUNNING", "WAVE_VALIDATING", "FIXING", "INTEGRATION_TESTING"
        }:
            raise SystemExit(f"error: task verification is invalid during phase {state.get('phase')}")
        if task.get("id") != args.task:
            raise SystemExit("error: task record identity mismatch")
        worktree = Path(task["worktree"]).resolve()
        if worktree != (run_dir / "worktrees" / args.task).resolve():
            raise SystemExit("error: task worktree identity mismatch")
        branch = git(["branch", "--show-current"], worktree).stdout.strip()
        if branch != task["branch"]:
            raise SystemExit("error: task worktree is on the wrong branch")
        head = git(["rev-parse", "HEAD"], worktree).stdout.strip()
        env = os.environ.copy()
        env.update({key: str(value) for key, value in task.get("env", {}).items()
                    if key in TASK_ENV_KEYS})
        log_path = run_dir / "verifications" / f"{args.task}.log"
        code = run_verification(task["verification"], worktree, env, log_path)
        evidence = {"command": task["verification"], "exit_code": code,
                    "head_sha": head, "ts": now(),
                    "log": str(log_path.relative_to(run_dir))}
        task.setdefault("verification_evidence", []).append(evidence)
        task["updated_at"] = now()
        commit_transaction(run_dir, {task_path: task},
                           {"event": "task_verification", "task": args.task,
                            "exit_code": code, "head_sha": head,
                            "log": evidence["log"]})
    print(json.dumps(evidence, sort_keys=True))
    if code:
        raise SystemExit(code)


def cmd_verify_final(args, repo, run_dir):
    with locked(run_dir):
        state_path = run_dir / "state.json"
        state = load_json(state_path)
        if state.get("finished") or state.get("phase") not in {"LEARNING_EXPORT", "READY_TO_FINISH"}:
            raise SystemExit("error: final verification requires LEARNING_EXPORT phase")
        worktree = (run_dir / "worktrees" / "integration").resolve()
        if not worktree.is_dir() or Path(git(["rev-parse", "--show-toplevel"], worktree).stdout.strip()).resolve() != worktree:
            raise SystemExit("error: missing exact integration worktree")
        if git(["branch", "--show-current"], worktree).stdout.strip() != state["integration_branch"]:
            raise SystemExit("error: integration worktree is on the wrong branch")
        head = git(["rev-parse", "HEAD"], worktree).stdout.strip()
        log_path = run_dir / "verifications" / "final.log"
        code = run_verification(args.command, worktree, os.environ.copy(), log_path)
        old = state.setdefault("gates", {}).get("final_verification", {}).get("status", "pending")
        state["gates"]["final_verification"] = {
            "status": "passed" if code == 0 else "failed", "command": args.command,
            "exit_code": code, "head_sha": head,
            "log": str(log_path.relative_to(run_dir)), "updated_at": now(),
        }
        state["updated_at"] = now()
        commit_transaction(run_dir, {state_path: state},
                           {"event": "gate", "gate": "final_verification",
                            "from": old, "to": state["gates"]["final_verification"]["status"],
                            "head_sha": head, "log": str(log_path.relative_to(run_dir))})
    print(json.dumps(state["gates"]["final_verification"], sort_keys=True))
    if code:
        raise SystemExit(code)


def cmd_reviews_checked(args, repo, run_dir):
    with locked(run_dir):
        path = run_dir / "state.json"
        state = load_json(path)
        if state.get("finished"):
            raise SystemExit("error: finished run is immutable")
        head = integration_head(repo, state)
        if args.head and git(["rev-parse", args.head], repo).stdout.strip() != head:
            raise SystemExit("error: supplied review head is not integration HEAD")
        effective_risk = args.require_risk or state.get("risk_required", False)
        axes = validate_reviews(repo, run_dir, state, head, effective_risk)
        if effective_risk:
            state["risk_required"] = True
        old = state.setdefault("gates", {}).get("reviews", {}).get("status", "pending")
        state["gates"]["reviews"] = {
            "status": "passed", "head_sha": head, "axes": axes,
            "updated_at": now(),
        }
        state["updated_at"] = now()
        commit_transaction(run_dir, {path: state},
                           {"event": "gate", "gate": "reviews", "from": old,
                            "to": "passed", "head_sha": head, "axes": axes})
    print("passed")


def cmd_event(args, _repo, run_dir):
    details = json.loads(args.details) if args.details else {}
    if not isinstance(details, dict):
        raise SystemExit("error: --details must be a JSON object")
    with locked(run_dir):
        state = load_json(run_dir / "state.json")
        if state.get("finished") or state.get("phase") == "DONE":
            raise SystemExit("error: finished run is immutable")
        append_event(run_dir, {"event": args.event, **details})


def cmd_gate(args, _repo, run_dir):
    allowed = {
        "learning": {"pending", "passed", "failed", "skipped"},
    }
    if args.name not in allowed or args.status not in allowed[args.name]:
        raise SystemExit("error: invalid gate name/status")
    if args.status in {"passed", "skipped", "failed"} and not args.evidence:
        raise SystemExit("error: non-pending gate status requires --evidence")
    with locked(run_dir):
        path = run_dir / "state.json"
        state = load_json(path)
        if state.get("finished") or state.get("phase") == "DONE":
            raise SystemExit("error: finished run is immutable")
        old = state.setdefault("gates", {}).get(args.name, {}).get("status", "pending")
        state["gates"][args.name] = {
            "status": args.status, "evidence": args.evidence, "updated_at": now()
        }
        state["updated_at"] = now()
        commit_transaction(run_dir, {path: state},
                           {"event": "gate", "gate": args.name,
                            "from": old, "to": args.status,
                            "evidence": args.evidence})
    print(args.status)


def cmd_finish(_args, _repo, run_dir):
    with locked(run_dir):
        path = run_dir / "state.json"
        state = load_json(path)
        if state.get("phase") == "DONE" and state.get("finished"):
            print("DONE")
            return
        if state.get("phase") != "READY_TO_FINISH":
            raise SystemExit("error: run is not READY_TO_FINISH")
        unfinished = [task for task, value in state.get("tasks", {}).items()
                      if value.get("status") not in {"merged", "superseded", "artifact_complete"}]
        if unfinished:
            raise SystemExit(f"error: unfinished tasks: {', '.join(unfinished)}")
        current_head = integration_head(_repo, state)
        head = (git(["rev-parse", f"{_args.expected_head}^{{commit}}"], _repo).stdout.strip()
                if _args.expected_head else current_head)
        if _args.check_only and _args.expected_head:
            raise SystemExit("error: --expected-head is invalid with --check-only")
        if state.get("gates", {}).get("final_verification", {}).get("head_sha") != head:
            raise SystemExit("error: stale final verification")
        validate_merged_tasks(_repo, run_dir, state, head)
        validate_reviews(_repo, run_dir, state, head)
        if getattr(_args, "check_only", False):
            print(json.dumps({"status": "READY", "integration_head": head}, sort_keys=True))
            return
        current_branch = git(["branch", "--show-current"], _repo).stdout.strip()
        current_commit = git(["rev-parse", "HEAD"], _repo).stdout.strip()
        if current_branch != state.get("base_branch") or current_commit != head:
            raise SystemExit("error: frozen reviewed head is not checked out on the base branch")
        state["phase"] = "DONE"
        state["finished"] = True
        state["finished_at"] = now()
        state["finished_head"] = head
        state["updated_at"] = now()
        commit_transaction(run_dir, {path: state},
                           {"event": "phase", "from": "READY_TO_FINISH", "to": "DONE"})
    print("DONE")


def cmd_show(_args, _repo, run_dir):
    with locked(run_dir):
        state = load_json(run_dir / "state.json")
    print(json.dumps(state, indent=2, sort_keys=True))


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True, help="run id or path under .agents/runs")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init")
    p.add_argument("--goal", required=True)
    p.add_argument("--base-branch")
    p.add_argument("--base-commit")
    p.add_argument("--integration-branch")
    p.add_argument("--plan-file")
    p.add_argument("--require-risk", action="store_true")
    p.add_argument("--shared-surface", action="append", default=[])
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("phase")
    p.add_argument("phase")
    p.add_argument("--wave", type=int)
    p.add_argument("--reason")
    p.set_defaults(func=cmd_phase)
    p = sub.add_parser("task-put")
    p.add_argument("--file", required=True)
    p.add_argument("--replace", action="store_true")
    p.set_defaults(func=cmd_task_put)
    p = sub.add_parser("task-status")
    p.add_argument("task")
    p.add_argument("status")
    p.add_argument("--failure")
    p.add_argument("--commit")
    p.set_defaults(func=cmd_task_status)
    p = sub.add_parser("task-check")
    p.add_argument("task")
    p.add_argument("status")
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--evidence", required=True)
    p.set_defaults(func=cmd_task_check)
    p = sub.add_parser("artifact-complete")
    p.add_argument("task")
    p.add_argument("--kind", required=True)
    p.add_argument("--result", required=True)
    p.set_defaults(func=cmd_artifact_complete)
    p = sub.add_parser("verify-task")
    p.add_argument("task")
    p.set_defaults(func=cmd_verify_task)
    p = sub.add_parser("verify-final")
    p.add_argument("--command", required=True)
    p.set_defaults(func=cmd_verify_final)
    p = sub.add_parser("reviews-checked")
    p.add_argument("--head")
    p.add_argument("--require-risk", action="store_true")
    p.set_defaults(func=cmd_reviews_checked)
    p = sub.add_parser("event")
    p.add_argument("event")
    p.add_argument("--details")
    p.set_defaults(func=cmd_event)
    p = sub.add_parser("gate")
    p.add_argument("name")
    p.add_argument("status")
    p.add_argument("--evidence")
    p.set_defaults(func=cmd_gate)
    p = sub.add_parser("finish")
    p.add_argument("--check-only", action="store_true")
    p.add_argument("--expected-head",
                   help="frozen reviewed SHA already integrated into the base branch")
    p.set_defaults(func=cmd_finish)
    sub.add_parser("show").set_defaults(func=cmd_show)
    return ap


def main():
    args = parser().parse_args()
    repo = git_root()
    run_dir = resolve_run(repo, args.run)
    args.func(args, repo, run_dir)


if __name__ == "__main__":
    main()
