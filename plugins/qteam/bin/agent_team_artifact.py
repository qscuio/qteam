#!/usr/bin/env python3
"""Validate and seal QTeam planning, portfolio, and knowledge artifacts."""

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


sys.dont_write_bytecode = True

SPEC_MARKER = "<!-- qteam-artifact: spec-v1 -->"
TICKETS_MARKER = "<!-- qteam-artifact: tickets-v1 -->"
ARTIFACT_MARKER = re.compile(r"<!--\s*qteam-artifact:\s*([^\s>]+)\s*-->")
HEX_OBJECT = re.compile(r"^[0-9a-f]{40,64}$")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$")
DEFINITION = re.compile(
    r"^\s*(?:[-*+]\s+|#{1,6}\s+)?(?P<id>(?:US|AC)-[0-9]+(?:\.[0-9]+)*)\s*[:.)-]",
    re.IGNORECASE,
)
TASK_DEFINITION = re.compile(
    r"^\s*(?:[-*+]\s+|#{1,6}\s+)?(?P<id>T[0-9]+)\s*[:.)-]",
    re.IGNORECASE,
)
TASK_REFERENCE = re.compile(r"\bT[0-9]+\b", re.IGNORECASE)
REQUIREMENT_REFERENCE = re.compile(
    r"\b(?:US|AC)-[0-9]+(?:\.[0-9]+)*\b", re.IGNORECASE
)


