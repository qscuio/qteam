#!/usr/bin/env python3
"""Transactional state manager for QTeam runs.

Coordinators and workers must use this command instead of editing state.json or
task records directly. Writes are locked, atomic, and mirrored to events.jsonl.
"""

import argparse
import fnmatch
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True

from agent_team_policy import (
    MODEL_PROFILES, REVIEW_MODEL_PROFILES, REVERSIBILITY_ORDER,
    derive_task_policy, review_contract_digest, safe_identifier,
)
from agent_team_eval import (
    calibration_suite, execution_profile, object_sha256,
    run_regular_file, trajectory_independence, validate_calibration,
    validate_eval_case, validate_trajectory, wave_trajectory,
)
from agent_team_artifact import (
    ArtifactError, epic_binding, locked_regular, require_bound_drift, safe_regular,
)


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
    "running": {"blocked", "completed", "failed"},
    "blocked": {"pending", "running", "failed", "superseded"},
    "completed": {"merged", "running", "failed"},
    "failed": {"pending", "superseded"},
    "superseded": set(),
    "merged": set(),
    "artifact_complete": set(),
}
TASK_ENV_KEYS = {"TMPDIR", "PORT_BASE", "TEST_DB_NAME", "COMPOSE_PROJECT_NAME", "BUILD_DIR"}
DEPENDENCY_READY_STATUSES = {"merged", "artifact_complete"}
DEFAULT_SHARED_SURFACES = [
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "pyproject.toml", "setup.cfg", "tox.ini", "go.mod", "go.sum",
    "Cargo.toml", "Cargo.lock", "requirements*.txt", "Makefile",
    "CMakeLists.txt", "configure.ac", "Dockerfile*", "docker-compose*.yml",
    ".gitignore", ".github/**", "migrations/**", "schemas/**", "generated/**",
    "**/*.proto", "**/openapi*", "**/schemas/**", "**/fixtures/**",
    "**/__snapshots__/**", "**/*.snap",
]
SCENARIO_DIMENSIONS = {
    "happy-path", "error-path", "boundary", "abuse-security", "scale",
    "concurrency", "temporal", "data-variation", "permissions",
    "integrations", "recovery", "state-transitions",
}
DECISION_AUTHORITIES = {"user", "maintainer", "security", "release-owner"}
DECISION_ACTIONS = {
    "task-start", "wave-start", "merge", "learning-export", "finish", "publish",
}
PUBLIC_RUNTIME_PATTERNS = (
    ".agents/runs/**", ".codex/qteam-backups/**", ".codex/qteam-project.json",
    "**/.qteam-*.json",
)
PUBLIC_SECRET_PATTERNS = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("aws-key", re.compile(r"\bAKIA[A-Z0-9]{16}\b")),
    ("secret-assignment", re.compile(
        r"(?i)\b(?:password|passwd|api[_-]?key|access[_-]?token|secret)\b"
        r"\s*[:=]\s*['\"]?(?!example\b|dummy\b|redacted\b|test\b|none\b|null\b)"
        r"[^\s'\";#]{8,}"
    )),
)
LOCAL_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:/home/([^/\s]+)/|/Users/([^/\s]+)/|"
    r"[A-Za-z]:\\Users\\([^\\\s]+)\\|/(root)/)",
    re.IGNORECASE,
)
LOCAL_PATH_PLACEHOLDERS = {"user", "username", "name", "example", "<user>"}


def safe_task_id(value):
    return safe_identifier(value)


def finite_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def validate_experiment_contract(record):
    experiment = record.get("experiment")
    if record.get("work_kind") != "experiment":
        if "experiment" in record:
            raise SystemExit("error: experiment contract requires work_kind=experiment")
        return
    required = {
        "goal", "metric", "guard_command", "holdout_command",
        "max_attempts", "plateau_window",
    }
    if not isinstance(experiment, dict) or set(experiment) != required:
        raise SystemExit("error: experiment task needs the exact frozen experiment fields")
    if any(not isinstance(experiment.get(field), str) or not experiment[field]
           for field in ("goal", "guard_command", "holdout_command")):
        raise SystemExit("error: experiment goal and commands must be non-empty strings")
    metric = experiment.get("metric")
    metric_fields = {"name", "direction", "command", "baseline", "minimum_delta"}
    if not isinstance(metric, dict) or set(metric) != metric_fields:
        raise SystemExit("error: experiment metric needs exact frozen fields")
    if (any(not isinstance(metric.get(field), str) or not metric[field]
            for field in ("name", "command"))
            or metric.get("direction") not in {"higher_is_better", "lower_is_better"}
            or (metric.get("baseline") is not None
                and not finite_number(metric.get("baseline")))
            or not finite_number(metric.get("minimum_delta"))
            or metric["minimum_delta"] < 0):
        raise SystemExit("error: invalid experiment metric contract")
    maximum, plateau = experiment.get("max_attempts"), experiment.get("plateau_window")
    if (not isinstance(maximum, int) or isinstance(maximum, bool)
            or not 1 <= maximum <= 50
            or not isinstance(plateau, int) or isinstance(plateau, bool)
            or not 1 <= plateau <= maximum):
        raise SystemExit("error: experiment budget must satisfy 1 <= plateau <= attempts <= 50")


def parse_metric(log_path):
    raw = log_path.read_text(encoding="utf-8", errors="replace").strip()
    try:
        value = float(raw)
    except ValueError:
        raise SystemExit("error: experiment metric command must print exactly one number")
    if not math.isfinite(value):
        raise SystemExit("error: experiment metric command returned a non-finite number")
    return value


def same_number(left, right):
    return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-12)


def metric_delta(direction, candidate, incumbent):
    return candidate - incumbent if direction == "higher_is_better" else incumbent - candidate


def git(args, cwd, check=True):
    res = subprocess.run(["git", *args], cwd=cwd, text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and res.returncode:
        raise SystemExit(res.stderr.strip() or f"error: git {' '.join(args)} failed")
    return res


def git_bytes(args, cwd):
    res = subprocess.run(["git", *args], cwd=cwd, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    if res.returncode:
        raise SystemExit(res.stderr.decode(errors="replace").strip())
    return res.stdout


def stable_patch_id(repo, commit):
    shown = git_bytes(["show", "--pretty=format:", "--binary", commit], repo)
    result = subprocess.run(
        ["git", "patch-id", "--stable"], cwd=repo, input=shown,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise SystemExit(result.stderr.decode(errors="replace").strip())
    output = result.stdout.decode().split()
    return output[0] if output else "EMPTY"


def validate_owned_commit_range(repo, task, previous, integration, ancestor):
    task_commits = git(
        ["rev-list", "--reverse", f"{task['base_commit']}..{task['check_result']['head_sha']}"],
        repo,
    ).stdout.splitlines()
    owned = git(["rev-list", "--reverse", f"{previous}..{integration}"], repo).stdout.splitlines()
    if not task_commits or not owned:
        raise SystemExit("error: task/integration commit provenance is empty")
    if ancestor:
        extras = [commit for commit in owned if commit not in set(task_commits)]
        direct = owned == task_commits
        merged = False
        if len(extras) == 1 and extras[0] == integration:
            parents = git(["rev-list", "--parents", "-n", "1", integration], repo).stdout.split()[1:]
            merged = (len(parents) >= 2 and previous in parents
                      and task["check_result"]["head_sha"] in parents
                      and [commit for commit in owned if commit != integration]
                      == task_commits)
        if not direct and not merged:
            raise SystemExit(
                "error: integration range includes commits not owned by checked task"
            )
    else:
        task_merges = git(
            ["rev-list", "--merges", f"{task['base_commit']}..{task['check_result']['head_sha']}"],
            repo,
        ).stdout.splitlines()
        integration_merges = git(
            ["rev-list", "--merges", f"{previous}..{integration}"], repo
        ).stdout.splitlines()
        if task_merges or integration_merges or len(task_commits) != len(owned):
            raise SystemExit(
                "error: patch-equivalent integration must map one-to-one to task commits"
            )
        if ([stable_patch_id(repo, commit) for commit in task_commits]
                != [stable_patch_id(repo, commit) for commit in owned]):
            raise SystemExit(
                "error: integration commits are not one-to-one patch-equivalent to task commits"
            )
    return owned


def rebuild_legacy_integration_provenance(repo, state, records):
    merged = [task for task in records if task.get("status") == "merged"]
    if not merged:
        return state["base_commit"], []
    current = integration_head(repo, state)
    for task in merged:
        if (task.get("check_result", {}).get("status") != "passed"
                or not task.get("check_result", {}).get("head_sha")
                or not task.get("merge_commit")):
            raise SystemExit(
                "error: cannot safely migrate merged v2 task without check/merge evidence"
            )
        task["merge_commit"] = git(
            ["rev-parse", f"{task['merge_commit']}^{{commit}}"], repo
        ).stdout.strip()
    merged.sort(key=lambda task: int(git(
        ["rev-list", "--count", f"{state['base_commit']}..{task['merge_commit']}"], repo
    ).stdout.strip()))
    previous = state["base_commit"]
    entries = []
    for task in merged:
        target = task["merge_commit"]
        checked = task["check_result"]["head_sha"]
        if (git(["merge-base", "--is-ancestor", previous, target], repo,
                check=False).returncode
                or git(["merge-base", "--is-ancestor", target, current], repo,
                       check=False).returncode):
            raise SystemExit("error: merged v2 task history is not a linear ownership chain")
        task_patch = git_bytes(
            ["diff", "--binary", "--full-index", task["base_commit"], checked], repo
        )
        integration_patch = git_bytes(
            ["diff", "--binary", "--full-index", previous, target], repo
        )
        if task_patch != integration_patch:
            raise SystemExit(
                "error: merged v2 integration delta differs from checked task; "
                "start a new run from the current branch"
            )
        ancestor = not git(
            ["merge-base", "--is-ancestor", checked, target], repo, check=False
        ).returncode
        commits = validate_owned_commit_range(repo, task, previous, target, ancestor)
        entries.append({
            "task": task["id"], "from_sha": previous, "to_sha": target,
            "commits": commits,
            "patch_sha256": hashlib.sha256(integration_patch).hexdigest(),
            "recorded_at": now(), "migrated_from_schema": 2,
        })
        previous = target
    if previous != current:
        raise SystemExit(
            "error: v2 integration contains commits after the last gated task; "
            "start a new run from the current branch"
        )
    return current, entries


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


def state_regular(path, label, *, required=False):
    try:
        return safe_regular(path, label, required=required)
    except ArtifactError as exc:
        raise SystemExit(f"error: {exc}")


def event_recorded(run_dir, txid):
    path = state_regular(run_dir / "events.jsonl", "run event log")
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise SystemExit("error: run event log entries must be JSON objects")
            if record.get("txid") == txid:
                return True
        except (json.JSONDecodeError, RecursionError) as exc:
            raise SystemExit(f"error: invalid run event log: {exc}")
    return False


def repair_truncated_event_tail(run_dir):
    path = state_regular(run_dir / "events.jsonl", "run event log")
    if not path.exists():
        return
    raw = path.read_bytes()
    offset = 0
    lines = raw.splitlines(keepends=True)
    for index, line in enumerate(lines):
        complete = line.endswith(b"\n")
        try:
            text = line.decode("utf-8", errors="strict").strip()
            record = json.loads(text) if text else None
            if text and not isinstance(record, dict):
                raise ValueError("event is not an object")
        except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
            if index == len(lines) - 1 and not complete:
                flags = os.O_RDWR
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(path, flags)
                try:
                    os.ftruncate(descriptor, offset)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                return
            raise SystemExit(f"error: invalid durable run event log: {exc}")
        offset += len(line)


def validate_transaction_write(run_dir, relative, value):
    if not isinstance(relative, str) or not isinstance(value, dict):
        raise SystemExit("error: run transaction writes must map paths to objects")
    path = Path(relative)
    if (path.is_absolute() or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)):
        raise SystemExit("error: invalid run transaction path")
    if relative == "state.json":
        required = {
            "schema_version", "run_id", "goal", "base_branch", "base_commit",
            "integration_branch", "phase", "current_wave", "tasks",
            "decisions", "gates", "risk_required", "shared_surfaces",
            "plan_file", "finished", "created_at", "updated_at",
        }
        if (not required.issubset(value)
                or value.get("schema_version") not in {2, 3, 4, 5, 6}
                or value.get("run_id") != run_dir.name
                or value.get("phase") not in PHASES
                or not isinstance(value.get("finished"), bool)
                or any(not isinstance(value.get(field), dict)
                       for field in ("tasks", "decisions", "gates"))
                or ("waves" in value and not isinstance(value["waves"], dict))
                or ("integration_provenance" in value
                    and not isinstance(value["integration_provenance"], list))):
            raise SystemExit("error: invalid state.json in run transaction")
        if value.get("schema_version") == 6:
            v6_required = {
                "integration_provenance_head", "integration_provenance", "waves",
                "risk_forced", "hard_to_reverse", "model_profiles",
                "review_model_profiles",
            }
            if (not v6_required.issubset(value)
                    or not isinstance(value.get("integration_provenance_head"), str)
                    or not isinstance(value.get("integration_provenance"), list)
                    or not isinstance(value.get("waves"), dict)
                    or not isinstance(value.get("risk_forced"), bool)
                    or not isinstance(value.get("hard_to_reverse"), bool)
                    or any(not isinstance(value.get(field), dict)
                           for field in ("model_profiles", "review_model_profiles"))):
                raise SystemExit("error: invalid schema-version-6 state core")
        return
    if relative == "learning-outbox/manifest.json":
        try:
            current = load_json(run_regular_file(run_dir, relative))
        except ValueError as exc:
            raise SystemExit(f"error: invalid learning manifest target: {exc}")
        if current == value:
            return
        if (not isinstance(current, dict) or not isinstance(value, dict)
                or set(current) != set(value)
                or any(current.get(key) != value.get(key)
                       for key in set(current) - {"items"})
                or not isinstance(current.get("items"), list)
                or not isinstance(value.get("items"), list)
                or len(current["items"]) != len(value["items"])):
            raise SystemExit("error: invalid learning manifest transition")
        changed = 0
        for old, new in zip(current["items"], value["items"]):
            if old == new:
                continue
            changed += 1
            decision = new.get("decision") if isinstance(new, dict) else None
            if (not isinstance(old, dict) or not isinstance(new, dict)
                    or any(old.get(key) != new.get(key)
                           for key in set(old) | set(new)
                           if key not in {"status", "decision"})
                    or old.get("status") != "proposed"
                    or new.get("status") not in {"approved", "rejected"}
                    or not isinstance(decision, dict)
                    or decision.get("authority") != "coordinator"
                    or decision.get("outcome") != new.get("status")
                    or not decision.get("evidence")
                    or not decision.get("decided_at")):
                raise SystemExit("error: invalid learning item decision transition")
        if changed != 1:
            raise SystemExit("error: learning decision must change exactly one item")
        return
    if (len(path.parts) == 3 and path.parts[:2]
            == ("learning-outbox", "eval-cases") and path.suffix == ".json"):
        try:
            current = validate_eval_case(
                load_json(run_regular_file(run_dir, relative)), run_dir
            )
            validate_eval_case(value, run_dir)
        except ValueError as exc:
            raise SystemExit(f"error: invalid eval-case transaction: {exc}")
        if current == value:
            return
        if (current.get("id") != path.stem or value.get("id") != path.stem
                or current.get("status") != "candidate"
                or value.get("status") not in {"approved", "rejected"}
                or any(current.get(key) != value.get(key)
                       for key in current if key != "status")):
            raise SystemExit("error: invalid eval-case decision transition")
        return
    if len(path.parts) != 2 or path.suffix != ".json":
        raise SystemExit("error: unsupported run transaction target")
    category, filename = path.parts
    record_id = filename[:-5]
    if not safe_task_id(record_id) or value.get("id") != record_id:
        raise SystemExit("error: run transaction record identity mismatch")
    if category == "tasks":
        required = {
            "id", "status", "branch", "worktree", "base_commit", "wave",
            "write_set",
        }
        if (not required.issubset(value)
                or not isinstance(value.get("status"), str)
                or not isinstance(value.get("wave"), int)
                or isinstance(value.get("wave"), bool)
                or not isinstance(value.get("write_set"), list)
                or ("depends_on" in value
                    and not isinstance(value["depends_on"], list))):
            raise SystemExit("error: invalid task record in run transaction")
        return
    if category == "decisions":
        required = {
            "schema_version", "id", "status", "question", "authority", "scope",
        }
        if (not required.issubset(value) or value.get("schema_version") != 1
                or value.get("status") not in {"open", "resolved", "superseded"}
                or not isinstance(value.get("scope"), dict)):
            raise SystemExit("error: invalid decision record in run transaction")
        return
    raise SystemExit("error: unsupported run transaction target")


def validate_run_transaction(run_dir, transaction):
    if not isinstance(transaction, dict):
        raise SystemExit("error: invalid run transaction")
    version = transaction.get("schema_version")
    fields = {"schema_version", "txid", "writes", "event"}
    if (set(transaction) != fields
            or version not in {1, 2}
            or not isinstance(transaction.get("txid"), str)
            or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", transaction["txid"])
            or not isinstance(transaction.get("writes"), dict)
            or not transaction["writes"]
            or not isinstance(transaction.get("event"), dict)
            or not isinstance(transaction["event"].get("event"), str)
            or not transaction["event"]["event"]):
        raise SystemExit("error: invalid run transaction")
    if version == 1:
        state_path = run_dir / "state.json"
        current_version = (load_json(state_path).get("schema_version")
                           if state_path.is_file() else None)
        legacy_init = False
        if current_version is None:
            value = transaction.get("writes", {}).get("state.json")
            legacy_init = (
                set(transaction.get("writes", {})) == {"state.json"}
                and isinstance(value, dict)
                and value.get("schema_version") in {2, 3, 4, 5}
                and value.get("run_id") == run_dir.name
                and value.get("phase") == "INIT"
                and value.get("finished") is False
                and value.get("tasks") == {}
                and transaction.get("event", {}).get("event") == "run_created"
            )
        if current_version not in {2, 3, 4, 5} and not legacy_init:
            raise SystemExit(
                "error: legacy run transactions are rejected for current runs"
            )
    else:
        digest = object_sha256(transaction)
        prepared = False
        final_recorded = False
        event_path = state_regular(run_dir / "events.jsonl", "run event log")
        if event_path.exists():
            try:
                event_text = event_path.read_text(
                    encoding="utf-8", errors="strict"
                )
            except UnicodeError as exc:
                raise SystemExit(f"error: invalid run event log encoding: {exc}")
            for line in event_text.splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, RecursionError) as exc:
                    raise SystemExit(f"error: invalid run event log: {exc}")
                if not isinstance(record, dict):
                    raise SystemExit("error: run event log entries must be objects")
                if (record.get("event") == "transaction_prepared"
                        and record.get("prepared_txid") == transaction["txid"]
                        and record.get("transaction_sha256") == digest):
                    prepared = True
                if record.get("txid") == transaction["txid"]:
                    final_recorded = True
        if not prepared:
            raise SystemExit("error: run transaction has no durable prepare record")
        if final_recorded:
            for relative, updated in transaction["writes"].items():
                path = run_dir / relative
                if not path.is_file() or load_json(path) != updated:
                    raise SystemExit(
                        "error: completed run transaction cannot replay stale writes"
                    )
    for relative, value in transaction["writes"].items():
        validate_transaction_write(run_dir, relative, value)
    learning = [path for path in transaction["writes"]
                if path.startswith("learning-outbox/")]
    if learning:
        event = transaction["event"]
        manifest = transaction["writes"].get(
            "learning-outbox/manifest.json", {}
        )
        decided = [
            item for item in manifest.get("items", [])
            if isinstance(item, dict) and item.get("id") == event.get("item")
        ] if isinstance(manifest, dict) else []
        if (event.get("event") != "learning_item_decided"
                or event.get("outcome") not in {"approved", "rejected"}
                or not safe_task_id(event.get("item"))
                or len(decided) != 1
                or decided[0].get("status") != event.get("outcome")
                or decided[0].get("decision", {}).get("evidence")
                != event.get("evidence")
                or event.get("item_sha256") != object_sha256(decided[0])):
            raise SystemExit("error: invalid learning decision transaction binding")
        if decided[0].get("category") == "eval":
            expected_case = (
                f"learning-outbox/eval-cases/{event.get('case_id')}.json"
            )
            if (set(learning) != {
                    "learning-outbox/manifest.json", expected_case,
                    }
                    or not safe_task_id(event.get("case_id"))
                    or event.get("case_sha256") != object_sha256(
                        transaction["writes"][expected_case]
                    )):
                raise SystemExit(
                    "error: invalid eval learning decision transaction binding"
                )
        elif (decided[0].get("category")
              not in {"knowledge", "lesson", "skill"}
              or set(learning) != {"learning-outbox/manifest.json"}
              or event.get("category") != decided[0].get("category")
              or "case_id" in event or "case_sha256" in event):
            raise SystemExit(
                "error: invalid non-eval learning decision transaction binding"
            )
    return transaction


def apply_transaction(run_dir, transaction):
    transaction = validate_run_transaction(run_dir, transaction)
    for rel, value in transaction["writes"].items():
        lexical = run_dir / rel
        state_regular(lexical, "run transaction target")
        path = lexical.resolve()
        if run_dir.resolve() not in path.parents:
            raise SystemExit("error: transaction path escaped run directory")
        atomic_json(path, value)
    event = transaction.get("event")
    if event and not event_recorded(run_dir, transaction["txid"]):
        append_event(run_dir, {"txid": transaction["txid"], **event})


def recover_transaction(run_dir):
    intent = state_regular(run_dir / ".transaction.json", "run transaction")
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
        "schema_version": 2, "txid": uuid.uuid4().hex,
        "writes": {str(Path(path).relative_to(run_dir)): value
                   for path, value in writes.items()},
        "event": event,
    }
    intent = state_regular(run_dir / ".transaction.json", "run transaction")
    state_regular(run_dir / "events.jsonl", "run event log")
    repair_truncated_event_tail(run_dir)
    event_recorded(run_dir, transaction["txid"])
    append_event(run_dir, {
        "event": "transaction_prepared",
        "prepared_txid": transaction["txid"],
        "transaction_sha256": object_sha256(transaction),
    })
    atomic_json(intent, transaction)
    apply_transaction(run_dir, transaction)
    intent.unlink()
    fd_dir = os.open(run_dir, os.O_RDONLY)
    try:
        os.fsync(fd_dir)
    finally:
        os.close(fd_dir)


