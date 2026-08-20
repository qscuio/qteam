#!/usr/bin/env python3
"""Local QTeam observability and allowlisted-control web server."""

import argparse
import hashlib
import hmac
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_BODY_BYTES = 64 * 1024
MAX_ACTION_OUTPUT = 1024 * 1024
MAX_LOG_CHUNK = 64 * 1024
MAX_TASKS = 256
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
MAX_SSE_CONNECTIONS = 8
MAX_RECORD_BYTES = 256 * 1024
SAFE_ID = re.compile(r"^(?!.*\.\.)(?=.{1,128}$)[A-Za-z0-9][A-Za-z0-9._-]*$")
EVENT_FIELDS = {
    "ts", "event", "txid", "task", "wave", "lane", "from", "to",
    "gate", "item", "kind", "outcome", "consumer", "items", "phase",
    "head_sha", "status", "priority",
}


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def safe_id(value):
    return isinstance(value, str) and bool(SAFE_ID.fullmatch(value))


def git_root(value=None):
    command = ["git"]
    if value:
        command.extend(["-C", str(value)])
    command.extend(["rev-parse", "--show-toplevel"])
    result = subprocess.run(
        command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=10,
    )
    if result.returncode:
        raise ValueError("not inside a Git repository")
    return Path(result.stdout.strip()).resolve()


def direct_run(repo, run_id, *, required=True):
    if not safe_id(run_id):
        raise ValueError("unsafe run id")
    agents = repo / ".agents"
    root = agents / "runs"
    for directory, label in ((agents, "agent state root"), (root, "run root")):
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise ValueError(f"{label} must be a real directory")
    if required and not root.is_dir():
        raise ValueError("missing run root")
    path = root / run_id
    if path.is_symlink() or (required and not path.is_dir()):
        raise ValueError("run must be a regular direct child")
    if path.exists() and path.resolve().parent != root.resolve():
        raise ValueError("run escaped the run root")
    return path


def regular_child(path, root, label, *, required=True):
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"{label} root must be a real directory")
    current = root
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escaped its root") from exc
    for part in relative.parts:
        if part in {"", ".", ".."}:
            raise ValueError(f"unsafe {label} path")
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} path contains a symlink")
    if required and not current.is_file():
        raise ValueError(f"missing {label}")
    if current.exists():
        info = current.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{label} must be a regular file")
    return current


def open_regular_child(path, root, label):
    target = regular_child(path, root, label)
    relative = target.relative_to(root)
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory = os.open(root, directory_flags)
    try:
        for part in relative.parts[:-1]:
            next_directory = os.open(part, directory_flags, dir_fd=directory)
            os.close(directory)
            directory = next_directory
        descriptor = os.open(relative.parts[-1], file_flags, dir_fd=directory)
    finally:
        os.close(directory)
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(descriptor)
        raise ValueError(f"{label} must be a singly-linked regular file")
    return descriptor, info


def read_bytes(path, root, label, limit=MAX_JSON_BYTES):
    descriptor, info = open_regular_child(path, root, label)
    try:
        if info.st_size > limit:
            raise ValueError(f"{label} exceeds {limit} bytes")
        chunks = []
        retained = 0
        while retained <= limit:
            chunk = os.read(descriptor, min(64 * 1024, limit + 1 - retained))
            if not chunk:
                break
            chunks.append(chunk)
            retained += len(chunk)
        value = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(value) > limit:
        raise ValueError(f"{label} exceeds {limit} bytes")
    return value