class ArtifactError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def canonical_bytes(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def object_sha256(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(args, cwd, check=True):
    result = subprocess.run(
        ["git", *args], cwd=cwd, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if check and result.returncode:
        raise ArtifactError(result.stderr.strip() or "git command failed")
    return result.stdout.strip(), result.returncode


def repo_root():
    root, _ = git(["rev-parse", "--show-toplevel"], Path.cwd())
    return Path(root).resolve()


def safe_identifier(value, label="identifier"):
    if (not isinstance(value, str) or not value or value in {".", ".."}
            or ".." in value or not value[0].isascii()
            or not value[0].isalnum()
            or any(not ch.isascii() or not (ch.isalnum() or ch in "._-")
                   for ch in value)):
        raise ArtifactError(f"unsafe {label}: {value!r}")
    return value


def repo_path(repo, raw, *, must_exist=True):
    value = Path(raw)
    if value.is_absolute():
        try:
            relative = value.relative_to(repo)
        except ValueError:
            raise ArtifactError(f"path escapes repository: {raw}")
    else:
        relative = value
    if (relative.is_absolute() or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)):
        raise ArtifactError(f"unsafe repository path: {raw}")
    path = repo
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ArtifactError(f"repository path contains a symlink: {raw}")
    if must_exist:
        if not path.is_file():
            raise ArtifactError(f"missing or unsafe repository file: {raw}")
    else:
        if path == repo:
            raise ArtifactError(f"output cannot be the repository root: {raw}")
    return path


def unique_strings(value, label, allowed=None):
    if (not isinstance(value, list)
            or any(not isinstance(item, str) or not item for item in value)):
        raise ArtifactError(f"{label} must be an array of non-empty strings")
    if len(value) != len(set(value)):
        raise ArtifactError(f"{label} must not contain duplicates")
    if allowed is not None:
        unknown = sorted(set(value) - set(allowed))
        if unknown:
            raise ArtifactError(f"{label} contains unknown values: {', '.join(unknown)}")
    return value


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ArtifactError(f"missing {path}")
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"invalid JSON in {path}: {exc}")
    if not isinstance(value, dict):
        raise ArtifactError(f"JSON artifact must be an object: {path}")
    return value


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, indent=2, sort_keys=True, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def safe_regular(path, label, *, required=False):
    if path.is_symlink():
        raise ArtifactError(f"unsafe symlink for {label}: {path}")
    if path.exists() and not path.is_file():
        raise ArtifactError(f"unsafe non-regular file for {label}: {path}")
    if required and not path.is_file():
        raise ArtifactError(f"missing {label}: {path}")
    return path


def regular_bytes_and_mode(path, label):
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ArtifactError(f"cannot open {label}: {path}: {exc}")
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise ArtifactError(f"unsafe non-regular file for {label}: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            return source.read(), stat.S_IMODE(status.st_mode)
    finally:
        os.close(descriptor)


def regular_bytes(path, label):
    return regular_bytes_and_mode(path, label)[0]


def qteam_project_module():
    module_path = Path(__file__).with_name("qteam_project.py")
    if not module_path.is_file():
        module_path = Path(__file__).resolve().parent.parent / "scripts/qteam_project.py"
    spec = importlib.util.spec_from_file_location(
        "qteam_managed_runtime_contract", module_path
    )
    if spec is None or spec.loader is None:
        raise ArtifactError("cannot load QTeam project manifest contract")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def managed_runtime_bytes(repo, relative):
    """Capture bytes authorized by a complete installed project manifest."""
    runtime = repo_path(repo, relative)
    marker = repo_path(repo, ".codex/qteam-project.json")
    try:
        manifest = json.loads(regular_bytes(marker, "project manifest"))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ArtifactError(f"invalid project manifest: {exc}")
    if not isinstance(manifest, dict):
        raise ArtifactError("project manifest must be an object")
    try:
        records = qteam_project_module().installed_records(repo, manifest)
    except (ValueError, OSError) as exc:
        raise ArtifactError(f"invalid project manifest: {exc}")
    record = records.get(relative)
    if record is None:
        raise ArtifactError(f"project manifest does not own runtime: {relative}")
    data, mode = regular_bytes_and_mode(runtime, f"managed runtime {relative}")
    digest = hashlib.sha256(data).hexdigest()
    if digest != record["sha256"]:
        raise ArtifactError(f"managed runtime digest mismatch: {relative}")
    if manifest["schema_version"] == 3 and mode != record["mode"]:
        raise ArtifactError(f"managed runtime mode mismatch: {relative}")
    return runtime, data, digest


@contextmanager
def locked_regular(path, label):
    safe_regular(path, label)
    flags = os.O_RDWR | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ArtifactError(f"cannot safely open {label}: {path}: {exc}")
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            descriptor = None
            raise ArtifactError(f"unsafe non-regular file for {label}: {path}")
        with os.fdopen(descriptor, "a+", encoding="utf-8") as handle:
            descriptor = None
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield handle
    finally:
        if descriptor is not None:
            os.close(descriptor)


def issue(code, severity, message, source=None, line=None):
    value = {"code": code, "severity": severity, "message": message}
    if source is not None:
        value["source"] = source
    if line is not None:
        value["line"] = line
    return value


def _normalized_heading(value):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value.lower()).strip()


def _ticket_field_label(line):
    normalized = re.sub(r"^\s*[-*+]\s+", "", line)
    normalized = normalized.replace("**", "").replace("__", "")
    match = re.match(
        r"^\s*(depends_on|requirements|done when|verify)\s*:",
        normalized, re.IGNORECASE,
    )
    return match.group(1).lower() if match else None


SPEC_SECTIONS = {
    "problem statement": ("problem statement", "problem", "问题陈述", "问题"),
    "user-visible solution": (
        "user visible solution", "solution", "用户可见方案", "解决方案",
    ),
    "user stories": ("user stories", "stories", "用户故事"),
    "acceptance criteria": ("acceptance criteria", "acceptance", "验收标准", "验收条件"),
    "testing decisions": ("testing decisions", "test strategy", "testing", "测试决策", "测试策略"),
    "implementation decisions": (
        "implementation decisions", "implementation decision",
        "实现决策", "实施决策",
    ),
    "constraints/invariants": (
        "constraints and invariants", "constraints", "invariants", "约束与不变量", "约束", "不变量",
    ),
    "out of scope": ("out of scope", "non goals", "范围外", "不在范围"),
    "assumptions/blockers": (
        "assumptions and unresolved blockers", "assumptions", "unresolved blockers", "假设与未决阻塞", "假设", "未决阻塞",
    ),
}


def _source_records(repo, paths):
    records = []
    for raw in paths:
        path = repo_path(repo, raw)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError as exc:
            raise ArtifactError(f"artifact is not UTF-8 text: {path}: {exc}")
        records.append({
            "path": path,
            "source": path.relative_to(repo).as_posix(),
            "sha256": file_sha256(path),
            "text": text,
        })
    if not records:
        raise ArtifactError("at least one --file is required")
    return records


def _lint_spec(records):
    errors = []
    warnings = []
    combined = "\n".join(record["text"] for record in records)
    markers = [
        marker
        for record in records
        for marker in ARTIFACT_MARKER.findall(record["text"])
    ]
    has_marker = "spec-v1" in markers
    has_unknown_marker = any(marker != "spec-v1" for marker in markers)
    if has_unknown_marker:
        errors.append(issue("SPEC001", "error", "unsupported QTeam spec artifact marker"))
    if not has_marker and not has_unknown_marker:
        warnings.append(issue(
            "SPEC000", "warning",
            f"legacy/untyped spec source; add {SPEC_MARKER} to enable strict deterministic checks",
        ))

    headings = set()
    definitions = {}
    for record in records:
        for number, line in enumerate(record["text"].splitlines(), start=1):
            heading = HEADING.match(line)
            if heading:
                headings.add(_normalized_heading(heading.group(1)))
            match = DEFINITION.match(line)
            if match:
                identifier = match.group("id").upper()
                if identifier in definitions:
                    first_source, first_line = definitions[identifier]
                    errors.append(issue(
                        "SPEC004", "error",
                        f"duplicate definition {identifier}; first defined at {first_source}:{first_line}",
                        record["source"], number,
                    ))
                else:
                    definitions[identifier] = (record["source"], number)

    if has_marker:
        for label, aliases in SPEC_SECTIONS.items():
            if not any(alias in headings for alias in aliases):
                errors.append(issue(
                    "SPEC002", "error", f"typed spec is missing section: {label}",
                ))
        if not any(identifier.startswith("US-") for identifier in definitions):
            errors.append(issue("SPEC003", "error", "typed spec needs at least one US-* definition"))
        if not any(identifier.startswith("AC-") for identifier in definitions):
            errors.append(issue("SPEC003", "error", "typed spec needs at least one AC-* definition"))

    lowered = combined.lower()
    if any(identifier.startswith("AC-") for identifier in definitions):
        if not all(word in lowered for word in ("given", "when", "then")):
            warnings.append(issue(
                "SPEC101", "warning",
                "acceptance criteria have no complete Given/When/Then observable scenario",
            ))
    vague = sorted({
        token for token in ("tbd", "as appropriate", "as needed", "etc.", "should support")
        if token in lowered
    })
    if vague:
        warnings.append(issue(
            "SPEC102", "warning", "potentially vague terms require semantic review: " + ", ".join(vague),
        ))
    return errors, warnings


def _lint_tickets(records):
    errors = []
    warnings = []
    markers = [
        marker
        for record in records
        for marker in ARTIFACT_MARKER.findall(record["text"])
    ]
    has_marker = "tickets-v1" in markers
    has_unknown_marker = any(marker != "tickets-v1" for marker in markers)
    if has_unknown_marker:
        errors.append(issue("TICKET001", "error", "unsupported QTeam tickets artifact marker"))
    if not has_marker and not has_unknown_marker:
        warnings.append(issue(
            "TICKET000", "warning",
            f"legacy/untyped tickets source; add {TICKETS_MARKER} to enable strict deterministic checks",
        ))
    definitions = {}
    task_blocks = {}
    combined_lines = []
    for record in records:
        lines = record["text"].splitlines()
        combined_lines.extend((record["source"], number, line)
                              for number, line in enumerate(lines, start=1))
        current = None
        for number, line in enumerate(lines, start=1):
            match = TASK_DEFINITION.match(line)
            if not match:
                if current is not None:
                    task_blocks[current].append((record["source"], number, line))
                continue
            identifier = match.group("id").upper()
            if identifier in definitions:
                first_source, first_line = definitions[identifier]
                errors.append(issue(
                    "TICKET003", "error",
                    f"duplicate task {identifier}; first defined at {first_source}:{first_line}",
                    record["source"], number,
                ))
            else:
                definitions[identifier] = (record["source"], number)
                task_blocks[identifier] = [(record["source"], number, line)]
                current = identifier
    if has_marker and not definitions:
        errors.append(issue("TICKET002", "error", "typed tickets artifact needs at least one T* task"))
    if has_marker:
        for task_id, lines in task_blocks.items():
            labels = {_ticket_field_label(line) for _, _, line in lines}
            source, number = definitions[task_id]
            for field in ("depends_on", "requirements", "done when", "verify"):
                if field not in labels:
                    errors.append(issue(
                        "TICKET004", "error",
                        f"typed ticket {task_id} is missing field: {field}",
                        source, number,
                    ))
    known = set(definitions)
    for source, number, line in combined_lines:
        if _ticket_field_label(line) != "depends_on":
            continue
        for reference in TASK_REFERENCE.findall(line):
            normalized = reference.upper()
            if normalized not in known:
                errors.append(issue(
                    "TICKET005", "error", f"unknown task dependency {normalized}", source, number,
                ))
    if has_marker and not any(REQUIREMENT_REFERENCE.search(line)
                              for _, _, line in combined_lines):
        warnings.append(issue(
            "TICKET101", "warning", "tasks do not reference any US-* or AC-* requirement IDs",
        ))
    return errors, warnings


def lint_documents(kind, paths, repo=None):
    repo = repo_root() if repo is None else Path(repo).resolve()
    records = _source_records(repo, paths)
    if kind == "spec":
        errors, warnings = _lint_spec(records)
    elif kind == "tickets":
        errors, warnings = _lint_tickets(records)
    else:
        raise ArtifactError(f"unsupported artifact kind: {kind}")
    errors.sort(key=lambda item: (
        item["code"], item.get("source", ""), item.get("line", 0), item["message"]
    ))
    warnings.sort(key=lambda item: (
        item["code"], item.get("source", ""), item.get("line", 0), item["message"]
    ))
    status = "fail" if errors else ("pass-with-warnings" if warnings else "pass")
    return {
        "schema_version": 1,
        "kind": kind,
        "status": status,
        "sources": [
            {"source": record["source"], "sha256": record["sha256"]}
            for record in records
        ],
        "errors": errors,
        "warnings": warnings,
    }


def cmd_lint(args, repo):
    report = lint_documents(args.kind, args.file, repo)
    if args.output:
        atomic_json(repo_path(repo, args.output, must_exist=False), report)
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    if report["errors"]:
        raise SystemExit(1)


def epic_dir(repo, epic_id):
    safe_identifier(epic_id, "epic id")
    return repo_path(
        repo, Path(".agents") / "epics" / epic_id, must_exist=False
    )


def _events_contain(path, txid):
    safe_regular(path, "event log")
    if not path.exists():
        return False
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ArtifactError(f"event log entry must be an object: {path}")
                if record.get("txid") == txid:
                    return True
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"invalid epic event log {path}: {exc}")
    return False


def _append_event(path, event):
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_regular(path, "event log")
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ArtifactError(f"cannot safely append event log {path}: {exc}")
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ArtifactError(f"unsafe non-regular file for event log: {path}")
    with os.fdopen(descriptor, "a", encoding="utf-8") as output:
        output.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n")
        output.flush()
        os.fsync(output.fileno())


