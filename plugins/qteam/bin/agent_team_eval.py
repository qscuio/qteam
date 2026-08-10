#!/usr/bin/env python3
"""Deterministic trajectory, judge-calibration, and eval-case primitives."""

import hashlib
import json
import subprocess
import os
import stat
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


CALIBRATION_CASES = {
    "spec": [
        {
            "id": "cal-spec-01",
            "candidate": (
                "The frozen acceptance criterion requires the public status command "
                "to expose the completed result. The diff does so and the focused "
                "public-command test passes."
            ),
            "expected": "pass",
        },
        {
            "id": "cal-spec-02",
            "candidate": (
                "The happy-path test passes, but the frozen acceptance criterion also "
                "requires an unavailable-result error and the diff silently returns an "
                "empty success response instead."
            ),
            "expected": "needs-fix",
        },
    ],
    "standards": [
        {
            "id": "cal-standards-01",
            "candidate": (
                "The atomic state write checks write, flush, fsync, rename, and parent "
                "directory fsync failures and reports the original error."
            ),
            "expected": "pass",
        },
        {
            "id": "cal-standards-02",
            "candidate": (
                "The state writer catches fsync failure, reports success, and continues "
                "because the in-memory value looks correct."
            ),
            "expected": "needs-fix",
        },
    ],
    "risk": [
        {
            "id": "cal-risk-01",
            "candidate": (
                "The named migration risk has a tested rollback that restores the prior "
                "schema and data after an injected mid-commit failure."
            ),
            "expected": "pass",
        },
        {
            "id": "cal-risk-02",
            "candidate": (
                "The forward migration passes on a fixture, but there is no rollback or "
                "recovery path after a partial production write."
            ),
            "expected": "needs-fix",
        },
    ],
}
MAX_TRACE_BYTES = 64 * 1024 * 1024
MAX_TRACE_LINE_BYTES = 4 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def object_sha256(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def regular_output(path, label, *, readwrite=False):
    target = Path(path)
    if target.parent.is_symlink() or not target.parent.is_dir():
        raise ValueError(f"{label} parent must be a regular directory")
    flags = (os.O_RDWR if readwrite else os.O_WRONLY) | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(target, flags, 0o600)
    except OSError as exc:
        raise ValueError(f"cannot safely open {label}: {exc}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError(f"{label} must be a regular file")
        os.ftruncate(descriptor, 0)
        os.lseek(descriptor, 0, os.SEEK_SET)
        mode = "w+" if readwrite else "w"
        with os.fdopen(descriptor, mode, encoding="utf-8") as handle:
            descriptor = None
            try:
                yield handle
            finally:
                handle.flush()
                os.fsync(handle.fileno())
    finally:
        if descriptor is not None:
            os.close(descriptor)
        parent_descriptor = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)


def run_regular_file(run_dir, raw):
    if not isinstance(raw, str):
        raise ValueError("run evidence path must be a string")
    relative = PurePosixPath(raw)
    if (relative.is_absolute() or "\\" in raw
            or any(part in {"", ".", ".."} for part in relative.parts)
            or relative.as_posix() != raw):
        raise ValueError("run evidence path must be canonical and relative")
    candidate = Path(run_dir)
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError("run evidence path must not contain symlinks")
    if not candidate.is_file():
        raise ValueError("run evidence path must be a regular file")
    return candidate


def model_family(model):
    if not isinstance(model, str) or not model:
        raise ValueError("model must be a non-empty string")
    for suffix in ("-terra", "-luna", "-sol"):
        if model.endswith(suffix):
            return model[:-len(suffix)]
    return model


def execution_profile(model, thinking, provider="openai", family=None):
    if not isinstance(provider, str) or not provider:
        raise ValueError("provider must be a non-empty string")
    return {
        "model": model,
        "thinking": thinking,
        "provider": provider,
        "family": family or model_family(model),
    }


def codex_version(command="codex"):
    try:
        result = subprocess.run(
            [command, "--version"], text=True, timeout=10,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("cannot resolve the Codex runner version") from exc
    value = result.stdout.strip()
    if result.returncode or not value:
        raise ValueError("cannot resolve the Codex runner version")
    return value


def wait_capped_process(process, stdout_target, stderr_target,
                        limit=MAX_TRACE_BYTES, process_group=False):
    """Drain a child concurrently while never retaining more than ``limit``."""
    overflow = threading.Event()

    def stop(kill=False):
        try:
            if process_group:
                os.killpg(process.pid, 9 if kill else 15)
            elif kill:
                process.kill()
            else:
                process.terminate()
        except ProcessLookupError:
            pass

    def pump(source, target):
        written = 0
        read_chunk = getattr(source, "read1", source.read)
        try:
            for chunk in iter(lambda: read_chunk(64 * 1024), b""):
                remaining = max(0, limit - written)
                if remaining:
                    kept = chunk[:remaining]
                    target.buffer.write(kept)
                    target.buffer.flush()
                    written += len(kept)
                if len(chunk) > remaining and not overflow.is_set():
                    overflow.set()
                    stop()
        finally:
            source.close()

    threads = [
        threading.Thread(target=pump, args=(process.stdout, stdout_target),
                         daemon=True),
        threading.Thread(target=pump, args=(process.stderr, stderr_target),
                         daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        while True:
            try:
                return_code = process.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                if not overflow.is_set():
                    continue
                stop()
                try:
                    return_code = process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    stop(kill=True)
                    return_code = process.wait()
                break
    except BaseException:
        stop()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            stop(kill=True)
            process.wait()
        for thread in threads:
            thread.join(timeout=2)
        raise
    for thread in threads:
        thread.join()
    return return_code, overflow.is_set()


def calibration_suite(axis):
    cases = CALIBRATION_CASES.get(axis)
    if cases is None:
        raise ValueError(f"unknown review axis: {axis}")
    public = [{"id": item["id"], "candidate": item["candidate"]}
              for item in cases]
    return {
        "schema_version": 1,
        "axis": axis,
        "cases": public,
        # This digest freezes only the public canary inputs. It is not a
        # commitment to, or oracle for, the locally validated labels.
        "sha256": object_sha256(public),
    }


def trajectory_independence(trajectory, judge_family):
    """Claim cross-family independence only with complete worker visibility."""
    if not isinstance(trajectory, dict) or not isinstance(judge_family, str):
        return "identity-only"
    tasks = trajectory.get("tasks")
    workers = trajectory.get("worker_trajectories")
    unavailable = trajectory.get("tool_visibility_unavailable")
    if (not isinstance(tasks, list) or not tasks
            or not isinstance(workers, list) or len(workers) != len(tasks)
            or unavailable != []):
        return "identity-only"
    families = [
        item.get("execution", {}).get("family")
        if isinstance(item, dict) else None
        for item in workers
    ]
    return (
        "cross-family"
        if all(isinstance(family, str) and family
               and family != judge_family for family in families)
        else "identity-only"
    )


def validate_calibration(axis, suite_sha256, results):
    cases = CALIBRATION_CASES.get(axis)
    public = ([{"id": item["id"], "candidate": item["candidate"]}
               for item in cases] if cases is not None else None)
    if cases is None or object_sha256(public) != suite_sha256:
        raise ValueError("review calibration suite does not match the runner")
    expected = {item["id"]: item["expected"] for item in cases}
    if results != expected:
        raise ValueError("reviewer failed the frozen judge calibration cases")


def _canonical_tool_input(item):
    keys = ("command", "server", "tool", "name", "arguments", "input", "query")
    return {key: item[key] for key in keys if key in item}


def _has_empty_result(item):
    if item.get("type") not in {
        "mcp_tool_call", "web_search", "tool_call",
    }:
        return False
    for key in ("aggregated_output", "output", "result", "content"):
        if key in item:
            value = item[key]
            return value is None or value == "" or value == [] or value == {}
    return False


def parse_codex_trace(path, subject_type, subject_id, execution, runner_version):
    """Summarize JSONL without retaining raw tool arguments or tool output."""
    trace = Path(path)
    if trace.is_symlink() or not trace.is_file():
        raise ValueError("Codex trajectory must be a regular JSONL file")
    started = 0
    completed_count = 0
    usage = {}
    messages = 0
    tool_count = 0
    fingerprints = []
    failed = 0
    empty = 0
    command_calls = 0
    event_count = 0
    trace_bytes = 0
    trace_digest = hashlib.sha256()
    call_types = {
        "command_execution", "mcp_tool_call", "web_search", "tool_call",
    }
    with trace.open("rb") as source:
        for number, raw_line in enumerate(source, 1):
            trace_bytes += len(raw_line)
            if (trace_bytes > MAX_TRACE_BYTES
                    or len(raw_line) > MAX_TRACE_LINE_BYTES):
                raise ValueError("Codex trajectory exceeds the bounded JSONL size")
            trace_digest.update(raw_line)
            try:
                line = raw_line.decode("utf-8", errors="strict")
            except UnicodeError as exc:
                raise ValueError(
                    f"invalid Codex JSONL encoding at line {number}: {exc}"
                ) from exc
            if not line.strip():
                continue
            event_count += 1
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, RecursionError) as exc:
                raise ValueError(f"invalid Codex JSONL at line {number}: {exc}") from exc
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise ValueError(f"Codex JSONL line {number} must be an event object")
            if event["type"] == "thread.started":
                started += 1
            elif event["type"] == "turn.completed":
                completed_count += 1
                usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
            elif event["type"] == "item.completed":
                item = event.get("item")
                if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                    raise ValueError(
                        "completed trajectory items must be objects with a type"
                    )
                if item["type"] == "agent_message":
                    messages += 1
                elif item["type"] in call_types:
                    tool_count += 1
                    fingerprints.append(object_sha256({
                        "type": item["type"],
                        "input": _canonical_tool_input(item),
                    }))
                    if item["type"] == "command_execution":
                        command_calls += 1
                    exit_code = item.get("exit_code")
                    if ((isinstance(exit_code, int)
                         and not isinstance(exit_code, bool) and exit_code != 0)
                            or item.get("status") in {
                                "failed", "error", "cancelled",
                            }):
                        failed += 1
                    if _has_empty_result(item):
                        empty += 1
    if not event_count:
        raise ValueError("Codex trajectory is empty")
    if started != 1 or completed_count != 1 or messages < 1:
        raise ValueError("Codex trajectory must contain one complete turn and a final message")

    duplicate = len(fingerprints) - len(set(fingerprints))
    def usage_count(name):
        value = usage.get(name, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"Codex trajectory usage {name} must be non-negative")
        return value

    counts = {
        "events": event_count,
        "tool_calls": tool_count,
        "command_calls": command_calls,
        "failed_calls": failed,
        "empty_result_calls": empty,
        "duplicate_calls": duplicate,
        "input_tokens": usage_count("input_tokens"),
        "cached_input_tokens": usage_count("cached_input_tokens"),
        "output_tokens": usage_count("output_tokens"),
        "reasoning_output_tokens": usage_count("reasoning_output_tokens"),
    }
    anomalies = []
    for code, count in (
        ("TRACE_DUPLICATE_CALL", duplicate),
        ("TRACE_EMPTY_RESULT", empty),
        ("TRACE_FAILED_CALL", failed),
    ):
        if count:
            anomalies.append({"code": code, "count": count})
    # Repeated calls are useful telemetry, but are not proof of a stuck agent:
    # RED/GREEN loops and post-edit verification legitimately repeat commands.
    # Failed calls are tolerated in small numbers because exploration is expected.
    disposition = "escalate" if empty or failed > 3 else "pass"
    return {
        "schema_version": 1,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "execution": execution,
        "runner": {"name": "codex-cli", "version": runner_version},
        "trace_sha256": trace_digest.hexdigest(),
        "counts": counts,
        "anomalies": anomalies,
        "disposition": disposition,
        "generated_at": now(),
    }


def validate_trajectory(value, subject_type, subject_id, execution,
                        trace_path=None):
    fields = {
        "schema_version", "subject_type", "subject_id", "execution", "runner",
        "trace_sha256", "counts", "anomalies", "disposition", "generated_at",
    }
    if (not isinstance(value, dict) or set(value) != fields
            or value.get("schema_version") != 1
            or value.get("subject_type") != subject_type
            or value.get("subject_id") != subject_id
            or value.get("execution") != execution):
        raise ValueError("trajectory identity/execution does not match its owner")
    runner = value.get("runner")
    if (not isinstance(runner, dict) or set(runner) != {"name", "version"}
            or runner.get("name") != "codex-cli"
            or not isinstance(runner.get("version"), str) or not runner["version"]):
        raise ValueError("trajectory runner identity is invalid")
    digest = value.get("trace_sha256")
    if (not isinstance(digest, str) or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)):
        raise ValueError("trajectory trace digest is invalid")
    count_fields = {
        "events", "tool_calls", "command_calls", "failed_calls",
        "empty_result_calls", "duplicate_calls", "input_tokens",
        "cached_input_tokens", "output_tokens", "reasoning_output_tokens",
    }
    counts = value.get("counts")
    if (not isinstance(counts, dict) or set(counts) != count_fields
            or any(not isinstance(item, int) or isinstance(item, bool) or item < 0
                   for item in counts.values())):
        raise ValueError("trajectory counts are invalid")
    anomalies = value.get("anomalies")
    if (not isinstance(anomalies, list)
            or any(not isinstance(item, dict)
                   or set(item) != {"code", "count"}
                   or item.get("code") not in {
                       "TRACE_DUPLICATE_CALL", "TRACE_EMPTY_RESULT",
                       "TRACE_FAILED_CALL",
                   }
                   or not isinstance(item.get("count"), int)
                   or isinstance(item.get("count"), bool) or item["count"] < 1
                   for item in anomalies)
            or value.get("disposition") not in {"pass", "escalate", "quarantine"}
            or not isinstance(value.get("generated_at"), str)
            or not value["generated_at"]):
        raise ValueError("trajectory anomaly/disposition evidence is invalid")
    expected_disposition = (
        "escalate" if counts["empty_result_calls"]
        or counts["failed_calls"] > 3 else "pass"
    )
    expected_anomalies = []
    for code, field in (
        ("TRACE_DUPLICATE_CALL", "duplicate_calls"),
        ("TRACE_EMPTY_RESULT", "empty_result_calls"),
        ("TRACE_FAILED_CALL", "failed_calls"),
    ):
        if counts[field]:
            expected_anomalies.append({"code": code, "count": counts[field]})
    if anomalies != expected_anomalies:
        raise ValueError("trajectory anomalies do not match their counts")
    if (expected_disposition == "pass" and value["disposition"] != "pass"
            or expected_disposition == "escalate"
            and value["disposition"] not in {"escalate", "quarantine"}):
        raise ValueError("trajectory disposition does not match its counts")
    if trace_path is not None:
        recomputed = parse_codex_trace(
            trace_path, subject_type, subject_id, execution, runner["version"]
        )
        if recomputed["trace_sha256"] != digest:
            raise ValueError("trajectory digest does not match its JSONL trace")
        compared = fields - {"generated_at"}
        if any(value.get(field) != recomputed.get(field) for field in compared):
            raise ValueError("trajectory summary does not match its JSONL trace")
    return value


def read_event_log(path):
    log = Path(path)
    if log.is_symlink() or not log.is_file():
        raise ValueError("run event log must be a regular file")
    events = []
    total = 0
    with log.open("rb") as source:
      for number, raw_line in enumerate(source, 1):
        total += len(raw_line)
        if total > 64 * 1024 * 1024 or len(raw_line) > 4 * 1024 * 1024:
            raise ValueError("run event log exceeds the bounded JSONL size")
        try:
            line = raw_line.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ValueError(
                f"invalid run event encoding at line {number}: {exc}"
            ) from exc
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise ValueError(f"invalid run event JSON at line {number}: {exc}") from exc
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            raise ValueError(f"run event line {number} must be an event object")
        events.append(event)
    return events


def read_bounded_json_object(path, label, limit=4 * 1024 * 1024):
    target = Path(path)
    try:
        with target.open("rb") as source:
            raw = source.read(limit + 1)
    except OSError as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    if len(raw) > limit:
        raise ValueError(f"{label} exceeds the bounded JSON size")
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def wave_trajectory(run, state, wave, base_sha, head_sha, task_ids=None):
    policy = state.get("waves", {}).get(str(wave), {})
    current_task_ids = policy.get("tasks", [])
    repo = Path(run).resolve().parents[2]
    expected_task_ids = []
    for task_id in current_task_ids:
        task_path = run_regular_file(run, f"tasks/{task_id}.json")
        task = read_bounded_json_object(task_path, f"task record for {task_id}")
        merge_commit = task.get("merge_commit")
        if not isinstance(merge_commit, str) or not merge_commit:
            continue
        contained = subprocess.run(
            ["git", "merge-base", "--is-ancestor", merge_commit, head_sha],
            cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if contained.returncode == 0:
            expected_task_ids.append(task_id)
    if task_ids is None:
        task_ids = expected_task_ids
    elif task_ids != expected_task_ids:
        raise ValueError(
            "trajectory task snapshot does not exactly cover the reviewed wave head"
        )
    events = read_event_log(Path(run) / "events.jsonl")
    relevant = [event for event in events
                if event.get("task") in task_ids or event.get("wave") == wave]
    workers = []
    unavailable = []
    aggregate = {
        "tool_calls": 0, "failed_calls": 0,
        "empty_result_calls": 0, "duplicate_calls": 0,
    }
    for task_id in task_ids:
        result_path = Path(run) / "workers" / f"{task_id}.result.json"
        if not result_path.is_file() or result_path.is_symlink():
            unavailable.append(task_id)
            continue
        result = read_bounded_json_object(
            result_path, f"worker result for {task_id}"
        )
        trajectory = result.get("trajectory")
        if not isinstance(trajectory, dict):
            unavailable.append(task_id)
            continue
        try:
            record = read_bounded_json_object(
                run_regular_file(run, f"workers/{task_id}.json"),
                f"worker record for {task_id}",
            )
            trace_path = run_regular_file(run, result.get("stdout"))
            if (record.get("task") != task_id
                    or record.get("state") != "succeeded"
                    or record.get("execution") != trajectory.get("execution")
                    or record.get("runner") != trajectory.get("runner")
                    or record.get("stdout") != f"workers/{task_id}.stdout.log"
                    or record.get("stderr") != f"workers/{task_id}.stderr.log"
                    or record.get("last_message") != f"workers/{task_id}.final.txt"
                    or record.get("result") != f"workers/{task_id}.result.json"
                    or result.get("stdout") != f"workers/{task_id}.stdout.log"
                    or result.get("stderr") != f"workers/{task_id}.stderr.log"
                    or result.get("task") != task_id or result.get("exit_code") != 0):
                raise ValueError("worker result/record identity is inconsistent")
            validate_trajectory(
                trajectory, "worker", task_id, record["execution"], trace_path
            )
        except ValueError as exc:
            raise ValueError(f"invalid worker trajectory for {task_id}: {exc}") from exc
        workers.append({
            "task": task_id,
            "trajectory_sha256": object_sha256(trajectory),
            "disposition": trajectory.get("disposition"),
            "anomalies": trajectory.get("anomalies", []),
            "counts": trajectory.get("counts", {}),
            "execution": trajectory.get("execution", {}),
            "runner": trajectory.get("runner", {}),
        })
        for key in aggregate:
            value = trajectory.get("counts", {}).get(key, 0)
            if isinstance(value, int) and not isinstance(value, bool):
                aggregate[key] += value
    anomalies = []
    if unavailable:
        anomalies.append({
            "code": "TRACE_TOOL_VISIBILITY_UNAVAILABLE",
            "count": len(unavailable),
            "tasks": unavailable,
        })
    for worker in workers:
        for anomaly in worker["anomalies"]:
            anomalies.append({"task": worker["task"], **anomaly})
    disposition = "escalate" if anomalies else "pass"
    report = {
        "schema_version": 1,
        "run_id": Path(run).name,
        "wave": wave,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "tasks": task_ids,
        "state_event_count": len(relevant),
        "state_event_sha256": object_sha256(relevant),
        "worker_trajectories": workers,
        "tool_visibility_unavailable": unavailable,
        "counts": aggregate,
        "anomalies": anomalies,
        "disposition": disposition,
    }
    report["sha256"] = object_sha256(report)
    return report


def validate_eval_case(value, run_dir=None):
    if not isinstance(value, dict):
        raise ValueError("eval case must be an object")
    required = {
        "schema_version", "id", "status", "source", "observation",
        "attribution", "capability", "expected_outcome", "validation_scope",
        "claim_boundary",
    }
    status = value.get("status")
    expected = required | ({"decision"} if status in {"approved", "rejected"}
                           else set())
    if set(value) != expected or value.get("schema_version") != 1:
        raise ValueError("eval case has unknown or missing fields")
    identifier = value.get("id")
    if (not isinstance(identifier, str) or not identifier
            or identifier[0] not in
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
            or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                   for ch in identifier) or ".." in identifier):
        raise ValueError("eval case id must be a safe identifier")
    if status not in {"candidate", "approved", "rejected"}:
        raise ValueError("eval case status must be candidate, approved, or rejected")
    if status != "candidate":
        decision = value.get("decision")
        if (not isinstance(decision, dict)
                or set(decision) != {
                    "authority", "outcome", "evidence", "decided_at",
                }
                or decision.get("authority") != "coordinator"
                or decision.get("outcome") != status
                or not isinstance(decision.get("evidence"), str)
                or not decision["evidence"].strip()
                or not isinstance(decision.get("decided_at"), str)
                or not decision["decided_at"]):
            raise ValueError("terminal eval cases require a coordinator decision")
    source = value.get("source")
    if (not isinstance(source, dict)
            or set(source) != {"kind", "run_id", "evidence", "sha256"}
            or source.get("kind") not in {
                "user-correction", "trajectory-anomaly", "review-finding",
                "rollback", "tool-failure",
            }
            or not isinstance(source.get("run_id"), str) or not source["run_id"]
            or not isinstance(source.get("evidence"), str) or not source["evidence"]
            or not isinstance(source.get("sha256"), str)
            or len(source["sha256"]) != 64
            or any(ch not in "0123456789abcdef" for ch in source["sha256"])):
        raise ValueError("eval case source must be a frozen run evidence reference")
    if value.get("attribution") not in {"agent", "dependency", "mixed"}:
        raise ValueError("eval case attribution must be agent, dependency, or mixed")
    for field in (
        "observation", "capability", "expected_outcome",
        "validation_scope", "claim_boundary",
    ):
        item = value.get(field)
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"eval case {field} must be a non-empty string")
    if run_dir is not None:
        if source["run_id"] != Path(run_dir).name:
            raise ValueError("eval case source run_id does not match its run")
        evidence = run_regular_file(run_dir, source["evidence"])
        if file_sha256(evidence) != source["sha256"]:
            raise ValueError("eval case evidence digest does not match the run file")
    return value


def validate_learning_manifest(value, run_id=None):
    source_commits = value.get("source_commits", []) if isinstance(value, dict) else []
    if (not isinstance(value, dict)
            or not {"schema_version", "run_id", "project", "items"} <= set(value)
            or value.get("schema_version") != 1
            or not isinstance(value.get("run_id"), str) or not value["run_id"]
            or not isinstance(value.get("project"), str) or not value["project"].strip()
            or not isinstance(source_commits, list)
            or any(not isinstance(commit, str)
                   or not re.fullmatch(r"[0-9a-fA-F]{7,64}", commit)
                   for commit in source_commits)
            or len(source_commits) != len(set(source_commits))
            or not isinstance(value.get("items"), list)
            or any(not isinstance(item, dict) for item in value["items"])):
        raise ValueError("learning manifest must contain typed run/project/items fields")
    if run_id is not None and value["run_id"] != run_id:
        raise ValueError("learning manifest run_id does not match its run")
    for item in value["items"]:
        if (not safe_identifier_value(item.get("id"))
                or not isinstance(item.get("title"), str) or not item["title"].strip()
                or item.get("category") not in {"knowledge", "lesson", "skill", "eval"}
                or not isinstance(item.get("status"), str) or not item["status"]
                or any(field in item and not isinstance(item[field], str)
                       for field in (
                           "file", "section", "intended_destination",
                           "validation_scope", "claim_boundary",
                       ))
                or ("skill_name" in item
                    and not safe_identifier_value(item["skill_name"]))):
            raise ValueError("learning manifest items need id/title/category/status")
    return value


def safe_identifier_value(value):
    return bool(
        isinstance(value, str) and value
        and value[0] in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        and ".." not in value
        and all(character in
                "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                for character in value)
    )