@contextmanager
def locked(run_dir, *, allow_sealed=False):
    run_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_regular(run_dir / ".state.lock", "run state lock")
    try:
        with locked_regular(lock_path, "run state lock"):
            state_regular(run_dir / ".transaction.json", "run transaction")
            state_regular(run_dir / "events.jsonl", "run event log")
            repair_truncated_event_tail(run_dir)
            event_recorded(run_dir, "")
            state_path = state_regular(run_dir / "state.json", "run state")
            recover_transaction(run_dir)
            if (state_path.is_file() and not allow_sealed
                    and load_json(state_path).get("publication_seal")):
                raise SystemExit(
                    "error: publication seal freezes READY state and gate mutations"
                )
            yield
    except ArtifactError as exc:
        raise SystemExit(f"error: {exc}")


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"error: missing {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"error: invalid JSON in {path}: {exc}")


def append_event(run_dir, event):
    event = {"ts": now(), **event}
    path = state_regular(run_dir / "events.jsonl", "run event log")
    flags = os.O_RDWR | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise SystemExit(f"error: cannot safely append run event log: {exc}")
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise SystemExit("error: run event log is not a regular file")
    size = os.fstat(descriptor).st_size
    needs_newline = size > 0 and os.pread(descriptor, 1, size - 1) != b"\n"
    with os.fdopen(descriptor, "a", encoding="utf-8") as out:
        if needs_newline:
            out.write("\n")
        out.write(json.dumps(event, sort_keys=True) + "\n")
        out.flush()
        os.fsync(out.fileno())


def integration_head(repo, state):
    return git(["rev-parse", state["integration_branch"]], repo).stdout.strip()


def validate_reviews(repo, run_dir, state, head_sha=None, require_risk=None,
                     through_wave=None):
    head_sha = head_sha or integration_head(repo, state)
    errors = []
    waves = state.get("waves")
    if not isinstance(waves, dict):
        raise SystemExit("error: review gate invalid: run has no wave policy")
    try:
        available_waves = sorted(int(wave) for wave in waves)
    except (TypeError, ValueError):
        raise SystemExit("error: review gate invalid: malformed wave policy keys")
    if (any(wave < 1 or str(wave) not in waves for wave in available_waves)
            or len(available_waves) != len(waves)):
        raise SystemExit("error: review gate invalid: malformed wave policy keys")
    if not available_waves:
        available_waves = [1]
    if through_wave is not None and through_wave not in available_waves:
        raise SystemExit(f"error: review gate invalid: unknown wave {through_wave}")
    selected_waves = [wave for wave in available_waves
                      if through_wave is None or wave <= through_wave]

    def wave_policy(wave):
        if waves:
            return waves[str(wave)]
        return {
            "execution_tier": "standard", "review_intensity": "full",
            "require_risk_review": bool(state.get("risk_forced")),
            "risk_flags": [], "tasks": [],
            "reversibility": "contained-reversible",
            "integration_lane": "shadow",
            "require_user_finish_decision": False,
        }

    def object_sha(value):
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def packet_policy_valid(packet, wave):
        if (not isinstance(packet, dict)
                or not isinstance(packet.get("trajectory"), dict)):
            return False
        policy = wave_policy(wave)
        tier = policy.get("execution_tier")
        profiles = state.get("review_model_profiles")
        profile = profiles.get(tier) if isinstance(profiles, dict) else None
        expected_execution = ({"tier": tier, "model": profile.get("model"),
                               "thinking": profile.get("thinking"),
                               "provider": profile.get("provider"),
                               "family": profile.get("family")}
                              if isinstance(profile, dict) else None)
        if (expected_execution is None
                or any(not isinstance(expected_execution.get(field), str)
                       or not expected_execution[field]
                       for field in (
                           "tier", "model", "thinking", "provider", "family"
                       ))):
            return False
        try:
            expected_trajectory = wave_trajectory(
                run_dir, state, wave, packet.get("base_sha"),
                packet.get("head_sha"),
                task_ids=packet["trajectory"].get("tasks"),
            )
            expected_calibration = calibration_suite(packet.get("axis"))
        except ValueError:
            return False
        expected_families = sorted({
            item.get("execution", {}).get("family")
            for item in expected_trajectory["worker_trajectories"]
            if item.get("execution", {}).get("family")
        })
        expected_independence = trajectory_independence(
            expected_trajectory, expected_execution["family"]
        )
        return (
            packet.get("schema_version") == 3
            and packet.get("wave") == wave
            and packet.get("execution_tier") == tier
            and packet.get("review_intensity") == policy.get("review_intensity")
            and packet.get("risk_flags") == policy.get("risk_flags", [])
            and packet.get("review_execution") == expected_execution
            and packet.get("trajectory") == expected_trajectory
            and packet.get("calibration") == expected_calibration
            and packet.get("generator_families") == expected_families
            and packet.get("judge_independence") == expected_independence
            and isinstance(packet.get("runner"), dict)
            and packet.get("runner", {}).get("name") == "codex-cli"
            and isinstance(packet.get("runner", {}).get("version"), str)
            and bool(packet.get("runner", {}).get("version"))
            and packet.get("review_contract_sha256") == review_contract_digest(
                packet.get("axis"), policy.get("review_intensity")
            )
        )

    def source_snapshots_valid(packet):
        source_root = (run_dir / "reviews" / "sources").resolve()
        for field in ("spec_sources", "standards_sources", "digest_sources"):
            records = packet.get(field)
            if not isinstance(records, list):
                return False
            for record in records:
                if (not isinstance(record, dict)
                        or not isinstance(record.get("sha256"), str)
                        or len(record["sha256"]) != 64
                        or not isinstance(record.get("snapshot"), str)):
                    return False
                path = (run_dir / record["snapshot"]).resolve()
                if (path.parent != source_root
                        or path.name != f"{record['sha256']}.source"
                        or path.is_symlink() or not path.is_file()
                        or hashlib.sha256(path.read_bytes()).hexdigest()
                        != record["sha256"]):
                    return False
        return True

    def receipt_trajectory_valid(receipt):
        try:
            trace_path = run_regular_file(run_dir, receipt.get("stdout_log"))
            validate_trajectory(
                receipt.get("trajectory"), "review", receipt.get("session_id"),
                receipt.get("execution"), trace_path,
            )
        except ValueError:
            return False
        return (
            receipt.get("trajectory", {}).get("runner") == receipt.get("runner")
            and receipt.get("trajectory", {}).get("disposition") == "pass"
        )

    def receipt_paths_valid(receipt):
        session = receipt.get("session_id") if isinstance(receipt, dict) else None
        return bool(
            safe_task_id(session)
            and receipt.get("result") == f"reviews/results/{session}.json"
            and receipt.get("stdout_log")
            == f"reviews/logs/{session}.stdout.log"
            and receipt.get("stderr_log")
            == f"reviews/logs/{session}.stderr.log"
        )

    def attestation_valid(ledger):
        packet = ledger.get("packet", {})
        attestation = ledger.get("attestation", {})
        result = attestation.get("result", {})
        receipt_rel = attestation.get("receipt")
        if (not source_snapshots_valid(packet)
                or not isinstance(receipt_rel, str) or not receipt_rel
                or not attestation.get("reviewer")
                or not attestation.get("session_id")
                or result.get("axis") != packet.get("axis")
                or result.get("verdict") != "pass"
                or result.get("trajectory_verdict") != "pass"):
            return False
        try:
            validate_calibration(
                packet.get("axis"), packet.get("calibration", {}).get("sha256"),
                result.get("calibration_results"),
            )
        except ValueError:
            return False
        receipt_path = (run_dir / receipt_rel).resolve()
        receipt_root = (run_dir / "reviews" / "receipts").resolve()
        if (receipt_path.parent != receipt_root or receipt_path.is_symlink()
                or not receipt_path.is_file()
                or hashlib.sha256(receipt_path.read_bytes()).hexdigest()
                != attestation.get("receipt_sha256")):
            return False
        receipt = load_json(receipt_path)
        result_path = (run_dir / receipt.get("result", "")).resolve()
        result_root = (run_dir / "reviews" / "results").resolve()
        if (result_path.parent != result_root or result_path.is_symlink()
                or not result_path.is_file()):
            return False
        saved_result = load_json(result_path)
        iteration = packet.get("iteration", 1)
        suffix = "" if iteration == 1 else f"-r{iteration}"
        expected_ledger = f"reviews/wave-{packet.get('wave')}-{packet.get('axis')}{suffix}.json"
        return (
            receipt.get("status") == "passed"
            and receipt.get("exit_code") == 0
            and receipt_paths_valid(receipt)
            and receipt.get("ledger") == expected_ledger
            and receipt.get("packet_sha256") == object_sha(packet)
            and receipt.get("ledger_findings_sha256")
            == object_sha(ledger.get("findings", []))
            and receipt.get("review_contract_sha256")
            == packet.get("review_contract_sha256")
            and receipt.get("calibration_sha256")
            == packet.get("calibration", {}).get("sha256")
            and receipt.get("review_head_sha") == packet.get("head_sha")
            and receipt.get("reviewer") == attestation.get("reviewer")
            and receipt.get("session_id") == attestation.get("session_id")
            and receipt.get("execution") == packet.get("review_execution")
            and receipt.get("runner") == packet.get("runner")
            and isinstance(receipt.get("trajectory"), dict)
            and receipt_trajectory_valid(receipt)
            and attestation.get("execution") == packet.get("review_execution")
            and receipt.get("result_sha256") == object_sha(saved_result)
            and attestation.get("result_sha256") == receipt.get("result_sha256")
            and saved_result == result
        )

    def needs_fix_receipt_valid(path, ledger):
        packet = ledger.get("packet", {})
        if not source_snapshots_valid(packet):
            return False
        packet_sha = object_sha(packet)
        expected_ledger = str(path.relative_to(run_dir))
        ledger_findings = {item.get("id"): item
                           for item in ledger.get("findings", [])}
        attempts = ledger.get("review_attempts", [])
        for receipt_path in (run_dir / "reviews" / "receipts").glob("*.json"):
            if receipt_path.is_symlink() or not receipt_path.is_file():
                continue
            receipt = load_json(receipt_path)
            if (receipt.get("status") != "needs-fix"
                    or receipt.get("exit_code") != 0
                    or not receipt_paths_valid(receipt)
                    or receipt.get("ledger") != expected_ledger
                    or receipt.get("packet_sha256") != packet_sha
                    or receipt.get("review_contract_sha256")
                    != packet.get("review_contract_sha256")
                    or receipt.get("calibration_sha256")
                    != packet.get("calibration", {}).get("sha256")
                    or receipt.get("review_head_sha") != packet.get("head_sha")
                    or receipt.get("execution") != packet.get("review_execution")
                    or receipt.get("runner") != packet.get("runner")
                    or not isinstance(receipt.get("trajectory"), dict)
                    or not receipt_trajectory_valid(receipt)):
                continue
            result_path = (run_dir / receipt.get("result", "")).resolve()
            if (result_path.parent != (run_dir / "reviews" / "results").resolve()
                    or result_path.is_symlink() or not result_path.is_file()):
                continue
            result = load_json(result_path)
            result_findings = [item for item in result.get("findings", [])
                               if isinstance(item, dict)]
            ids = [item.get("id") for item in result_findings]
            try:
                validate_calibration(
                    packet.get("axis"), packet.get("calibration", {}).get("sha256"),
                    result.get("calibration_results"),
                )
            except ValueError:
                continue
            if (result.get("axis") != packet.get("axis")
                    or result.get("verdict") != "needs-fix" or not ids
                    or result.get("trajectory_verdict") not in {"pass", "needs-fix"}
                    or receipt.get("result_sha256") != object_sha(result)):
                continue
            attempt = next((item for item in attempts
                            if item.get("status") == "needs-fix"
                            and item.get("session_id") == receipt.get("session_id")
                            and item.get("reviewer") == receipt.get("reviewer")
                            and item.get("packet_sha256") == packet_sha
                            and item.get("result_sha256") == receipt.get("result_sha256")
                            and item.get("receipt") == str(receipt_path.relative_to(run_dir))
                            and item.get("receipt_sha256")
                            == hashlib.sha256(receipt_path.read_bytes()).hexdigest()
                            and item.get("finding_ids") == ids), None)
            if not attempt:
                continue
            def freshly_closed(finding_id):
                finding = ledger_findings.get(finding_id, {})
                closure_receipt_rel = finding.get("evidence")
                if (finding.get("status") not in {"resolved", "invalid"}
                        or not isinstance(closure_receipt_rel, str)):
                    return False
                closure_receipt_path = (run_dir / closure_receipt_rel).resolve()
                if (closure_receipt_path.parent
                        != (run_dir / "reviews" / "receipts").resolve()
                        or closure_receipt_path.is_symlink()
                        or not closure_receipt_path.is_file()):
                    return False
                closure_receipt = load_json(closure_receipt_path)
                closure_ledger_path = (run_dir / closure_receipt.get("ledger", "")).resolve()
                if (closure_ledger_path.parent != (run_dir / "reviews").resolve()
                        or closure_ledger_path.is_symlink()
                        or not closure_ledger_path.is_file()):
                    return False
                closure_ledger = load_json(closure_ledger_path)
                closure_packet = closure_ledger.get("packet", {})
                frozen = [item for item in closure_packet.get("closure_findings", [])
                          if item.get("ledger") == str(path.relative_to(run_dir))
                          and item.get("id") == finding_id]
                closure_result = closure_ledger.get("attestation", {}).get("result", {})
                resolved = finding.get("status") == "resolved"
                invalid = finding.get("status") == "invalid"
                return (
                    len(frozen) == 1
                    and closure_receipt.get("status") == "passed"
                    and closure_receipt.get("reviewer") == finding.get("reviewer")
                    and closure_ledger.get("completed_at")
                    and attestation_valid(closure_ledger)
                    and ((resolved and finding_id in closure_result.get("resolved_ids", []))
                         or (invalid and finding_id in closure_result.get("invalid_ids", [])))
                )

            result_by_id = {item.get("id"): item for item in result_findings}
            if all(finding_id in ledger_findings
                   and all(ledger_findings[finding_id].get(field)
                           == result_by_id[finding_id].get(field)
                           for field in ("severity", "title", "review_evidence",
                                         "impact", "fix_direction", "owner"))
                   and freshly_closed(finding_id) for finding_id in ids):
                return True
        return False

    def ledgers_for(axis, wave=None):
        found = []
        for path in (run_dir / "reviews").glob(f"wave-*-{axis}*.json"):
            ledger = load_json(path)
            packet = ledger.get("packet", {})
            if packet.get("axis") == axis and (wave is None or packet.get("wave") == wave):
                found.append((packet.get("iteration", 1), path, ledger))
        return found

    for path in (run_dir / "reviews").glob("wave-*.json"):
        packet_wave = load_json(path).get("packet", {}).get("wave")
        if packet_wave not in available_waves:
            errors.append(f"review ledger uses unknown wave: {path.name}")

    def merged_commits(wave):
        commits = []
        for task_id, summary in state.get("tasks", {}).items():
            task = load_json(run_dir / "tasks" / f"{task_id}.json")
            if task.get("wave") != wave or summary.get("status") != "merged":
                continue
            commit = task.get("merge_commit")
            if not commit:
                errors.append(f"merged task {task_id} has no merge commit for review")
            else:
                commits.append(commit)
        return commits

    def range_covers(ledger, commits):
        packet = ledger.get("packet", {})
        base, head = packet.get("base_sha"), packet.get("head_sha")
        if (packet.get("scope") not in {"wave", "fix", "final"} or not base or not head
                or (commits and base == head)):
            return False
        if git(["merge-base", "--is-ancestor", base, head], repo,
               check=False).returncode:
            return False
        return all(
            not git(["merge-base", "--is-ancestor", commit, head], repo,
                    check=False).returncode
            and git(["merge-base", "--is-ancestor", commit, base], repo,
                    check=False).returncode
            for commit in commits
        )

    latest_by_wave = {}
    required_risk_waves = set()
    force_risk = bool(state.get("risk_forced")) or bool(require_risk)
    for wave in selected_waves:
        policy = wave_policy(wave)
        commits = merged_commits(wave)
        wave_latest = {}
        wave_attestations = {}
        for axis in ("spec", "standards"):
            candidates = ledgers_for(axis, wave)
            if not candidates:
                errors.append(f"missing {axis} review for wave {wave}")
                continue
            open_ids = [item.get("id") for _, _, ledger in candidates
                        for item in ledger.get("findings", [])
                        if item.get("status") == "open"]
            if open_ids:
                errors.append(f"{axis} wave {wave} unresolved: {', '.join(open_ids)}")
            latest = max(candidates, key=lambda item: item[0])[2]
            wave_latest[axis] = latest
            if not latest.get("completed_at"):
                errors.append(f"{axis} latest review incomplete for wave {wave}")
            if not packet_policy_valid(latest.get("packet", {}), wave):
                errors.append(f"{axis} review policy mismatch for wave {wave}")
            if not attestation_valid(latest):
                errors.append(f"{axis} review for wave {wave} has no valid runner receipt")
            else:
                wave_attestations[axis] = latest["attestation"]
            if any(not any(
                    ((ledger.get("completed_at") and attestation_valid(ledger))
                     or needs_fix_receipt_valid(path, ledger))
                    and packet_policy_valid(ledger.get("packet", {}), wave)
                    and range_covers(ledger, [commit])
                    for _, path, ledger in candidates) for commit in commits):
                errors.append(f"{axis} review range does not cover merged wave {wave}")
        if all(axis in wave_attestations for axis in ("spec", "standards")):
            if (wave_attestations["spec"]["session_id"]
                    == wave_attestations["standards"]["session_id"]):
                errors.append(f"wave {wave} spec/standards require distinct sessions")
            if (wave_attestations["spec"]["reviewer"]
                    == wave_attestations["standards"]["reviewer"]):
                errors.append(f"wave {wave} spec/standards require distinct reviewers")
        if all(axis in wave_latest for axis in ("spec", "standards")):
            heads = {wave_latest[axis].get("packet", {}).get("head_sha")
                     for axis in ("spec", "standards")}
            if len(heads) != 1:
                errors.append(f"wave {wave} mandatory reviews cover different heads")
            latest_by_wave[wave] = wave_latest
        risk_required = force_risk or bool(policy.get("require_risk_review"))
        if risk_required:
            required_risk_waves.add(wave)
            candidates = ledgers_for("risk", wave)
            if not candidates:
                errors.append(f"missing risk review for wave {wave}")
                continue
            open_ids = [item.get("id") for _, _, ledger in candidates
                        for item in ledger.get("findings", [])
                        if item.get("status") == "open"]
            if open_ids:
                errors.append(f"risk wave {wave} unresolved: {', '.join(open_ids)}")
            latest = max(candidates, key=lambda item: item[0])[2]
            if not latest.get("completed_at") or not attestation_valid(latest):
                errors.append(f"risk review for wave {wave} has no valid runner receipt")
            if not packet_policy_valid(latest.get("packet", {}), wave):
                errors.append(f"risk review policy mismatch for wave {wave}")
            if any(not any(
                    ((ledger.get("completed_at") and attestation_valid(ledger))
                     or needs_fix_receipt_valid(path, ledger))
                    and packet_policy_valid(ledger.get("packet", {}), wave)
                    and range_covers(ledger, [commit])
                    for _, path, ledger in candidates) for commit in commits):
                errors.append(f"risk review range does not cover merged wave {wave}")
            mandatory_heads = {item.get("packet", {}).get("head_sha")
                               for item in wave_latest.values()}
            if mandatory_heads and latest.get("packet", {}).get("head_sha") not in mandatory_heads:
                errors.append(f"risk review for wave {wave} is stale against mandatory reviews")
            risk_attestation = latest.get("attestation", {})
            for axis, attestation in wave_attestations.items():
                if risk_attestation.get("session_id") == attestation.get("session_id"):
                    errors.append(f"wave {wave} risk/{axis} require distinct sessions")
                if risk_attestation.get("reviewer") == attestation.get("reviewer"):
                    errors.append(f"wave {wave} risk/{axis} require distinct reviewers")
    if selected_waves:
        latest_wave = max(selected_waves)
        for axis, ledger in latest_by_wave.get(latest_wave, {}).items():
            if ledger.get("packet", {}).get("head_sha") != head_sha:
                errors.append(f"{axis} latest review does not cover integration HEAD")
    axes = ["spec", "standards"]
    if required_risk_waves:
        axes.append("risk")
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