def _apply_epic_transaction(directory, transaction):
    if (not isinstance(transaction, dict)
            or set(transaction) != {"schema_version", "manifest", "event"}
            or transaction.get("schema_version") != 1
            or not isinstance(transaction.get("manifest"), dict)
            or not isinstance(transaction.get("event"), dict)
            or not isinstance(transaction["event"].get("txid"), str)):
        raise ArtifactError("invalid epic transaction")
    event = transaction["event"]
    if (not re.fullmatch(r"[0-9a-f]{32}", event["txid"])
            or not isinstance(event.get("event"), str) or not event["event"]
            or not isinstance(event.get("recorded_at"), str)
            or not event["recorded_at"]):
        raise ArtifactError("invalid epic transaction event")
    manifest = validate_epic_manifest(transaction["manifest"], directory.name)
    manifest_path = safe_regular(directory / "epic.json", "epic manifest")
    atomic_json(manifest_path, manifest)
    if not _events_contain(directory / "events.jsonl", event["txid"]):
        _append_event(directory / "events.jsonl", event)


@contextmanager
def locked_epic(repo, epic_id):
    directory = epic_dir(repo, epic_id)
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = safe_regular(directory / ".lock", "epic lock")
    with locked_regular(lock_path, "epic lock"):
        intent = safe_regular(
            directory / ".transaction.json", "epic transaction"
        )
        safe_regular(directory / "epic.json", "epic manifest")
        events = safe_regular(directory / "events.jsonl", "epic event log")
        _events_contain(events, "")
        if intent.exists():
            transaction = read_json(intent)
            _apply_epic_transaction(directory, transaction)
            intent.unlink()
        yield directory


def commit_epic(directory, manifest, event):
    event = {**event, "txid": uuid.uuid4().hex, "recorded_at": now()}
    transaction = {"schema_version": 1, "manifest": manifest, "event": event}
    intent = safe_regular(directory / ".transaction.json", "epic transaction")
    atomic_json(intent, transaction)
    _apply_epic_transaction(directory, transaction)
    intent.unlink()
    directory_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


@contextmanager
def locked_run_state(repo, run_id):
    safe_identifier(run_id, "run id")
    relative = Path(".agents") / "runs" / run_id
    directory = repo_path(repo, relative, must_exist=False)
    if not directory.is_dir() or directory.is_symlink():
        raise ArtifactError(f"missing or unsafe QTeam run: {run_id}")
    lock_path = repo_path(repo, relative / ".state.lock", must_exist=False)
    with locked_regular(lock_path, f"QTeam run lock {run_id}"):
        transaction = repo_path(
            repo, relative / ".transaction.json", must_exist=False
        )
        safe_regular(transaction, f"QTeam run transaction {run_id}")
        events = repo_path(repo, relative / "events.jsonl", must_exist=False)
        safe_regular(events, f"QTeam run event log {run_id}")
        if transaction.exists():
            raise ArtifactError(
                f"QTeam run {run_id} has an unrecovered transaction; run status first"
            )
        state_path = repo_path(repo, relative / "state.json")
        yield directory, read_json(state_path), state_path


def commit_run_binding(directory, writes, event):
    txid = uuid.uuid4().hex
    transaction = {
        "schema_version": 1, "txid": txid,
        "writes": {
            path.relative_to(directory).as_posix(): value
            for path, value in writes.items()
        },
        "event": event,
    }
    for path in writes:
        if directory not in path.parents:
            raise ArtifactError("run binding write escaped run directory")
        safe_regular(path, "QTeam run binding file")
    intent = safe_regular(
        directory / ".transaction.json", "QTeam run transaction"
    )
    events = safe_regular(directory / "events.jsonl", "QTeam run event log")
    event_recorded = _events_contain(events, txid)
    atomic_json(intent, transaction)
    for path, value in writes.items():
        atomic_json(path, value)
    if not event_recorded:
        _append_event(events, {"ts": now(), "txid": txid, **event})
    intent.unlink()
    directory_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _unique_ids(items, label):
    values = []
    for item in items:
        if not isinstance(item, dict):
            raise ArtifactError(f"every {label} must be an object")
        values.append(safe_identifier(item.get("id"), f"{label} id"))
    if len(values) != len(set(values)):
        raise ArtifactError(f"duplicate {label} id")
    return values


def assert_acyclic(graph):
    visiting = set()
    visited = set()

    def visit(run_id):
        if run_id in visiting:
            raise ArtifactError(f"epic dependency cycle includes {run_id}")
        if run_id in visited:
            return
        visiting.add(run_id)
        for dependency in graph[run_id]:
            visit(dependency)
        visiting.remove(run_id)
        visited.add(run_id)

    for run_id in sorted(graph):
        visit(run_id)


def normalize_epic_plan(value):
    if set(value) != {"runs", "contracts"}:
        raise ArtifactError("epic plan must contain exactly runs and contracts")
    runs = value.get("runs")
    contracts = value.get("contracts")
    if not isinstance(runs, list) or not runs:
        raise ArtifactError("epic plan runs must be a non-empty array")
    if not isinstance(contracts, list):
        raise ArtifactError("epic plan contracts must be an array")
    run_ids = set(_unique_ids(runs, "run"))
    contract_ids = set(_unique_ids(contracts, "contract"))
    normalized_contracts = []
    for item in contracts:
        if set(item) != {"id", "owner_run", "consumers", "summary"}:
            raise ArtifactError("epic contract has unknown or missing fields")
        owner = safe_identifier(item["owner_run"], "contract owner run")
        consumers = unique_strings(
            item["consumers"], f"contract {item['id']} consumers", run_ids
        )
        if owner not in run_ids:
            raise ArtifactError(f"contract {item['id']} has unknown run ownership")
        if not isinstance(item["summary"], str) or not item["summary"].strip():
            raise ArtifactError(f"contract {item['id']} needs a summary")
        normalized_contracts.append({
            "id": item["id"], "owner_run": owner,
            "consumers": sorted(consumers), "summary": item["summary"].strip(),
        })

    normalized_runs = []
    graph = {}
    for item in runs:
        required = {"id", "title", "spec", "depends_on", "contracts"}
        if set(item) != required:
            raise ArtifactError("epic run has unknown or missing fields")
        run_id = item["id"]
        depends_on = unique_strings(
            item["depends_on"], f"epic run {run_id} dependencies", run_ids
        )
        used_contracts = unique_strings(
            item["contracts"], f"epic run {run_id} contracts", contract_ids
        )
        if (not isinstance(item["title"], str) or not item["title"].strip()
                or not isinstance(item["spec"], str) or not item["spec"].strip()):
            raise ArtifactError(f"epic run {run_id} needs title and spec")
        if run_id in depends_on:
            raise ArtifactError(f"epic dependency cycle at {run_id}")
        graph[run_id] = depends_on
        normalized_runs.append({
            "id": run_id, "title": item["title"].strip(),
            "spec": item["spec"].strip(), "depends_on": sorted(depends_on),
            "contracts": sorted(used_contracts), "status": "planned",
        })

    assert_acyclic(graph)
    normalized_runs.sort(key=lambda item: item["id"])
    normalized_contracts.sort(key=lambda item: item["id"])
    declarations = {
        contract_id: {
            record["id"] for record in normalized_runs
            if contract_id in record["contracts"]
        }
        for contract_id in contract_ids
    }
    for contract in normalized_contracts:
        expected = {contract["owner_run"], *contract["consumers"]}
        if declarations[contract["id"]] != expected:
            raise ArtifactError(
                f"contract {contract['id']} must be declared by exactly its owner "
                "and consumers"
            )
    return normalized_runs, normalized_contracts


def epic_run_sha256(record):
    return object_sha256({
        key: record[key]
        for key in ("id", "title", "spec", "depends_on", "contracts")
    })


