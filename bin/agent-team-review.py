#!/usr/bin/env python3
"""Create immutable review packets and maintain machine-readable findings."""

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


AXES = {"spec", "standards", "risk"}
SEVERITIES = {"P0", "P1", "P2", "P3"}


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def git(args, cwd):
    res = subprocess.run(["git", *args], cwd=cwd, text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode:
        raise SystemExit(res.stderr.strip())
    return res.stdout.strip()


def repo_root():
    return Path(git(["rev-parse", "--show-toplevel"], Path.cwd())).resolve()


def run_dir(repo, value):
    path = Path(value)
    if not path.is_absolute():
        path = repo / (path if "/" in value else Path(".agents/runs") / value)
    path = path.resolve()
    if path.parent != (repo / ".agents/runs").resolve():
        raise SystemExit("error: run must be a direct child of .agents/runs")
    return path


def read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"error: missing {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"error: invalid JSON in {path}: {exc}")


def write(path, value):
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


def snapshot_sources(repo, run, sources):
    """Freeze review inputs so a later edit cannot change what was reviewed."""
    records = []
    snapshot_dir = run / "reviews" / "sources"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for source in sources:
        source_path = ((repo / source).resolve() if not Path(source).is_absolute()
                       else Path(source).resolve())
        data = source_path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        snapshot = snapshot_dir / f"{digest}.source"
        if snapshot.exists():
            if snapshot.read_bytes() != data:
                raise SystemExit(f"error: review snapshot hash collision: {snapshot}")
        else:
            fd, tmp = tempfile.mkstemp(prefix=f".{digest}.", dir=snapshot_dir)
            try:
                with os.fdopen(fd, "wb") as out:
                    out.write(data)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(tmp, snapshot)
                fd_dir = os.open(snapshot_dir, os.O_RDONLY)
                try:
                    os.fsync(fd_dir)
                finally:
                    os.close(fd_dir)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        records.append({
            "source": str(source_path.relative_to(repo)),
            "sha256": digest,
            "snapshot": str(snapshot.relative_to(run)),
        })
    return records


def review_result(path, expected_axis):
    result_path = Path(path).resolve()
    if result_path.is_symlink() or not result_path.is_file():
        raise SystemExit("error: --result must be a regular JSON file")
    result = read(result_path)
    if not isinstance(result, dict):
        raise SystemExit("error: review result must be a JSON object")
    if result.get("axis") != expected_axis:
        raise SystemExit("error: review result axis does not match ledger")
    if result.get("verdict") != "pass":
        raise SystemExit("error: review result verdict must be pass before completion")
    if not isinstance(result.get("findings"), list):
        raise SystemExit("error: review result findings must be an array")
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    return result, hashlib.sha256(encoded).hexdigest()


def ledger_path(run, wave, axis, iteration=1):
    suffix = "" if iteration == 1 else f"-r{iteration}"
    return run / "reviews" / f"wave-{wave}-{axis}{suffix}.json"


def checked_ledger_path(repo, value):
    path = Path(value).resolve()
    runs = (repo / ".agents/runs").resolve()
    if path.parent.name != "reviews" or path.parent.parent.parent != runs:
        raise SystemExit("error: ledger must be under .agents/runs/<run>/reviews")
    return path


@contextmanager
def review_lock(review_dir):
    review_dir.mkdir(parents=True, exist_ok=True)
    with (review_dir / ".ledger.lock").open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def cmd_create(args, repo, run):
    if args.axis not in AXES:
        raise SystemExit("error: invalid review axis")
    if args.iteration < 1:
        raise SystemExit("error: review iteration must be >= 1")
    if args.axis == "spec" and not args.spec_source:
        raise SystemExit("error: spec review requires at least one --spec-source")
    if args.axis in {"standards", "risk"} and not args.standards_source:
        raise SystemExit(f"error: {args.axis} review requires at least one --standards-source")
    for source in [*args.spec_source, *args.standards_source]:
        source_path = (repo / source).resolve() if not Path(source).is_absolute() else Path(source).resolve()
        if repo not in source_path.parents or not source_path.is_file():
            raise SystemExit(f"error: review source must be a repository file: {source}")
    for source in args.digest_source:
        source_path = (repo / source).resolve() if not Path(source).is_absolute() else Path(source).resolve()
        if repo not in source_path.parents or not source_path.is_file():
            raise SystemExit(f"error: digest source must be a repository file: {source}")
    base = git(["rev-parse", args.base], repo)
    head = git(["rev-parse", args.head], repo)
    merge_base = git(["merge-base", base, head], repo)
    path = ledger_path(run, args.wave, args.axis, args.iteration)
    with review_lock(run / "reviews"):
        spec_sources = snapshot_sources(repo, run, args.spec_source)
        standards_sources = snapshot_sources(repo, run, args.standards_source)
        digest_sources = snapshot_sources(repo, run, args.digest_source)
    packet = {
        "schema_version": 1,
        "run_id": run.name,
        "wave": args.wave,
        "axis": args.axis,
        "iteration": args.iteration,
        "scope": args.scope,
        "base_sha": base,
        "head_sha": head,
        "merge_base_sha": merge_base,
        "diff_range": f"{base}...{head}",
        "commits": git(["log", "--format=%H", f"{base}..{head}"], repo).splitlines(),
        "spec_sources": spec_sources,
        "standards_sources": standards_sources,
        "digest_sources": digest_sources,
        "created_at": now(),
    }
    with review_lock(path.parent):
        if path.exists():
            old = read(path)
            old_packet = old.get("packet", {})
            stable_keys = [key for key in packet if key != "created_at"]
            if any(old_packet.get(key) != packet[key] for key in stable_keys):
                raise SystemExit(f"error: review packet already exists with different immutable inputs: {path}")
            print(path)
            return
        write(path, {"packet": packet, "findings": [], "completed_at": None})
    print(path)


def cmd_add(args, repo, _run):
    path = checked_ledger_path(repo, args.ledger)
    if args.severity not in SEVERITIES:
        raise SystemExit("error: severity must be P0, P1, P2, or P3")
    finding = {
        "id": args.id or f"F-{uuid.uuid4().hex[:8]}",
        "severity": args.severity,
        "title": args.title,
        "body": args.body,
        "file": args.file,
        "line": args.line,
        "owner": args.owner,
        "reviewer": args.reviewer,
        "status": "open",
        "created_at": now(),
        "resolution": None,
        "evidence": None,
    }
    with review_lock(path.parent):
        ledger = read(path)
        if ledger.get("completed_at"):
            raise SystemExit("error: completed review ledger is immutable")
        if any(item["id"] == finding["id"] for item in ledger["findings"]):
            raise SystemExit(f"error: duplicate finding id {finding['id']}")
        ledger["findings"].append(finding)
        write(path, ledger)
    print(finding["id"])


def cmd_resolve(args, repo, _run):
    path = checked_ledger_path(repo, args.ledger)
    with review_lock(path.parent):
        ledger = read(path)
        if ledger.get("completed_at"):
            raise SystemExit("error: completed review ledger is immutable")
        matches = [item for item in ledger["findings"] if item["id"] == args.id]
        if not matches:
            raise SystemExit(f"error: finding not found: {args.id}")
        item = matches[0]
        if item.get("status") != "open":
            raise SystemExit(f"error: finding {args.id} is already {item.get('status')}")
        item["status"] = "resolved" if not args.invalid else "invalid"
        item["resolution"] = args.resolution
        item["evidence"] = args.evidence
        item["reviewer"] = args.reviewer
        item["resolved_at"] = now()
        write(path, ledger)
    print(item["status"])


def cmd_complete(args, repo, _run):
    path = checked_ledger_path(repo, args.ledger)
    with review_lock(path.parent):
        ledger = read(path)
        if ledger.get("completed_at"):
            print("complete")
            return
        open_items = [item["id"] for item in ledger["findings"] if item["status"] == "open"]
        if open_items:
            raise SystemExit(f"error: unresolved findings: {', '.join(open_items)}")
        result, result_sha = review_result(args.result, ledger["packet"]["axis"])
        result_ids = {item.get("id") for item in result["findings"] if isinstance(item, dict)}
        ledger_ids = {item.get("id") for item in ledger["findings"]}
        unknown = sorted(item for item in result_ids - ledger_ids if item)
        if unknown:
            raise SystemExit("error: result contains findings absent from ledger: "
                             + ", ".join(unknown))
        ledger["completed_at"] = now()
        ledger["completed_by"] = args.reviewer
        ledger["attestation"] = {
            "reviewer": args.reviewer,
            "session_id": args.session_id,
            "result_sha256": result_sha,
            "result": result,
        }
        write(path, ledger)
    print("complete")


def cmd_check(args, repo, run):
    axes = ["spec", "standards"] + (["risk"] if args.require_risk else [])
    errors = []
    attestations = {}
    for axis in axes:
        candidates = []
        with review_lock(run / "reviews"):
            for path in (run / "reviews").glob(f"wave-{args.wave}-{axis}*.json"):
                ledger = read(path)
                packet = ledger.get("packet", {})
                if packet.get("axis") == axis and packet.get("wave") == args.wave:
                    candidates.append((packet.get("iteration", 1), path, ledger))
        if not candidates:
            errors.append(f"missing {axis} ledger")
            continue
        _iteration, path, ledger = max(candidates, key=lambda item: item[0])
        packet = ledger.get("packet", {})
        if args.head and packet.get("head_sha") != git(["rev-parse", args.head], repo):
            errors.append(f"{axis} ledger head is stale")
        open_items = [item["id"] for _, _, candidate in candidates
                      for item in candidate.get("findings", []) if item.get("status") == "open"]
        if open_items:
            errors.append(f"{axis} unresolved: {', '.join(open_items)}")
        if not ledger.get("completed_at"):
            errors.append(f"{axis} review not marked complete")
        attestation = ledger.get("attestation", {})
        result = attestation.get("result", {})
        if (not attestation.get("reviewer") or not attestation.get("session_id")
                or result.get("axis") != axis or result.get("verdict") != "pass"):
            errors.append(f"{axis} review has no valid independent attestation")
        else:
            attestations[axis] = attestation
    if all(axis in attestations for axis in ("spec", "standards")):
        if attestations["spec"]["session_id"] == attestations["standards"]["session_id"]:
            errors.append("spec and standards reviews must use distinct sessions")
        if attestations["spec"]["reviewer"] == attestations["standards"]["reviewer"]:
            errors.append("spec and standards reviews must use distinct reviewers")
    if errors:
        print("review gate: FAIL")
        for error in errors:
            print(f"  - {error}")
        raise SystemExit(1)
    print("review gate: PASS")
    state_tool = Path(__file__).with_name("agent-team-state.py")
    if not state_tool.exists():
        state_tool = repo / ".codex/bin/agent-team-state"
    subprocess.run(
        [sys.executable, str(state_tool), "--run", str(run), "reviews-checked",
         "--head", args.head, *(["--require-risk"] if args.require_risk else [])],
        cwd=repo, check=True,
    )


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", help="run id/path (required by create/check)")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create")
    p.add_argument("--wave", type=int, required=True)
    p.add_argument("--axis", required=True)
    p.add_argument("--iteration", type=int, default=1)
    p.add_argument("--scope", choices=("wave", "fix", "final"), default="wave")
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--spec-source", action="append", default=[])
    p.add_argument("--standards-source", action="append", default=[])
    p.add_argument("--digest-source", action="append", default=[])
    p.set_defaults(func=cmd_create)
    p = sub.add_parser("add")
    p.add_argument("--ledger", required=True)
    p.add_argument("--id")
    p.add_argument("--severity", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--body", required=True)
    p.add_argument("--file")
    p.add_argument("--line", type=int)
    p.add_argument("--owner")
    p.add_argument("--reviewer", required=True,
                   help="identity of the read-only reviewer whose structured finding is recorded")
    p.set_defaults(func=cmd_add)
    p = sub.add_parser("resolve")
    p.add_argument("--ledger", required=True)
    p.add_argument("--id", required=True)
    p.add_argument("--resolution", required=True)
    p.add_argument("--evidence", required=True)
    p.add_argument("--reviewer", required=True)
    p.add_argument("--invalid", action="store_true")
    p.set_defaults(func=cmd_resolve)
    p = sub.add_parser("complete")
    p.add_argument("--ledger", required=True)
    p.add_argument("--reviewer", required=True)
    p.add_argument("--session-id", required=True,
                   help="stable id of the independent reviewer invocation")
    p.add_argument("--result", required=True,
                   help="bounded JSON verdict produced by that reviewer invocation")
    p.set_defaults(func=cmd_complete)
    p = sub.add_parser("check")
    p.add_argument("--wave", type=int, required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--require-risk", action="store_true")
    p.set_defaults(func=cmd_check)
    return ap


def main():
    args = parser().parse_args()
    repo = repo_root()
    if args.command in {"create", "check"} and not args.run:
        raise SystemExit("error: --run is required")
    run = run_dir(repo, args.run) if args.run else None
    args.func(args, repo, run)


if __name__ == "__main__":
    main()