def dependency_graph_records(run_dir, state, replacement=None):
    records = {}
    replacement_id = replacement.get("id") if replacement else None
    for task_id in state.get("tasks", {}):
        task = (replacement if task_id == replacement_id else
                load_json(run_dir / "tasks" / f"{task_id}.json"))
        if task.get("id") != task_id:
            raise SystemExit(f"error: task record identity mismatch {task_id}")
        records[task_id] = task
    if replacement and replacement_id not in records:
        records[replacement_id] = replacement
    return records


def valid_dependency_ids(dependencies):
    return (
        isinstance(dependencies, list)
        and all(safe_task_id(item) for item in dependencies)
        and len(dependencies) == len(set(dependencies))
    )


def validate_dependency_records(records, state, replacement_id=None):
    for task_id, task in records.items():
        summary_status = state.get("tasks", {}).get(task_id, {}).get("status")
        if (task_id != replacement_id
                and task.get("status") != summary_status):
            raise SystemExit(
                f"error: task {task_id} status mismatch between task record "
                f"({task.get('status')}) and state summary ({summary_status})"
            )
        dependencies = task.get("depends_on")
        if not valid_dependency_ids(dependencies):
            raise SystemExit(
                f"error: task {task_id} depends_on must be unique safe task ids"
            )
        if task_id in dependencies:
            raise SystemExit(f"error: task {task_id} cannot depend on itself")
        for dependency_id in dependencies:
            if dependency_id not in records:
                raise SystemExit(
                    f"error: task {task_id} references unknown dependency "
                    f"{dependency_id}"
                )

    visiting = set()
    visited = set()

    def visit(task_id, path):
        if task_id in visiting:
            start = path.index(task_id)
            cycle = path[start:] + [task_id]
            raise SystemExit(
                "error: task dependency cycle: " + " -> ".join(cycle)
            )
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency_id in records[task_id]["depends_on"]:
            visit(dependency_id, [*path, task_id])
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in sorted(records):
        visit(task_id, [])

    for task_id, task in records.items():
        for dependency_id in task["depends_on"]:
            predecessor = records[dependency_id]
            if predecessor.get("wave", 0) >= task.get("wave", 0):
                raise SystemExit(
                    f"error: dependency {dependency_id} of task {task_id} must be "
                    "in a strictly earlier wave"
                )
    return records


def validate_dependency_graph(run_dir, state, replacement=None):
    records = dependency_graph_records(run_dir, state, replacement)
    replacement_id = replacement.get("id") if replacement else None
    return validate_dependency_records(records, state, replacement_id)


def dependency_blockers(records, task):
    blockers = []
    for dependency_id in task.get("depends_on", []):
        dependency = records.get(dependency_id)
        status = dependency.get("status") if dependency else "missing"
        if status not in DEPENDENCY_READY_STATUSES:
            blockers.append({"task": dependency_id, "status": status})
    return blockers


def require_dependencies_ready(records, task):
    blockers = dependency_blockers(records, task)
    if blockers:
        details = ", ".join(
            f"{item['task']}={item['status']}" for item in blockers
        )
        raise SystemExit(
            f"error: task {task['id']} blocked by dependencies: {details}"
        )


def validate_scenario_coverage(record):
    if not record.get("tdd_required"):
        if "scenario_coverage" in record:
            raise SystemExit("error: scenario_coverage requires a TDD task")
        return
    coverage = record.get("scenario_coverage")
    if not isinstance(coverage, list) or len(coverage) != len(SCENARIO_DIMENSIONS):
        raise SystemExit("error: TDD task requires exactly one row for all 12 scenario dimensions")
    dimensions = [row.get("dimension") for row in coverage if isinstance(row, dict)]
    if len(dimensions) != len(coverage) or set(dimensions) != SCENARIO_DIMENSIONS:
        raise SystemExit("error: scenario_coverage must contain each dimension exactly once")
    seam_ids = {seam["id"] for seam in record.get("test_seams", [])}
    normalized_scenarios = set()
    for row in coverage:
        if set(row) != {"dimension", "applicability", "scenario", "seam_ids", "rationale"}:
            raise SystemExit("error: scenario_coverage rows need exact typed fields")
        applicability = row.get("applicability")
        scenario = row.get("scenario")
        links = row.get("seam_ids")
        rationale = row.get("rationale")
        if (applicability not in {"applicable", "not-applicable"}
                or not isinstance(scenario, str)
                or not isinstance(links, list) or len(links) != len(set(links))
                or not isinstance(rationale, str) or not rationale.strip()):
            raise SystemExit("error: malformed scenario_coverage row")
        if applicability == "applicable":
            prefix = f"{row['dimension']}: "
            if not scenario.startswith(prefix) or not scenario[len(prefix):].strip():
                raise SystemExit(
                    "error: applicable scenario must start with '<dimension>: ' "
                    "and contain a concrete case"
                )
            normalized = " ".join(scenario.lower().split())
            if not normalized or not links or any(link not in seam_ids for link in links):
                raise SystemExit(
                    "error: applicable scenario needs one concrete scenario and approved seam_ids"
                )
            if normalized in normalized_scenarios:
                raise SystemExit("error: scenario_coverage contains duplicate scenarios")
            normalized_scenarios.add(normalized)
        elif scenario or links:
            raise SystemExit(
                "error: not-applicable scenario row must leave scenario and seam_ids empty"
            )


def validate_handoff(record):
    required = record.setdefault("handoff_required", False)
    record.setdefault("required_decisions", [])
    if (not isinstance(required, bool)
            or not isinstance(record["required_decisions"], list)
            or len(record["required_decisions"]) != len(set(record["required_decisions"]))
            or any(not safe_task_id(item) for item in record["required_decisions"])):
        raise SystemExit("error: invalid handoff_required or required_decisions")
    handoff = record.get("handoff")
    if not required:
        if handoff is not None:
            raise SystemExit("error: handoff requires handoff_required=true")
        return
    if not isinstance(handoff, dict) or set(handoff) - {
        "kind", "rationale", "target_task", "decision_id",
    }:
        raise SystemExit("error: handoff needs a typed handoff object")
    if (handoff.get("kind") not in {"successor", "user-decision", "replan", "no-followup"}
            or not isinstance(handoff.get("rationale"), str)
            or not handoff["rationale"].strip()):
        raise SystemExit("error: handoff needs kind and rationale")
    if handoff["kind"] == "successor" and not safe_task_id(handoff.get("target_task")):
        raise SystemExit("error: successor handoff needs target_task")
    if handoff["kind"] == "successor" and handoff["target_task"] == record.get("id"):
        raise SystemExit("error: successor handoff cannot target the same task")
    if handoff["kind"] == "successor" and set(handoff) != {
        "kind", "rationale", "target_task",
    }:
        raise SystemExit("error: successor handoff has incompatible target fields")
    if handoff["kind"] == "user-decision":
        decision_id = handoff.get("decision_id")
        if not safe_task_id(decision_id):
            raise SystemExit("error: user-decision handoff needs a decision_id")
        if set(handoff) != {"kind", "rationale", "decision_id"}:
            raise SystemExit("error: user-decision handoff has incompatible target fields")
    if handoff["kind"] in {"replan", "no-followup"} and set(handoff) != {
        "kind", "rationale",
    }:
        raise SystemExit("error: replan/no-followup handoff cannot name a target")


def decision_records(run_dir, state):
    records = {}
    for decision_id in state.get("decisions", {}):
        path = run_dir / "decisions" / f"{decision_id}.json"
        if not path.is_file():
            raise SystemExit(f"error: missing decision record {decision_id}")
        record = load_json(path)
        if record.get("id") != decision_id:
            raise SystemExit(f"error: decision record identity mismatch {decision_id}")
        records[decision_id] = record
    return records


def hard_to_reverse_subject(repo, run_dir, state):
    head = integration_head(repo, state)
    tasks = []
    for task_id in sorted(state.get("tasks", {})):
        task = load_json(run_dir / "tasks" / f"{task_id}.json")
        policy = task.get("policy", {})
        if policy.get("require_user_finish_decision"):
            tasks.append({
                "id": task_id,
                "status": task.get("status"),
                "policy_sha256": object_sha256(policy),
                "merge_commit": task.get("merge_commit"),
            })
    payload = {
        "run_id": state.get("run_id"), "head_sha": head, "tasks": tasks,
    }
    return {
        "kind": "hard-to-reverse-run",
        "sha256": object_sha256(payload),
    }, payload


def require_hard_to_reverse_authorization(repo, run_dir, state):
    if not state.get("hard_to_reverse"):
        return
    subject, _payload = hard_to_reverse_subject(repo, run_dir, state)
    matches = []
    for record in decision_records(run_dir, state).values():
        scope = record.get("scope", {})
        if (record.get("authority") == "user"
                and scope.get("kind") == "action"
                and scope.get("targets") == ["finish"]
                and record.get("subject") == subject
                and record.get("status") == "resolved"
                and record.get("resolution", {}).get("outcome") == "allow"):
            matches.append(record["id"])
    if not matches:
        raise SystemExit(
            "error: hard-to-reverse run requires an allowed user finish decision "
            "bound to the current reversibility-subject"
        )