def epic_plan_sha256(manifest):
    return object_sha256({
        "schema_version": manifest["schema_version"],
        "epic_id": manifest["epic_id"],
        "goal": manifest["goal"],
        "base_commit": manifest["base_commit"],
        "runs": {
            run_id: {
                key: record[key]
                for key in ("id", "title", "spec", "depends_on", "contracts")
            }
            for run_id, record in sorted(manifest["runs"].items())
        },
        "contracts": manifest["contracts"],
    })


def validate_epic_manifest(manifest, epic_id=None):
    top_level = {
        "schema_version", "epic_id", "goal", "base_commit", "runs",
        "contracts", "revision", "created_at", "updated_at",
    }
    if set(manifest) != top_level:
        raise ArtifactError("epic manifest has unknown or missing top-level fields")
    if manifest.get("schema_version") != 1:
        raise ArtifactError("unsupported epic schema")
    actual_id = safe_identifier(manifest.get("epic_id"), "epic id")
    if epic_id is not None and actual_id != epic_id:
        raise ArtifactError("epic manifest identity mismatch")
    if not isinstance(manifest.get("goal"), str) or not manifest["goal"].strip():
        raise ArtifactError("epic goal is missing")
    if not HEX_OBJECT.fullmatch(str(manifest.get("base_commit", ""))):
        raise ArtifactError("epic base_commit is invalid")
    revision = manifest.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ArtifactError("epic revision is invalid")
    if (not isinstance(manifest.get("created_at"), str)
            or not manifest["created_at"]
            or not isinstance(manifest.get("updated_at"), str)
            or not manifest["updated_at"]):
        raise ArtifactError("epic timestamps are invalid")
    runs = manifest.get("runs")
    contracts = manifest.get("contracts")
    if not isinstance(runs, dict) or not isinstance(contracts, dict):
        raise ArtifactError("epic plan is malformed")
    run_ids = set(runs)
    contract_ids = set(contracts)
    graph = {}
    for run_id, record in runs.items():
        safe_identifier(run_id, "epic run id")
        if not isinstance(record, dict) or record.get("id") != run_id:
            raise ArtifactError(f"invalid epic run record {run_id}")
        required = {"id", "title", "spec", "depends_on", "contracts", "status"}
        start = {
            "start_base_commit", "started_at", "plan_sha256", "run_sha256",
            "dependency_heads",
        }
        completion = {"finished_head", "completed_at", "run_state_sha256"}
        status = record.get("status")
        expected_by_status = {
            "planned": required,
            "active": required | start,
            "done": required | start | completion,
        }
        if status not in expected_by_status or set(record) != expected_by_status[status]:
            raise ArtifactError(f"invalid epic run status/evidence {run_id}")
        if (not isinstance(record.get("title"), str) or not record["title"].strip()
                or not isinstance(record.get("spec"), str) or not record["spec"].strip()):
            raise ArtifactError(f"invalid epic run title/spec {run_id}")
        dependencies = unique_strings(
            record.get("depends_on"), f"epic run {run_id} dependencies", run_ids
        )
        if run_id in dependencies:
            raise ArtifactError(f"epic dependency cycle at {run_id}")
        unique_strings(record.get("contracts"), f"epic run {run_id} contracts",
                       contract_ids)
        if status in {"active", "done"}:
            dependency_heads = record.get("dependency_heads")
            if (not HEX_OBJECT.fullmatch(str(record.get("start_base_commit", "")))
                    or not isinstance(record.get("started_at"), str)
                    or not record["started_at"]
                    or not re.fullmatch(r"[0-9a-f]{64}",
                                        str(record.get("plan_sha256", "")))
                    or not re.fullmatch(r"[0-9a-f]{64}",
                                        str(record.get("run_sha256", "")))
                    or not isinstance(dependency_heads, dict)
                    or set(dependency_heads) != set(dependencies)
                    or any(not HEX_OBJECT.fullmatch(str(head))
                           for head in dependency_heads.values())):
                raise ArtifactError(f"invalid epic start evidence {run_id}")
        if status == "done":
            if (not HEX_OBJECT.fullmatch(str(record.get("finished_head", "")))
                    or not isinstance(record.get("completed_at"), str)
                    or not record["completed_at"]
                    or not re.fullmatch(r"[0-9a-f]{64}",
                                        str(record.get("run_state_sha256", "")))):
                raise ArtifactError(f"invalid epic completion evidence {run_id}")
        graph[run_id] = dependencies
    for contract_id, record in contracts.items():
        safe_identifier(contract_id, "epic contract id")
        if (not isinstance(record, dict)
                or set(record) != {"id", "owner_run", "consumers", "summary"}
                or record.get("id") != contract_id
                or record.get("owner_run") not in run_ids
                or not isinstance(record.get("summary"), str)
                or not record["summary"].strip()):
            raise ArtifactError(f"invalid epic contract {contract_id}")
        unique_strings(record.get("consumers"),
                       f"epic contract {contract_id} consumers", run_ids)
        expected = {record["owner_run"], *record["consumers"]}
        declared = {
            run_id for run_id, run in runs.items()
            if contract_id in run["contracts"]
        }
        if declared != expected:
            raise ArtifactError(
                f"contract {contract_id} must be declared by exactly its owner "
                "and consumers"
            )
    assert_acyclic(graph)
    return manifest


def cmd_epic_init(args, repo):
    base, _ = git(["rev-parse", f"{args.base}^{{commit}}"], repo)
    with locked_epic(repo, args.epic) as directory:
        path = directory / "epic.json"
        if path.exists():
            raise ArtifactError(f"epic already exists: {args.epic}")
        manifest = {
            "schema_version": 1, "epic_id": args.epic,
            "goal": args.goal.strip(), "base_commit": base,
            "runs": {}, "contracts": {}, "revision": 1,
            "created_at": now(), "updated_at": now(),
        }
        if not manifest["goal"]:
            raise ArtifactError("epic goal cannot be empty")
        commit_epic(directory, manifest, {"event": "epic_created"})
    print(directory / "epic.json")


def cmd_epic_plan(args, repo):
    draft = read_json(Path(args.file).resolve())
    runs, contracts = normalize_epic_plan(draft)
    with locked_epic(repo, args.epic) as directory:
        path = directory / "epic.json"
        manifest = validate_epic_manifest(read_json(path), args.epic)
        if manifest["runs"] and not args.replace:
            raise ArtifactError("epic already has a plan; use --replace before any run completes")
        if any(record.get("status") != "planned" for record in manifest["runs"].values()):
            raise ArtifactError("cannot replace an epic plan after a run starts")
        manifest["runs"] = {record["id"]: record for record in runs}
        manifest["contracts"] = {record["id"]: record for record in contracts}
        manifest["revision"] += 1
        manifest["updated_at"] = now()
        commit_epic(directory, manifest, {
            "event": "epic_planned", "runs": sorted(manifest["runs"]),
        })
    print(path)