def read_json(path, root, label, limit=MAX_JSON_BYTES):
    try:
        value = json.loads(read_bytes(path, root, label, limit).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def bounded_records(directory, pattern, root, label, limit=256):
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise ValueError(f"{label} directory is unsafe")
    if not directory.is_dir():
        return []
    paths = sorted(directory.glob(pattern))
    if len(paths) > limit:
        raise ValueError(f"too many {label} records")
    return [read_json(path, root, f"{label} record", MAX_RECORD_BYTES) for path in paths]


def event_summary(run_dir):
    path = run_dir / "events.jsonl"
    if not path.exists():
        return []
    descriptor, info = open_regular_child(path, run_dir, "event log")
    try:
        window = min(info.st_size, MAX_JSON_BYTES)
        if window:
            os.lseek(descriptor, -window, os.SEEK_END)
        raw = os.read(descriptor, window)
    finally:
        os.close(descriptor)
    if info.st_size > window:
        _partial, separator, raw = raw.partition(b"\n")
        if not separator:
            raise ValueError("event log has no complete record in the bounded tail")
    events = []
    for line in raw.splitlines()[-100:]:
        if not line.strip():
            continue
        try:
            item = json.loads(line.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            raise ValueError(f"invalid event log: {exc}") from exc
        if not isinstance(item, dict):
            raise ValueError("event log entries must be objects")
        events.append({key: item[key] for key in EVENT_FIELDS if key in item})
    return events


def decision_summary(run_dir, state):
    decisions = []
    raw = state.get("decisions", {})
    if not isinstance(raw, dict):
        raise ValueError("malformed decision summaries")
    ordered = sorted(
        raw,
        key=lambda item: (
            0 if isinstance(raw.get(item), dict)
            and raw[item].get("status") == "open" else 1,
            item,
        ),
    )
    for decision_id in ordered[:256]:
        if not safe_id(decision_id):
            raise ValueError("unsafe decision id in state")
        record = read_json(
            run_dir / "decisions" / f"{decision_id}.json", run_dir,
            "decision record", MAX_RECORD_BYTES,
        )
        decisions.append({
            "id": decision_id, "status": record.get("status"),
            "question": record.get("question"), "authority": record.get("authority"),
            "scope": record.get("scope"),
            "outcome": record.get("resolution", {}).get("outcome")
            if isinstance(record.get("resolution"), dict) else None,
        })
    return decisions, {
        "total": len(ordered), "shown": min(len(ordered), 256),
        "truncated": len(ordered) > 256,
        "open_total": sum(
            1 for value in raw.values()
            if isinstance(value, dict) and value.get("status") == "open"
        ),
    }


def worker_summary(run_dir):
    values = []
    directory = run_dir / "workers"
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise ValueError("worker directory is unsafe")
    paths = [] if not directory.is_dir() else [
        path for path in sorted(directory.glob("*.json"))
        if not path.name.endswith(".result.json")
    ]
    if len(paths) > 256:
        raise ValueError("too many worker records")
    for path in paths:
        record = read_json(path, run_dir, "worker record", MAX_RECORD_BYTES)
        task = record.get("task")
        if not safe_id(task) or path.name != f"{task}.json":
            raise ValueError("worker record identity mismatch")
        result_path = record.get("result")
        result = None
        if isinstance(result_path, str):
            candidate = run_dir / result_path
            if candidate.exists():
                result = read_json(candidate, run_dir, "worker result", MAX_RECORD_BYTES)
        pid = record.get("pid")
        alive = False
        if isinstance(pid, int):
            try:
                alive = Path(f"/proc/{pid}/stat").read_text().split()[21] == record.get(
                    "proc_start"
                )
            except (OSError, IndexError, UnicodeError):
                alive = False
        if isinstance(result, dict) and isinstance(result.get("exit_code"), int):
            status = ("cancelled" if result.get("cancelled") else
                      "succeeded" if result["exit_code"] == 0 else "failed")
        elif alive:
            status = "running"
        else:
            status = "lost"
        values.append({
            "task": task, "role": record.get("role"), "status": status,
            "pid": record.get("pid"), "started_at": record.get("started_at"),
            "finished_at": record.get("finished_at"),
            "exit_code": result.get("exit_code") if isinstance(result, dict) else None,
            "execution": {
                key: record.get("execution", {}).get(key)
                for key in ("tier", "model", "thinking", "provider", "family")
                if isinstance(record.get("execution"), dict)
                and key in record["execution"]
            },
            "runner": {
                key: record.get("runner", {}).get(key)
                for key in ("command", "version")
                if isinstance(record.get("runner"), dict) and key in record["runner"]
            },
        })
    return sorted(values, key=lambda item: item["task"])


def review_summary(run_dir):
    values = []
    for ledger in bounded_records(run_dir / "reviews", "wave-*.json", run_dir, "review"):
        packet = ledger.get("packet") if isinstance(ledger.get("packet"), dict) else {}
        findings = ledger.get("findings") if isinstance(ledger.get("findings"), list) else []
        values.append({
            "wave": packet.get("wave"), "axis": packet.get("axis"),
            "status": ledger.get("status"), "iteration": packet.get("iteration"),
            "open_findings": sum(
                1 for item in findings
                if isinstance(item, dict) and item.get("status") == "open"
            ),
            "completed_at": ledger.get("completed_at"),
        })
    return sorted(values, key=lambda item: (item.get("wave") or 0, item.get("axis") or ""))


def quality_summary(state):
    quality_lanes = state.get("quality_lanes", {})
    if not isinstance(quality_lanes, dict):
        raise ValueError("malformed quality lane state")
    values = {}
    for wave, lanes in quality_lanes.items():
        if not isinstance(lanes, dict):
            raise ValueError("malformed quality lane state")
        values[wave] = {}
        for name, lane in lanes.items():
            if not isinstance(lane, dict):
                raise ValueError("malformed quality lane record")
            results = lane.get("results", [])
            values[wave][name] = {
                "lane": lane.get("lane"), "required_by": lane.get("required_by", []),
                "command_count": len(lane.get("commands", []))
                if isinstance(lane.get("commands"), list) else None,
                "status": lane.get("status"), "head_sha": lane.get("head_sha"),
                "exit_codes": [item.get("exit_code") for item in results
                               if isinstance(item, dict)],
                "updated_at": lane.get("updated_at"),
                "attempt_count": len(lane.get("attempts", []))
                if isinstance(lane.get("attempts", []), list) else None,
                "assessment": {
                    key: lane.get("assessment", {}).get(key)
                    for key in ("outcome", "rationale", "head_sha", "task_id")
                    if isinstance(lane.get("assessment"), dict)
                    and key in lane["assessment"]
                },
            }
    return values


def queue_summary(state):
    queue = state.get("work_queue", [])
    if not isinstance(queue, list):
        raise ValueError("malformed coordinator queue")
    values = []
    for item in queue:
        if not isinstance(item, dict):
            raise ValueError("malformed coordinator queue item")
        values.append({
            key: item.get(key) for key in (
                "id", "kind", "targets", "priority", "status", "created_at",
                "claimed_by", "claimed_at", "completed_at",
            ) if key in item
        })
    return values


def gate_summary(state):
    gates = state.get("gates", {})
    if not isinstance(gates, dict):
        raise ValueError("malformed gate state")
    retained = {"status", "head_sha", "through_wave", "updated_at", "axes"}
    return {
        name: {key: record[key] for key in retained if key in record}
        for name, record in gates.items() if isinstance(record, dict)
    }


def snapshot(repo, run_id, *, raw_logs_enabled=False):
    run_dir = direct_run(repo, run_id)
    state = read_json(run_dir / "state.json", run_dir, "run state")
    if state.get("run_id") != run_id:
        raise ValueError("run identity mismatch")
    task_state = state.get("tasks", {})
    if not isinstance(task_state, dict):
        raise ValueError("run tasks must be an object")
    if len(task_state) > MAX_TASKS:
        raise ValueError("run exceeds the 256-task Web projection limit")
    tasks = []
    for task_id, summary in sorted(task_state.items()):
        if not safe_id(task_id) or not isinstance(summary, dict):
            raise ValueError("malformed task summary")
        record = read_json(
            run_dir / "tasks" / f"{task_id}.json", run_dir, "task record",
            MAX_RECORD_BYTES,
        )
        policy = record.get("policy") if isinstance(record.get("policy"), dict) else {}
        tasks.append({
            "id": task_id, "title": record.get("title"),
            "status": summary.get("status"), "wave": record.get("wave"),
            "depends_on": record.get("depends_on", []),
            "work_kind": record.get("work_kind"),
            "workflow_shape": policy.get("workflow_shape", "legacy"),
            "quality_lanes": policy.get("required_quality_lanes", []),
            "execution_tier": policy.get("execution_tier"),
            "reversibility": policy.get("reversibility"),
        })
    decisions, decisions_meta = decision_summary(run_dir, state)
    value = {
        "observed_at": now(), "run_id": run_id, "goal": state.get("goal"),
        "phase": state.get("phase"), "current_wave": state.get("current_wave"),
        "integration_head": state.get("integration_provenance_head"),
        "finished": state.get("finished"), "updated_at": state.get("updated_at"),
        "waves": {
            wave: {
                key: policy.get(key) for key in (
                    "tasks", "workflow_shape", "execution_tier",
                    "review_intensity", "required_quality_lanes",
                )
            }
            for wave, policy in state.get("waves", {}).items()
            if isinstance(policy, dict)
        }, "tasks": tasks,
        "quality_lanes": quality_summary(state),
        "work_queue": queue_summary(state),
        "decisions": decisions, "decisions_meta": decisions_meta,
        "gates": gate_summary(state), "workers": worker_summary(run_dir),
        "reviews": review_summary(run_dir), "events": event_summary(run_dir),
        "policy_layers": [
            {key: layer.get(key) for key in (
                "name", "kind", "policy_version", "path", "sha256",
                "defaults_sha256", "effective_sha256",
            ) if key in layer}
            for layer in state.get("policy_layers", []) if isinstance(layer, dict)
        ],
        "raw_logs_enabled": raw_logs_enabled,
    }
    if len(json.dumps(value, sort_keys=True).encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise ValueError("redacted snapshot exceeds the 4 MiB projection limit")
    return value


def list_runs(repo, allowed_run=None):
    agents = repo / ".agents"
    root = agents / "runs"
    if (agents.is_symlink() or root.is_symlink() or not agents.is_dir()
            or not root.is_dir()):
        return []
    values = []
    candidates = ([direct_run(repo, allowed_run)] if allowed_run is not None
                  else sorted(root.iterdir(), key=lambda item: item.name)[:256])
    for path in candidates:
        if path.is_symlink() or not path.is_dir() or not safe_id(path.name):
            continue
        try:
            state = read_json(path / "state.json", path, "run state")
        except ValueError:
            continue
        values.append({
            "run_id": path.name, "goal": state.get("goal"),
            "phase": state.get("phase"), "current_wave": state.get("current_wave"),
            "finished": state.get("finished"), "updated_at": state.get("updated_at"),
        })
    return values


def script_tool(name):
    directory = Path(__file__).resolve().parent
    installed = directory / name
    source = directory / f"{name}.py"
    target = installed if installed.is_file() else source
    if not target.is_file() or target.is_symlink():
        raise ValueError(f"missing QTeam tool {name}")
    return [sys.executable, str(target)] if target.suffix == ".py" else [str(target)]


def run_action(repo, run_id, action, payload):
    if not isinstance(payload, dict):
        raise ValueError("action body must be a JSON object")
    state = script_tool("agent-team-state")
    worker = script_tool("agent-team-worker")
    if action == "finish-check" and not payload:
        command = [*state, "--run", run_id, "finish", "--check-only"]
    elif action == "worker-cancel" and set(payload) == {"task"} and safe_id(payload["task"]):
        command = [*worker, "cancel", "--run", run_id, "--task", payload["task"]]
    elif action == "decision-resolve" and set(payload) == {
            "decision", "outcome", "choice", "evidence"}:
        if (not safe_id(payload["decision"])
                or not isinstance(payload["outcome"], str)
                or payload["outcome"] not in {"allow", "deny"}
                or any(not isinstance(payload[key], str) or not payload[key].strip()
                       or len(payload[key]) > 4096 for key in ("choice", "evidence"))):
            raise ValueError("invalid decision resolution")
        command = [
            *state, "--run", run_id, "decision-resolve", payload["decision"],
            "--outcome", payload["outcome"], "--choice", payload["choice"],
            "--evidence", payload["evidence"],
        ]
    elif action == "queue-claim" and set(payload).issubset({"consumer", "kind", "limit"}):
        consumer = payload.get("consumer")
        kind = payload.get("kind")
        limit = payload.get("limit", 1)
        if (not safe_id(consumer)
                or not (kind is None or isinstance(kind, str))
                or kind not in {None, "task", "quality", "review", "fix", "research"}
                or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 16):
            raise ValueError("invalid queue claim")
        command = [
            *state, "--run", run_id, "queue-claim", "--consumer", consumer,
            "--limit", str(limit),
        ]
        if kind:
            command.extend(["--kind", kind])
    elif action == "queue-complete" and set(payload) == {
            "item", "consumer", "outcome", "evidence"}:
        if (not safe_id(payload["item"]) or not safe_id(payload["consumer"])
                or not isinstance(payload["outcome"], str)
                or payload["outcome"] not in {"completed", "failed"}
                or not isinstance(payload["evidence"], str)
                or not payload["evidence"].strip() or len(payload["evidence"]) > 4096):
            raise ValueError("invalid queue completion")
        command = [
            *state, "--run", run_id, "queue-complete", payload["item"],
            "--consumer", payload["consumer"], "--outcome", payload["outcome"],
            "--evidence", payload["evidence"],
        ]
    elif action == "quality-check" and set(payload) == {"wave", "lane"}:
        if (not isinstance(payload["wave"], int) or isinstance(payload["wave"], bool)
                or payload["wave"] < 1
                or not isinstance(payload["lane"], str)
                or payload["lane"] not in {"refactor", "hardening", "public-surface-qa"}):
            raise ValueError("invalid quality check")
        command = [
            *state, "--run", run_id, "quality-check", "--wave", str(payload["wave"]),
            "--lane", payload["lane"],
        ]
    elif action == "quality-assess" and set(payload).issubset({
            "wave", "lane", "outcome", "rationale", "task"}):
        required = {"wave", "lane", "outcome", "rationale"}
        if (not required.issubset(payload)
                or not isinstance(payload["wave"], int)
                or isinstance(payload["wave"], bool) or payload["wave"] < 1
                or not isinstance(payload["lane"], str)
                or payload["lane"] != "refactor"
                or not isinstance(payload["outcome"], str)
                or payload["outcome"] not in {"not-needed", "task-created"}
                or not isinstance(payload["rationale"], str)
                or not payload["rationale"].strip()
                or len(payload["rationale"]) > 2048
                or (payload["outcome"] == "task-created")
                != safe_id(payload.get("task"))):
            raise ValueError("invalid refactor assessment")
        command = [
            *state, "--run", run_id, "quality-assess",
            "--wave", str(payload["wave"]), "--lane", "refactor",
            "--outcome", payload["outcome"],
            "--rationale", payload["rationale"].strip(),
        ]
        if payload.get("task"):
            command.extend(["--task", payload["task"]])
    else:
        raise ValueError("unsupported or malformed action")
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(
            command, cwd=repo, stdout=stdout_file, stderr=stderr_file,
            start_new_session=True,
        )
        try:
            return_code = process.wait(timeout=2700)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, 15)
            except ProcessLookupError:
                pass
            try:
                return_code = process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, 9)
                except ProcessLookupError:
                    pass
                return_code = process.wait()
            raise ValueError("action exceeded its 45-minute process-tree timeout")
        stdout_file.seek(0, os.SEEK_END)
        stdout_size = stdout_file.tell()
        stderr_file.seek(0, os.SEEK_END)
        stderr_size = stderr_file.tell()
        output_truncated = (
            stdout_size > MAX_ACTION_OUTPUT or stderr_size > MAX_ACTION_OUTPUT
        )
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read(MAX_ACTION_OUTPUT).decode("utf-8", errors="replace")
        stderr = stderr_file.read(MAX_ACTION_OUTPUT).decode("utf-8", errors="replace")
    return {"ok": return_code == 0, "exit_code": return_code,
            "stdout": stdout, "stderr": stderr,
            "output_truncated": output_truncated}


def ui_root():
    plugin = Path(__file__).resolve().parent.parent / "ui"
    installed = Path(__file__).resolve().parent.parent / "qteam-ui"
    root = plugin if plugin.is_dir() else installed
    if root.is_symlink() or not root.is_dir():
        raise ValueError("missing QTeam UI assets")
    return root


class QTeamServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, *, repo, token, allow_raw_logs, allowed_run,
                 trusted_origin):
        super().__init__(address, handler)
        self.repo = repo
        self.token = token
        self.csrf = hashlib.sha256(os.urandom(32)).hexdigest()
        self.allow_raw_logs = allow_raw_logs
        self.allowed_run = allowed_run
        self.trusted_origin = trusted_origin
        self.sse_slots = threading.BoundedSemaphore(MAX_SSE_CONNECTIONS)
        self.assets = ui_root()
        self.bound_host = address[0]
        self.bound_port = self.server_address[1]


