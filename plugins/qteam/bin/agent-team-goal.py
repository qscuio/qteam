#!/usr/bin/env python3
"""Project QTeam run state into host-native, session-scoped goals.

The run state remains the only delivery authority.  This reader gives Codex,
Claude Code, and Cursor one bounded completion condition and a blocking wait
that does not spend model turns polling external workers.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from agent_team_eval import wait_capped_process


sys.dont_write_bytecode = True

MAX_CAPTURE_BYTES = 2 * 1024 * 1024
MAX_HOOK_BYTES = 64 * 1024
MAX_RECORD_BYTES = 1024 * 1024
MAX_TRACKED_FILES = 1024
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TRACKED_GLOBS = (
    "state.json", "events.jsonl", "workers/*.json", "reviews/wave-*.json",
    "reviews/receipts/*.json", "reviews/results/*.json",
)


class GoalError(ValueError):
    pass


class GoalStatusTimeout(GoalError):
    pass


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def proc_start(pid):
    if type(pid) is not int or pid <= 1:
        return None
    try:
        return Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()[21]
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


def recorded_process_alive(record, *, launching=False):
    pid_key = "launch_owner_pid" if launching else "pid"
    start_key = "launch_owner_start" if launching else "proc_start"
    pid = record.get(pid_key)
    expected = record.get(start_key)
    if type(pid) is not int or pid <= 1:
        return False
    return (isinstance(expected, str) and bool(expected)
            and proc_start(pid) == expected)


def git_root():
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise GoalError(result.stderr.strip() or "not inside a Git repository")
    return Path(result.stdout.strip()).resolve()


def safe_run(repo, run_id):
    if not isinstance(run_id, str) or not SAFE_ID.fullmatch(run_id):
        raise GoalError(f"unsafe run id: {run_id!r}")
    agents = repo / ".agents"
    runs = agents / "runs"
    run_dir = runs / run_id
    if (agents.is_symlink() or runs.is_symlink() or run_dir.is_symlink()
            or not run_dir.is_dir()):
        raise GoalError(f"missing or unsafe QTeam run: {run_id}")
    if run_dir.resolve().parent != runs.resolve():
        raise GoalError("QTeam run escaped the repository run directory")
    return run_dir


def regular(path, label, *, required=True):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise GoalError(f"unsafe {label}: {path}")
    if required and not path.is_file():
        raise GoalError(f"missing {label}: {path}")
    info = path.stat() if path.exists() else None
    if info is not None and info.st_nlink != 1:
        raise GoalError(f"unsafe hard-linked {label}: {path}")
    return path


def run_relative_regular(run_dir, raw, label, *, required=True):
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise GoalError(f"unsafe {label}: {raw!r}")
    relative = PurePosixPath(raw)
    if (relative.is_absolute() or relative.as_posix() != raw
            or any(part in {"", ".", ".."} for part in relative.parts)):
        raise GoalError(f"unsafe {label}: {raw!r}")
    current = run_dir
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink() or not current.is_dir():
            raise GoalError(f"unsafe {label} parent: {raw!r}")
    candidate = run_dir.joinpath(*relative.parts)
    if candidate.resolve(strict=False).parent != current.resolve():
        raise GoalError(f"escaped {label}: {raw!r}")
    return regular(candidate, label, required=required)


def read_bounded_json(path, label):
    regular(path, label)
    with path.open("rb") as handle:
        raw = handle.read(MAX_RECORD_BYTES + 1)
    if len(raw) > MAX_RECORD_BYTES:
        raise GoalError(f"{label} exceeds the bounded size: {path.name}")
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise GoalError(f"invalid {label} {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise GoalError(f"{label} must be an object: {path.name}")
    return value


def state_command():
    override = os.environ.get("QTEAM_STATE_BIN")
    candidates = ([Path(override)] if override else []) + [
        Path(__file__).with_name("agent-team-state"),
        Path(__file__).with_name("agent-team-state.py"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file() and not candidate.is_symlink():
            return ([sys.executable, str(candidate)]
                    if candidate.suffix == ".py" else [str(candidate)])
    raise GoalError("cannot locate the QTeam state command")


def read_status(repo, run_id, *, timeout=30.0):
    command = [*state_command(), "--run", run_id, "status"]
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stdout, \
            tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            command, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
        code, overflow, timed_out = wait_capped_process(
            process, stdout, stderr, limit=MAX_CAPTURE_BYTES,
            process_group=True, timeout_seconds=timeout,
        )
        stdout.seek(0)
        stderr.seek(0)
        try:
            stdout_value = stdout.read(MAX_CAPTURE_BYTES + 1)
            stderr_value = stderr.read(MAX_CAPTURE_BYTES + 1)
        except UnicodeError as exc:
            raise GoalError(f"invalid UTF-8 from QTeam status: {exc}") from exc
    if timed_out:
        raise GoalStatusTimeout("QTeam status exceeded the wait deadline")
    if overflow:
        raise GoalError("QTeam status output exceeded the bounded goal packet")
    if code:
        error = stderr_value.strip()
        raise GoalError(error or "QTeam status failed")
    try:
        packet = json.loads(stdout_value)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise GoalError(f"invalid QTeam status packet: {exc}") from exc
    if not isinstance(packet, dict) or packet.get("run_id") != run_id:
        raise GoalError("QTeam status returned the wrong run identity")
    return packet


def safe_optional_directory(run_dir, raw, label):
    relative = PurePosixPath(raw)
    current = run_dir
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise GoalError(f"unsafe {label}: {raw}")
        if current.exists() and not current.is_dir():
            raise GoalError(f"unsafe {label}: {raw}")
        if not current.exists():
            return None
    return current


def validate_wake_directories(run_dir):
    for raw in ("workers", "reviews", "reviews/receipts", "reviews/results"):
        safe_optional_directory(run_dir, raw, "goal wake directory")


def tracked_signature(run_dir):
    validate_wake_directories(run_dir)
    entries = []
    seen = set()
    for pattern in TRACKED_GLOBS:
        for path in sorted(run_dir.glob(pattern)):
            relative = path.relative_to(run_dir).as_posix()
            if relative in seen:
                continue
            seen.add(relative)
            if len(seen) > MAX_TRACKED_FILES:
                raise GoalError("QTeam goal wake set exceeds the bounded file count")
            regular(path, f"goal wake file {relative}")
            info = path.stat()
            entries.append((relative, info.st_ino, info.st_size, info.st_mtime_ns))
    return entries


def external_work(run_dir):
    active = []
    records_seen = 0
    workers = safe_optional_directory(run_dir, "workers", "workers directory")
    if workers is not None:
        for record_path in sorted(workers.glob("*.json")):
            if record_path.name.endswith(".result.json"):
                continue
            records_seen += 1
            if records_seen > MAX_TRACKED_FILES:
                raise GoalError("QTeam worker records exceed the bounded count")
            regular(record_path, "worker record")
            record = read_bounded_json(record_path, "worker record")
            result = record.get("result")
            state = record.get("state")
            if state in {"launching", "running"}:
                result_path = run_relative_regular(
                    run_dir, result, "worker result", required=False,
                ) if isinstance(result, str) else None
                task = record.get("task", record_path.stem)
                if not isinstance(task, str) or not SAFE_ID.fullmatch(task):
                    raise GoalError(f"invalid task identity in {record_path.name}")
                alive = recorded_process_alive(
                    record, launching=state == "launching"
                )
                if alive and (result_path is None or not result_path.exists()):
                    active.append(task)
    receipts = safe_optional_directory(
        run_dir, "reviews/receipts", "review receipts directory"
    )
    if receipts is not None:
        for receipt_path in sorted(receipts.glob("*.json")):
            records_seen += 1
            if records_seen > MAX_TRACKED_FILES:
                raise GoalError("QTeam goal records exceed the bounded count")
            regular(receipt_path, "review receipt")
            receipt = read_bounded_json(receipt_path, "review receipt")
            if (receipt.get("status") == "running"
                    and recorded_process_alive(receipt)):
                active.append(f"review:{receipt_path.stem}")
    return active


def compact_status(status):
    tasks = status.get("tasks")
    if not isinstance(tasks, dict):
        raise GoalError("QTeam status task summary is invalid")
    task_counts = {}
    for name in ("active", "blocked", "failed", "pending", "ready_to_merge"):
        values = tasks.get(name)
        if not isinstance(values, list):
            raise GoalError(f"QTeam status task list is invalid: {name}")
        task_counts[name] = len(values)
    next_action = status.get("next_action")
    if next_action is not None and (
            not isinstance(next_action, str) or len(next_action) > 4096):
        raise GoalError("QTeam next action exceeds the goal-packet bound")
    ready = status.get("ready_tasks")
    decisions = status.get("blocking_decisions")
    handoffs = status.get("blocking_handoffs")
    dependencies = status.get("dependency_blockers")
    if (not isinstance(ready, list) or not isinstance(decisions, list)
            or not isinstance(handoffs, list) or not isinstance(dependencies, dict)):
        raise GoalError("QTeam status blocker summary is invalid")
    queue = status.get("work_queue")
    queue_counts = queue.get("counts") if isinstance(queue, dict) else None
    if not isinstance(queue_counts, dict):
        raise GoalError("QTeam status queue summary is invalid")
    return {
        "run_id": status.get("run_id"),
        "phase": status.get("phase"),
        "current_wave": status.get("current_wave"),
        "next_action": next_action,
        "task_counts": task_counts,
        "ready_task_count": len(ready),
        "blocking_decision_count": len(decisions),
        "blocking_handoff_count": len(handoffs),
        "dependency_blocker_count": len(dependencies),
        "work_queue_counts": queue_counts,
        "evidence_freshness": status.get("evidence_freshness"),
    }


def goal_packet(repo, run_id, *, status_timeout=30.0):
    run_dir = safe_run(repo, run_id)
    status = read_status(repo, run_id, timeout=status_timeout)
    signature = tracked_signature(run_dir)
    checkpoint = hashlib.sha256(json.dumps(
        {"status": status, "wake": signature}, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    blockers = status.get("blocking_decisions")
    if not isinstance(blockers, list):
        raise GoalError("QTeam blocking decision summary is invalid")
    for blocker in blockers:
        if (not isinstance(blocker, dict)
                or blocker.get("status") not in {"open", "resolved"}):
            raise GoalError("QTeam blocking decision record is invalid")
    handoffs = status.get("blocking_handoffs")
    if not isinstance(handoffs, list) or any(
            not isinstance(item, dict) for item in handoffs):
        raise GoalError("QTeam blocking handoff summary is invalid")
    waiting_for_human = (
        any(item["status"] == "open" for item in blockers)
        or any(item.get("kind") == "user-decision" for item in handoffs)
    )
    active = external_work(run_dir)
    phase = status.get("phase")
    if phase == "DONE":
        goal_state = "achieved"
    elif waiting_for_human:
        goal_state = "waiting-for-human"
    elif active:
        goal_state = "waiting-for-external-work"
    else:
        goal_state = "actionable"
    return {
        "schema_version": 1,
        "run_id": run_id,
        "goal_state": goal_state,
        "phase": phase,
        "checkpoint": checkpoint,
        "next_action": status.get("next_action"),
        "waiting_for_human": waiting_for_human,
        "waiting_for_external": bool(active),
        "external_work": active,
        "proof": {
            "state": f".agents/runs/{run_id}/state.json",
            "events": f".agents/runs/{run_id}/events.jsonl",
            "integration_head": status.get("integration_head"),
        },
        "status": compact_status(status),
        "generated_at": now(),
    }


def condition_text(run_id, host):
    command = f".codex/bin/agent-team-goal --run {run_id} status"
    terminal = (
        "goal_state=achieved or goal_state=waiting-for-human"
        if host == "claude" else "goal_state=achieved"
    )
    condition = (
        f"Advance the durable QTeam run {run_id} until `{command}` reports "
        f"{terminal}. After every coherent action, run that command and "
        "surface its bounded result. If it reports waiting-for-human, return "
        "control without inventing an answer; the durable goal remains active. "
        "When it reports waiting-for-external-work, call `"
        f".codex/bin/agent-team-goal --run {run_id} wait --after <checkpoint> "
        "--timeout 300` once instead of polling in model turns. QTeam run state, "
        "Git commits, gates, and evidence are authoritative; transcript memory "
        "and native goal evaluators are not proof of completion."
    )
    if host == "claude":
        condition += (
            " For this Claude session lease, the completion condition is true "
            "when the fresh status reports either goal_state=achieved or "
            "goal_state=waiting-for-human. The latter ends only this /goal lease "
            "so the user can answer; it does not complete the durable QTeam goal."
        )
    return condition


def cmd_status(args, repo):
    print(json.dumps(goal_packet(repo, args.run), indent=2, sort_keys=True))


def cmd_condition(args, repo):
    safe_run(repo, args.run)
    condition = condition_text(args.run, args.host)
    payload = {
        "schema_version": 1, "host": args.host, "run_id": args.run,
        "condition": condition,
    }
    if args.host == "claude":
        payload["native_command"] = f"/goal {condition}"
    elif args.host == "codex":
        payload["native_action"] = (
            "Create or update the host goal with the exact condition. "
            "Do not mark it complete until QTeam reports achieved."
        )
    elif args.host == "cursor":
        payload["hook_command"] = (
            f".codex/bin/agent-team-goal --run {args.run} cursor-stop"
        )
        payload["native_action"] = (
            "Use the hook command as a Cursor stop hook. It returns a bounded "
            "followup_message only while the durable run is actionable."
        )
    print(json.dumps(payload, indent=2, sort_keys=True))


def cmd_wait(args, repo):
    safe_run(repo, args.run)
    if not re.fullmatch(r"[0-9a-f]{64}", args.after):
        raise GoalError("--after must be a QTeam goal checkpoint digest")
    deadline = time.monotonic() + args.timeout
    last_packet = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if last_packet is not None:
                last_packet["wait_timed_out"] = True
                print(json.dumps(last_packet, indent=2, sort_keys=True))
            raise SystemExit(124)
        try:
            packet = goal_packet(
                repo, args.run, status_timeout=min(30.0, remaining)
            )
        except GoalStatusTimeout:
            if last_packet is not None:
                last_packet["wait_timed_out"] = True
                print(json.dumps(last_packet, indent=2, sort_keys=True))
            raise SystemExit(124)
        last_packet = packet
        if (packet["checkpoint"] != args.after
                or packet["goal_state"] in {"achieved", "waiting-for-human"}):
            print(json.dumps(packet, indent=2, sort_keys=True))
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            continue
        time.sleep(min(args.interval, remaining))


def bounded_hook_input():
    raw = sys.stdin.buffer.read(MAX_HOOK_BYTES + 1)
    if len(raw) > MAX_HOOK_BYTES:
        raise GoalError("Cursor stop-hook input exceeds the bounded size")
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise GoalError(f"invalid Cursor stop-hook input: {exc}") from exc
    if not isinstance(value, dict):
        raise GoalError("Cursor stop-hook input must be an object")
    return value


def cmd_cursor_stop(args, repo):
    hook = bounded_hook_input()
    status = hook.get("status")
    loop_count = hook.get("loop_count", 0)
    if not isinstance(status, str) or type(loop_count) is not int or loop_count < 0:
        raise GoalError("Cursor stop-hook status/loop_count is invalid")
    if status != "completed" or loop_count >= args.max_iterations:
        print("{}")
        return
    packet = goal_packet(repo, args.run)
    if packet["goal_state"] in {"achieved", "waiting-for-human"}:
        print("{}")
        return
    followup = (
        f"Continue durable QTeam run {args.run}. Its goal checkpoint is "
        f"{packet['checkpoint']}; next action: {packet.get('next_action')}. "
        f"Run `.codex/bin/agent-team-goal --run {args.run} status` before acting."
    )
    if packet["waiting_for_external"]:
        followup += (
            " External work is active; call the blocking goal wait command with "
            "that checkpoint rather than polling status in repeated turns."
        )
    print(json.dumps({"followup_message": followup}, sort_keys=True))


def parser():
    ap = argparse.ArgumentParser(
        description="Project a durable QTeam run into a host-native goal"
    )
    ap.add_argument("--run", required=True)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("status").set_defaults(func=cmd_status)
    condition = sub.add_parser("condition")
    condition.add_argument("--host", choices=("codex", "claude", "cursor", "generic"),
                           required=True)
    condition.set_defaults(func=cmd_condition)
    wait = sub.add_parser("wait")
    wait.add_argument("--after", required=True)
    wait.add_argument("--timeout", type=float, default=300.0)
    wait.add_argument("--interval", type=float, default=0.5)
    wait.set_defaults(func=cmd_wait)
    cursor = sub.add_parser("cursor-stop")
    cursor.add_argument("--max-iterations", type=int, default=64)
    cursor.set_defaults(func=cmd_cursor_stop)
    return ap


def main():
    args = parser().parse_args()
    if args.command == "wait" and not (0 < args.timeout <= 3600
                                         and 0.1 <= args.interval <= 5):
        raise GoalError("wait timeout/interval is outside the supported bound")
    if args.command == "cursor-stop" and not 1 <= args.max_iterations <= 256:
        raise GoalError("Cursor goal max iterations must be between 1 and 256")
    args.func(args, git_root())


if __name__ == "__main__":
    try:
        main()
    except (GoalError, OSError) as exc:
        raise SystemExit(f"error: {exc}")