def epic_binding(repo, epic_id, run_id, base_commit, *, activate=True):
    safe_identifier(run_id, "run id")
    with locked_epic(repo, epic_id) as directory:
        path = directory / "epic.json"
        manifest = validate_epic_manifest(read_json(path), epic_id)
        record = manifest["runs"].get(run_id)
        if record is None:
            raise ArtifactError(f"run {run_id} is absent from epic {epic_id}")
        if record["status"] == "done":
            raise ArtifactError(f"epic run {run_id} is already done")
        plan_sha = epic_plan_sha256(manifest)
        run_sha = epic_run_sha256(record)
        if record["status"] == "active":
            if not activate:
                return {
                    "schema_version": 1, "epic_id": epic_id,
                    "run_id": run_id, "status": "active",
                    "base_commit": record["start_base_commit"],
                    "plan_sha256": plan_sha, "run_sha256": run_sha,
                    "dependency_heads": record["dependency_heads"],
                }
            if (record["start_base_commit"] != base_commit
                    or record["plan_sha256"] != plan_sha
                    or record["run_sha256"] != run_sha):
                raise ArtifactError(
                    f"epic run {run_id} already started from a different binding"
                )
            return {
                "id": epic_id, "run": run_id,
                "manifest": path.relative_to(repo).as_posix(),
                "manifest_sha256": object_sha256(manifest),
                "plan_sha256": plan_sha, "run_sha256": run_sha,
                "base_commit": base_commit,
                "dependency_heads": record["dependency_heads"],
                "revision": manifest["revision"], "checked_at": now(),
            }
        dependency_heads = {}
        for dependency in record["depends_on"]:
            dependency_record = manifest["runs"][dependency]
            if dependency_record["status"] != "done":
                raise ArtifactError(f"run {run_id} is blocked by epic dependency {dependency}")
            dependency_heads[dependency] = dependency_record["finished_head"]
        _, ancestor_status = git(
            ["merge-base", "--is-ancestor", manifest["base_commit"], base_commit],
            repo, check=False,
        )
        if ancestor_status:
            raise ArtifactError("run base commit does not descend from the epic base commit")
        for dependency, finished_head in dependency_heads.items():
            _, ancestor_status = git(
                ["merge-base", "--is-ancestor", finished_head, base_commit],
                repo, check=False,
            )
            if ancestor_status:
                raise ArtifactError(
                    f"run base commit does not contain finished head of {dependency}"
                )
        if not activate:
            return {
                "schema_version": 1, "epic_id": epic_id, "run_id": run_id,
                "status": "ready", "base_commit": base_commit,
                "plan_sha256": plan_sha, "run_sha256": run_sha,
                "dependency_heads": dependency_heads,
            }
        record.update({
            "status": "active", "start_base_commit": base_commit,
            "started_at": now(), "plan_sha256": plan_sha,
            "run_sha256": run_sha, "dependency_heads": dependency_heads,
        })
        manifest["revision"] += 1
        manifest["updated_at"] = now()
        commit_epic(directory, manifest, {
            "event": "epic_run_started", "run": run_id,
            "base_commit": base_commit,
        })
        return {
            "id": epic_id, "run": run_id,
            "manifest": path.relative_to(repo).as_posix(),
            "manifest_sha256": object_sha256(manifest),
            "plan_sha256": plan_sha, "run_sha256": run_sha,
            "base_commit": base_commit,
            "dependency_heads": dependency_heads,
            "revision": manifest["revision"], "checked_at": now(),
        }


def cmd_epic_ready(args, repo):
    base, _ = git(["rev-parse", f"{args.base}^{{commit}}"], repo)
    print(json.dumps(epic_binding(
        repo, args.epic, args.run, base, activate=False,
    ),
                     indent=2, sort_keys=True))


def cmd_epic_complete_run(args, repo):
    with locked_run_state(repo, args.run) as (_, state, run_state):
        if (state.get("run_id") != args.run or state.get("phase") != "DONE"
                or state.get("finished") is not True
                or not HEX_OBJECT.fullmatch(str(state.get("finished_head", "")))):
            raise ArtifactError(f"QTeam run {args.run} is not durably DONE")
        binding = state.get("epic")
        expected_manifest = f".agents/epics/{args.epic}/epic.json"
        required_binding = {
            "id", "run", "manifest", "manifest_sha256", "plan_sha256",
            "run_sha256", "base_commit", "dependency_heads", "revision",
            "checked_at",
        }
        if (not isinstance(binding, dict) or set(binding) != required_binding
                or binding.get("id") != args.epic
                or binding.get("run") != args.run
                or binding.get("manifest") != expected_manifest
                or any(not re.fullmatch(r"[0-9a-f]{64}", str(binding.get(field, "")))
                       for field in ("manifest_sha256", "plan_sha256", "run_sha256"))
                or not HEX_OBJECT.fullmatch(str(binding.get("base_commit", "")))
                or not isinstance(binding.get("dependency_heads"), dict)):
            raise ArtifactError(f"QTeam run {args.run} has no matching epic binding")
        run_state_sha = file_sha256(run_state)
        with locked_epic(repo, args.epic) as directory:
            path = directory / "epic.json"
            manifest = validate_epic_manifest(read_json(path), args.epic)
            record = manifest["runs"].get(args.run)
            if record is None:
                raise ArtifactError(f"run {args.run} is absent from epic {args.epic}")
            if record["status"] == "done":
                if record.get("finished_head") != state["finished_head"]:
                    raise ArtifactError("epic completion evidence conflicts with QTeam run")
                print(path)
                return
            if record["status"] != "active":
                raise ArtifactError(f"epic run {args.run} was never started")
            if (epic_plan_sha256(manifest) != binding["plan_sha256"]
                    or epic_run_sha256(record) != binding["run_sha256"]
                    or record["plan_sha256"] != binding["plan_sha256"]
                    or record["run_sha256"] != binding["run_sha256"]
                    or record["start_base_commit"] != binding["base_commit"]
                    or record["dependency_heads"] != binding["dependency_heads"]):
                raise ArtifactError("epic plan or run binding changed after start")
            for dependency, frozen_head in binding["dependency_heads"].items():
                dependency_record = manifest["runs"].get(dependency)
                if (dependency_record is None or dependency_record["status"] != "done"
                        or dependency_record["finished_head"] != frozen_head):
                    raise ArtifactError("epic dependency evidence changed after start")
                _, ancestor_status = git(
                    ["merge-base", "--is-ancestor", frozen_head,
                     binding["base_commit"]], repo, check=False,
                )
                if ancestor_status:
                    raise ArtifactError("epic dependency output is absent from run base")
            _, ancestor_status = git(
                ["merge-base", "--is-ancestor", binding["base_commit"],
                 state["finished_head"]], repo, check=False,
            )
            if ancestor_status:
                raise ArtifactError("finished run does not descend from its bound base")
            record["status"] = "done"
            record["finished_head"] = state["finished_head"]
            record["completed_at"] = now()
            record["run_state_sha256"] = run_state_sha
            manifest["revision"] += 1
            manifest["updated_at"] = now()
            commit_epic(directory, manifest, {
                "event": "epic_run_completed", "run": args.run,
                "finished_head": state["finished_head"],
            })
    print(path)


def cmd_epic_status(args, repo):
    with locked_epic(repo, args.epic) as directory:
        manifest = validate_epic_manifest(read_json(directory / "epic.json"), args.epic)
        ready = []
        blocked = {}
        for run_id, record in manifest["runs"].items():
            if record["status"] in {"active", "done"}:
                continue
            blockers = [dependency for dependency in record["depends_on"]
                        if manifest["runs"][dependency]["status"] != "done"]
            if blockers:
                blocked[run_id] = blockers
            else:
                ready.append(run_id)
        payload = {
            "epic_id": args.epic, "revision": manifest["revision"],
            "ready": sorted(ready), "blocked": blocked,
            "done": sorted(run_id for run_id, record in manifest["runs"].items()
                           if record["status"] == "done"),
            "active": sorted(run_id for run_id, record in manifest["runs"].items()
                             if record["status"] == "active"),
            "next_unblocked": sorted(ready)[0] if ready else None,
        }
    print(json.dumps(payload, indent=2, sort_keys=True))


def _git_blob(repo, path, base=None):
    relative = path.relative_to(repo).as_posix()
    if base is not None:
        blob, status = git(["rev-parse", f"{base}:{relative}"], repo, check=False)
        if status or not HEX_OBJECT.fullmatch(blob):
            raise ArtifactError(f"index source is not tracked at base commit: {relative}")
        return blob
    blob, status = git(["hash-object", f"--path={relative}", str(path)], repo, check=False)
    if status or not HEX_OBJECT.fullmatch(blob):
        raise ArtifactError(f"cannot hash index source: {relative}")
    return blob