def publication_decision_digest(records):
    payload = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def publication_authorization_digest(run_dir, state, records):
    reviews = {}
    for path in sorted((run_dir / "reviews").glob("wave-*.json")):
        reviews[str(path.relative_to(run_dir))] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    payload = {
        "decisions": records,
        "gates": state.get("gates", {}),
        "tasks": state.get("tasks", {}),
        "phase": state.get("phase"),
        "reviews": reviews,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def seal_ready_authorization(run_dir, state, head, purpose):
    records = decision_records(run_dir, state)
    decision_digest = publication_decision_digest(records)
    authorization_digest = publication_authorization_digest(
        run_dir, state, records
    )
    existing = state.get("publication_seal")
    if existing:
        if (existing.get("decision_sha256") != decision_digest
                or existing.get("authorization_sha256") != authorization_digest):
            raise SystemExit("error: publication authorization changed after sealing")
        if existing.get("head_sha") != head:
            raise SystemExit("error: publication seal belongs to a different reviewed head")
        if existing.get("purpose") != purpose:
            raise SystemExit("error: publication seal has a different purpose")
        return False, existing
    seal = {
        "status": "sealed", "purpose": purpose, "head_sha": head,
        "decision_sha256": decision_digest,
        "authorization_sha256": authorization_digest, "sealed_at": now(),
    }
    state["publication_seal"] = seal
    state["updated_at"] = now()
    return True, seal


def decision_covers(record, *, task=None, wave=None, action=None):
    scope = record.get("scope", {})
    kind, targets = scope.get("kind"), scope.get("targets", [])
    if kind == "global":
        return True
    if kind == "task" and task is not None:
        return task in targets
    if kind == "wave" and wave is not None:
        return wave in targets
    if kind == "action" and action is not None:
        return action in targets
    return False


def decision_blocks_source_start(record, task):
    scope = record.get("scope", {})
    kind, targets = scope.get("kind"), scope.get("targets", [])
    return (kind == "global"
            or kind == "task" and task.get("id") in targets
            or kind == "wave" and task.get("wave") in targets
            or kind == "action" and bool({"task-start", "wave-start"} & set(targets)))


def blocking_decisions(run_dir, state, *, task=None, wave=None, action=None,
                       required=None):
    records = decision_records(run_dir, state)
    blockers = []
    for decision_id, record in records.items():
        blocks = (record.get("status") == "open"
                  or record.get("status") == "resolved"
                  and record.get("resolution", {}).get("outcome") == "deny")
        if blocks and decision_covers(record, task=task, wave=wave, action=action):
            blockers.append(record)
    for decision_id in required or []:
        record = records.get(decision_id)
        if record is None:
            raise SystemExit(f"error: task references unknown decision {decision_id}")
        if (record.get("status") == "open"
                or record.get("status") == "resolved"
                and record.get("resolution", {}).get("outcome") == "deny"):
            if record not in blockers:
                blockers.append(record)
    return sorted(blockers, key=lambda item: item["id"])


def require_no_decision_blockers(run_dir, state, label, **scope):
    blockers = blocking_decisions(run_dir, state, **scope)
    if blockers:
        questions = "; ".join(
            f"{item['id']}: {item['question']} [{item['status']}]" for item in blockers
        )
        raise SystemExit(f"error: decision gates block {label}: {questions}")


def handoff_state(run_dir, state, task):
    if not task.get("handoff_required"):
        return {"status": "not-required"}
    handoff = task["handoff"]
    kind = handoff["kind"]
    if task.get("status") == "superseded":
        return {"status": "closed", "kind": kind, "reason": "source-superseded"}
    if task.get("status") not in {"completed", "merged", "artifact_complete"}:
        return {"status": "pending", "kind": kind}
    if kind == "no-followup":
        return {"status": "closed", "kind": kind, "rationale": handoff["rationale"]}
    if kind == "replan":
        return {"status": "blocking", "kind": kind, "rationale": handoff["rationale"]}
    if kind == "successor":
        target = handoff["target_task"]
        summary = state.get("tasks", {}).get(target)
        if summary is None:
            return {"status": "blocking", "kind": kind, "target_task": target,
                    "reason": "successor-not-registered"}
        terminal = summary.get("status") in {"merged", "artifact_complete"}
        return {"status": "closed" if terminal else "blocking", "kind": kind,
                "target_task": target, "target_status": summary.get("status")}
    decision_id = handoff["decision_id"]
    decision = decision_records(run_dir, state).get(decision_id)
    status = decision.get("status") if decision else "missing"
    return {"status": "closed" if status in {"resolved", "superseded"} else "blocking",
            "kind": kind, "decision_id": decision_id, "decision_status": status}


def public_boundary_findings(repo, state, head):
    base = state["base_commit"]
    names = git([
        "diff", "--diff-filter=ACMRTUXB", "--name-only", "-z", base, head, "--",
    ], repo).stdout.split("\0")
    findings = []
    for path in sorted(item for item in names if item):
        if any(glob_match(path, pattern) for pattern in PUBLIC_RUNTIME_PATTERNS):
            findings.append({"kind": "private-runtime-path", "path": path})
    for path in sorted(item for item in names if item):
        shown = subprocess.run(
            ["git", "show", f"{head}:{path}"], cwd=repo,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if shown.returncode:
            continue
        blob = shown.stdout
        if b"\0" in blob:
            findings.append({"kind": "unscannable-binary", "path": path})
            continue
        try:
            value = blob.decode("utf-8")
        except UnicodeDecodeError:
            findings.append({"kind": "unscannable-binary", "path": path})
            continue
        for kind, pattern in PUBLIC_SECRET_PATTERNS:
            if pattern.search(value):
                findings.append({"kind": kind, "path": path})
        for match in LOCAL_PATH_PATTERN.finditer(value):
            owner = next((item for item in match.groups() if item), "")
            if owner.lower() not in LOCAL_PATH_PLACEHOLDERS:
                findings.append({"kind": "machine-local-path", "path": path})
    unique = []
    seen = set()
    for finding in findings:
        key = (finding["kind"], finding["path"])
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    return unique


def summarize_wave_policies(records):
    waves = {}
    intensity_order = {"compact": 0, "full": 1, "risk": 2}
    for task in records:
        if "policy" not in task:
            raise SystemExit(
                f"error: task {task.get('id', '<unknown>')} predates policy derivation; "
                "replace every active task through task-put before continuing"
            )
        wave = str(task["wave"])
        item = waves.setdefault(wave, {
            "tasks": [], "execution_tier": "economy",
            "review_intensity": "compact", "require_risk_review": False,
            "risk_flags": [], "reversibility": "contained-reversible",
            "integration_lane": "shadow",
            "require_user_finish_decision": False,
        })
        item["tasks"].append(task["id"])
        policy = task["policy"]
        tier_order = {"economy": 0, "standard": 1, "deep": 2}
        if tier_order[policy["execution_tier"]] > tier_order[item["execution_tier"]]:
            item["execution_tier"] = policy["execution_tier"]
        if intensity_order[policy["review_intensity"]] > intensity_order[item["review_intensity"]]:
            item["review_intensity"] = policy["review_intensity"]
        item["require_risk_review"] |= policy["require_risk_review"]
        if (REVERSIBILITY_ORDER[policy["reversibility"]]
                > REVERSIBILITY_ORDER[item["reversibility"]]):
            item["reversibility"] = policy["reversibility"]
            item["integration_lane"] = policy["integration_lane"]
        item["require_user_finish_decision"] |= policy[
            "require_user_finish_decision"
        ]
        item["risk_flags"] = sorted(set(item["risk_flags"])
                                    | set(policy["effective_risk_flags"]))
    for item in waves.values():
        item["tasks"].sort()
        if len(item["tasks"]) >= 4 and item["execution_tier"] == "economy":
            item["execution_tier"] = "standard"
            item["review_intensity"] = "full"
    return waves


def rebuild_wave_policies(run_dir, state, replacement=None):
    records = []
    for task_id in state.get("tasks", {}):
        if replacement and task_id == replacement.get("id"):
            records.append(replacement)
        else:
            records.append(load_json(run_dir / "tasks" / f"{task_id}.json"))
    if replacement and replacement.get("id") not in state.get("tasks", {}):
        records.append(replacement)
    return summarize_wave_policies(records)


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


def validate_integration_provenance(repo, run_dir, state, head_sha):
    """Prove every integration commit is the exact delta of one gated task."""
    entries = state.get("integration_provenance")
    cursor = state.get("base_commit")
    if (not isinstance(entries, list)
            or state.get("integration_provenance_head") != head_sha):
        raise SystemExit("error: integration provenance does not cover frozen HEAD")
    merged = {task_id for task_id, summary in state.get("tasks", {}).items()
              if summary.get("status") == "merged"}
    owned = []
    flattened = []
    for entry in entries:
        if (not isinstance(entry, dict) or entry.get("from_sha") != cursor
                or not safe_task_id(entry.get("task"))
                or not isinstance(entry.get("commits"), list)
                or not entry.get("commits")):
            raise SystemExit("error: integration provenance chain is malformed")
        target = entry.get("to_sha")
        task = load_json(run_dir / "tasks" / f"{entry['task']}.json")
        checked = task.get("check_result", {}).get("head_sha")
        if target != task.get("merge_commit") or not checked:
            raise SystemExit("error: integration provenance differs from task merge evidence")
        expected = git(["rev-list", "--reverse", f"{cursor}..{target}"], repo).stdout.splitlines()
        task_patch = git_bytes(
            ["diff", "--binary", "--full-index", task["base_commit"], checked], repo
        )
        integration_patch = git_bytes(
            ["diff", "--binary", "--full-index", cursor, target], repo
        )
        ancestor = not git(
            ["merge-base", "--is-ancestor", checked, target], repo, check=False
        ).returncode
        owned_commits = validate_owned_commit_range(
            repo, task, cursor, target, ancestor
        )
        if (expected != entry["commits"] or owned_commits != entry["commits"]
                or task_patch != integration_patch
                or entry.get("patch_sha256")
                != hashlib.sha256(integration_patch).hexdigest()):
            raise SystemExit("error: integration provenance commit range changed")
        owned.append(entry["task"])
        flattened.extend(entry["commits"])
        cursor = target
    all_commits = git(
        ["rev-list", "--reverse", f"{state['base_commit']}..{head_sha}"], repo
    ).stdout.splitlines()
    if (cursor != head_sha or flattened != all_commits or len(owned) != len(set(owned))
            or set(owned) != merged):
        raise SystemExit(
            "error: integration contains changes not owned by exactly one gated task"
        )


def cmd_init(args, repo, run_dir):
    try:
        model_profiles = {
            "economy": execution_profile(
                args.model_economy, MODEL_PROFILES["economy"]["thinking"]
            ),
            "standard": execution_profile(
                args.model_standard, MODEL_PROFILES["standard"]["thinking"]
            ),
            "deep": execution_profile(
                args.model_deep, MODEL_PROFILES["deep"]["thinking"]
            ),
        }
        review_model_profiles = {
            "economy": execution_profile(
                args.review_model_economy,
                REVIEW_MODEL_PROFILES["economy"]["thinking"],
            ),
            "standard": execution_profile(
                args.review_model_standard,
                REVIEW_MODEL_PROFILES["standard"]["thinking"],
            ),
            "deep": execution_profile(
                args.review_model_deep,
                REVIEW_MODEL_PROFILES["deep"]["thinking"],
            ),
        }
    except ValueError as exc:
        raise SystemExit(f"error: {exc}")
    state_file = run_dir / "state.json"
    with locked(run_dir):
        if state_file.exists():
            state = load_json(state_file)
            if state.get("finished"):
                raise SystemExit("error: run already exists and is finished")
            if args.epic and state.get("epic", {}).get("id") != args.epic:
                raise SystemExit("error: existing run has a different epic binding")
            print(json.dumps(state, indent=2, sort_keys=True))
            return
        base_branch = args.base_branch or subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=repo, text=True
        ).strip()
        base_commit = args.base_commit or subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()
        base_commit = git(["rev-parse", f"{base_commit}^{{commit}}"], repo).stdout.strip()
        epic = None
        if args.epic:
            try:
                epic = epic_binding(repo, args.epic, run_dir.name, base_commit)
            except ArtifactError as exc:
                raise SystemExit(f"error: {exc}")
        state = {
            "schema_version": 6,
            "run_id": run_dir.name,
            "goal": args.goal,
            "base_branch": base_branch,
            "base_commit": base_commit,
            "integration_branch": args.integration_branch or f"agent/{run_dir.name}/integration",
            "integration_provenance_head": base_commit,
            "integration_provenance": [],
            "phase": "INIT",
            "current_wave": 0,
            "waves": {},
            "tasks": {},
            "decisions": {},
            "gates": {
                "final_verification": {"status": "pending"},
                "reviews": {"status": "pending"},
                "learning": {"status": "pending"},
                "public_boundary": {"status": "pending"},
            },
            "risk_forced": args.require_risk,
            "risk_required": args.require_risk,
            "hard_to_reverse": False,
            "model_profiles": model_profiles,
            "review_model_profiles": review_model_profiles,
            "shared_surfaces": list(dict.fromkeys([*DEFAULT_SHARED_SURFACES,
                                                     *args.shared_surface])),
            "plan_file": args.plan_file,
            "finished": False,
            "created_at": now(),
            "updated_at": now(),
        }
        if epic:
            state["epic"] = epic
        event = {"event": "run_created", "phase": "INIT"}
        if epic:
            event["epic"] = epic["id"]
        commit_transaction(run_dir, {state_file: state}, event)
    print(json.dumps(state, indent=2, sort_keys=True))


def cmd_migrate_run(_args, _repo, run_dir):
    with locked(run_dir, allow_sealed=True):
        state_path = run_dir / "state.json"
        state = load_json(state_path)
        source_schema = state.get("schema_version")
        if source_schema == 6:
            print("already-v6")
            return
        if source_schema not in {2, 3, 4, 5} or state.get("finished"):
            raise SystemExit(
                "error: only unfinished schema-version-2/3/4/5 runs can migrate"
            )
        records = []
        writes = {}
        replan = []
        reopened_decisions = []
        immutable = {"merged", "superseded", "artifact_complete"}
        for task_id in state.get("tasks", {}):
            task_path = run_dir / "tasks" / f"{task_id}.json"
            task = load_json(task_path)
            if task.get("id") != task_id:
                raise SystemExit(f"error: task record identity mismatch {task_id}")
            summary_status = state.get("tasks", {}).get(task_id, {}).get("status")
            if task.get("status") != summary_status:
                raise SystemExit(
                    f"error: task {task_id} status mismatch between task record "
                    f"({task.get('status')}) and state summary ({summary_status})"
                )
            if source_schema == 2 and not task.get("policy"):
                task["work_kind"] = "integration"
                task["risk_flags"] = []
                task["tdd_required"] = False
                task["diagnosis_required"] = False
            task["policy"] = derive_task_policy(task)
            task["reversibility"] = task["policy"]["reversibility"]
            task["tdd_required"] = task["policy"]["tdd_required"]
            task["diagnosis_required"] = task["policy"]["diagnosis_required"]
            task.setdefault("required_decisions", [])
            task.setdefault("handoff_required", False)
            task.setdefault("depends_on", [])
            needs_replan = task.get("status") not in immutable
            task["policy_migration"] = {
                "from_schema": source_schema,
                "mode": "conservative-dependency-contract-upgrade",
                "requires_replan": needs_replan,
                "migrated_at": now(),
            }
            if needs_replan:
                replan.append(task_id)
            task["updated_at"] = now()
            writes[task_path] = task
            records.append(task)
        validate_dependency_records(
            {task["id"]: task for task in records}, state
        )
        state["schema_version"] = 6
        state.setdefault("decisions", {})
        for decision_id in state["decisions"]:
            decision_path = run_dir / "decisions" / f"{decision_id}.json"
            decision = load_json(decision_path)
            resolution = decision.get("resolution")
            if decision.get("status") == "resolved" and isinstance(resolution, dict):
                if "outcome" not in resolution:
                    decision["status"] = "open"
                    decision.pop("resolution", None)
                    decision["updated_at"] = now()
                    summary = state["decisions"].get(decision_id, {})
                    summary["status"] = "open"
                    summary.pop("resolved_at", None)
                    summary.pop("outcome", None)
                    reopened_decisions.append(decision_id)
                    writes[decision_path] = decision
        state.setdefault("gates", {}).setdefault("public_boundary", {"status": "pending"})
        state.pop("publication_seal", None)
        state["risk_forced"] = (
            bool(state.get("risk_forced", False)) if source_schema >= 5
            else bool(state.get("risk_required", False))
        )

        def migrated_profiles(raw, defaults):
            source = raw if isinstance(raw, dict) else {}
            migrated = {}
            for tier in ("economy", "standard", "deep"):
                item = source.get(tier)
                default = defaults[tier]
                model = item.get("model") if isinstance(item, dict) else None
                thinking = item.get("thinking") if isinstance(item, dict) else None
                migrated[tier] = execution_profile(
                    model if isinstance(model, str) and model else default["model"],
                    thinking if thinking in {"low", "medium", "high", "xhigh"}
                    else default["thinking"],
                )
            return migrated

        state["model_profiles"] = migrated_profiles(
            state.get("model_profiles"), MODEL_PROFILES
        )
        state["review_model_profiles"] = migrated_profiles(
            state.get("review_model_profiles", state.get("model_profiles")),
            REVIEW_MODEL_PROFILES,
        )
        state["waves"] = summarize_wave_policies(records)
        if source_schema in {2, 3}:
            provenance_head, provenance = rebuild_legacy_integration_provenance(
                _repo, state, records
            )
        else:
            provenance_head = state.get("integration_provenance_head")
            provenance = state.get("integration_provenance")
            if not isinstance(provenance_head, str) or not isinstance(provenance, list):
                raise SystemExit(
                    "error: schema-version-4 run has no durable integration provenance"
                )
        state["integration_provenance_head"] = provenance_head
        state["integration_provenance"] = provenance
        state["risk_required"] = state["risk_forced"] or any(
            item["require_risk_review"] for item in state["waves"].values()
        )
        state["hard_to_reverse"] = any(
            item["require_user_finish_decision"] for item in state["waves"].values()
        )
        state["policy_migration_pending"] = sorted(replan)
        state["updated_at"] = now()
        writes[state_path] = state
        commit_transaction(
            run_dir, writes,
            {"event": "run_migrated", "from_schema": source_schema, "to_schema": 6,
             "requires_replan": sorted(replan),
             "reopened_decisions": sorted(reopened_decisions)},
        )
    print(json.dumps({
        "status": "migrated", "requires_replan": sorted(replan),
        "reopened_decisions": sorted(reopened_decisions),
    }, sort_keys=True))


def cmd_phase(args, _repo, run_dir):
    target = args.phase
    if target not in PHASES or target == "DONE":
        raise SystemExit("error: invalid phase (DONE is set only by finish)")
    with locked(run_dir):
        path = run_dir / "state.json"
        state = load_json(path)
        old = state["phase"]
        migration_pending = []
        for task_id in state.get("tasks", {}):
            task = load_json(run_dir / "tasks" / f"{task_id}.json")
            if task.get("policy_migration", {}).get("requires_replan"):
                migration_pending.append(task_id)
        if migration_pending and target != "REPLANNING":
            raise SystemExit(
                "error: migrated tasks require REPLANNING before execution: "
                + ", ".join(sorted(migration_pending))
            )
        if target == old:
            print(old)
            return
        if target not in TRANSITIONS.get(old, set()):
            raise SystemExit(f"error: illegal phase transition {old} -> {target}")
        wave = args.wave if args.wave is not None else state.get("current_wave", 0)
        if target in {"PLAN_READY", "WAVE_RUNNING"}:
            dependency_records = validate_dependency_graph(run_dir, state)
        else:
            dependency_records = None
        if target == "WAVE_RUNNING":
            if not wave:
                raise SystemExit("error: WAVE_RUNNING requires --wave")
            records = wave_tasks(run_dir, state, wave)
            if not records or any(task.get("status") not in {"pending", "blocked"}
                                  for task in records):
                raise SystemExit("error: wave must have only pending/blocked tasks before start")
            for task in records:
                require_dependencies_ready(dependency_records, task)
            require_no_decision_blockers(
                run_dir, state, f"wave {wave} start", wave=wave, action="wave-start"
            )
        elif target == "WAVE_VALIDATING":
            records = wave_tasks(run_dir, state, wave)
            if not records or any(task.get("status") != "running" for task in records):
                raise SystemExit("error: all wave tasks must be running before validation")
        elif target == "WAVE_MERGING":
            records = wave_tasks(run_dir, state, wave)
            if not records or any(task.get("status") != "completed" for task in records):
                raise SystemExit("error: all wave tasks must pass checks before merging")
            replans = [task["id"] for task in records
                       if task.get("handoff_required")
                       and task.get("handoff", {}).get("kind") == "replan"]
            if replans:
                raise SystemExit(
                    "error: replan handoffs require REPLANNING and supersede before merge: "
                    + ", ".join(replans)
                )
            require_no_decision_blockers(
                run_dir, state, f"wave {wave} merge", wave=wave, action="merge"
            )
        elif target in {"INTEGRATION_TESTING", "REVIEWING"}:
            records = wave_tasks(run_dir, state, wave)
            if records and any(task.get("status") != "merged" for task in records):
                raise SystemExit("error: all wave tasks must be merged before integration/review")
        elif target == "RE_REVIEWING":
            fix_tasks = [task for task in wave_tasks(run_dir, state, wave)
                         if task.get("review_fix")]
            if not fix_tasks or any(task.get("status") not in {"merged", "superseded"}
                                    for task in fix_tasks):
                raise SystemExit(
                    "error: RE_REVIEWING requires every finding-owned fix task merged"
                )
        if target == "LEARNING_EXPORT":
            require_no_decision_blockers(
                run_dir, state, "learning export", action="learning-export"
            )
        if target == "READY_TO_FINISH":
            gates = state.get("gates", {})
            required = {
                "final_verification": {"passed"},
                "reviews": {"passed"},
                "learning": {"passed", "skipped"},
                "public_boundary": {"passed"},
            }
            missing = [name for name, statuses in required.items()
                       if gates.get(name, {}).get("status") not in statuses]
            if missing:
                raise SystemExit(f"error: finish gates not satisfied: {', '.join(missing)}")
            unfinished = [task for task, value in state.get("tasks", {}).items()
                          if value.get("status") not in {"merged", "superseded", "artifact_complete"}]
            if unfinished:
                raise SystemExit("error: unfinished tasks block finish: " + ", ".join(unfinished))
            require_no_decision_blockers(run_dir, state, "finish", action="finish")
            require_hard_to_reverse_authorization(_repo, run_dir, state)
            handoff_blockers = []
            for task_id in state.get("tasks", {}):
                task = load_json(run_dir / "tasks" / f"{task_id}.json")
                handoff = handoff_state(run_dir, state, task)
                if handoff["status"] == "blocking":
                    handoff_blockers.append(f"{task_id}:{handoff['kind']}")
            if handoff_blockers:
                raise SystemExit(
                    "error: unresolved typed handoffs block finish: "
                    + ", ".join(handoff_blockers)
                )
            head = integration_head(_repo, state)
            if gates["final_verification"].get("head_sha") != head:
                raise SystemExit("error: final verification does not cover integration HEAD")
            if gates["public_boundary"].get("head_sha") != head:
                raise SystemExit("error: public-boundary check does not cover integration HEAD")
            validate_merged_tasks(_repo, run_dir, state, head)
            validate_integration_provenance(_repo, run_dir, state, head)
            validate_reviews(_repo, run_dir, state, head)
        if target in {"WAVE_RUNNING", "FIXING", "REPLANNING"}:
            gates = state.setdefault("gates", {})
            gates["reviews"] = {"status": "pending", "updated_at": now()}
            gates["final_verification"] = {"status": "pending", "updated_at": now()}
            gates["public_boundary"] = {"status": "pending", "updated_at": now()}
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
                "base_commit", "parallel_group", "verification", "work_kind",
                "risk_flags", "depends_on"]
    missing = [key for key in required if key not in record]
    if missing:
        raise SystemExit(f"error: task record missing required fields: {', '.join(missing)}")
    nonempty = [key for key in ("write_set", "branch", "worktree", "base_commit",
                                "parallel_group", "verification") if not record.get(key)]
    if nonempty:
        raise SystemExit(f"error: task record has empty required fields: {', '.join(nonempty)}")
    if (not isinstance(record["write_set"], list)
            or any(not isinstance(pattern, str) or not pattern
                   for pattern in record["write_set"])):
        raise SystemExit("error: write_set must be a non-empty array of path globs")
    if (not isinstance(record["forbidden_paths"], list)
            or any(not isinstance(pattern, str) or not pattern
                   for pattern in record["forbidden_paths"])):
        raise SystemExit("error: forbidden_paths must be an array of path globs")
    if (not isinstance(record["wave"], int) or isinstance(record["wave"], bool)
            or record["wave"] < 1):
        raise SystemExit("error: task wave must be a positive integer")
    if not valid_dependency_ids(record["depends_on"]):
        raise SystemExit("error: depends_on must be an array of unique safe task ids")
    record["base_commit"] = git(
        ["rev-parse", f"{record['base_commit']}^{{commit}}"], _repo
    ).stdout.strip()
    try:
        record["policy"] = derive_task_policy(record)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}")
    record["tdd_required"] = record["policy"]["tdd_required"]
    record["diagnosis_required"] = record["policy"]["diagnosis_required"]
    record["reversibility"] = record["policy"]["reversibility"]
    validate_experiment_contract(record)
    validate_handoff(record)
    if record["tdd_required"]:
        seams = record.get("test_seams")
        if not isinstance(seams, list) or not seams:
            raise SystemExit("error: TDD task requires at least one approved test_seam")
        seam_fields = {"id", "behavior", "test_paths", "command", "red_pattern"}
        if any(not isinstance(seam, dict) or not seam_fields.issubset(seam)
               or any(not isinstance(seam.get(field), str) or not seam[field]
                      for field in seam_fields - {"test_paths"})
               or not isinstance(seam["test_paths"], list)
               or not seam["test_paths"]
               or any(not isinstance(path, str) or not path
                      or any(char in path for char in "*?[")
                      or Path(path).is_absolute() or ".." in Path(path).parts
                      for path in seam["test_paths"])
               for seam in seams):
            raise SystemExit(
                "error: each test_seam needs id, behavior, exact test_paths, "
                "command, red_pattern"
            )
        seam_ids = [seam["id"] for seam in seams]
        if any(not safe_task_id(seam_id) for seam_id in seam_ids):
            raise SystemExit("error: test_seam ids must be safe non-empty identifiers")
        if len(seam_ids) != len(set(seam_ids)):
            raise SystemExit("error: test_seam ids must be unique")
        test_files = [path for seam in seams for path in seam["test_paths"]]
        if len(test_files) != len(set(test_files)):
            raise SystemExit("error: each approved test file may belong to only one seam")
        for seam in seams:
            if any(not any(glob_match(path, write)
                           for write in record["write_set"])
                   for path in seam["test_paths"]):
                raise SystemExit(
                    f"error: test_seam {seam['id']} test_paths must be inside write_set"
                )
    validate_scenario_coverage(record)
    if record["diagnosis_required"] and any(
            not isinstance(record.get(field), str) or not record[field]
            for field in ("diagnosis_command", "failure_pattern")):
        raise SystemExit(
            "error: diagnosis task requires diagnosis_command and failure_pattern"
        )
    with locked(run_dir):
        state_path = run_dir / "state.json"
        state = load_json(state_path)
        if state.get("finished") or state.get("phase") == "DONE":
            raise SystemExit("error: finished run is immutable")
        for decision_id in record["required_decisions"]:
            if decision_id not in state.get("decisions", {}):
                raise SystemExit(f"error: task references unknown decision {decision_id}")
        handoff = record.get("handoff", {})
        if record.get("handoff_required") and handoff.get("kind") == "user-decision":
            decision_id = handoff["decision_id"]
            if decision_id not in state.get("decisions", {}):
                raise SystemExit("error: user-decision handoff gate must exist before task-put")
            decision = decision_records(run_dir, state)[decision_id]
            if decision.get("status") != "open":
                raise SystemExit("error: post-task handoff decision must start open")
            if decision_blocks_source_start(decision, record):
                raise SystemExit(
                    "error: post-task handoff decision gate blocks its source task start"
                )
            if decision_id in record.get("required_decisions", []):
                raise SystemExit(
                    "error: post-task handoff decision cannot also be a required_decision"
                )
        if record.get("handoff_required") and handoff.get("kind") == "successor":
            target_id = handoff["target_task"]
            if target_id in state.get("tasks", {}):
                target = load_json(run_dir / "tasks" / f"{target_id}.json")
                if target.get("wave", 0) <= record["wave"]:
                    raise SystemExit(
                        "error: successor handoff target must be in a later wave"
                    )
        phase = state.get("phase")
        planning = phase in {"INIT", "SPEC_READY", "PLAN_READY", "REPLANNING"}
        finding_fix = phase == "FIXING"
        if not planning and not finding_fix:
            raise SystemExit(
                "error: tasks may be created during planning or as finding-owned FIXING tasks"
            )
        task_path = run_dir / "tasks" / f"{task_id}.json"
        if finding_fix:
            finding_ids = record.get("finding_ids")
            if (args.replace or task_path.exists()
                    or not isinstance(finding_ids, list) or not finding_ids
                    or len(finding_ids) != len(set(finding_ids))
                    or any(not safe_task_id(item) for item in finding_ids)):
                raise SystemExit(
                    "error: FIXING permits only a fresh task with unique finding_ids"
                )
            if record.get("parallel_group") != "serial":
                raise SystemExit("error: finding-owned fix tasks must be serial")
            if record["base_commit"] != integration_head(_repo, state):
                raise SystemExit(
                    "error: finding-owned fix task must start at current integration HEAD"
                )
            open_findings = {}
            for ledger_path in (run_dir / "reviews").glob("wave-*.json"):
                review = load_json(ledger_path)
                if review.get("packet", {}).get("wave") != record["wave"]:
                    continue
                for item in review.get("findings", []):
                    if item.get("status") == "open":
                        open_findings.setdefault(item.get("id"), []).append(
                            str(ledger_path.relative_to(run_dir))
                        )
            missing_findings = [item for item in finding_ids
                                if len(open_findings.get(item, [])) != 1]
            if missing_findings:
                raise SystemExit(
                    "error: finding_ids must each name one open finding in this wave: "
                    + ", ".join(missing_findings)
                )
            current_policy = state.get("waves", {}).get(str(record["wave"]), {})
            tier_order = {"economy": 0, "standard": 1, "deep": 2}
            intensity_order = {"compact": 0, "full": 1, "risk": 2}
            if (not current_policy
                    or tier_order[record["policy"]["execution_tier"]]
                    > tier_order[current_policy["execution_tier"]]
                    or intensity_order[record["policy"]["review_intensity"]]
                    > intensity_order[current_policy["review_intensity"]]
                    or record["policy"]["require_risk_review"]
                    and not current_policy["require_risk_review"]
                    or REVERSIBILITY_ORDER[record["policy"]["reversibility"]]
                    > REVERSIBILITY_ORDER[current_policy["reversibility"]]
                    or not set(record["policy"]["effective_risk_flags"]).issubset(
                        current_policy.get("risk_flags", []))):
                raise SystemExit(
                    "error: fix task raises frozen wave risk/review policy; enter REPLANNING"
                )
            record["review_fix"] = True
            record["finding_ledgers"] = {
                item: open_findings[item][0] for item in finding_ids
            }
        if task_path.exists() and not args.replace:
            raise SystemExit(f"error: task {task_id} exists; use --replace")
        if task_path.exists():
            existing = load_json(task_path)
            if state.get("phase") != "REPLANNING":
                if existing.get("depends_on") != record["depends_on"]:
                    raise SystemExit(
                        "error: task dependencies are immutable outside REPLANNING"
                    )
                if existing.get("wave") != record["wave"]:
                    raise SystemExit(
                        "error: task wave is immutable outside REPLANNING"
                    )
            migrated_completed = (
                existing.get("status") == "completed"
                and existing.get("policy_migration", {}).get("requires_replan")
                and state.get("phase") == "REPLANNING"
            )
            if (existing.get("status") not in {"pending", "blocked", "failed"}
                    and not migrated_completed):
                raise SystemExit(f"error: cannot replace task {task_id} in status "
                                 f"{existing.get('status')}")
            if (existing.get("policy_migration", {}).get("requires_replan")
                    and state.get("phase") != "REPLANNING"):
                raise SystemExit(
                    "error: migrated task replacement requires REPLANNING phase"
                )
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
        if (not isinstance(declared_shared, list)
                or any(not isinstance(pattern, str) or not pattern
                       for pattern in declared_shared)):
            raise SystemExit("error: allow_shared_surfaces must be an array of path globs")
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
        validate_dependency_graph(run_dir, state, record)
        record["updated_at"] = now()
        old = state.setdefault("tasks", {}).get(task_id)
        state["tasks"][task_id] = {"status": status, "attempt": record.get("attempt", 1)}
        state["waves"] = rebuild_wave_policies(run_dir, state, record)
        state["risk_required"] = bool(state.get("risk_forced")) or any(
            item["require_risk_review"] for item in state["waves"].values()
        )
        state["hard_to_reverse"] = any(
            item["require_user_finish_decision"]
            for item in state["waves"].values()
        )
        state["policy_migration_pending"] = sorted(
            item for item in state.get("tasks", {})
            if item != task_id
            and load_json(run_dir / "tasks" / f"{item}.json").get(
                "policy_migration", {}).get("requires_replan")
        )
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
    if args.status == "artifact_complete":
        raise SystemExit(
            "error: artifact completion requires the dedicated artifact-complete command"
        )
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
        if args.status == "running":
            if task.get("policy_migration", {}).get("requires_replan"):
                raise SystemExit(
                    f"error: migrated task {args.task} requires REPLANNING and "
                    "replacement before execution"
                )
            if (phase == "WAVE_RUNNING"
                    and task.get("wave") != state.get("current_wave")):
                raise SystemExit(
                    f"error: task {args.task} belongs to wave {task.get('wave')}, "
                    f"not current wave {state.get('current_wave')}"
                )
            dependency_records = validate_dependency_graph(run_dir, state)
            require_dependencies_ready(dependency_records, task)
            handoff = task.get("handoff", {})
            if (task.get("handoff_required") and handoff.get("kind") == "successor"
                    and handoff.get("target_task") not in state.get("tasks", {})):
                raise SystemExit("error: successor handoff target is not registered")
            if task.get("handoff_required") and handoff.get("kind") == "successor":
                target = load_json(
                    run_dir / "tasks" / f"{handoff['target_task']}.json"
                )
                if (target.get("wave", 0) <= task.get("wave", 0)
                        or target.get("status") not in {"pending", "blocked"}):
                    raise SystemExit(
                        "error: successor must be undelivered and in a later wave at source start"
                    )
            if (task.get("handoff_required") and handoff.get("kind") == "user-decision"
                    and handoff.get("decision_id") not in state.get("decisions", {})):
                raise SystemExit("error: user-decision handoff gate is not registered")
            if task.get("handoff_required") and handoff.get("kind") == "user-decision":
                decision = decision_records(run_dir, state)[handoff["decision_id"]]
                if (decision.get("status") != "open"
                        or decision_blocks_source_start(decision, task)):
                    raise SystemExit(
                        "error: post-task handoff decision is closed or blocks source start"
                    )
            require_no_decision_blockers(
                run_dir, state, f"task {args.task} start", task=args.task,
                wave=task.get("wave"), action="task-start",
                required=task.get("required_decisions", []),
            )
        if args.status == "merged" and phase not in {
            "WAVE_MERGING", "FIXING", "INTEGRATION_TESTING"
        }:
            raise SystemExit(f"error: task cannot merge during phase {phase}")
        if args.status != old and args.status not in TASK_TRANSITIONS.get(old, set()):
            raise SystemExit(f"error: illegal task transition {old} -> {args.status}")
        if args.status == "completed" and task.get("check_result", {}).get("status") != "passed":
            raise SystemExit("error: task cannot complete before mechanical check passes")
        if args.status == "completed" and task.get("handoff_required"):
            handoff = task["handoff"]
            if (handoff["kind"] == "successor"
                    and handoff["target_task"] not in state.get("tasks", {})):
                raise SystemExit("error: successor handoff target is not registered")
            if (handoff["kind"] == "user-decision"
                    and handoff["decision_id"] not in state.get("decisions", {})):
                raise SystemExit("error: user-decision handoff gate is not registered")
        if args.status == "merged" and not (args.commit or task.get("merge_commit")):
            raise SystemExit("error: merged status requires --commit <integration-commit>")
        if args.status == "merged":
            if (task.get("handoff_required")
                    and task.get("handoff", {}).get("kind") == "replan"):
                raise SystemExit(
                    "error: replan handoff task must be superseded during REPLANNING, not merged"
                )
            require_no_decision_blockers(
                run_dir, state, f"task {args.task} merge", task=args.task,
                wave=task.get("wave"), action="merge",
                required=task.get("required_decisions", []),
            )
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
            previous = state.get("integration_provenance_head")
            if (not previous or git(["merge-base", "--is-ancestor", previous,
                                     integration_head], _repo,
                                    check=False).returncode):
                raise SystemExit("error: integration provenance is stale or non-linear")
            task_patch = git_bytes(
                ["diff", "--binary", "--full-index", task["base_commit"], checked_head],
                _repo,
            )
            integration_patch = git_bytes(
                ["diff", "--binary", "--full-index", previous, integration_head],
                _repo,
            )
            if task_patch != integration_patch:
                raise SystemExit(
                    "error: integration delta contains changes not owned by checked task"
                )
            owned_commits = validate_owned_commit_range(
                _repo, task, previous, integration_head, ancestor
            )
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
                state.setdefault("integration_provenance", []).append({
                    "task": args.task,
                    "from_sha": previous,
                    "to_sha": integration_head,
                    "commits": owned_commits,
                    "patch_sha256": hashlib.sha256(integration_patch).hexdigest(),
                    "recorded_at": now(),
                })
                state["integration_provenance_head"] = integration_head
            else:
                task.setdefault("commits", []).append(args.commit)
        task["updated_at"] = now()
        state.setdefault("tasks", {})[args.task] = {
            "status": args.status, "attempt": task.get("attempt", 1)
        }
        state["updated_at"] = now()
        writes = {task_path: task, state_path: state}
        if args.status == "superseded" and task.get("handoff_required"):
            handoff = task.get("handoff", {})
            if handoff.get("kind") == "user-decision":
                decision_id = handoff["decision_id"]
                decision_path = run_dir / "decisions" / f"{decision_id}.json"
                decision = load_json(decision_path)
                if decision.get("status") == "open":
                    stamp = now()
                    decision["status"] = "superseded"
                    decision["superseded_reason"] = (
                        f"source task {args.task} was superseded before delivery"
                    )
                    decision["updated_at"] = stamp
                    summary = state.setdefault("decisions", {}).get(decision_id)
                    if not isinstance(summary, dict):
                        raise SystemExit("error: decision summary is missing")
                    summary["status"] = "superseded"
                    summary["superseded_at"] = stamp
                    writes[decision_path] = decision
        commit_transaction(run_dir, writes,
                           {"event": "task_status", "task": args.task,
                            "from": old, "to": args.status,
                            "failure": args.failure})
    print(args.status)