class Handler(BaseHTTPRequestHandler):
    server_version = "QTeamWeb/0.16"

    def log_message(self, format_value, *args):
        sys.stderr.write("qteam-web: " + format_value % args + "\n")

    def security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
        )
        self.send_header("Cache-Control", "no-store")

    def send_bytes(self, code, body, content_type):
        self.send_response(code)
        self.security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, code, value):
        body = json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
        self.send_bytes(code, body, "application/json; charset=utf-8")

    def authorized(self):
        expected = self.server.token
        if expected is None:
            return True
        actual = self.headers.get("Authorization", "")
        return hmac.compare_digest(actual, "Bearer " + expected)

    def require_auth(self, *, mutation=False):
        if not self.valid_request_origin(require_origin=mutation):
            self.send_json(HTTPStatus.FORBIDDEN, {"error": "invalid Host or Origin"})
            return False
        if mutation and self.server.token is None:
            self.send_json(HTTPStatus.UNAUTHORIZED, {
                "error": "control actions require --token-file; this server is read-only",
            })
            return False
        if self.authorized():
            return True
        self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
        return False

    def valid_request_origin(self, *, require_origin=False):
        host = self.headers.get("Host", "")
        try:
            parsed_host = urlsplit("//" + host)
            hostname = parsed_host.hostname.lower()
            host_port = parsed_host.port
        except (AttributeError, ValueError):
            return False
        if (not hostname or parsed_host.username or parsed_host.password
                or parsed_host.path not in {"", "/"}):
            return False
        origin = self.headers.get("Origin")
        if self.server.trusted_origin:
            trusted = urlsplit(self.server.trusted_origin)
            trusted_port = trusted.port or 443
            host_matches = (
                hostname == trusted.hostname.lower()
                and (host_port or trusted_port) == trusted_port
            )
            if host_matches:
                return ((not require_origin and origin is None)
                        or origin == self.server.trusted_origin)
        if hostname not in {"127.0.0.1", "localhost", "::1"}:
            return False
        if host_port not in {None, self.server.bound_port}:
            return False
        if require_origin and not origin:
            return False
        if origin:
            parsed = urlsplit(origin)
            if (parsed.scheme != "http" or parsed.hostname is None
                    or parsed.hostname.lower() != hostname
                    or (parsed.port or 80) != (host_port or self.server.bound_port)):
                return False
        return True

    def require_allowed_run(self, run_id):
        if self.server.allowed_run is not None and run_id != self.server.allowed_run:
            raise ValueError("run is outside this server's allowlist")

    def read_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("request body is too large")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            raise ValueError(f"invalid JSON body: {exc}") from exc

    def do_GET(self):
        try:
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            if path in {"/", "/index.html", "/app.js", "/styles.css"}:
                name = "index.html" if path in {"/", "/index.html"} else path[1:]
                mime = {"index.html": "text/html; charset=utf-8",
                        "app.js": "text/javascript; charset=utf-8",
                        "styles.css": "text/css; charset=utf-8"}[name]
                self.send_bytes(HTTPStatus.OK, read_bytes(
                    self.server.assets / name, self.server.assets, "UI asset", 1024 * 1024
                ), mime)
                return
            if not self.require_auth():
                return
            if path == "/api/config":
                self.send_json(HTTPStatus.OK, {
                    "csrf": self.server.csrf,
                    "raw_logs_enabled": self.server.allow_raw_logs,
                    "controls_enabled": self.server.token is not None,
                })
                return
            if path == "/api/runs":
                runs = list_runs(self.server.repo, self.server.allowed_run)
                self.send_json(HTTPStatus.OK, {"runs": runs})
                return
            parts = [part for part in path.split("/") if part]
            if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "snapshot":
                self.require_allowed_run(parts[2])
                self.send_json(HTTPStatus.OK, snapshot(
                    self.server.repo, parts[2],
                    raw_logs_enabled=self.server.allow_raw_logs,
                ))
                return
            if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "stream":
                self.require_allowed_run(parts[2])
                self.stream(parts[2])
                return
            if (len(parts) == 6 and parts[:2] == ["api", "runs"]
                    and parts[3] == "logs" and parts[4] == "worker"):
                self.require_allowed_run(parts[2])
                self.worker_log(parts[2], parts[5], parse_qs(parsed.query))
                return
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except (BrokenPipeError, ConnectionResetError):
            return
        except (OSError, OverflowError, TypeError, ValueError,
                subprocess.SubprocessError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def stream(self, run_id):
        if not self.server.sse_slots.acquire(blocking=False):
            self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                "error": "too many concurrent QTeam streams"
            })
            return
        self.send_response(HTTPStatus.OK)
        self.security_headers()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        previous = None
        sequence = 0
        try:
            while True:
                try:
                    value = snapshot(
                        self.server.repo, run_id,
                        raw_logs_enabled=self.server.allow_raw_logs,
                    )
                except ValueError as exc:
                    encoded_error = json.dumps({"error": str(exc)})
                    self.wfile.write(
                        f"event: error\ndata: {encoded_error}\n\n".encode("utf-8")
                    )
                    self.wfile.flush()
                    return
                encoded = json.dumps(value, sort_keys=True, ensure_ascii=False)
                stable = dict(value)
                stable.pop("observed_at", None)
                digest = hashlib.sha256(json.dumps(
                    stable, sort_keys=True, ensure_ascii=False
                ).encode("utf-8")).hexdigest()
                if digest != previous:
                    sequence += 1
                    self.wfile.write(
                        f"id: {sequence}\nevent: snapshot\ndata: {encoded}\n\n".encode("utf-8")
                    )
                    self.wfile.flush()
                    previous = digest
                else:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                time.sleep(1)
        finally:
            self.server.sse_slots.release()

    def worker_log(self, run_id, task, query):
        if not self.server.allow_raw_logs:
            self.send_json(HTTPStatus.FORBIDDEN, {"error": "raw logs are disabled"})
            return
        if not safe_id(task):
            raise ValueError("unsafe task id")
        stream = query.get("stream", ["stdout"])[0]
        if stream not in {"stdout", "stderr", "final"}:
            raise ValueError("invalid worker log stream")
        try:
            offset = int(query.get("offset", ["0"])[0])
            limit = int(query.get("limit", [str(MAX_LOG_CHUNK)])[0])
        except ValueError as exc:
            raise ValueError("invalid log bounds") from exc
        if offset < 0 or offset > (1 << 63) - 1 or not 1 <= limit <= MAX_LOG_CHUNK:
            raise ValueError("invalid log bounds")
        run_dir = direct_run(self.server.repo, run_id)
        suffix = {"stdout": "stdout.log", "stderr": "stderr.log", "final": "final.txt"}[stream]
        descriptor, _info = open_regular_child(
            run_dir / "workers" / f"{task}.{suffix}", run_dir, "worker log"
        )
        try:
            os.lseek(descriptor, offset, os.SEEK_SET)
            data = os.read(descriptor, limit)
        finally:
            os.close(descriptor)
        self.send_json(HTTPStatus.OK, {
            "task": task, "stream": stream, "offset": offset,
            "next_offset": offset + len(data), "text": data.decode("utf-8", errors="replace"),
        })

    def do_POST(self):
        try:
            if not self.require_auth(mutation=True):
                return
            if not hmac.compare_digest(
                    self.headers.get("X-QTeam-CSRF", ""), self.server.csrf):
                self.send_json(HTTPStatus.FORBIDDEN, {"error": "invalid CSRF token"})
                return
            parts = [part for part in unquote(urlsplit(self.path).path).split("/") if part]
            if len(parts) != 5 or parts[:2] != ["api", "runs"] or parts[3] != "actions":
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            direct_run(self.server.repo, parts[2])
            self.require_allowed_run(parts[2])
            result = run_action(self.server.repo, parts[2], parts[4], self.read_body())
            self.send_json(HTTPStatus.OK if result["ok"] else HTTPStatus.CONFLICT, result)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})