def cmd_index_seal(args, repo):
    draft = read_json(Path(args.file).resolve())
    if set(draft) != {"components", "external_resources"}:
        raise ArtifactError("index draft must contain exactly components and external_resources")
    components = draft["components"]
    external = draft["external_resources"]
    if not isinstance(components, list) or not components:
        raise ArtifactError("code index needs at least one component")
    if not isinstance(external, list):
        raise ArtifactError("external_resources must be an array")
    _unique_ids(components, "component")
    _unique_ids(external, "external resource")
    base, _ = git(["rev-parse", f"{args.base}^{{commit}}"], repo)
    sealed_components = []
    for component in components:
        required = {"id", "summary", "sources", "symbols", "contracts"}
        if set(component) != required:
            raise ArtifactError(f"component {component.get('id')} has unknown or missing fields")
        if not isinstance(component["summary"], str) or not component["summary"].strip():
            raise ArtifactError(f"component {component['id']} needs a summary")
        source_values = unique_strings(
            component["sources"], f"component {component['id']} sources"
        )
        if not source_values:
            raise ArtifactError(f"component {component['id']} needs unique sources")
        sources = []
        for raw in source_values:
            path = repo_path(repo, raw)
            base_blob = _git_blob(repo, path, base)
            current_blob = _git_blob(repo, path)
            if current_blob != base_blob:
                raise ArtifactError(
                    f"index source differs from base commit: {path.relative_to(repo)}"
                )
            sources.append({
                "path": path.relative_to(repo).as_posix(), "blob": base_blob,
            })
        symbols = unique_strings(
            component["symbols"], f"component {component['id']} symbols"
        )
        contracts = unique_strings(
            component["contracts"], f"component {component['id']} contracts"
        )
        sealed_components.append({
            "id": component["id"], "summary": component["summary"].strip(),
            "sources": sorted(sources, key=lambda item: item["path"]),
            "symbols": sorted(symbols), "contracts": sorted(contracts),
        })
    for resource in external:
        allowed_fields = {"id", "url", "evidence", "version", "verified_at"}
        if (set(resource) - allowed_fields
                or not {"id", "url", "evidence"}.issubset(resource)):
            raise ArtifactError(
                f"external resource {resource.get('id')} has unknown or missing fields"
            )
        if not isinstance(resource.get("url"), str) or not resource["url"].startswith(("https://", "http://")):
            raise ArtifactError(f"external resource {resource.get('id')} needs an http(s) URL")
        if not isinstance(resource.get("evidence"), str) or not resource["evidence"].strip():
            raise ArtifactError(f"external resource {resource.get('id')} needs evidence")
        if any(field in resource and not isinstance(resource[field], str)
               for field in ("version", "verified_at")):
            raise ArtifactError(
                f"external resource {resource.get('id')} version/verified_at must be strings"
            )
    payload = {
        "schema_version": 1, "base_commit": base, "generated_at": now(),
        "components": sorted(sealed_components, key=lambda item: item["id"]),
        "external_resources": sorted(external, key=lambda item: item["id"]),
    }
    output = repo_path(repo, args.output, must_exist=False)
    atomic_json(output, payload)
    print(output)


def validate_index_artifact(artifact):
    if artifact.get("schema_version") != 1:
        raise ArtifactError("unsupported code index schema")
    if set(artifact) != {
        "schema_version", "base_commit", "generated_at", "components",
        "external_resources",
    }:
        raise ArtifactError("code index has unknown or missing fields")
    base = artifact.get("base_commit")
    if not isinstance(base, str) or not HEX_OBJECT.fullmatch(base):
        raise ArtifactError("code index has invalid base_commit")
    if not isinstance(artifact.get("generated_at"), str) or not artifact["generated_at"]:
        raise ArtifactError("code index has invalid generated_at")
    components = artifact.get("components")
    external = artifact.get("external_resources")
    if not isinstance(components, list) or not components or not isinstance(external, list):
        raise ArtifactError("code index components/resources are malformed")
    _unique_ids(components, "component")
    _unique_ids(external, "external resource")
    for component in components:
        if set(component) != {"id", "summary", "sources", "symbols", "contracts"}:
            raise ArtifactError(f"invalid sealed component {component.get('id')}")
        if not isinstance(component.get("summary"), str) or not component["summary"]:
            raise ArtifactError(f"invalid component summary {component.get('id')}")
        sources = component.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ArtifactError(f"invalid component sources {component.get('id')}")
        paths = []
        for source in sources:
            if (not isinstance(source, dict) or set(source) != {"path", "blob"}
                    or not isinstance(source.get("path"), str) or not source["path"]
                    or not HEX_OBJECT.fullmatch(str(source.get("blob", "")))):
                raise ArtifactError(f"invalid sealed source in {component.get('id')}")
            paths.append(source["path"])
        unique_strings(paths, f"component {component['id']} sealed source paths")
        unique_strings(component.get("symbols"), f"component {component['id']} symbols")
        unique_strings(component.get("contracts"), f"component {component['id']} contracts")
    for resource in external:
        allowed_fields = {"id", "url", "evidence", "version", "verified_at"}
        if (not isinstance(resource, dict) or set(resource) - allowed_fields
                or not {"id", "url", "evidence"}.issubset(resource)
                or not isinstance(resource.get("url"), str)
                or not resource["url"].startswith(("http://", "https://"))
                or not isinstance(resource.get("evidence"), str)
                or not resource["evidence"]
                or any(field in resource and not isinstance(resource[field], str)
                       for field in ("version", "verified_at"))):
            raise ArtifactError(f"invalid sealed external resource {resource.get('id')}")
    return artifact


def check_index(repo, artifact):
    artifact = validate_index_artifact(artifact)
    base = artifact["base_commit"]
    _, ancestor_status = git(["merge-base", "--is-ancestor", base, "HEAD"], repo, check=False)
    stale = []
    if ancestor_status:
        stale.append({"path": None, "reason": "base commit is not an ancestor of HEAD"})
    for component in artifact["components"]:
        for source in component.get("sources", []):
            raw = source.get("path")
            expected = source.get("blob")
            try:
                path = repo_path(repo, raw)
                base_blob = _git_blob(repo, path, base)
                actual = _git_blob(repo, path)
            except ArtifactError as exc:
                stale.append({"path": raw, "reason": str(exc)})
                continue
            if base_blob != expected:
                stale.append({
                    "path": raw, "reason": "sealed blob differs from base commit blob",
                    "expected": base_blob, "actual": expected,
                })
                continue
            if actual != expected:
                stale.append({
                    "path": raw, "reason": "source blob changed",
                    "expected": expected, "actual": actual,
                })
    return {
        "schema_version": 1, "status": "stale" if stale else "fresh",
        "base_commit": base, "stale_sources": stale,
    }


def cmd_index_check(args, repo):
    report = check_index(repo, read_json(repo_path(repo, args.file)))
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["stale_sources"]:
        raise SystemExit(1)


def _validate_drift_change(change):
    required = {"id", "layer", "original", "actual", "reason", "proposal", "decision_id"}
    if not isinstance(change, dict) or set(change) != required:
        raise ArtifactError("every drift change must contain the exact proposal fields")
    safe_identifier(change["id"], "drift id")
    safe_identifier(change["decision_id"], "drift decision id")
    if change["layer"] not in {"requirements", "design", "tasks"}:
        raise ArtifactError(f"invalid drift layer for {change['id']}")
    for field in ("original", "actual", "reason", "proposal"):
        if not isinstance(change[field], str) or not change[field].strip():
            raise ArtifactError(f"drift change {change['id']} needs {field}")