def cmd_handoff_close(args, _repo, run_dir):
    if (not safe_task_id(args.task) or not args.reason.strip()
            or args.replacement and not safe_task_id(args.replacement)):
        raise SystemExit("error: handoff-close needs a task, --reason, and safe replacement")
    with locked(run_dir):
        state_path = run_dir / "state.json"
        task_path = run_dir / "tasks" / f"{args.task}.json"
        state, task = load_json(state_path), load_json(task_path)
        if state.get("phase") != "REPLANNING":
            raise SystemExit("error: replan handoff closes only during REPLANNING")
        if (task.get("status") != "completed" or not task.get("handoff_required")
                or task.get("handoff", {}).get("kind") != "replan"):
            raise SystemExit("error: handoff-close requires a completed replan handoff")
        if args.replacement:
            target = state.get("tasks", {}).get(args.replacement)
            if args.replacement == args.task or target is None:
                raise SystemExit("error: replacement task must be a different registered task")
        stamp = now()
        task["status"] = "superseded"
        task["handoff_resolution"] = {
            "kind": "replan", "reason": args.reason,
            "replacement_task": args.replacement, "closed_at": stamp,
        }
        task["updated_at"] = stamp
        state["tasks"][args.task]["status"] = "superseded"
        state["updated_at"] = stamp
        commit_transaction(
            run_dir, {task_path: task, state_path: state},
            {"event": "handoff_closed", "task": args.task, "kind": "replan",
             "reason": args.reason, "replacement_task": args.replacement},
        )
    print("closed")


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
            validate_method_evidence(_repo, task, head_sha)
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