def loopback(host):
    try:
        return all(address[4][0] in {"127.0.0.1", "::1"}
                   for address in socket.getaddrinfo(host, None))
    except socket.gaierror:
        return False


def token_from_file(path):
    if path is None:
        return None
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise ValueError("token file must be a regular file")
    descriptor = os.open(
        target,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("token file must be a singly-linked regular file")
        if info.st_mode & 0o077:
            raise ValueError("token file permissions must not grant group/other access")
        if info.st_size > 4097:
            raise ValueError("token file is too large")
        raw = os.read(descriptor, 4097)
    finally:
        os.close(descriptor)
    value = raw.decode("utf-8", errors="strict").strip()
    if len(value) < 32 or len(value) > 4096:
        raise ValueError("token must contain 32-4096 non-whitespace characters")
    return value


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo")
    ap.add_argument("--run")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--token-file")
    ap.add_argument("--trusted-origin")
    ap.add_argument("--allow-raw-logs", action="store_true")
    ap.add_argument("--snapshot-once", action="store_true")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=float, default=2.0)
    return ap


def main():
    args = parser().parse_args()
    try:
        repo = git_root(args.repo)
        token = token_from_file(args.token_file)
        if not loopback(args.host):
            raise ValueError(
                "QTeam Web binds loopback only; use an SSH tunnel or trusted HTTPS proxy"
            )
        trusted_origin = None
        if args.trusted_origin:
            parsed_origin = urlsplit(args.trusted_origin)
            if (parsed_origin.scheme != "https" or not parsed_origin.hostname
                    or parsed_origin.path not in {"", "/"} or parsed_origin.query
                    or parsed_origin.fragment or parsed_origin.username
                    or parsed_origin.password or token is None):
                raise ValueError(
                    "--trusted-origin requires an exact HTTPS origin and --token-file"
                )
            trusted_origin = f"https://{parsed_origin.netloc}"
        if args.port < 0 or args.port > 65535:
            raise ValueError("port must be between 0 and 65535")
        if (args.snapshot_once or args.watch) and not args.run:
            raise ValueError("--run is required for snapshot/watch mode")
        if args.run:
            direct_run(repo, args.run)
        if args.snapshot_once:
            print(json.dumps(snapshot(repo, args.run), sort_keys=True))
            return
        if args.watch:
            if args.interval < 0.25 or args.interval > 60:
                raise ValueError("watch interval must be between 0.25 and 60 seconds")
            previous = None
            while True:
                value = snapshot(repo, args.run)
                compact = {
                    "run_id": value["run_id"],
                    "phase": value["phase"], "current_wave": value["current_wave"],
                    "tasks": {item["id"]: item["status"] for item in value["tasks"]},
                    "queue": {item["id"]: item["status"] for item in value["work_queue"]},
                    "quality": value["quality_lanes"],
                }
                encoded = json.dumps(compact, sort_keys=True)
                if encoded != previous:
                    print(encoded, flush=True)
                    previous = encoded
                time.sleep(args.interval)
        bind_host = {"localhost": "127.0.0.1"}.get(args.host, args.host)
        if bind_host not in {"127.0.0.1", "::1"}:
            raise ValueError(
                "QTeam Web accepts only numeric loopback hosts or localhost"
            )
        server = QTeamServer(
            (bind_host, args.port), Handler, repo=repo, token=token,
            allow_raw_logs=args.allow_raw_logs, allowed_run=args.run,
            trusted_origin=trusted_origin,
        )
        host, port = server.server_address[:2]
        print(f"QTeam Web: http://{host}:{port}/", flush=True)
        server.serve_forever(poll_interval=0.25)
    except (KeyboardInterrupt, OSError, UnicodeError, ValueError) as exc:
        if isinstance(exc, KeyboardInterrupt):
            return
        raise SystemExit(f"error: {exc}")


if __name__ == "__main__":
    main()