def _integration_head(repo, state):
    branch = state.get("integration_branch")
    if not isinstance(branch, str) or not branch:
        raise ArtifactError("QTeam run has no integration branch")
    _, valid = git(["check-ref-format", "--branch", branch], repo, check=False)
    if valid:
        raise ArtifactError("QTeam run has an invalid integration branch")
    head, status = git(
        ["rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}"],
        repo, check=False,
    )
    if status or not HEX_OBJECT.fullmatch(head):
        raise ArtifactError("QTeam integration branch does not exist")
    provenance_head = state.get("integration_provenance_head")
    if head != provenance_head:
        raise ArtifactError("QTeam integration head differs from durable provenance")
    return head


def _decision_binding(record):
    fields = {"id", "question", "authority", "scope", "subject", "created_at"}
    if not fields.issubset(record):
        raise ArtifactError("drift decision has incomplete stable identity")
    return {key: record[key] for key in sorted(fields)}


def _load_drift_decision(repo, run_dir, state, decision_id, expected_change=None):
    safe_identifier(decision_id, "drift decision id")
    path = repo_path(
        repo, run_dir.relative_to(repo) / "decisions" / f"{decision_id}.json"
    )
    record = read_json(path)
    summary = state.get("decisions", {}).get(decision_id)
    scope = record.get("scope")
    if (record.get("id") != decision_id
            or record.get("authority") != "user"
            or not isinstance(scope, dict)
            or scope.get("kind") != "action"
            or scope.get("targets") != ["finish"]
            or not isinstance(summary, dict)
            or summary.get("status") != record.get("status")):
        raise ArtifactError(
            f"drift decision {decision_id} must be a user-owned finish gate"
        )
    subject = record.get("subject")
    if expected_change is not None and subject != {
            "kind": "spec-drift-change", "sha256": expected_change,
    }:
        raise ArtifactError(
            f"drift decision {decision_id} is not bound to its exact change"
        )
    if expected_change is not None and summary.get("subject") != subject:
        raise ArtifactError(
            f"drift decision {decision_id} state summary changed"
        )
    status = record.get("status")
    if status == "resolved":
        resolution = record.get("resolution")
        if (not isinstance(resolution, dict)
                or resolution.get("outcome") not in {"allow", "deny"}):
            raise ArtifactError(f"drift decision {decision_id} has invalid resolution")
    elif status != "open":
        raise ArtifactError(f"drift decision {decision_id} is not actionable")
    return record, path


def cmd_drift_seal(args, repo):
    draft = read_json(Path(args.file).resolve())
    allowed = {"summary", "changes", "no_drift_evidence"}
    if not set(draft).issubset(allowed) or not {"summary", "changes"}.issubset(draft):
        raise ArtifactError("drift draft has unknown or missing fields")
    if not isinstance(draft["summary"], str) or not draft["summary"].strip():
        raise ArtifactError("drift report needs a summary")
    changes = draft["changes"]
    if not isinstance(changes, list):
        raise ArtifactError("drift changes must be an array")
    _unique_ids(changes, "drift change")
    decision_ids = []
    for change in changes:
        _validate_drift_change(change)
        decision_ids.append(change["decision_id"])
    if len(decision_ids) != len(set(decision_ids)):
        raise ArtifactError("drift decision ids must be unique")
    no_drift_evidence = draft.get("no_drift_evidence")
    if no_drift_evidence is not None and not isinstance(no_drift_evidence, str):
        raise ArtifactError("no_drift_evidence must be a string or null")
    if (not changes and (not isinstance(no_drift_evidence, str)
                         or not no_drift_evidence.strip())):
        raise ArtifactError("an empty drift report needs no_drift_evidence")
    source_values = unique_strings(args.source, "drift source paths")
    sources = []
    for raw in source_values:
        path = repo_path(repo, raw)
        sources.append({
            "path": path.relative_to(repo).as_posix(), "sha256": file_sha256(path),
        })
    unique_strings(
        [source["path"] for source in sources], "normalized drift source paths"
    )
    if not sources:
        raise ArtifactError("drift report needs at least one --source")
    head, _ = git(["rev-parse", f"{args.head}^{{commit}}"], repo)
    with locked_run_state(repo, args.run) as (run_dir, state, state_path):
        if state.get("run_id") != args.run:
            raise ArtifactError("QTeam run identity mismatch")
        if (state.get("finished") or state.get("phase") == "DONE"
                or state.get("publication_seal")):
            raise ArtifactError("finished or publication-sealed run cannot add drift")
        integration_head = _integration_head(repo, state)
        if head != integration_head:
            raise ArtifactError("requested head is not the QTeam integration head")
        decisions = []
        decision_writes = {}
        changes_by_decision = {
            change["decision_id"]: change for change in changes
        }
        for decision_id in decision_ids:
            record, decision_path = _load_drift_decision(
                repo, run_dir, state, decision_id
            )
            if record["status"] != "open" or record.get("subject") is not None:
                raise ArtifactError(
                    f"drift decision {decision_id} must be unbound and open before seal"
                )
            change_sha = object_sha256(changes_by_decision[decision_id])
            record["subject"] = {
                "kind": "spec-drift-change", "sha256": change_sha,
            }
            record["updated_at"] = now()
            state["decisions"][decision_id]["subject"] = record["subject"]
            decision_writes[decision_path] = record
            decisions.append({
                "id": decision_id,
                "change_sha256": change_sha,
                "binding_sha256": object_sha256(_decision_binding(record)),
            })
        payload = {
            "schema_version": 1, "run_id": safe_identifier(args.run, "run id"),
            "head_sha": head, "sources": sources,
            "summary": draft["summary"].strip(),
            "changes": [{**change, "status": "pending"} for change in changes],
            "decisions": sorted(decisions, key=lambda item: item["id"]),
            "no_drift_evidence": draft.get("no_drift_evidence"),
            "approval_required": bool(changes), "apply_status": "proposal-only",
            "created_at": now(),
        }
        output = repo_path(repo, args.output, must_exist=False)
        expected_output = run_dir / "spec-drift.json"
        if output != expected_output:
            raise ArtifactError(
                f"drift output must be {expected_output.relative_to(repo)}"
            )
        atomic_json(output, payload)
        report_sha = file_sha256(output)
        state["spec_drift"] = {
            "report": output.relative_to(repo).as_posix(),
            "report_sha256": report_sha, "head_sha": head,
            "decision_ids": sorted(decision_ids), "sealed_at": now(),
        }
        state["updated_at"] = now()
        commit_run_binding(run_dir, {state_path: state, **decision_writes}, {
            "event": "spec_drift_sealed", "head_sha": head,
            "report_sha256": report_sha,
            "decision_ids": sorted(decision_ids),
        })
    print(output)


@contextmanager
def _drift_run_context(repo, run_id, run_context):
    if run_context is not None:
        yield run_context
        return
    with locked_run_state(repo, run_id) as (run_dir, state, _):
        yield run_dir, state