def cmd_learning_item_decision(args, _repo, run_dir):
    if not safe_task_id(args.item) or not args.evidence.strip():
        raise SystemExit("error: learning decision needs a safe item and evidence")
    with locked(run_dir):
        state = load_json(run_dir / "state.json")
        if state.get("finished") or state.get("phase") != "LEARNING_EXPORT":
            raise SystemExit(
                "error: learning decisions are allowed only during LEARNING_EXPORT"
            )
        manifest_path = run_regular_file(
            run_dir, "learning-outbox/manifest.json"
        )
        manifest = load_json(manifest_path)
        items = manifest.get("items") if isinstance(manifest, dict) else None
        if not isinstance(items, list):
            raise SystemExit("error: learning manifest items must be a list")
        matches = [item for item in items
                   if isinstance(item, dict) and item.get("id") == args.item]
        if len(matches) != 1:
            raise SystemExit("error: learning item must identify exactly one proposal")
        item = matches[0]
        category = item.get("category")
        if category not in {"knowledge", "lesson", "skill", "eval"}:
            raise SystemExit("error: learning item has an unsupported category")
        if item.get("status") != "proposed":
            raise SystemExit("error: learning item is not an undecided proposal")
        stamp = now()
        decision = {
            "authority": "coordinator", "outcome": args.outcome,
            "evidence": args.evidence.strip(), "decided_at": stamp,
        }
        item["status"] = args.outcome
        item["decision"] = decision
        if category != "eval":
            commit_transaction(
                run_dir, {manifest_path: manifest},
                {
                    "event": "learning_item_decided", "item": args.item,
                    "category": category, "outcome": args.outcome,
                    "evidence": decision["evidence"],
                    "item_sha256": object_sha256(item),
                },
            )
            print(args.outcome)
            return
        case_relative = item.get("file")
        if (not isinstance(case_relative, str)
                or not re.fullmatch(
                    r"eval-cases/[A-Za-z0-9][A-Za-z0-9._-]*\.json",
                    case_relative,
                )):
            raise SystemExit("error: eval learning item has an unsafe case path")
        case_path = run_regular_file(
            run_dir, f"learning-outbox/{case_relative}"
        )
        case = validate_eval_case(load_json(case_path), run_dir)
        if case_path.stem != case.get("id"):
            raise SystemExit("error: eval case filename differs from its identity")
        if case.get("status") != "candidate":
            raise SystemExit("error: learning item is not an undecided eval candidate")
        case["status"] = args.outcome
        case["decision"] = decision
        case_sha = object_sha256(case)
        commit_transaction(
            run_dir, {manifest_path: manifest, case_path: case},
            {
                "event": "learning_item_decided", "item": args.item,
                "case_id": case["id"], "outcome": args.outcome,
                "evidence": decision["evidence"], "case_sha256": case_sha,
                "item_sha256": object_sha256(item),
            },
        )
    print(args.outcome)