def check_drift(repo, artifact, artifact_path, run_context=None):
    required = {
        "schema_version", "run_id", "head_sha", "sources", "summary",
        "changes", "decisions", "no_drift_evidence", "approval_required",
        "apply_status", "created_at",
    }
    if set(artifact) != required:
        raise ArtifactError("spec drift report has unknown or missing fields")
    if artifact.get("schema_version") != 1 or artifact.get("apply_status") != "proposal-only":
        raise ArtifactError("unsupported spec drift report")
    safe_identifier(artifact.get("run_id"), "drift run id")
    if (not HEX_OBJECT.fullmatch(str(artifact.get("head_sha", "")))
            or not isinstance(artifact.get("summary"), str) or not artifact["summary"]
            or not isinstance(artifact.get("created_at"), str) or not artifact["created_at"]):
        raise ArtifactError("spec drift report identity/head is invalid")
    sources = artifact.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ArtifactError("spec drift report sources are invalid")
    source_paths = []
    for source in sources:
        if (not isinstance(source, dict) or set(source) != {"path", "sha256"}
                or not isinstance(source.get("path"), str) or not source["path"]
                or not re.fullmatch(r"[0-9a-f]{64}",
                                    str(source.get("sha256", "")))):
            raise ArtifactError("spec drift report has an invalid sealed source")
        source_paths.append(source["path"])
    unique_strings(source_paths, "spec drift source paths")
    changes = artifact.get("changes")
    if not isinstance(changes, list):
        raise ArtifactError("spec drift changes are invalid")
    change_ids = []
    decision_ids = []
    for change in changes:
        if not isinstance(change, dict) or change.get("status") != "pending":
            raise ArtifactError("spec drift changes must remain pending proposals")
        draft_change = {key: value for key, value in change.items() if key != "status"}
        _validate_drift_change(draft_change)
        change_ids.append(change["id"])
        decision_ids.append(change["decision_id"])
    unique_strings(change_ids, "spec drift change ids")
    unique_strings(decision_ids, "spec drift decision ids")
    decisions = artifact.get("decisions")
    if not isinstance(decisions, list):
        raise ArtifactError("spec drift decision bindings are invalid")
    sealed_decisions = {}
    for decision in decisions:
        if (not isinstance(decision, dict)
                or set(decision) != {
                    "id", "change_sha256", "binding_sha256",
                }):
            raise ArtifactError("spec drift decision binding is malformed")
        decision_id = safe_identifier(decision.get("id"), "drift decision id")
        if (decision_id in sealed_decisions
                or not re.fullmatch(r"[0-9a-f]{64}",
                                    str(decision.get("change_sha256", "")))
                or not re.fullmatch(r"[0-9a-f]{64}",
                                    str(decision.get("binding_sha256", "")))):
            raise ArtifactError("spec drift decision binding is invalid")
        sealed_decisions[decision_id] = decision
    if set(sealed_decisions) != set(decision_ids):
        raise ArtifactError("spec drift decisions do not match change proposals")
    if artifact.get("approval_required") is not bool(changes):
        raise ArtifactError("spec drift approval contract was modified")
    no_drift_evidence = artifact.get("no_drift_evidence")
    if no_drift_evidence is not None and not isinstance(no_drift_evidence, str):
        raise ArtifactError("spec drift no_drift_evidence must be a string or null")
    if (not changes and (not isinstance(no_drift_evidence, str)
                         or not no_drift_evidence.strip())):
        raise ArtifactError("empty spec drift report lacks no_drift_evidence")
    stale = []
    pending = []
    rejected = []
    with _drift_run_context(repo, artifact["run_id"], run_context) as (run_dir, state):
        if state.get("run_id") != artifact["run_id"]:
            raise ArtifactError("QTeam run identity mismatch")
        binding = state.get("spec_drift")
        required_binding = {
            "report", "report_sha256", "head_sha", "decision_ids", "sealed_at",
        }
        report_path = repo_path(repo, artifact_path)
        if (not isinstance(binding, dict) or set(binding) != required_binding
                or binding.get("report") != report_path.relative_to(repo).as_posix()
                or binding.get("report_sha256") != file_sha256(report_path)
                or binding.get("head_sha") != artifact["head_sha"]
                or binding.get("decision_ids") != sorted(decision_ids)
                or not isinstance(binding.get("sealed_at"), str)
                or not binding["sealed_at"]):
            raise ArtifactError("spec drift report differs from its QTeam run binding")
        try:
            current_head = _integration_head(repo, state)
        except ArtifactError as exc:
            stale.append({"path": None, "reason": str(exc)})
        else:
            if current_head != artifact.get("head_sha"):
                stale.append({"path": None, "reason": "integration head changed"})
        for source in sources:
            raw = source.get("path")
            try:
                path = repo_path(repo, raw)
                actual = file_sha256(path)
            except ArtifactError as exc:
                stale.append({"path": raw, "reason": str(exc)})
                continue
            if actual != source.get("sha256"):
                stale.append({"path": raw, "reason": "source content changed"})
        changes_by_decision = {
            change["decision_id"]: {
                key: value for key, value in change.items() if key != "status"
            }
            for change in changes
        }
        for decision_id, sealed_decision in sealed_decisions.items():
            expected_change = object_sha256(changes_by_decision[decision_id])
            if sealed_decision["change_sha256"] != expected_change:
                stale.append({
                    "path": None,
                    "reason": f"drift change {decision_id} differs from decision binding",
                })
                continue
            try:
                record, _ = _load_drift_decision(
                    repo, run_dir, state, decision_id, expected_change
                )
            except ArtifactError as exc:
                stale.append({"path": None, "reason": str(exc)})
                continue
            if (object_sha256(_decision_binding(record))
                    != sealed_decision["binding_sha256"]):
                stale.append({
                    "path": None,
                    "reason": f"drift decision {decision_id} identity changed",
                })
            elif record["status"] == "open":
                pending.append(decision_id)
            elif record["resolution"]["outcome"] == "deny":
                rejected.append(decision_id)
    if stale:
        status = "stale"
    elif rejected:
        status = "rejected"
    elif pending:
        status = "pending-approval"
    elif changes:
        status = "approved"
    else:
        status = "fresh"
    return {
        "schema_version": 1, "status": status, "stale": stale,
        "pending_decisions": sorted(pending),
        "rejected_decisions": sorted(rejected),
    }


def cmd_drift_check(args, repo):
    path = repo_path(repo, args.file)
    report = check_drift(repo, read_json(path), path)
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["status"] not in {"fresh", "approved"}:
        raise SystemExit(1)


def require_bound_drift(repo, run_dir, state):
    binding = state.get("spec_drift")
    if binding is None:
        return
    if not isinstance(binding, dict) or not isinstance(binding.get("report"), str):
        raise ArtifactError("QTeam run has an invalid spec drift binding")
    path = repo_path(repo, binding["report"])
    report = check_drift(
        repo, read_json(path), path, run_context=(run_dir, state)
    )
    if report["status"] not in {"fresh", "approved"}:
        raise ArtifactError(
            f"spec drift gate is {report['status']}; run drift-check before finish"
        )


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("lint")
    p.add_argument("--kind", choices=("spec", "tickets"), required=True)
    p.add_argument("--file", action="append", default=[], required=True)
    p.add_argument("--output")
    p.set_defaults(func=cmd_lint)
    p = sub.add_parser("epic-init")
    p.add_argument("--epic", required=True)
    p.add_argument("--goal", required=True)
    p.add_argument("--base", default="HEAD")
    p.set_defaults(func=cmd_epic_init)
    p = sub.add_parser("epic-plan")
    p.add_argument("--epic", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--replace", action="store_true")
    p.set_defaults(func=cmd_epic_plan)
    p = sub.add_parser("epic-ready")
    p.add_argument("--epic", required=True)
    p.add_argument("--run", required=True)
    p.add_argument("--base", default="HEAD")
    p.set_defaults(func=cmd_epic_ready)
    p = sub.add_parser("epic-complete-run")
    p.add_argument("--epic", required=True)
    p.add_argument("--run", required=True)
    p.set_defaults(func=cmd_epic_complete_run)
    p = sub.add_parser("epic-status")
    p.add_argument("--epic", required=True)
    p.set_defaults(func=cmd_epic_status)
    p = sub.add_parser("index-seal")
    p.add_argument("--file", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--base", default="HEAD")
    p.set_defaults(func=cmd_index_seal)
    p = sub.add_parser("index-check")
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_index_check)
    p = sub.add_parser("drift-seal")
    p.add_argument("--run", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--source", action="append", default=[], required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--head", default="HEAD")
    p.set_defaults(func=cmd_drift_seal)
    p = sub.add_parser("drift-check")
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_drift_check)
    return ap


def main():
    args = parser().parse_args()
    repo = repo_root()
    try:
        args.func(args, repo)
    except ArtifactError as exc:
        raise SystemExit(f"error: {exc}")


if __name__ == "__main__":
    main()