def run_verification(command, cwd, env, log_path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(["bash", "-lc", command], cwd=cwd, env=env,
                                stdout=log, stderr=subprocess.STDOUT)
    return result.returncode


def run_at_commit(repo, run_dir, task, commit, command, label):
    verify_root = run_dir / "verifications"
    verify_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{task['id']}-{label}.", dir=verify_root))
    checkout = staging / "checkout"
    log_path = verify_root / f"{task['id']}-{label}.log"
    added = False
    try:
        git(["worktree", "add", "--detach", str(checkout), commit], repo)
        added = True
        env = os.environ.copy()
        env.update({key: str(value) for key, value in task.get("env", {}).items()
                    if key in TASK_ENV_KEYS})
        code = run_verification(command, checkout, env, log_path)
    finally:
        try:
            if added:
                git(["worktree", "remove", "--force", str(checkout)], repo)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return code, log_path


def validate_method_evidence(repo, task, head_sha, require_diagnosis_green=True):
    if task.get("work_kind") == "experiment":
        evidence = task.get("experiment_evidence")
        replay = task.get("experiment_verification")
        if not evidence or not replay:
            raise SystemExit("error: experiment task has no independently replayed evidence")
        if evidence.get("final_head") != head_sha or replay.get("head_sha") != head_sha:
            raise SystemExit("error: experiment evidence does not cover task HEAD")
        if any(replay.get(field) != 0 for field in (
                "baseline_exit_code", "metric_exit_code", "guard_exit_code",
                "holdout_exit_code")):
            raise SystemExit("error: experiment replay did not pass every frozen command")
    if task.get("tdd_required"):
        cycles = task.get("tdd_evidence", [])
        expected = {seam["id"] for seam in task["test_seams"]}
        completed = {cycle.get("seam_id") for cycle in cycles}
        missing = sorted(expected - completed)
        if missing:
            raise SystemExit(
                "error: TDD task has no verified RED/GREEN cycle for seams: "
                + ", ".join(missing)
            )
        for cycle in cycles:
            if git(["merge-base", "--is-ancestor", cycle["green_commit"], head_sha],
                   repo, check=False).returncode:
                raise SystemExit(
                    f"error: GREEN commit for seam {cycle.get('seam_id')} "
                    "is absent from task HEAD"
                )
            changed_after_green = git(
                ["diff", "--name-only", cycle["green_commit"], head_sha,
                 "--", *cycle["test_files"]], repo
            ).stdout.splitlines()
            if changed_after_green:
                raise SystemExit(
                    f"error: tests proven by seam {cycle.get('seam_id')} changed "
                    "after GREEN; declare a new seam cycle"
                )
    if task.get("diagnosis_required"):
        diagnosis = task.get("diagnosis_evidence")
        if not diagnosis:
            raise SystemExit("error: diagnosis task has no structured root-cause evidence")
        if require_diagnosis_green and (
                diagnosis.get("green_exit_code") != 0
                or diagnosis.get("green_head") != head_sha):
            raise SystemExit(
                "error: diagnosis task has no GREEN original-loop evidence at task HEAD"
            )


def cmd_verify_tdd_cycle(args, repo, run_dir):
    if not safe_task_id(args.task):
        raise SystemExit("error: unsafe task id")
    with locked(run_dir):
        state_path = run_dir / "state.json"
        task_path = run_dir / "tasks" / f"{args.task}.json"
        state, task = load_json(state_path), load_json(task_path)
        if state.get("phase") not in {"WAVE_RUNNING", "FIXING", "INTEGRATION_TESTING"}:
            raise SystemExit("error: TDD verification is invalid in this phase")
        if task.get("status") != "running" or not task.get("tdd_required"):
            raise SystemExit("error: TDD verification requires a running TDD task")
        seams = {seam["id"]: seam for seam in task["test_seams"]}
        if args.seam not in seams:
            raise SystemExit(f"error: unknown approved test seam {args.seam}")
        if any(item.get("seam_id") == args.seam
               for item in task.get("tdd_evidence", [])):
            raise SystemExit(f"error: test seam {args.seam} already has verified evidence")
        used_commits = {item[key] for item in task.get("tdd_evidence", [])
                        for key in ("red_commit", "green_commit")}
        seam = seams[args.seam]
        red = git(["rev-parse", f"{args.red_commit}^{{commit}}"], repo).stdout.strip()
        green = git(["rev-parse", f"{args.green_commit}^{{commit}}"], repo).stdout.strip()
        branch_head = git(["rev-parse", task["branch"]], repo).stdout.strip()
        if red == green or red == task["base_commit"]:
            raise SystemExit("error: RED and GREEN must be distinct task commits")
        if red in used_commits or green in used_commits:
            raise SystemExit("error: each approved seam requires its own RED/GREEN commits")
        for older, newer, label in ((task["base_commit"], red, "base -> RED"),
                                    (red, green, "RED -> GREEN"),
                                    (green, branch_head, "GREEN -> task HEAD")):
            if git(["merge-base", "--is-ancestor", older, newer], repo,
                   check=False).returncode:
                raise SystemExit(f"error: invalid TDD commit order: {label}")
        parents = git(["rev-list", "--parents", "-n", "1", red], repo).stdout.split()
        if len(parents) != 2:
            raise SystemExit("error: RED must be one atomic non-merge commit")
        expected_parent = (task.get("tdd_evidence", [{}])[-1].get("green_commit")
                           if task.get("tdd_evidence") else task["base_commit"])
        if parents[1] != expected_parent:
            raise SystemExit(
                "error: RED must immediately follow task base or the previous GREEN"
            )
        green_parents = git(["rev-list", "--parents", "-n", "1", green],
                            repo).stdout.split()
        if len(green_parents) != 2 or green_parents[1] != red:
            raise SystemExit("error: GREEN must be one atomic commit immediately after RED")
        red_paths = git(["diff-tree", "--no-commit-id", "--name-only", "-r",
                         parents[1], red], repo).stdout.splitlines()
        if not red_paths or any(not any(glob_match(path, pattern)
                                        for pattern in seam["test_paths"])
                                for path in red_paths):
            raise SystemExit(
                f"error: RED commit for seam {args.seam} must change only its test_paths"
            )
        green_paths = git(["diff-tree", "--no-commit-id", "--name-only", "-r",
                           red, green], repo).stdout.splitlines()
        if not green_paths:
            raise SystemExit("error: GREEN commit must contain a production change")
        all_test_patterns = [pattern for item in task["test_seams"]
                             for pattern in item["test_paths"]]
        if any(any(glob_match(path, pattern) for pattern in all_test_patterns)
               for path in green_paths):
            raise SystemExit("error: GREEN commit must not change declared test paths")
        red_code, red_log = run_at_commit(repo, run_dir, task, red,
                                          seam["command"],
                                          f"tdd-red-{len(task.get('tdd_evidence', [])) + 1}")
        red_output = red_log.read_text(encoding="utf-8", errors="replace")
        if red_code == 0:
            raise SystemExit("error: RED command passed; it did not prove missing behavior")
        if seam["red_pattern"] not in red_output:
            raise SystemExit("error: RED output does not contain the approved red_pattern")
        green_code, green_log = run_at_commit(
            repo, run_dir, task, green, seam["command"],
            f"tdd-green-{len(task.get('tdd_evidence', [])) + 1}",
        )
        if green_code != 0:
            raise SystemExit("error: GREEN command failed")
        evidence = {
            "seam_id": args.seam, "behavior": seam["behavior"],
            "test_files": sorted(seam["test_paths"]),
            "command": seam["command"], "red_pattern": seam["red_pattern"],
            "red_commit": red, "red_exit_code": red_code,
            "red_log": str(red_log.relative_to(run_dir)),
            "green_commit": green, "green_exit_code": green_code,
            "green_log": str(green_log.relative_to(run_dir)), "verified_at": now(),
        }
        task.setdefault("tdd_evidence", []).append(evidence)
        task["updated_at"] = now()
        commit_transaction(run_dir, {task_path: task},
                           {"event": "tdd_cycle_verified", "task": args.task,
                            "seam_id": args.seam,
                            "red_commit": red, "green_commit": green,
                            "command": seam["command"]})
    print(json.dumps(evidence, sort_keys=True))


def cmd_experiment_put(args, repo, run_dir):
    if not safe_task_id(args.task):
        raise SystemExit("error: unsafe task id")
    report_path = Path(args.file).resolve()
    task_path = run_dir / "tasks" / f"{args.task}.json"
    with locked(run_dir):
        state, task = load_json(run_dir / "state.json"), load_json(task_path)
        expected_report = Path(task["worktree"]).resolve() / ".qteam-experiment.json"
        if (report_path != expected_report or report_path.is_symlink()
                or not report_path.is_file()):
            raise SystemExit(
                f"error: experiment report must be the regular file {expected_report}"
            )
        if state.get("phase") not in {"WAVE_RUNNING", "FIXING", "INTEGRATION_TESTING"}:
            raise SystemExit("error: experiment evidence is invalid in this phase")
        if task.get("status") != "running" or task.get("work_kind") != "experiment":
            raise SystemExit("error: experiment evidence requires a running experiment task")
        validate_experiment_contract(task)

        dirty = git(
            ["status", "--porcelain=v1", "--untracked-files=all"], expected_report.parent
        ).stdout.splitlines()
        if dirty != ["?? .qteam-experiment.json"]:
            raise SystemExit(
                "error: experiment worktree must be clean except for .qteam-experiment.json"
            )
        report = load_json(report_path)
        report_fields = {
            "schema_version", "task", "goal", "metric", "guard_command",
            "holdout_command", "max_attempts", "plateau_window", "attempts",
            "stop_reason", "final_head",
        }
        if not isinstance(report, dict) or set(report) != report_fields:
            raise SystemExit("error: experiment report has missing or unknown fields")
        if report.get("schema_version") != 1 or isinstance(report.get("schema_version"), bool):
            raise SystemExit("error: unsupported experiment schema_version")
        contract = task["experiment"]
        for field in ("goal", "guard_command", "holdout_command",
                      "max_attempts", "plateau_window"):
            if report.get(field) != contract[field]:
                raise SystemExit(f"error: experiment report changed frozen {field}")
        if report.get("task") != args.task:
            raise SystemExit("error: experiment report task identity mismatch")
        report_metric = report.get("metric")
        metric_fields = {
            "name", "direction", "command", "baseline", "final", "minimum_delta"
        }
        if not isinstance(report_metric, dict) or set(report_metric) != metric_fields:
            raise SystemExit("error: experiment report metric has invalid fields")
        frozen_metric = contract["metric"]
        for field in ("name", "direction", "command", "minimum_delta"):
            if report_metric.get(field) != frozen_metric[field]:
                raise SystemExit(f"error: experiment report changed frozen metric {field}")
        if not finite_number(report_metric.get("baseline")) or not finite_number(
                report_metric.get("final")):
            raise SystemExit("error: experiment baseline/final metrics must be finite numbers")

        head = git(["rev-parse", task["branch"]], repo).stdout.strip()
        worktree_head = git(["rev-parse", "HEAD"], expected_report.parent).stdout.strip()
        final_head = git(["rev-parse", f"{report['final_head']}^{{commit}}"], repo).stdout.strip()
        if head != worktree_head or final_head != head:
            raise SystemExit("error: experiment final_head is not current task branch HEAD")
        if git(["merge-base", "--is-ancestor", task["base_commit"], head], repo,
               check=False).returncode:
            raise SystemExit("error: experiment task HEAD is outside its frozen base")

        attempts = report.get("attempts")
        maximum, plateau = contract["max_attempts"], contract["plateau_window"]
        if not isinstance(attempts, list) or not 1 <= len(attempts) <= maximum:
            raise SystemExit("error: experiment attempts exceed or omit the frozen budget")
        attempt_fields = {
            "number", "hypothesis", "status", "commit", "metric", "delta",
            "guard_exit_code", "evidence",
        }
        incumbent = report_metric["baseline"]
        used_commits = set()
        kept = 0
        no_improvement_streak = 0
        plateau_reached_at = None
        for index, attempt in enumerate(attempts, 1):
            if plateau_reached_at is not None:
                raise SystemExit(
                    "error: experiment continued after frozen plateau window"
                )
            if not isinstance(attempt, dict) or set(attempt) != attempt_fields:
                raise SystemExit("error: experiment attempt has missing or unknown fields")
            if (attempt.get("number") != index
                    or attempt.get("status") not in {"kept", "discarded", "crashed", "no-op"}
                    or not isinstance(attempt.get("hypothesis"), str)
                    or not attempt["hypothesis"]
                    or not isinstance(attempt.get("evidence"), str)
                    or not attempt["evidence"]):
                raise SystemExit("error: invalid experiment attempt identity or narrative")
            commit = attempt.get("commit")
            if commit is not None:
                if not isinstance(commit, str) or not commit:
                    raise SystemExit("error: experiment commit must be a SHA or null")
                commit = git(["rev-parse", f"{commit}^{{commit}}"], repo).stdout.strip()
                if commit in used_commits:
                    raise SystemExit("error: experiment attempt commits must be unique")
                used_commits.add(commit)
                if (git(["merge-base", "--is-ancestor", task["base_commit"], commit],
                        repo, check=False).returncode
                        or git(["merge-base", "--is-ancestor", commit, head], repo,
                               check=False).returncode):
                    raise SystemExit("error: experiment attempt commit is outside task history")
            if attempt["status"] == "kept" and commit is None:
                raise SystemExit("error: kept experiment attempt requires a commit")
            if attempt["status"] == "no-op" and commit is not None:
                raise SystemExit("error: no-op experiment attempt cannot name a commit")
            value, delta = attempt.get("metric"), attempt.get("delta")
            if (value is None) != (delta is None):
                raise SystemExit("error: experiment metric and delta must both be numbers or null")
            if value is not None:
                if not finite_number(value) or not finite_number(delta):
                    raise SystemExit("error: experiment attempt metric/delta must be finite")
                expected_delta = metric_delta(frozen_metric["direction"], value, incumbent)
                if not same_number(delta, expected_delta):
                    raise SystemExit("error: experiment attempt delta does not match incumbent")
            guard_code = attempt.get("guard_exit_code")
            if guard_code is not None and (not isinstance(guard_code, int)
                                           or isinstance(guard_code, bool)):
                raise SystemExit("error: experiment guard_exit_code must be an integer or null")
            if attempt["status"] == "kept":
                if (value is None or delta < frozen_metric["minimum_delta"]
                        or guard_code != 0):
                    raise SystemExit("error: kept attempt missed metric delta or guard")
                incumbent = value
                kept += 1
                no_improvement_streak = 0
            else:
                no_improvement_streak += 1
                if no_improvement_streak == plateau:
                    plateau_reached_at = index
        if kept == 0 or not same_number(report_metric["final"], incumbent):
            raise SystemExit("error: experiment final metric is not the last kept incumbent")
        stop_reason = report.get("stop_reason")
        if stop_reason not in {"goal-met", "budget", "plateau", "blocked"}:
            raise SystemExit("error: invalid experiment stop_reason")
        if plateau_reached_at is not None and stop_reason != "plateau":
            raise SystemExit("error: plateau boundary requires stop_reason=plateau")
        if stop_reason == "plateau" and plateau_reached_at != len(attempts):
            raise SystemExit("error: plateau stop requires a terminal no-improvement window")
        if stop_reason == "budget" and len(attempts) != maximum:
            raise SystemExit("error: budget stop requires exhausting max_attempts")

        baseline_code, baseline_log = run_at_commit(
            repo, run_dir, task, task["base_commit"], frozen_metric["command"],
            "experiment-baseline",
        )
        if baseline_code != 0:
            raise SystemExit("error: experiment baseline metric command failed")
        baseline = parse_metric(baseline_log)
        if (frozen_metric["baseline"] is not None
                and not same_number(baseline, frozen_metric["baseline"])):
            raise SystemExit("error: frozen observed baseline does not replay at base_commit")
        if not same_number(baseline, report_metric["baseline"]):
            raise SystemExit("error: experiment report baseline differs from base replay")

        replay_results = {}
        for label, command in (
            ("metric", frozen_metric["command"]),
            ("guard", contract["guard_command"]),
            ("holdout", contract["holdout_command"]),
        ):
            code, log = run_at_commit(repo, run_dir, task, head, command,
                                      f"experiment-{label}")
            replay_results[label] = (code, log)
        if replay_results["metric"][0] != 0:
            raise SystemExit("error: final experiment metric command failed")
        final_metric = parse_metric(replay_results["metric"][1])
        if not same_number(final_metric, report_metric["final"]):
            raise SystemExit("error: final experiment metric differs from independent replay")
        if replay_results["guard"][0] != 0:
            raise SystemExit("error: final experiment guard failed")
        if replay_results["holdout"][0] != 0:
            raise SystemExit("error: held-out experiment acceptance failed")

        task["experiment_evidence"] = report
        task["experiment_verification"] = {
            "head_sha": head,
            "baseline": baseline,
            "final": final_metric,
            "baseline_exit_code": baseline_code,
            "baseline_log": str(baseline_log.relative_to(run_dir)),
            "metric_exit_code": replay_results["metric"][0],
            "metric_log": str(replay_results["metric"][1].relative_to(run_dir)),
            "guard_exit_code": replay_results["guard"][0],
            "guard_log": str(replay_results["guard"][1].relative_to(run_dir)),
            "holdout_exit_code": replay_results["holdout"][0],
            "holdout_log": str(replay_results["holdout"][1].relative_to(run_dir)),
            "recorded_at": now(),
        }
        task["updated_at"] = now()
        commit_transaction(
            run_dir, {task_path: task},
            {"event": "experiment_recorded", "task": args.task,
             "base": baseline, "final": final_metric, "head": head},
        )
        report_path.unlink()
        fd_dir = os.open(report_path.parent, os.O_RDONLY)
        try:
            os.fsync(fd_dir)
        finally:
            os.close(fd_dir)
    print("recorded")


def cmd_diagnosis_put(args, _repo, run_dir):
    if not safe_task_id(args.task):
        raise SystemExit("error: unsafe task id")
    report_path = Path(args.file).resolve()
    task_path = run_dir / "tasks" / f"{args.task}.json"
    with locked(run_dir):
        state, task = load_json(run_dir / "state.json"), load_json(task_path)
        expected_report = Path(task["worktree"]).resolve() / ".qteam-diagnosis.json"
        if (report_path != expected_report or report_path.is_symlink()
                or not report_path.is_file()):
            raise SystemExit(
                f"error: diagnosis report must be the regular file {expected_report}"
            )
        report = load_json(report_path)
        required = ["schema_version", "repro_commit", "feedback_loop", "observed_red",
                    "minimized_repro", "hypotheses", "root_cause", "causal_chain",
                    "fix_boundary", "cleanup", "preventive_lesson"]
        missing = [key for key in required if not report.get(key)]
        if missing:
            raise SystemExit("error: diagnosis report missing: " + ", ".join(missing))
        if (not isinstance(report["schema_version"], int)
                or isinstance(report["schema_version"], bool)
                or report["schema_version"] != 1):
            raise SystemExit("error: unsupported diagnosis schema_version")
        string_fields = ["repro_commit", "feedback_loop", "observed_red",
                         "minimized_repro", "root_cause", "fix_boundary", "cleanup",
                         "preventive_lesson"]
        if any(not isinstance(report[field], str) for field in string_fields):
            raise SystemExit("error: diagnosis narrative/command fields must be strings")
        if report["feedback_loop"] != task.get("diagnosis_command"):
            raise SystemExit("error: diagnosis feedback_loop differs from the approved command")
        hypotheses = report["hypotheses"]
        if not isinstance(hypotheses, list) or not 3 <= len(hypotheses) <= 5:
            raise SystemExit("error: diagnosis needs 3-5 ranked hypotheses")
        fields = {"rank", "statement", "prediction", "check", "outcome"}
        if any(not isinstance(item, dict) or not fields.issubset(item)
               or not isinstance(item.get("rank"), int)
               or isinstance(item.get("rank"), bool)
               or any(not isinstance(item.get(field), str) or not item[field]
                      for field in fields - {"rank"}) for item in hypotheses):
            raise SystemExit(
                "error: every hypothesis needs rank/statement/prediction/check/outcome"
            )
        if [item["rank"] for item in hypotheses] != list(range(1, len(hypotheses) + 1)):
            raise SystemExit("error: diagnosis hypothesis ranks must be consecutive from 1")
        if (not isinstance(report["causal_chain"], list)
                or len(report["causal_chain"]) < 2
                or any(not isinstance(item, str) or not item
                       for item in report["causal_chain"])):
            raise SystemExit("error: causal_chain must trace at least two boundaries")
        if state.get("phase") not in {"WAVE_RUNNING", "FIXING", "INTEGRATION_TESTING"}:
            raise SystemExit("error: diagnosis evidence is invalid in this phase")
        if task.get("status") != "running" or not task.get("diagnosis_required"):
            raise SystemExit("error: diagnosis evidence requires a running diagnosis task")
        repro = git(["rev-parse", f"{report['repro_commit']}^{{commit}}"], _repo).stdout.strip()
        branch_head = git(["rev-parse", task["branch"]], _repo).stdout.strip()
        for older, newer in ((task["base_commit"], repro), (repro, branch_head)):
            if git(["merge-base", "--is-ancestor", older, newer], _repo,
                   check=False).returncode:
                raise SystemExit("error: diagnosis repro_commit is outside task history")
        repro_code, repro_log = run_at_commit(
            _repo, run_dir, task, repro, task["diagnosis_command"], "diagnosis-red"
        )
        repro_output = repro_log.read_text(encoding="utf-8", errors="replace")
        if repro_code == 0:
            raise SystemExit("error: diagnosis feedback loop passed at repro_commit")
        if task["failure_pattern"] not in repro_output:
            raise SystemExit("error: diagnosis output lacks the approved failure_pattern")
        task["diagnosis_evidence"] = {
            **report,
            "repro_commit": repro,
            "repro_exit_code": repro_code,
            "repro_log": str(repro_log.relative_to(run_dir)),
            "failure_pattern": task["failure_pattern"],
            "recorded_at": now(),
        }
        task["updated_at"] = now()
        commit_transaction(run_dir, {task_path: task},
                           {"event": "diagnosis_recorded", "task": args.task,
                            "root_cause": report["root_cause"]})
        report_path.unlink()
        fd_dir = os.open(report_path.parent, os.O_RDONLY)
        try:
            os.fsync(fd_dir)
        finally:
            os.close(fd_dir)
    print("recorded")


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
        validate_method_evidence(repo, task, head, require_diagnosis_green=False)
        env = os.environ.copy()
        env.update({key: str(value) for key, value in task.get("env", {}).items()
                    if key in TASK_ENV_KEYS})
        diagnosis_result = None
        if task.get("diagnosis_required"):
            diagnosis_log = run_dir / "verifications" / f"{args.task}-diagnosis-green.log"
            diagnosis_code = run_verification(
                task["diagnosis_command"], worktree, env, diagnosis_log
            )
            diagnosis_result = {
                "command": task["diagnosis_command"],
                "exit_code": diagnosis_code,
                "head_sha": head,
                "log": str(diagnosis_log.relative_to(run_dir)),
                "ts": now(),
            }
            task["diagnosis_evidence"].update({
                "green_exit_code": diagnosis_code,
                "green_head": head,
                "green_log": diagnosis_result["log"],
                "green_verified_at": diagnosis_result["ts"],
            })
            if diagnosis_code:
                task["updated_at"] = now()
                commit_transaction(
                    run_dir, {task_path: task},
                    {"event": "diagnosis_green", "task": args.task,
                     "exit_code": diagnosis_code, "head_sha": head,
                     "log": diagnosis_result["log"]},
                )
                failure_code = diagnosis_code
            else:
                failure_code = 0
        else:
            failure_code = 0
        if failure_code:
            evidence = {"diagnosis": diagnosis_result}
        else:
            log_path = run_dir / "verifications" / f"{args.task}.log"
            code = run_verification(task["verification"], worktree, env, log_path)
            verification_evidence = {
                "command": task["verification"], "exit_code": code,
                "head_sha": head, "ts": now(),
                "log": str(log_path.relative_to(run_dir)),
            }
            evidence = dict(verification_evidence)
            if diagnosis_result:
                evidence["diagnosis"] = diagnosis_result
            task.setdefault("verification_evidence", []).append(verification_evidence)
            task["updated_at"] = now()
            commit_transaction(run_dir, {task_path: task},
                               {"event": "task_verification", "task": args.task,
                                "exit_code": code, "head_sha": head,
                                "log": verification_evidence["log"],
                                "diagnosis_log": (diagnosis_result or {}).get("log")})
    print(json.dumps(evidence, sort_keys=True))
    if failure_code:
        raise SystemExit(failure_code)
    if evidence["exit_code"]:
        raise SystemExit(evidence["exit_code"])


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
        if args.require_risk:
            state["risk_forced"] = True
            state["risk_required"] = True
        axes = validate_reviews(repo, run_dir, state, head, args.require_risk,
                                through_wave=args.wave)
        old = state.setdefault("gates", {}).get("reviews", {}).get("status", "pending")
        state["gates"]["reviews"] = {
            "status": "passed", "head_sha": head, "axes": axes,
            "through_wave": args.wave, "updated_at": now(),
        }
        state["updated_at"] = now()
        commit_transaction(run_dir, {path: state},
                           {"event": "gate", "gate": "reviews", "from": old,
                            "to": "passed", "head_sha": head, "axes": axes})
    print("passed")


def validate_decision_input(record):
    if not isinstance(record, dict):
        raise SystemExit("error: decision gate must be a JSON object")
    required = {"schema_version", "id", "status", "question", "authority", "scope"}
    if (frozenset(record) not in {frozenset(required), frozenset({*required, "subject"})}
            or record.get("schema_version") != 1):
        raise SystemExit("error: new decision gate needs exact schema-version-1 fields")
    if (not safe_task_id(record.get("id")) or record.get("status") != "open"
            or not isinstance(record.get("question"), str) or not record["question"].strip()
            or record.get("authority") not in DECISION_AUTHORITIES):
        raise SystemExit("error: invalid open decision gate identity/question/authority")
    scope = record.get("scope")
    if not isinstance(scope, dict) or set(scope) != {"kind", "targets"}:
        raise SystemExit("error: decision scope needs exact kind/targets fields")
    kind, targets = scope.get("kind"), scope.get("targets")
    if kind not in {"global", "task", "wave", "action"} or not isinstance(targets, list):
        raise SystemExit("error: invalid decision scope")
    if len(targets) != len({json.dumps(item, sort_keys=True) for item in targets}):
        raise SystemExit("error: decision scope targets must be unique")
    if kind == "global" and targets:
        raise SystemExit("error: global decision scope must have no targets")
    if kind != "global" and not targets:
        raise SystemExit("error: scoped decision gate needs exact targets")
    if kind == "task" and any(not safe_task_id(item) for item in targets):
        raise SystemExit("error: task decision targets must be safe task ids")
    if kind == "wave" and any(
            not isinstance(item, int) or isinstance(item, bool) or item < 1
            for item in targets):
        raise SystemExit("error: wave decision targets must be positive integers")
    if kind == "action" and any(item not in DECISION_ACTIONS for item in targets):
        raise SystemExit(
            "error: action decision targets must be task-start/wave-start/merge/"
            "learning-export/finish/publish"
        )
    subject = record.get("subject")
    if subject is not None:
        if (not isinstance(subject, dict)
                or set(subject) != {"kind", "sha256"}
                or subject.get("kind") != "hard-to-reverse-run"
                or not isinstance(subject.get("sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", subject["sha256"]) is None
                or record.get("authority") != "user"
                or kind != "action" or targets != ["finish"]):
            raise SystemExit(
                "error: hard-to-reverse subject requires a user action decision "
                "covering finish"
            )


def cmd_decision_put(args, _repo, run_dir):
    record = load_json(Path(args.file))
    validate_decision_input(record)
    decision_id = record["id"]
    with locked(run_dir):
        state_path = run_dir / "state.json"
        state = load_json(state_path)
        if state.get("finished") or state.get("phase") == "DONE":
            raise SystemExit("error: finished run is immutable")
        if state.get("publication_seal"):
            raise SystemExit("error: publication seal freezes decision gates")
        if record.get("subject", {}).get("kind") == "hard-to-reverse-run":
            expected, _payload = hard_to_reverse_subject(_repo, run_dir, state)
            if record["subject"] != expected:
                raise SystemExit(
                    "error: hard-to-reverse decision subject is stale or belongs "
                    "to a different run/head"
                )
        decision_path = run_dir / "decisions" / f"{decision_id}.json"
        if decision_path.exists() or decision_id in state.setdefault("decisions", {}):
            raise SystemExit(f"error: decision {decision_id} already exists")
        stamp = now()
        record["created_at"] = stamp
        record["updated_at"] = stamp
        state["decisions"][decision_id] = {
            "status": "open", "authority": record["authority"],
            "scope": record["scope"], "question": record["question"],
        }
        if record.get("subject"):
            state["decisions"][decision_id]["subject"] = record["subject"]
        state["updated_at"] = stamp
        commit_transaction(
            run_dir, {decision_path: record, state_path: state},
            {"event": "decision_opened", "decision": decision_id,
             "authority": record["authority"], "scope": record["scope"]},
        )
    print(decision_id)


def cmd_decision_resolve(args, _repo, run_dir):
    if (not safe_task_id(args.decision) or args.outcome not in {"allow", "deny"}
            or not args.choice.strip() or not args.evidence.strip()):
        raise SystemExit(
            "error: decision resolution needs id, --outcome, --choice, and --evidence"
        )
    with locked(run_dir):
        state_path = run_dir / "state.json"
        decision_path = run_dir / "decisions" / f"{args.decision}.json"
        state, record = load_json(state_path), load_json(decision_path)
        if state.get("publication_seal"):
            raise SystemExit("error: publication seal freezes decision gates")
        if state.get("finished") or record.get("status") != "open":
            raise SystemExit("error: only an open decision in an unfinished run may resolve")
        premature = []
        for task_id in state.get("tasks", {}):
            task = load_json(run_dir / "tasks" / f"{task_id}.json")
            handoff = task.get("handoff", {})
            if (task.get("handoff_required")
                    and handoff.get("kind") == "user-decision"
                    and handoff.get("decision_id") == args.decision
                    and task.get("status") not in {
                        "completed", "merged", "artifact_complete",
                    }):
                premature.append(task_id)
        if premature:
            raise SystemExit(
                "error: post-task handoff decision requires completed source tasks: "
                + ", ".join(sorted(premature))
            )
        stamp = now()
        record["status"] = "resolved"
        record["resolution"] = {
            "outcome": args.outcome, "choice": args.choice,
            "evidence": args.evidence, "resolved_at": stamp,
        }
        record["updated_at"] = stamp
        summary = state.setdefault("decisions", {}).get(args.decision)
        if not isinstance(summary, dict):
            raise SystemExit("error: decision summary is missing")
        summary["status"] = "resolved"
        summary["outcome"] = args.outcome
        summary["resolved_at"] = stamp
        state["updated_at"] = stamp
        commit_transaction(
            run_dir, {decision_path: record, state_path: state},
            {"event": "decision_resolved", "decision": args.decision,
             "outcome": args.outcome, "choice": args.choice,
             "evidence": args.evidence},
        )
    print("resolved")


def cmd_decision_supersede(args, _repo, run_dir):
    if not safe_task_id(args.decision) or not args.reason.strip():
        raise SystemExit("error: decision supersede needs id and --reason")
    with locked(run_dir):
        state_path = run_dir / "state.json"
        decision_path = run_dir / "decisions" / f"{args.decision}.json"
        state, record = load_json(state_path), load_json(decision_path)
        if state.get("publication_seal"):
            raise SystemExit("error: publication seal freezes decision gates")
        if state.get("finished") or record.get("status") != "open":
            raise SystemExit("error: only an open decision in an unfinished run may supersede")
        stamp = now()
        record["status"] = "superseded"
        record["superseded_reason"] = args.reason
        record["updated_at"] = stamp
        summary = state.setdefault("decisions", {}).get(args.decision)
        if not isinstance(summary, dict):
            raise SystemExit("error: decision summary is missing")
        summary["status"] = "superseded"
        summary["superseded_at"] = stamp
        state["updated_at"] = stamp
        commit_transaction(
            run_dir, {decision_path: record, state_path: state},
            {"event": "decision_superseded", "decision": args.decision,
             "reason": args.reason},
        )
    print("superseded")


def cmd_decision_check(args, _repo, run_dir):
    with locked(run_dir, allow_sealed=True):
        state_path = run_dir / "state.json"
        state = load_json(state_path)
        require_no_decision_blockers(
            run_dir, state, f"action {args.action}", action=args.action
        )
        if args.seal:
            if args.action != "publish":
                raise SystemExit("error: --seal is valid only for publish")
            if not args.expected_head:
                raise SystemExit("error: publication seal requires --expected-head")
            if state.get("phase") != "READY_TO_FINISH":
                raise SystemExit("error: publication seals only in READY_TO_FINISH")
            head = git(
                ["rev-parse", f"{args.expected_head}^{{commit}}"], _repo
            ).stdout.strip()
            if integration_head(_repo, state) != head:
                raise SystemExit(
                    "error: expected publication head is no longer the integration head"
                )
            validate_ready_invariants(
                _repo, run_dir, state, head
            )
            created, seal = seal_ready_authorization(
                run_dir, state, head, "publish"
            )
            if created:
                commit_transaction(
                    run_dir, {state_path: state},
                    {"event": "publication_sealed", "purpose": "publish",
                     "head_sha": head,
                     "decision_sha256": seal["decision_sha256"],
                     "authorization_sha256": seal["authorization_sha256"]},
                )
        elif args.expected_head:
            raise SystemExit("error: --expected-head requires --seal")
    print("sealed" if args.seal else "clear")


def cmd_reversibility_subject(_args, repo, run_dir):
    with locked(run_dir, allow_sealed=True):
        state = load_json(run_dir / "state.json")
        if not state.get("hard_to_reverse"):
            raise SystemExit("error: run has no hard-to-reverse task policy")
        subject, payload = hard_to_reverse_subject(repo, run_dir, state)
    print(json.dumps({"subject": subject, **payload}, sort_keys=True))


def cmd_boundary_check(_args, repo, run_dir):
    with locked(run_dir):
        state_path = run_dir / "state.json"
        state = load_json(state_path)
        if state.get("finished") or state.get("phase") not in {
            "LEARNING_EXPORT", "READY_TO_FINISH",
        }:
            raise SystemExit("error: public-boundary check requires LEARNING_EXPORT phase")
        worktree = (run_dir / "worktrees" / "integration").resolve()
        if (not worktree.is_dir()
                or Path(git(["rev-parse", "--show-toplevel"], worktree).stdout.strip()).resolve()
                != worktree
                or git(["branch", "--show-current"], worktree).stdout.strip()
                != state["integration_branch"]):
            raise SystemExit("error: public-boundary check needs the exact integration worktree")
        head = git(["rev-parse", "HEAD"], worktree).stdout.strip()
        findings = public_boundary_findings(worktree, state, head)
        old = state.setdefault("gates", {}).get("public_boundary", {}).get(
            "status", "pending"
        )
        gate = {
            "status": "failed" if findings else "passed", "head_sha": head,
            "base_sha": state["base_commit"], "findings": findings,
            "checked_at": now(),
        }
        state["gates"]["public_boundary"] = gate
        state["updated_at"] = now()
        commit_transaction(
            run_dir, {state_path: state},
            {"event": "gate", "gate": "public_boundary", "from": old,
             "to": gate["status"], "head_sha": head, "findings": findings},
        )
    print(json.dumps(gate, sort_keys=True))
    if findings:
        raise SystemExit(1)


def cmd_status(_args, repo, run_dir):
    with locked(run_dir, allow_sealed=True):
        state = load_json(run_dir / "state.json")
        phase = state.get("phase")
        records = decision_records(run_dir, state)
        decisions = [
            {"id": item["id"], "question": item["question"],
             "authority": item["authority"], "scope": item["scope"]}
            for item in records.values()
            if item.get("status") == "open"
        ]
        dependency_records = validate_dependency_graph(run_dir, state)
        tasks = {
            "active": [], "blocked": [], "failed": [], "pending": [],
            "ready_to_merge": [],
        }
        handoffs = []
        ready_tasks = []
        dependency_waits = {}
        migration_replan_tasks = []
        for task_id, summary in sorted(state.get("tasks", {}).items()):
            status = summary.get("status")
            bucket = ({"running": "active", "blocked": "blocked", "failed": "failed",
                       "pending": "pending", "completed": "ready_to_merge"}).get(status)
            if bucket:
                tasks[bucket].append(task_id)
            task = load_json(run_dir / "tasks" / f"{task_id}.json")
            requires_replan = task.get("policy_migration", {}).get(
                "requires_replan"
            )
            if requires_replan:
                migration_replan_tasks.append(task_id)
            blockers = dependency_blockers(dependency_records, task)
            if blockers and status in {"pending", "blocked", "failed"}:
                dependency_waits[task_id] = blockers
            elif status == "pending" and not requires_replan:
                ready_tasks.append(task_id)
            handoff = handoff_state(run_dir, state, task)
            if handoff["status"] == "blocking":
                handoffs.append({"task": task_id, **handoff})
        head_result = git(["rev-parse", state["integration_branch"]], repo, check=False)
        head = head_result.stdout.strip() if not head_result.returncode else None
        freshness = {}
        for gate_name in ("final_verification", "reviews", "public_boundary"):
            gate = state.get("gates", {}).get(gate_name, {})
            freshness[gate_name] = {
                "status": gate.get("status", "pending"),
                "fresh": bool(head and gate.get("head_sha") == head),
            }
        if phase == "WAVE_RUNNING":
            execution_ready_tasks = [
                task_id for task_id in ready_tasks
                if dependency_records[task_id].get("wave")
                == state.get("current_wave")
            ]
            execution_dependency_waits = {
                task_id: blockers
                for task_id, blockers in dependency_waits.items()
                if dependency_records[task_id].get("wave")
                == state.get("current_wave")
            }
        else:
            execution_ready_tasks = ready_tasks
            execution_dependency_waits = dependency_waits
        blocking_now = {}
        if phase == "READY_TO_FINISH":
            candidates = blocking_decisions(run_dir, state, action="finish")
        elif phase == "PLAN_READY" and tasks["pending"]:
            first_pending = load_json(
                run_dir / "tasks" / f"{tasks['pending'][0]}.json"
            )
            candidate_wave = state.get("current_wave") or first_pending.get("wave")
            candidates = blocking_decisions(
                run_dir, state, wave=candidate_wave, action="wave-start"
            )
        elif phase == "WAVE_MERGING":
            candidates = blocking_decisions(
                run_dir, state, wave=state.get("current_wave"), action="merge"
            )
            for task in wave_tasks(run_dir, state, state.get("current_wave")):
                candidates.extend(blocking_decisions(
                    run_dir, state, task=task["id"], wave=task.get("wave"),
                    action="merge", required=task.get("required_decisions", []),
                ))
        elif tasks["pending"] and phase in {
            "WAVE_RUNNING", "FIXING", "INTEGRATION_TESTING", "LEARNING_EXPORT",
        }:
            candidates = []
            for task_id in tasks["pending"]:
                task = load_json(run_dir / "tasks" / f"{task_id}.json")
                if (phase == "WAVE_RUNNING"
                        and task.get("wave") != state.get("current_wave")):
                    continue
                candidates.extend(blocking_decisions(
                    run_dir, state, task=task_id, wave=task.get("wave"),
                    action="task-start", required=task.get("required_decisions", []),
                ))
        else:
            candidates = []
        for item in candidates:
            blocking_now[item["id"]] = {
                "id": item["id"], "question": item["question"],
                "authority": item["authority"], "scope": item["scope"],
                "status": item["status"],
                "outcome": item.get("resolution", {}).get("outcome"),
            }
        current_blockers = [blocking_now[key] for key in sorted(blocking_now)]
        hard_authorization_pending = False
        if state.get("hard_to_reverse") and phase in {
            "LEARNING_EXPORT", "READY_TO_FINISH",
        }:
            try:
                require_hard_to_reverse_authorization(repo, run_dir, state)
            except SystemExit:
                hard_authorization_pending = True
        if migration_replan_tasks and phase != "REPLANNING":
            next_action = "enter REPLANNING to restore migrated task dependencies"
        elif current_blockers:
            first = current_blockers[0]
            next_action = (
                f"honor denied decision {first['id']}: {first['question']}"
                if first["outcome"] == "deny"
                else f"answer decision {first['id']}: {first['question']}"
            )
        elif hard_authorization_pending:
            next_action = (
                "create and allow a user finish decision from reversibility-subject"
            )
        elif phase == "WAVE_VALIDATING" and tasks["active"]:
            next_action = f"run mechanical task check for {tasks['active'][0]}"
        elif (phase == "WAVE_VALIDATING"
              and (tasks["failed"] or tasks["blocked"] or tasks["pending"])):
            next_action = "enter REPLANNING for non-completable wave tasks"
        elif (phase != "REPLANNING"
              and any(item["kind"] == "replan" for item in handoffs)):
            next_action = "enter REPLANNING to close replan handoff"
        elif tasks["failed"] and phase in {"WAVE_RUNNING", "FIXING"}:
            next_action = f"diagnose failed task {tasks['failed'][0]}"
        elif tasks["blocked"] and phase in {"WAVE_RUNNING", "FIXING"}:
            next_action = f"unblock or supersede task {tasks['blocked'][0]}"
        elif tasks["active"] and phase in {
            "WAVE_RUNNING", "FIXING", "INTEGRATION_TESTING", "LEARNING_EXPORT",
        }:
            next_action = f"continue task {tasks['active'][0]}"
        elif execution_ready_tasks and phase in {
            "WAVE_RUNNING", "FIXING", "INTEGRATION_TESTING", "LEARNING_EXPORT",
        }:
            next_action = f"start pending task {execution_ready_tasks[0]}"
        elif execution_dependency_waits and phase in {
            "WAVE_RUNNING", "FIXING", "INTEGRATION_TESTING", "LEARNING_EXPORT",
        }:
            task_id = sorted(execution_dependency_waits)[0]
            blocker = execution_dependency_waits[task_id][0]
            next_action = (
                f"wait for dependency {blocker['task']}={blocker['status']} "
                f"before {task_id}"
            )
        elif tasks["ready_to_merge"]:
            task_id = tasks["ready_to_merge"][0]
            next_action = (f"merge completed task {task_id}" if phase == "WAVE_MERGING"
                           else f"advance to merge completed task {task_id}")
        elif (phase in {"REVIEWING", "RE_REVIEWING", "LEARNING_EXPORT", "READY_TO_FINISH"}
              and (freshness["reviews"]["status"] != "passed"
                   or not freshness["reviews"]["fresh"])):
            next_action = "complete required wave reviews"
        elif (phase in {"LEARNING_EXPORT", "READY_TO_FINISH"}
              and (freshness["final_verification"]["status"] != "passed"
                   or not freshness["final_verification"]["fresh"])):
            next_action = "run final integration verification"
        elif (phase in {"LEARNING_EXPORT", "READY_TO_FINISH"}
              and state.get("gates", {}).get("learning", {}).get("status") not in {
                  "passed", "skipped",
              }):
            next_action = "complete or explicitly skip learning export"
        elif (phase in {"LEARNING_EXPORT", "READY_TO_FINISH"}
              and (freshness["public_boundary"]["status"] != "passed"
                   or not freshness["public_boundary"]["fresh"])):
            next_action = "run public-boundary check"
        elif any(item["kind"] == "user-decision" for item in handoffs):
            item = next(item for item in handoffs if item["kind"] == "user-decision")
            next_action = f"resolve {item['task']} user-decision handoff"
        elif any(item["kind"] == "successor"
                 and item.get("target_status") == "superseded" for item in handoffs):
            next_action = "enter REPLANNING for missing successor delivery"
        elif phase == "READY_TO_FINISH":
            next_action = "finish the reviewed run"
        else:
            next_action = f"advance phase from {state['phase']}"
        packet = {
            "run_id": state["run_id"], "phase": state["phase"],
            "current_wave": state.get("current_wave", 0), "integration_head": head,
            "tasks": tasks, "open_decisions": sorted(decisions, key=lambda item: item["id"]),
            "ready_tasks": ready_tasks,
            "dependency_blockers": dependency_waits,
            "blocking_decisions": current_blockers,
            "blocking_handoffs": handoffs, "evidence_freshness": freshness,
            "next_action": next_action,
        }
    print(json.dumps(packet, sort_keys=True))


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


def validate_ready_invariants(repo, run_dir, state, head):
    if state.get("phase") != "READY_TO_FINISH":
        raise SystemExit("error: run is not READY_TO_FINISH")
    unfinished = [task for task, value in state.get("tasks", {}).items()
                  if value.get("status") not in {
                      "merged", "superseded", "artifact_complete",
                  }]
    if unfinished:
        raise SystemExit(f"error: unfinished tasks: {', '.join(unfinished)}")
    gates = state.get("gates", {})
    final_gate = gates.get("final_verification", {})
    if final_gate.get("status") != "passed" or final_gate.get("head_sha") != head:
        raise SystemExit("error: stale or failed final verification")
    if gates.get("learning", {}).get("status") not in {"passed", "skipped"}:
        raise SystemExit("error: learning gate is not passed or explicitly skipped")
    require_no_decision_blockers(run_dir, state, "finish", action="finish")
    require_hard_to_reverse_authorization(repo, run_dir, state)
    try:
        require_bound_drift(repo, run_dir, state)
    except ArtifactError as exc:
        raise SystemExit(f"error: {exc}")
    unresolved_handoffs = []
    for task_id in state.get("tasks", {}):
        task = load_json(run_dir / "tasks" / f"{task_id}.json")
        if handoff_state(run_dir, state, task)["status"] == "blocking":
            unresolved_handoffs.append(task_id)
    if unresolved_handoffs:
        raise SystemExit(
            "error: unresolved typed handoffs: " + ", ".join(unresolved_handoffs)
        )
    validate_merged_tasks(repo, run_dir, state, head)
    validate_integration_provenance(repo, run_dir, state, head)
    validate_reviews(repo, run_dir, state, head)
    if (gates.get("public_boundary", {}).get("status") != "passed"
            or gates["public_boundary"].get("head_sha") != head):
        raise SystemExit("error: stale or failed public-boundary check")


def cmd_finish(_args, _repo, run_dir):
    with locked(run_dir, allow_sealed=True):
        path = run_dir / "state.json"
        state = load_json(path)
        if state.get("phase") == "DONE" and state.get("finished"):
            print("DONE")
            return
        current_head = integration_head(_repo, state)
        head = (git(["rev-parse", f"{_args.expected_head}^{{commit}}"], _repo).stdout.strip()
                if _args.expected_head else current_head)
        if _args.seal and not _args.check_only:
            raise SystemExit("error: --seal requires --check-only")
        if _args.check_only and _args.expected_head and not _args.seal:
            raise SystemExit("error: --expected-head is invalid with --check-only")
        seal = state.get("publication_seal")
        if seal:
            records = decision_records(run_dir, state)
            current_decisions = publication_decision_digest(records)
            current_authorization = publication_authorization_digest(
                run_dir, state, records
            )
            if seal.get("decision_sha256") != current_decisions:
                raise SystemExit("error: publication decision set changed after sealing")
            if seal.get("authorization_sha256") != current_authorization:
                raise SystemExit("error: publication authorization changed after sealing")
            if seal.get("head_sha") != head:
                raise SystemExit("error: publication seal belongs to a different reviewed head")
        validate_ready_invariants(_repo, run_dir, state, head)
        if getattr(_args, "check_only", False):
            if _args.seal:
                if current_head != head:
                    raise SystemExit(
                        "error: expected integration head changed before sealing"
                    )
                created, new_seal = seal_ready_authorization(
                    run_dir, state, head, "integrate"
                )
                if created:
                    commit_transaction(
                        run_dir, {path: state},
                        {"event": "publication_sealed", "purpose": "integrate",
                         "head_sha": head,
                         "decision_sha256": new_seal["decision_sha256"],
                         "authorization_sha256":
                         new_seal["authorization_sha256"]},
                    )
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
    with locked(run_dir, allow_sealed=True):
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
    p.add_argument("--epic", help="epic id whose dependency gate authorizes this run")
    p.add_argument("--require-risk", action="store_true")
    p.add_argument("--model-economy", default=MODEL_PROFILES["economy"]["model"])
    p.add_argument("--model-standard", default=MODEL_PROFILES["standard"]["model"])
    p.add_argument("--model-deep", default=MODEL_PROFILES["deep"]["model"])
    p.add_argument("--review-model-economy",
                   default=REVIEW_MODEL_PROFILES["economy"]["model"])
    p.add_argument("--review-model-standard",
                   default=REVIEW_MODEL_PROFILES["standard"]["model"])
    p.add_argument("--review-model-deep",
                   default=REVIEW_MODEL_PROFILES["deep"]["model"])
    p.add_argument("--shared-surface", action="append", default=[])
    p.set_defaults(func=cmd_init)
    sub.add_parser("migrate-run").set_defaults(func=cmd_migrate_run)
    sub.add_parser("migrate-v2").set_defaults(func=cmd_migrate_run)
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
    p = sub.add_parser("handoff-close")
    p.add_argument("task")
    p.add_argument("--reason", required=True)
    p.add_argument("--replacement")
    p.set_defaults(func=cmd_handoff_close)
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
    p = sub.add_parser("learning-item-decision")
    p.add_argument("item")
    p.add_argument("--outcome", required=True, choices=["approved", "rejected"])
    p.add_argument("--evidence", required=True)
    p.set_defaults(func=cmd_learning_item_decision)
    p = sub.add_parser("verify-task")
    p.add_argument("task")
    p.set_defaults(func=cmd_verify_task)
    p = sub.add_parser("verify-tdd-cycle")
    p.add_argument("task")
    p.add_argument("--seam", required=True)
    p.add_argument("--red-commit", required=True)
    p.add_argument("--green-commit", required=True)
    p.set_defaults(func=cmd_verify_tdd_cycle)
    p = sub.add_parser("diagnosis-put")
    p.add_argument("task")
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_diagnosis_put)
    p = sub.add_parser("experiment-put")
    p.add_argument("task")
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_experiment_put)
    p = sub.add_parser("verify-final")
    p.add_argument("--command", required=True)
    p.set_defaults(func=cmd_verify_final)
    p = sub.add_parser("reviews-checked")
    p.add_argument("--head")
    p.add_argument("--wave", type=int, required=True)
    p.add_argument("--require-risk", action="store_true")
    p.set_defaults(func=cmd_reviews_checked)
    p = sub.add_parser("decision-put")
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_decision_put)
    p = sub.add_parser("decision-resolve")
    p.add_argument("decision")
    p.add_argument("--outcome", required=True, choices=["allow", "deny"])
    p.add_argument("--choice", required=True)
    p.add_argument("--evidence", required=True)
    p.set_defaults(func=cmd_decision_resolve)
    p = sub.add_parser("decision-supersede")
    p.add_argument("decision")
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_decision_supersede)
    p = sub.add_parser("decision-check")
    p.add_argument("--action", required=True, choices=sorted(DECISION_ACTIONS))
    p.add_argument("--seal", action="store_true")
    p.add_argument("--expected-head")
    p.set_defaults(func=cmd_decision_check)
    sub.add_parser("reversibility-subject").set_defaults(
        func=cmd_reversibility_subject
    )
    sub.add_parser("boundary-check").set_defaults(func=cmd_boundary_check)
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
    p.add_argument("--seal", action="store_true",
                   help="freeze READY authorization before an external Git integration")
    p.add_argument("--expected-head",
                   help="frozen reviewed SHA already integrated into the base branch")
    p.set_defaults(func=cmd_finish)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("show").set_defaults(func=cmd_show)
    return ap


def main():
    args = parser().parse_args()
    repo = git_root()
    run_dir = resolve_run(repo, args.run)
    state_path = state_regular(run_dir / "state.json", "run state")
    if state_path.is_file() and args.command not in {"migrate-run", "migrate-v2"}:
        existing_state = load_json(state_path)
        version = existing_state.get("schema_version")
        if version != 6:
            raise SystemExit(
                f"error: run schema {version!r} is unsupported; run migrate-run first"
            )
        if (existing_state.get("publication_seal")
                and args.command not in {
                    "decision-check", "reversibility-subject",
                    "finish", "show", "status",
                }):
            raise SystemExit(
                "error: publication seal freezes READY state and gate mutations"
            )
    args.func(args, repo, run_dir)


if __name__ == "__main__":
    main()
