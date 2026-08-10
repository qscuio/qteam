#!/usr/bin/env python3
"""Create immutable review packets and maintain machine-readable findings."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True

from agent_team_policy import (
    REVIEW_AXIS_INSTRUCTIONS, REVIEW_CLOSURE_INSTRUCTIONS,
    REVIEW_FINDING_INSTRUCTIONS,
    REVIEW_INTENSITY_INSTRUCTIONS,
    review_contract_digest, safe_identifier,
)
from agent_team_artifact import (
    ArtifactError, lint_documents, locked_regular, safe_regular,
)
from agent_team_eval import (
    calibration_suite, codex_version, parse_codex_trace,
    read_bounded_json_object, regular_output, run_regular_file,
    trajectory_independence, validate_calibration,
    validate_trajectory, wave_trajectory, wait_capped_process,
)

AXES = {"spec", "standards", "risk"}
SEVERITIES = {"P0", "P1", "P2", "P3"}
THINKING_LEVELS = {"low", "medium", "high", "xhigh"}


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
        return read_bounded_json_object(path, f"review artifact {path.name}")
    except ValueError as exc:
        raise SystemExit(f"error: {exc}")


def safe_directory(path, label, *, create=False):
    directory = Path(path)
    if directory.is_symlink():
        raise SystemExit(f"error: {label} must not be a symlink")
    if not directory.exists() and create:
        parent = directory.parent
        if (parent.is_symlink() or not parent.is_dir()
                or parent.resolve() != parent.absolute()):
            raise SystemExit(f"error: {label} parent is not a real directory")
        directory.mkdir()
    if (not directory.is_dir()
            or directory.resolve() != directory.absolute()):
        raise SystemExit(f"error: {label} must be a real directory")
    return directory


def write(path, value):
    safe_directory(path.parent, "review artifact directory", create=True)
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
    safe_directory(snapshot_dir, "review source directory", create=True)
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


def review_result(path, expected_axis, require_pass=True):
    raw_path = Path(path)
    if raw_path.is_symlink():
        raise SystemExit("error: --result must be a regular JSON file")
    result_path = raw_path.resolve()
    if not result_path.is_file():
        raise SystemExit("error: --result must be a regular JSON file")
    result = read(result_path)
    if not isinstance(result, dict):
        raise SystemExit("error: review result must be a JSON object")
    result_fields = {
        "axis", "verdict", "trajectory_verdict", "calibration_results",
        "findings", "resolved_ids", "invalid_ids", "upheld_ids",
        "invalid_evidence",
    }
    if set(result) != result_fields:
        raise SystemExit("error: review result has unknown or missing fields")
    if result.get("axis") != expected_axis:
        raise SystemExit("error: review result axis does not match ledger")
    if result.get("verdict") not in {"pass", "needs-fix"}:
        raise SystemExit("error: review result verdict must be pass or needs-fix")
    if result.get("trajectory_verdict") not in {"pass", "needs-fix"}:
        raise SystemExit("error: review result trajectory_verdict must be pass or needs-fix")
    if (result.get("verdict") == "pass"
            and result.get("trajectory_verdict") != "pass"):
        raise SystemExit("error: a passing review must pass the trajectory evidence")
    calibration_results = result.get("calibration_results")
    if (not isinstance(calibration_results, dict) or not calibration_results
            or any(not safe_identity(key) for key in calibration_results)
            or any(value not in {"pass", "needs-fix"}
                   for value in calibration_results.values())):
        raise SystemExit("error: review result needs bounded calibration_results")
    if require_pass and result.get("verdict") != "pass":
        raise SystemExit("error: review result verdict must be pass before completion")
    if not isinstance(result.get("findings"), list):
        raise SystemExit("error: review result findings must be an array")
    if any(not isinstance(item, dict)
           or not isinstance(item.get("id"), str) or not item["id"]
           for item in result["findings"]):
        raise SystemExit("error: every review result finding needs a non-empty id")
    finding_fields = {
        "id", "severity", "title", "review_evidence", "impact",
        "fix_direction", "owner", "file", "line",
    }
    if any(set(item) - finding_fields for item in result["findings"]):
        raise SystemExit("error: review result finding has unknown fields")
    finding_ids = [item["id"] for item in result["findings"]]
    if (len(finding_ids) != len(set(finding_ids))
            or any(not safe_identity(item) for item in finding_ids)):
        raise SystemExit("error: review result finding ids must be unique safe identifiers")
    upheld_ids = result.get("upheld_ids")
    if (not isinstance(upheld_ids, list)
            or any(not safe_identity(item) for item in upheld_ids)
            or len(upheld_ids) != len(set(upheld_ids))):
        raise SystemExit("error: review result needs unique safe upheld_ids")
    if (result.get("verdict") == "needs-fix"
            and not result["findings"] and not upheld_ids):
        raise SystemExit("error: needs-fix review result requires findings or upheld_ids")
    if result.get("verdict") == "needs-fix":
        for item in result["findings"]:
            if item.get("severity") not in SEVERITIES:
                raise SystemExit("error: every needs-fix finding needs P0-P3 severity")
            validate_nonempty_finding_fields(
                item, ("title", "review_evidence", "impact", "fix_direction", "owner")
            )
    for item in result["findings"]:
        validate_finding_location(item.get("file"), item.get("line"))
    resolved_ids = result.get("resolved_ids")
    if (not isinstance(resolved_ids, list)
            or any(not safe_identity(item) for item in resolved_ids)
            or len(resolved_ids) != len(set(resolved_ids))):
        raise SystemExit("error: review result needs unique safe resolved_ids")
    invalid_ids = result.get("invalid_ids")
    if (not isinstance(invalid_ids, list)
            or any(not safe_identity(item) for item in invalid_ids)
            or len(invalid_ids) != len(set(invalid_ids))):
        raise SystemExit("error: review result needs unique safe invalid_ids")
    if ((set(resolved_ids) & set(invalid_ids))
            or (set(resolved_ids) & set(upheld_ids))
            or (set(invalid_ids) & set(upheld_ids))):
        raise SystemExit("error: closure result id sets must be disjoint")
    invalid_evidence = result.get("invalid_evidence")
    if (not isinstance(invalid_evidence, dict)
            or set(invalid_evidence) != set(invalid_ids)
            or any(not isinstance(value, str) or not value
                   for value in invalid_evidence.values())):
        raise SystemExit(
            "error: invalid_evidence must explain every and only invalid_ids"
        )
    if result.get("verdict") == "pass" and result["findings"]:
        raise SystemExit("error: pass review result cannot contain new findings")
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    return result, hashlib.sha256(encoded).hexdigest()


def object_sha256(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def json_file_sha256(value):
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    return hashlib.sha256(encoded).hexdigest()


def safe_identity(value):
    return safe_identifier(value)


def receipt_paths_match_session(receipt):
    session = receipt.get("session_id") if isinstance(receipt, dict) else None
    return bool(safe_identity(session) and
                receipt.get("result") == f"reviews/results/{session}.json" and
                receipt.get("stdout_log")
                == f"reviews/logs/{session}.stdout.log" and
                receipt.get("stderr_log")
                == f"reviews/logs/{session}.stderr.log")


def validate_nonempty_finding_fields(finding, fields):
    invalid = [field for field in fields
               if not isinstance(finding.get(field), str) or not finding[field]]
    if invalid:
        raise SystemExit(
            "error: finding fields must be non-empty strings: " + ", ".join(invalid)
        )


def validate_finding_location(file_value, line_value):
    if ((file_value is not None and not isinstance(file_value, str))
            or (line_value is not None
                and (not isinstance(line_value, int)
                     or isinstance(line_value, bool) or line_value < 1))):
        raise SystemExit(
            "error: finding file/line must be string/null and positive integer/null"
        )


def wave_policy_for(state, wave):
    if not isinstance(wave, int) or isinstance(wave, bool) or wave < 1:
        raise SystemExit("error: review wave must be a positive integer")
    waves = state.get("waves")
    if not isinstance(waves, dict):
        raise SystemExit("error: run has no valid wave policy; migrate/replan it")
    if waves:
        policy = waves.get(str(wave))
        if not isinstance(policy, dict):
            raise SystemExit(f"error: review wave {wave} is absent from run policy")
        return policy
    if wave != 1:
        raise SystemExit("error: an empty run permits only synthetic final wave 1")
    return {
        "execution_tier": "standard", "review_intensity": "full",
        "require_risk_review": bool(state.get("risk_forced")),
        "risk_flags": [], "tasks": [],
        "reversibility": "contained-reversible",
        "integration_lane": "shadow",
        "require_user_finish_decision": False,
    }


def review_execution_for(state, wave_policy):
    tier = wave_policy.get("execution_tier")
    profiles = state.get("review_model_profiles")
    profile = profiles.get(tier) if isinstance(profiles, dict) else None
    if (tier not in {"economy", "standard", "deep"}
            or not isinstance(profile, dict)
            or not isinstance(profile.get("model"), str) or not profile["model"]
            or profile.get("thinking") not in THINKING_LEVELS
            or not isinstance(profile.get("provider"), str) or not profile["provider"]
            or not isinstance(profile.get("family"), str) or not profile["family"]):
        raise SystemExit("error: run has an invalid model profile for review tier")
    return {"tier": tier, "model": profile["model"],
            "thinking": profile["thinking"],
            "provider": profile["provider"], "family": profile["family"]}


def packet_matches_policy(packet, state, wave_policy):
    expected_execution = review_execution_for(state, wave_policy)
    return (
        packet.get("execution_tier") == wave_policy.get("execution_tier")
        and packet.get("review_intensity") == wave_policy.get("review_intensity")
        and packet.get("risk_flags") == wave_policy.get("risk_flags", [])
        and packet.get("review_execution") == expected_execution
        and isinstance(packet.get("runner"), dict)
        and packet.get("runner", {}).get("name") == "codex-cli"
        and isinstance(packet.get("runner", {}).get("version"), str)
        and bool(packet.get("runner", {}).get("version"))
        and packet.get("review_contract_sha256") == review_contract_digest(
            packet.get("axis"), wave_policy.get("review_intensity")
        )
    )


def validate_source_snapshots(run, packet):
    packet_schema = packet.get("schema_version")
    if packet_schema not in {1, 2, 3}:
        raise SystemExit("error: unsupported review packet schema")
    source_root = (run / "reviews" / "sources").resolve()
    absolute = []
    for field in ("spec_sources", "standards_sources", "digest_sources"):
        records = packet.get(field)
        if not isinstance(records, list):
            raise SystemExit(f"error: review packet has invalid {field}")
        for record in records:
            if (not isinstance(record, dict)
                    or not isinstance(record.get("sha256"), str)
                    or len(record["sha256"]) != 64
                    or not isinstance(record.get("snapshot"), str)):
                raise SystemExit("error: malformed review source snapshot record")
            path = (run / record["snapshot"]).resolve()
            if (path.parent != source_root or path.name != f"{record['sha256']}.source"
                    or path.is_symlink() or not path.is_file()
                    or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]):
                raise SystemExit("error: review source snapshot integrity failure")
            absolute.append(str(path))
    lint_report = packet.get("artifact_lint")
    lint_digest = packet.get("artifact_lint_sha256")
    if packet.get("axis") == "spec":
        has_report = "artifact_lint" in packet
        has_digest = "artifact_lint_sha256" in packet
        if packet_schema == 1 and not has_report and not has_digest:
            return absolute
        if (has_report != has_digest or not isinstance(lint_report, dict)
                or lint_report.get("status") not in {"pass", "pass-with-warnings"}
                or lint_report.get("errors")
                or object_sha256(lint_report) != lint_digest):
            raise SystemExit("error: review packet artifact lint integrity failure")
        expected_sources = [
            {"source": record.get("source"), "sha256": record.get("sha256")}
            for record in packet.get("spec_sources", [])
        ]
        if lint_report.get("sources") != expected_sources:
            raise SystemExit("error: artifact lint does not cover frozen spec sources")
    elif lint_report is not None or lint_digest is not None:
        raise SystemExit("error: non-spec review packet cannot carry spec artifact lint")
    return absolute


def validate_completed_review(run, path, ledger):
    packet = ledger.get("packet", {})
    validate_source_snapshots(run, packet)
    attestation = ledger.get("attestation")
    if not isinstance(attestation, dict):
        raise SystemExit("error: completed review has no attestation")
    receipt_path = (run / str(attestation.get("receipt", ""))).resolve()
    receipt_root = (run / "reviews" / "receipts").resolve()
    if (receipt_path.parent != receipt_root or receipt_path.is_symlink()
            or not receipt_path.is_file()
            or hashlib.sha256(receipt_path.read_bytes()).hexdigest()
            != attestation.get("receipt_sha256")):
        raise SystemExit("error: completed review receipt integrity failure")
    receipt = read(receipt_path)
    result_path = (run / str(receipt.get("result", ""))).resolve()
    result_root = (run / "reviews" / "results").resolve()
    if result_path.parent != result_root:
        raise SystemExit("error: completed review result escaped its directory")
    result, result_sha = review_result(result_path, packet.get("axis"))
    if packet.get("schema_version") == 3:
        try:
            validate_calibration(
                packet.get("axis"), packet.get("calibration", {}).get("sha256"),
                result.get("calibration_results"),
            )
            trace_path = run_regular_file(run, receipt.get("stdout_log"))
            validate_trajectory(
                receipt.get("trajectory"), "review", receipt.get("session_id"),
                receipt.get("execution"), trace_path,
            )
            if receipt.get("trajectory", {}).get("runner") != receipt.get("runner"):
                raise ValueError("review trajectory runner differs from its receipt")
            if receipt.get("trajectory", {}).get("disposition") != "pass":
                raise ValueError("review trajectory requires escalation")
        except ValueError as exc:
            raise SystemExit(f"error: completed review evaluation failure: {exc}")
    if (receipt.get("status") != "passed" or receipt.get("exit_code") != 0
            or not receipt_paths_match_session(receipt)
            or receipt.get("ledger") != str(path.relative_to(run))
            or receipt.get("packet_sha256") != object_sha256(packet)
            or receipt.get("ledger_findings_sha256")
            != object_sha256(ledger.get("findings", []))
            or receipt.get("review_contract_sha256")
            != packet.get("review_contract_sha256")
            or (packet.get("schema_version") == 3
                and receipt.get("calibration_sha256")
                != packet.get("calibration", {}).get("sha256"))
            or receipt.get("review_head_sha") != packet.get("head_sha")
            or receipt.get("execution") != packet.get("review_execution")
            or (packet.get("schema_version") == 3
                and (receipt.get("runner") != packet.get("runner")
                     or not isinstance(receipt.get("trajectory"), dict)))
            or receipt.get("reviewer") != attestation.get("reviewer")
            or receipt.get("session_id") != attestation.get("session_id")
            or receipt.get("result_sha256") != result_sha
            or attestation.get("result_sha256") != result_sha
            or attestation.get("result") != result):
        raise SystemExit("error: completed review attestation integrity failure")
    return attestation


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
    run = review_dir.parent
    if (review_dir.name != "reviews" or review_dir.is_symlink()
            or (review_dir.exists() and not review_dir.is_dir())):
        raise SystemExit("error: review root must be a real run directory")
    safe_directory(review_dir, "review root", create=True)
    # Publication sealing and review commits share the run-state lock.  A review
    # may spend minutes in an external read-only reviewer without holding it, so
    # every artifact/ledger commit re-enters here and checks the seal atomically.
    try:
        with locked_regular(run / ".state.lock", "run state lock"):
            try:
                safe_regular(run / "state.json", "run state", required=True)
            except ArtifactError as exc:
                raise SystemExit(f"error: {exc}")
            state = read(run / "state.json")
            if state.get("publication_seal"):
                raise SystemExit(
                    "error: publication seal freezes review packets and findings"
                )
            with locked_regular(review_dir / ".ledger.lock", "review ledger lock"):
                recover_review_transaction(review_dir)
                yield
    except ArtifactError as exc:
        raise SystemExit(f"error: {exc}")


LEDGER_NAME = re.compile(
    r"^wave-([1-9][0-9]*)-(spec|standards|risk)(?:-r([1-9][0-9]*))?\.json$"
)


def validate_ledger_transition(current, updated):
    allowed = {
        "packet", "findings", "completed_at", "review_attempts",
        "completed_by", "attestation",
    }
    if (not isinstance(current, dict) or not isinstance(updated, dict)
            or not set(current) <= allowed or not set(updated) <= allowed
            or current.get("packet") != updated.get("packet")):
        raise SystemExit(
            "error: review closure transaction changed an immutable packet"
        )
    old_findings = current.get("findings")
    new_findings = updated.get("findings")
    if (not isinstance(old_findings, list) or not isinstance(new_findings, list)
            or len(new_findings) < len(old_findings)):
        raise SystemExit("error: review closure transaction removed findings")
    mutable = {"status", "resolution", "evidence", "reviewer", "resolved_at"}
    for old, new in zip(old_findings, new_findings):
        if not isinstance(old, dict) or not isinstance(new, dict):
            raise SystemExit("error: invalid finding in review transaction")
        if any(old.get(key) != new.get(key)
               for key in set(old) | set(new) if key not in mutable):
            raise SystemExit("error: review transaction changed finding identity")
        old_status = old.get("status")
        new_status = new.get("status")
        if old_status == new_status:
            if old != new:
                raise SystemExit("error: review transaction changed a stable finding")
        elif not (old_status == "open" and new_status in {"resolved", "invalid"}
                  and isinstance(new.get("resolution"), str)
                  and new.get("resolution")
                  and isinstance(new.get("evidence"), str)
                  and new.get("evidence")
                  and isinstance(new.get("reviewer"), str)
                  and new.get("reviewer")
                  and isinstance(new.get("resolved_at"), str)
                  and new.get("resolved_at")):
            raise SystemExit("error: invalid review finding state transition")
    for finding in new_findings[len(old_findings):]:
        if (not isinstance(finding, dict) or finding.get("status") != "open"
                or not isinstance(finding.get("id"), str) or not finding["id"]):
            raise SystemExit("error: invalid appended review finding")

    old_attempts = current.get("review_attempts", [])
    new_attempts = updated.get("review_attempts", [])
    if (not isinstance(old_attempts, list) or not isinstance(new_attempts, list)
            or len(new_attempts) not in {len(old_attempts), len(old_attempts) + 1}
            or new_attempts[:len(old_attempts)] != old_attempts
            or any(not isinstance(item, dict) for item in new_attempts)):
        raise SystemExit("error: invalid review-attempt transition")
    appended = new_findings[len(old_findings):]
    if len(new_attempts) > len(old_attempts):
        attempt = new_attempts[-1] if len(new_attempts) > len(old_attempts) else None
        if (not isinstance(attempt, dict)
                or attempt.get("status") != "needs-fix"
                or attempt.get("finding_ids")
                != [finding.get("id") for finding in appended]):
            raise SystemExit("error: appended findings lack their bound review attempt")
    elif appended:
        raise SystemExit("error: appended findings lack their bound review attempt")

    old_completed = current.get("completed_at")
    new_completed = updated.get("completed_at")
    if old_completed:
        if updated != current:
            raise SystemExit("error: completed review ledger is immutable")
    elif new_completed is not None:
        if (not isinstance(new_completed, str) or not new_completed
                or not isinstance(updated.get("completed_by"), str)
                or not updated["completed_by"]
                or not isinstance(updated.get("attestation"), dict)):
            raise SystemExit("error: invalid review completion transition")
    elif ("completed_by" in updated or "attestation" in updated):
        raise SystemExit("error: incomplete review cannot carry an attestation")


def validate_receipt_transition(path, updated):
    if not path.exists():
        return
    current = read(path)
    if current == updated:
        return
    immutable = {
        "schema_version", "ledger", "packet_sha256",
        "ledger_findings_sha256", "review_contract_sha256",
        "calibration_sha256", "review_head_sha", "reviewer", "session_id",
        "execution", "runner", "result", "stdout_log", "stderr_log",
        "started_at",
    }
    if (not isinstance(current, dict) or current.get("status") != "running"
            or updated.get("status") != "needs-fix"
            or updated.get("exit_code") != 0
            or any(current.get(key) != updated.get(key) for key in immutable)
            or not isinstance(updated.get("result_sha256"), str)
            or not isinstance(updated.get("trajectory"), dict)
            or not isinstance(updated.get("completed_at"), str)):
        raise SystemExit(
            "error: review closure transaction changed an existing receipt"
        )


def validate_review_transaction(review_dir, transaction):
    if (not isinstance(transaction, dict)
            or set(transaction) != {"schema_version", "txid", "writes"}
            or transaction.get("schema_version") != 1
            or not isinstance(transaction.get("txid"), str)
            or re.fullmatch(r"[0-9a-f]{32}", transaction["txid"]) is None
            or not isinstance(transaction.get("writes"), dict)
            or not transaction["writes"]):
        raise SystemExit("error: invalid review closure transaction")
    for relative, value in transaction["writes"].items():
        match = LEDGER_NAME.fullmatch(relative) if isinstance(relative, str) else None
        receipt_match = (re.fullmatch(
            r"receipts/((?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._-]*)\.json",
            relative,
        ) if isinstance(relative, str) else None)
        if not isinstance(value, dict) or (not match and not receipt_match):
            raise SystemExit("error: invalid review closure transaction write-set")
        if receipt_match:
            path = review_dir / relative
            try:
                safe_regular(path, "review transaction receipt")
            except ArtifactError as exc:
                raise SystemExit(f"error: {exc}")
            ledger_relative = value.get("ledger")
            if (value.get("schema_version") != 1
                    or value.get("status") != "needs-fix"
                    or not receipt_paths_match_session(value)
                    or value.get("session_id") != receipt_match.group(1)
                    or not isinstance(ledger_relative, str)
                    or not ledger_relative.startswith("reviews/")
                    or ledger_relative[len("reviews/"):]
                    not in transaction["writes"]):
                raise SystemExit("error: invalid review receipt in closure transaction")
            validate_receipt_transition(path, value)
            continue
        path = review_dir / relative
        try:
            safe_regular(path, "review transaction ledger", required=True)
        except ArtifactError as exc:
            raise SystemExit(f"error: {exc}")
        packet = value.get("packet")
        findings = value.get("findings")
        if (not isinstance(packet, dict) or not isinstance(findings, list)
                or "completed_at" not in value
                or packet.get("wave") != int(match.group(1))
                or packet.get("axis") != match.group(2)
                or packet.get("iteration", 1)
                != (int(match.group(3)) if match.group(3) else 1)
                or any(not isinstance(finding, dict) for finding in findings)):
            raise SystemExit("error: invalid review ledger in closure transaction")
        current = read(path)
        validate_ledger_transition(current, value)
        if value.get("completed_at"):
            validate_completed_review(review_dir.parent, path, value)
    receipt_writes = [
        (relative, value) for relative, value in transaction["writes"].items()
        if relative.startswith("receipts/")
    ]
    ledger_writes = [
        (relative, value) for relative, value in transaction["writes"].items()
        if LEDGER_NAME.fullmatch(relative)
    ]
    if receipt_writes:
        if len(receipt_writes) != 1 or len(ledger_writes) != 1:
            raise SystemExit("error: needs-fix transaction needs one ledger and receipt")
        receipt_relative, receipt = receipt_writes[0]
        ledger_relative, updated = ledger_writes[0]
        if receipt.get("ledger") != f"reviews/{ledger_relative}":
            raise SystemExit("error: review receipt is not bound to its ledger write")
        current = read(review_dir / ledger_relative)
        old_count = len(current.get("findings", []))
        appended_ids = [item.get("id")
                        for item in updated.get("findings", [])[old_count:]]
        attempts = updated.get("review_attempts", [])
        attempt = attempts[-1] if attempts else None
        if current == updated and isinstance(attempt, dict):
            appended_ids = attempt.get("finding_ids", [])
        result_path = (review_dir.parent / receipt.get("result", "")).resolve()
        if result_path.parent != (review_dir / "results").resolve():
            raise SystemExit("error: needs-fix result escaped its directory")
        result, result_sha = review_result(
            result_path, updated.get("packet", {}).get("axis"),
            require_pass=False,
        )
        try:
            trace_path = run_regular_file(
                review_dir.parent, receipt.get("stdout_log")
            )
            validate_trajectory(
                receipt.get("trajectory"), "review", receipt.get("session_id"),
                receipt.get("execution"), trace_path,
            )
        except ValueError as exc:
            raise SystemExit(f"error: invalid needs-fix trajectory: {exc}")
        if (result.get("verdict") != "needs-fix"
                or [item.get("id") for item in result.get("findings", [])]
                != appended_ids
                or receipt.get("result_sha256") != result_sha
                or receipt.get("trajectory", {}).get("disposition") != "pass"
                or receipt.get("trajectory", {}).get("runner")
                != receipt.get("runner")
                or receipt.get("execution")
                != updated.get("packet", {}).get("review_execution")
                or receipt.get("runner") != updated.get("packet", {}).get("runner")
                or receipt.get("packet_sha256")
                != object_sha256(updated.get("packet"))
                or (current != updated
                    and receipt.get("ledger_findings_sha256")
                    != object_sha256(current.get("findings", [])))
                or not isinstance(attempt, dict)
                or attempt.get("session_id") != receipt.get("session_id")
                or attempt.get("reviewer") != receipt.get("reviewer")
                or attempt.get("result_sha256") != result_sha
                or attempt.get("finding_ids") != appended_ids
                or attempt.get("receipt") != f"reviews/{receipt_relative}"
                or attempt.get("receipt_sha256") != json_file_sha256(receipt)):
            raise SystemExit("error: needs-fix ledger/receipt binding is invalid")
    else:
        completed = []
        for relative, updated in ledger_writes:
            current = read(review_dir / relative)
            if updated.get("completed_at"):
                completed.append((relative, updated))
        if len(completed) != 1:
            raise SystemExit(
                "error: closure transaction must complete exactly one ledger"
            )
        completed_relative, completed_ledger = completed[0]
        packet = completed_ledger.get("packet", {})
        result = completed_ledger.get("attestation", {}).get("result", {})
        expected = {completed_relative}
        closure_by_ledger = {}
        for item in packet.get("closure_findings", []):
            ledger = item.get("ledger")
            if isinstance(ledger, str) and ledger.startswith("reviews/"):
                closure_by_ledger.setdefault(ledger[len("reviews/"):], set()).add(
                    item.get("id")
                )
        expected.update(closure_by_ledger)
        if {relative for relative, _ in ledger_writes} != expected:
            raise SystemExit("error: closure transaction write-set differs from packet")
        resolved = set(result.get("resolved_ids", []))
        invalid = set(result.get("invalid_ids", []))
        for relative, ids in closure_by_ledger.items():
            old = read(review_dir / relative)
            new = transaction["writes"][relative]
            changed = {
                after.get("id") for before, after in zip(
                    old.get("findings", []), new.get("findings", [])
                ) if before != after
            }
            if not changed <= ids or any(
                    item.get("status") != (
                        "resolved" if item.get("id") in resolved else "invalid"
                    )
                    for item in new.get("findings", []) if item.get("id") in ids
                ) or ids != (ids & (resolved | invalid)):
                raise SystemExit("error: closure findings differ from frozen packet")
    return transaction


def apply_review_transaction(review_dir, transaction):
    validate_review_transaction(review_dir, transaction)
    fault_after = os.environ.get("QTEAM_FAULT_AFTER_REVIEW_WRITES")
    fault_after = int(fault_after) if fault_after else None
    completed = 0
    for relative, value in transaction.get("writes", {}).items():
        path = (review_dir / relative).resolve()
        if review_dir.resolve() not in path.parents:
            raise SystemExit("error: review transaction path escaped review directory")
        write(path, value)
        completed += 1
        if fault_after is not None and completed >= fault_after:
            raise SystemExit("error: injected review transaction interruption")


def recover_review_transaction(review_dir):
    intent = review_dir / ".closure-transaction.json"
    if not intent.exists():
        return
    try:
        safe_regular(intent, "review closure transaction", required=True)
    except ArtifactError as exc:
        raise SystemExit(f"error: {exc}")
    transaction = validate_review_transaction(review_dir, read(intent))
    apply_review_transaction(review_dir, transaction)
    intent.unlink()
    fd_dir = os.open(review_dir, os.O_RDONLY)
    try:
        os.fsync(fd_dir)
    finally:
        os.close(fd_dir)


def commit_review_transaction(review_dir, writes):
    transaction = {
        "schema_version": 1,
        "txid": uuid.uuid4().hex,
        "writes": {str(path.resolve().relative_to(review_dir.resolve())): value
                   for path, value in writes.items()},
    }
    intent = review_dir / ".closure-transaction.json"
    try:
        safe_regular(intent, "review closure transaction")
    except ArtifactError as exc:
        raise SystemExit(f"error: {exc}")
    validate_review_transaction(review_dir, transaction)
    write(intent, transaction)
    apply_review_transaction(review_dir, transaction)
    intent.unlink()
    fd_dir = os.open(review_dir, os.O_RDONLY)
    try:
        os.fsync(fd_dir)
    finally:
        os.close(fd_dir)


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
    artifact_lint = None
    artifact_lint_sha256 = None
    if args.axis == "spec":
        try:
            artifact_lint = lint_documents("spec", args.spec_source, repo)
        except ArtifactError as exc:
            raise SystemExit(f"error: artifact lint failed: {exc}")
        if artifact_lint["errors"]:
            codes = ", ".join(sorted({item["code"] for item in artifact_lint["errors"]}))
            raise SystemExit(f"error: artifact lint failed: {codes}")
        artifact_lint_sha256 = object_sha256(artifact_lint)
    base = git(["rev-parse", args.base], repo)
    head = git(["rev-parse", args.head], repo)
    merge_base = git(["merge-base", base, head], repo)
    if merge_base != base:
        raise SystemExit("error: review base must be an ancestor of review head")
    state = read(run / "state.json")
    wave_policy = wave_policy_for(state, args.wave)
    execution = review_execution_for(state, wave_policy)
    integration = subprocess.run(
        ["git", "rev-parse", state.get("integration_branch", "")], cwd=repo,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if integration.returncode == 0 and integration.stdout.strip() != head:
        raise SystemExit("error: review head must equal current integration HEAD")
    merged_commits = []
    for task_id in wave_policy.get("tasks", []):
        summary = state.get("tasks", {}).get(task_id, {})
        if summary.get("status") != "merged":
            continue
        task = read(run / "tasks" / f"{task_id}.json")
        if not task.get("merge_commit"):
            raise SystemExit(f"error: merged task {task_id} has no merge commit")
        merged_commits.append(task["merge_commit"])
    if merged_commits and base == head and args.scope != "dispute":
        raise SystemExit("error: empty review range cannot cover merged work")
    if args.scope in {"wave", "final"}:
        for commit in merged_commits:
            contains = subprocess.run(
                ["git", "merge-base", "--is-ancestor", commit, head], cwd=repo
            )
            predates = subprocess.run(
                ["git", "merge-base", "--is-ancestor", commit, base], cwd=repo
            )
            if contains.returncode or not predates.returncode:
                raise SystemExit(
                    f"error: review range does not contain wave merge commit {commit}"
                )
    path = ledger_path(run, args.wave, args.axis, args.iteration)
    if args.scope in {"fix", "dispute"}:
        if args.iteration <= 1:
            raise SystemExit("error: closure review requires iteration 2 or later")
        if args.scope == "fix" and base == head:
            raise SystemExit("error: fix re-review requires a non-empty fix range")
        if args.scope == "dispute" and base != head:
            raise SystemExit("error: dispute review must use an unchanged head")
        previous = ledger_path(run, args.wave, args.axis, args.iteration - 1)
        prior = read(previous)
        if (not prior.get("completed_at") and not prior.get("findings")
                and not prior.get("review_attempts")):
            raise SystemExit(
                "error: fix re-review requires prior findings or completion"
            )
        if prior.get("packet", {}).get("head_sha") != base:
            raise SystemExit(
                "error: fix re-review base must equal the prior reviewed head"
            )
    closure_findings = []
    if args.scope in {"fix", "dispute"}:
        for prior_path in (run / "reviews").glob(
                f"wave-{args.wave}-{args.axis}*.json"):
            prior_ledger = read(prior_path)
            prior_packet = prior_ledger.get("packet", {})
            if (prior_packet.get("axis") != args.axis
                    or prior_packet.get("wave") != args.wave
                    or prior_packet.get("iteration", 1) >= args.iteration):
                continue
            for finding in prior_ledger.get("findings", []):
                if finding.get("status") == "open":
                    closure_findings.append({
                        "ledger": str(prior_path.relative_to(run)),
                        "id": finding.get("id"),
                        "severity": finding.get("severity"),
                        "title": finding.get("title"),
                        "body": finding.get("body"),
                        "review_evidence": finding.get("review_evidence"),
                        "impact": finding.get("impact"),
                        "fix_direction": finding.get("fix_direction"),
                        "owner": finding.get("owner"),
                    })
        closure_findings.sort(key=lambda item: (item["ledger"], item["id"] or ""))
    with review_lock(run / "reviews"):
        spec_sources = snapshot_sources(repo, run, args.spec_source)
        standards_sources = snapshot_sources(repo, run, args.standards_source)
        digest_sources = snapshot_sources(repo, run, args.digest_source)
    try:
        trajectory = wave_trajectory(run, state, args.wave, base, head)
        calibration = calibration_suite(args.axis)
        runner_version = codex_version()
    except ValueError as exc:
        raise SystemExit(f"error: cannot freeze review evaluation evidence: {exc}")
    packet = {
        "schema_version": 3,
        "run_id": run.name,
        "wave": args.wave,
        "axis": args.axis,
        "iteration": args.iteration,
        "scope": args.scope,
        "execution_tier": wave_policy["execution_tier"],
        "review_execution": execution,
        "runner": {"name": "codex-cli", "version": runner_version},
        "generator_families": sorted({
            item.get("execution", {}).get("family")
            for item in trajectory["worker_trajectories"]
            if item.get("execution", {}).get("family")
        }),
        "judge_independence": (
            trajectory_independence(trajectory, execution["family"])
        ),
        "review_contract_sha256": review_contract_digest(
            args.axis, wave_policy["review_intensity"]
        ),
        "calibration": calibration,
        "trajectory": trajectory,
        "review_intensity": wave_policy["review_intensity"],
        "risk_flags": wave_policy.get("risk_flags", []),
        "closure_findings": closure_findings,
        "closure_findings_sha256": object_sha256(closure_findings),
        "base_sha": base,
        "head_sha": head,
        "merge_base_sha": merge_base,
        "diff_range": f"{base}...{head}",
        "commits": git(["log", "--format=%H", f"{base}..{head}"], repo).splitlines(),
        "spec_sources": spec_sources,
        "standards_sources": standards_sources,
        "digest_sources": digest_sources,
        "artifact_lint": artifact_lint,
        "artifact_lint_sha256": artifact_lint_sha256,
        "created_at": now(),
    }
    validate_source_snapshots(run, packet)
    with review_lock(path.parent):
        if path.exists():
            old = read(path)
            old_packet = old.get("packet", {})
            stable_keys = [key for key in packet if key != "created_at"]
            changed = {
                key for key in stable_keys
                if old_packet.get(key) != packet[key]
            }
            if changed:
                receipts = []
                for receipt_path in (run / "reviews" / "receipts").glob("*.json"):
                    try:
                        candidate = read(receipt_path)
                    except (SystemExit, AttributeError):
                        receipts.append(receipt_path)
                        continue
                    if candidate.get("ledger") == str(path.relative_to(run)):
                        receipts.append(receipt_path)
                inactive = (
                    not old.get("findings") and not old.get("review_attempts")
                    and not old.get("completed_at") and not receipts
                )
                if not (args.refresh_runner and changed == {"runner"} and inactive):
                    raise SystemExit(
                        "error: review packet already exists with different "
                        f"immutable inputs: {path}"
                    )
                old["packet"] = packet
                write(path, old)
            print(path)
            return
        write(path, {"packet": packet, "findings": [], "completed_at": None})
    print(path)


def cmd_run(args, repo, _run):
    path = checked_ledger_path(repo, args.ledger)
    if not safe_identity(args.reviewer) or not safe_identity(args.session_id):
        raise SystemExit("error: reviewer and session-id must be safe identifiers")
    run = path.parent.parent
    with review_lock(path.parent):
        ledger = read(path)
        if ledger.get("completed_at"):
            raise SystemExit("error: completed review ledger is immutable")
        packet = ledger.get("packet")
        if not isinstance(packet, dict):
            raise SystemExit("error: review ledger has no packet")
        state = read(run / "state.json")
        policy = wave_policy_for(state, packet.get("wave"))
        if not packet_matches_policy(packet, state, policy):
            raise SystemExit("error: review packet no longer matches run wave policy")
        try:
            packet_trajectory = packet.get("trajectory")
            if not isinstance(packet_trajectory, dict):
                raise ValueError("review packet trajectory must be an object")
            expected_trajectory = wave_trajectory(
                run, state, packet["wave"], packet["base_sha"], packet["head_sha"],
                task_ids=packet_trajectory.get("tasks"),
            )
            expected_calibration = calibration_suite(packet["axis"])
            runner_version = codex_version()
        except ValueError as exc:
            raise SystemExit(f"error: invalid review evaluation evidence: {exc}")
        expected_families = sorted({
            item.get("execution", {}).get("family")
            for item in expected_trajectory["worker_trajectories"]
            if item.get("execution", {}).get("family")
        })
        expected_independence = trajectory_independence(
            expected_trajectory, packet["review_execution"]["family"]
        )
        if (packet.get("trajectory") != expected_trajectory
                or packet.get("calibration") != expected_calibration
                or packet.get("generator_families") != expected_families
                or packet.get("judge_independence") != expected_independence):
            raise SystemExit("error: review trajectory/calibration evidence is stale")
        if packet.get("runner") != {
                "name": "codex-cli", "version": runner_version}:
            raise SystemExit(
                "error: Codex runner version changed since packet creation; "
                "refresh the unstarted packet"
            )
        source_paths = validate_source_snapshots(run, packet)
        packet_sha = object_sha256(packet)
        findings_sha = object_sha256(ledger.get("findings", []))
        execution = packet["review_execution"]
        receipt = run / "reviews" / "receipts" / f"{args.session_id}.json"
        result = run / "reviews" / "results" / f"{args.session_id}.json"
        stdout_log = run / "reviews" / "logs" / f"{args.session_id}.stdout.log"
        stderr_log = run / "reviews" / "logs" / f"{args.session_id}.stderr.log"
        if receipt.exists():
            previous_receipt = read(receipt)
            if (previous_receipt.get("status") in {"passed", "needs-fix"}
                    and previous_receipt.get("ledger") == str(path.relative_to(run))
                    and previous_receipt.get("packet_sha256") == packet_sha
                    and previous_receipt.get("reviewer") == args.reviewer
                    and previous_receipt.get("session_id") == args.session_id):
                print(receipt)
                return
        if receipt.exists() or result.exists():
            raise SystemExit("error: reviewer session/result already exists")
        safe_directory(result.parent, "review result directory", create=True)
        safe_directory(stdout_log.parent, "review log directory", create=True)
        started_at = now()
        write(receipt, {
            "schema_version": 1, "status": "running",
            "ledger": str(path.relative_to(run)), "packet_sha256": packet_sha,
            "ledger_findings_sha256": findings_sha,
            "review_contract_sha256": packet["review_contract_sha256"],
            "calibration_sha256": packet["calibration"]["sha256"],
            "review_head_sha": packet["head_sha"],
            "reviewer": args.reviewer, "session_id": args.session_id,
            "execution": execution,
            "runner": packet["runner"],
            "result": str(result.relative_to(run)),
            "stdout_log": str(stdout_log.relative_to(run)),
            "stderr_log": str(stderr_log.relative_to(run)),
            "started_at": started_at,
        })
    closure_ids = [item["id"] for item in packet.get("closure_findings", [])]
    if packet.get("artifact_lint") is None:
        lint_guidance = (
            "This pre-0.10 packet has no deterministic artifact preflight; check "
            "the frozen source structure semantically without widening review scope. "
        )
    else:
        lint_guidance = (
            "The packet's artifact_lint already covers deterministic structure and "
            "traceability checks. Do not repeat passing mechanical checks; inspect only "
            "its warnings plus semantic behavior, scope, acceptance, and risk relevant "
            "to this axis. An untyped-source warning is context, not by itself a defect. "
        )
    prompt = (
        "You are an independent, read-only QTeam reviewer. Do not edit files. "
        f"Review only axis {packet['axis']} at immutable range {packet['diff_range']}. "
        f"{REVIEW_AXIS_INSTRUCTIONS[packet['axis']]} "
        f"{REVIEW_INTENSITY_INSTRUCTIONS[packet['review_intensity']]} "
        f"{REVIEW_FINDING_INSTRUCTIONS} "
        f"{lint_guidance}"
        "The packet contains a compact trajectory report. Inspect its anomaly codes, "
        "coverage, state-event digest, and worker summaries; do not request or restate "
        "raw tool logs unless a concrete anomaly requires a finding. A passing review "
        "must return trajectory_verdict=pass. "
        "Classify each frozen calibration canary as pass or needs-fix and return "
        "those labels in calibration_results. This is a consistency check, not a "
        "secret or adversarial benchmark. "
        f"Read these frozen packet snapshots by absolute path: {source_paths}. "
        "Inspect the affected diff from this exact detached reviewed HEAD; obey "
        "review_intensity and risk_flags. Return JSON only. If defects exist, use "
        "{\"axis\":<axis>,\"verdict\":"
        "\"needs-fix\",\"findings\":[{\"id\":<stable-id>,\"severity\":<P0-P3>,"
        "\"title\":<title>,\"review_evidence\":<evidence>,\"impact\":<impact>,"
        "\"fix_direction\":<owned-fix>,\"owner\":<owner>}],\"resolved_ids\":[],"
        "\"trajectory_verdict\":\"pass\","
        "\"calibration_results\":{<case-id>:<pass-or-needs-fix>},"
        "\"invalid_ids\":[],\"upheld_ids\":[],\"invalid_evidence\":{}} "
        "so the coordinator can record them. Change trajectory_verdict to needs-fix "
        "only when trajectory evidence materially contributes to the defect. "
        "Use verdict pass with findings [] only "
        "when the reviewed range has no unresolved valid defect. "
        f"{REVIEW_CLOSURE_INSTRUCTIONS} "
        f"Frozen closure set: {closure_ids}. "
        "Packet: " + json.dumps(packet, sort_keys=True)
    )
    checkout = Path(tempfile.mkdtemp(prefix=f"qteam-review-{args.session_id}-"))
    env = os.environ.copy()
    env["QTEAM_REVIEW_AXIS"] = packet["axis"]
    env["QTEAM_REVIEW_RESOLVED_IDS"] = json.dumps(closure_ids)
    env["QTEAM_REVIEW_INVALID_IDS"] = "[]"
    env["QTEAM_REVIEW_INVALID_EVIDENCE"] = "{}"
    env["QTEAM_REVIEW_UPHELD_IDS"] = "[]"
    env["QTEAM_REVIEW_CALIBRATION_CASE_IDS"] = json.dumps(
        [item["id"] for item in packet["calibration"]["cases"]]
    )
    if packet.get("scope") == "dispute":
        env["QTEAM_REVIEW_RESOLVED_IDS"] = "[]"
        env["QTEAM_REVIEW_INVALID_IDS"] = json.dumps(closure_ids)
        env["QTEAM_REVIEW_INVALID_EVIDENCE"] = json.dumps({
            finding_id: "independently disproved by fresh dispute review"
            for finding_id in closure_ids
        })
    completed = subprocess.CompletedProcess([], 127)
    launch_error = None
    try:
        git(["worktree", "add", "--detach", str(checkout), packet["head_sha"]], repo)
        command = [
            "codex", "exec", "-C", str(checkout), "--sandbox", "read-only",
            "--model", execution["model"], "-c",
            f'model_reasoning_effort="{execution["thinking"]}"',
            "-c", f'model_provider="{execution["provider"]}"',
            "--json", "--output-last-message", str(result), prompt,
        ]
        with regular_output(stdout_log, "review stdout") as stdout, \
                regular_output(stderr_log, "review stderr") as stderr, \
                regular_output(result, "review result", readwrite=True) as result_file:
            command[command.index(str(result))] = (
                f"/proc/self/fd/{result_file.fileno()}"
            )
            child = subprocess.Popen(
                command, cwd=checkout, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                pass_fds=(result_file.fileno(),), start_new_session=True,
            )
            return_code, overflow = wait_capped_process(
                child, stdout, stderr, process_group=True
            )
            if overflow and return_code == 0:
                return_code = 65
            completed = subprocess.CompletedProcess(command, return_code)
            if overflow:
                launch_error = (
                    "Codex runner output exceeded the retained trace limit"
                )
    except KeyboardInterrupt as exc:
        completed = subprocess.CompletedProcess([], 130)
        launch_error = f"reviewer interrupted: {type(exc).__name__}"
    except (OSError, ValueError, SystemExit, subprocess.SubprocessError) as exc:
        launch_error = f"{type(exc).__name__}: {exc}"
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(checkout)], cwd=repo,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if checkout.exists():
            shutil.rmtree(checkout)
    receipt_value = {
        "schema_version": 1,
        "status": "passed" if completed.returncode == 0 else "failed",
        "ledger": str(path.relative_to(run)),
        "packet_sha256": packet_sha,
        "ledger_findings_sha256": findings_sha,
        "review_contract_sha256": packet["review_contract_sha256"],
        "calibration_sha256": packet["calibration"]["sha256"],
        "review_head_sha": packet["head_sha"],
        "review_cwd": str(checkout),
        "reviewer": args.reviewer,
        "session_id": args.session_id,
        "execution": execution,
        "runner": packet["runner"],
        "result": str(result.relative_to(run)),
        "stdout_log": str(stdout_log.relative_to(run)),
        "stderr_log": str(stderr_log.relative_to(run)),
        "started_at": started_at,
        "exit_code": completed.returncode,
        "completed_at": now(),
    }
    receipt_committed = False
    if launch_error:
        receipt_value["validation_error"] = launch_error
    if completed.returncode == 0:
        try:
            reviewed_result, result_sha = review_result(
                result, packet["axis"], require_pass=False
            )
            validate_calibration(
                packet["axis"], packet["calibration"]["sha256"],
                reviewed_result["calibration_results"],
            )
            receipt_value["trajectory"] = parse_codex_trace(
                stdout_log, "review", args.session_id, execution, runner_version
            )
            if receipt_value["trajectory"]["disposition"] != "pass":
                raise ValueError("reviewer execution trajectory requires escalation")
            upheld = set(reviewed_result["upheld_ids"])
            closure_scope = packet.get("scope") in {"fix", "dispute"}
            if upheld:
                if (not closure_scope or reviewed_result["verdict"] != "needs-fix"
                        or reviewed_result["findings"]
                        or upheld != set(closure_ids)
                        or reviewed_result["resolved_ids"]
                        or reviewed_result["invalid_ids"]):
                    raise SystemExit(
                        "error: upheld closure review must preserve the exact frozen set"
                    )
            elif (packet.get("scope") == "dispute"
                  and reviewed_result["verdict"] == "needs-fix"):
                raise SystemExit("error: failed dispute must uphold the frozen finding set")
            if reviewed_result["verdict"] == "pass" and upheld:
                raise SystemExit("error: pass review cannot contain upheld_ids")
        except (SystemExit, ValueError) as exc:
            receipt_value["status"] = "failed"
            receipt_value["exit_code"] = 65
            receipt_value["validation_error"] = str(exc)
            with review_lock(path.parent):
                write(receipt, receipt_value)
            raise
        receipt_value["result_sha256"] = result_sha
        receipt_value["status"] = (
            "passed" if reviewed_result["verdict"] == "pass" else "needs-fix"
        )
        if reviewed_result["verdict"] == "needs-fix":
            with review_lock(path.parent):
                current = read(path)
                if object_sha256(current.get("findings", [])) != findings_sha:
                    receipt_value["status"] = "failed"
                    receipt_value["exit_code"] = 66
                    receipt_value["validation_error"] = (
                        "review ledger changed while independent reviewer was running"
                    )
                    write(receipt, receipt_value)
                    raise SystemExit("error: " + receipt_value["validation_error"])
                existing_ids = set()
                for other_path in path.parent.glob(
                        f"wave-{packet['wave']}-{packet['axis']}*.json"):
                    existing_ids.update(
                        item.get("id") for item in read(other_path).get("findings", [])
                    )
                result_ids = {item["id"] for item in reviewed_result["findings"]}
                duplicates = sorted(result_ids & existing_ids)
                if duplicates:
                    receipt_value["status"] = "failed"
                    receipt_value["exit_code"] = 67
                    receipt_value["validation_error"] = (
                        "review result reuses finding ids: " + ", ".join(duplicates)
                    )
                    write(receipt, receipt_value)
                    raise SystemExit("error: " + receipt_value["validation_error"])
                for item in reviewed_result["findings"]:
                    current["findings"].append({
                        "id": item["id"], "severity": item["severity"],
                        "title": item["title"], "body": item["review_evidence"],
                        "review_evidence": item["review_evidence"],
                        "impact": item["impact"],
                        "fix_direction": item["fix_direction"],
                        "file": item.get("file"), "line": item.get("line"),
                        "owner": item["owner"], "reviewer": args.reviewer,
                        "status": "open", "created_at": now(),
                        "resolution": None, "evidence": None,
                    })
                current.setdefault("review_attempts", []).append({
                    "status": "needs-fix",
                    "reviewer": args.reviewer,
                    "session_id": args.session_id,
                    "packet_sha256": packet_sha,
                    "result_sha256": receipt_value["result_sha256"],
                    "receipt": str(receipt.relative_to(run)),
                    "receipt_sha256": json_file_sha256(receipt_value),
                    "finding_ids": [item["id"]
                                    for item in reviewed_result["findings"]],
                    "recorded_at": now(),
                })
                commit_review_transaction(
                    path.parent, {path: current, receipt: receipt_value}
                )
                receipt_committed = True
    if not receipt_committed:
        with review_lock(path.parent):
            write(receipt, receipt_value)
    print(receipt)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def cmd_add(args, repo, _run):
    path = checked_ledger_path(repo, args.ledger)
    if args.severity not in SEVERITIES:
        raise SystemExit("error: severity must be P0, P1, P2, or P3")
    if args.id and not safe_identity(args.id):
        raise SystemExit("error: finding id must be a safe identifier")
    validate_nonempty_finding_fields(vars(args), (
        "title", "body", "impact", "fix_direction", "owner", "reviewer",
    ))
    validate_finding_location(args.file, args.line)
    finding = {
        "id": args.id or f"F-{uuid.uuid4().hex[:8]}",
        "severity": args.severity,
        "title": args.title,
        "body": args.body,
        "review_evidence": args.body,
        "impact": args.impact,
        "fix_direction": args.fix_direction,
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
        packet = ledger.get("packet", {})
        duplicates = []
        for other_path in path.parent.glob(
                f"wave-{packet.get('wave')}-{packet.get('axis')}*.json"):
            duplicates.extend(item for item in read(other_path).get("findings", [])
                              if item.get("id") == finding["id"])
        if duplicates:
            raise SystemExit(f"error: duplicate finding id {finding['id']}")
        ledger["findings"].append(finding)
        write(path, ledger)
    print(finding["id"])


def cmd_complete(args, repo, _run):
    path = checked_ledger_path(repo, args.ledger)
    with review_lock(path.parent):
        ledger = read(path)
        if ledger.get("completed_at"):
            validate_completed_review(path.parent.parent, path, ledger)
            print("complete")
            return
        open_items = [item["id"] for item in ledger["findings"] if item["status"] == "open"]
        if open_items:
            raise SystemExit(f"error: unresolved findings: {', '.join(open_items)}")
        run = path.parent.parent
        receipt_path = Path(args.receipt).resolve()
        receipts = (run / "reviews" / "receipts").resolve()
        if (receipt_path.parent != receipts or receipt_path.is_symlink()
                or not receipt_path.is_file()):
            raise SystemExit("error: --receipt must be a regular QTeam review receipt")
        receipt = read(receipt_path)
        packet = ledger.get("packet", {})
        validate_source_snapshots(run, packet)
        expected_execution = packet.get("review_execution")
        expected_result = (run / receipt.get("result", "")).resolve()
        if (receipt.get("status") != "passed" or receipt.get("exit_code") != 0
                or not receipt_paths_match_session(receipt)
                or receipt.get("ledger") != str(path.relative_to(run))
                or receipt.get("packet_sha256") != object_sha256(packet)
                or receipt.get("ledger_findings_sha256")
                != object_sha256(ledger.get("findings", []))
                or receipt.get("review_contract_sha256")
                != packet.get("review_contract_sha256")
                or (packet.get("schema_version") == 3
                    and receipt.get("calibration_sha256")
                    != packet.get("calibration", {}).get("sha256"))
                or receipt.get("review_head_sha") != packet.get("head_sha")
                or receipt.get("execution") != expected_execution
                or (packet.get("schema_version") == 3
                    and receipt.get("runner") != packet.get("runner"))
                or not safe_identity(receipt.get("reviewer"))
                or not safe_identity(receipt.get("session_id"))
                or expected_result.parent != (run / "reviews" / "results").resolve()):
            raise SystemExit("error: reviewer receipt does not match immutable packet/session")
        result, result_sha = review_result(expected_result, packet["axis"])
        if result_sha != receipt.get("result_sha256"):
            raise SystemExit("error: review result differs from reviewer receipt")
        if packet.get("schema_version") == 3:
            try:
                validate_calibration(
                    packet["axis"], packet.get("calibration", {}).get("sha256"),
                    result.get("calibration_results"),
                )
                trace_path = run_regular_file(run, receipt.get("stdout_log"))
                validate_trajectory(
                    receipt.get("trajectory"), "review",
                    receipt.get("session_id"), receipt.get("execution"), trace_path,
                )
                if receipt.get("trajectory", {}).get("runner") != receipt.get("runner"):
                    raise ValueError("review trajectory runner differs from its receipt")
                if receipt.get("trajectory", {}).get("disposition") != "pass":
                    raise ValueError("review trajectory requires escalation")
            except ValueError as exc:
                raise SystemExit(f"error: reviewer evaluation evidence is invalid: {exc}")
        closure = packet.get("closure_findings", [])
        if object_sha256(closure) != packet.get("closure_findings_sha256"):
            raise SystemExit("error: frozen closure finding set is corrupt")
        closure_ids = {item.get("id") for item in closure}
        resolved_ids = set(result.get("resolved_ids", []))
        invalid_ids = set(result.get("invalid_ids", []))
        if resolved_ids | invalid_ids != closure_ids:
            raise SystemExit("error: reviewer result does not close the exact frozen finding set")
        if packet.get("scope") == "dispute" and resolved_ids:
            raise SystemExit("error: dispute review may only invalidate disproved findings")
        result_ids = {item["id"] for item in result["findings"]}
        ledger_ids = {item.get("id") for item in ledger["findings"]}
        unknown = sorted(result_ids - ledger_ids)
        if unknown:
            raise SystemExit("error: result contains findings absent from ledger: "
                             + ", ".join(unknown))
        resolved_ledgers = {}
        for frozen in closure:
            prior_path = (run / frozen.get("ledger", "")).resolve()
            if (prior_path.parent != path.parent or prior_path == path
                    or prior_path.is_symlink() or not prior_path.is_file()):
                raise SystemExit("error: frozen closure finding ledger is invalid")
            prior = resolved_ledgers.setdefault(prior_path, read(prior_path))
            matches = [item for item in prior.get("findings", [])
                       if item.get("id") == frozen.get("id")]
            if len(matches) != 1 or any(
                    matches[0].get(field) != frozen.get(field)
                    for field in ("severity", "title", "body", "review_evidence",
                                  "impact", "fix_direction", "owner")):
                raise SystemExit("error: frozen closure finding differs from its ledger")
            if matches[0].get("status") != "open":
                raise SystemExit("error: frozen closure finding is no longer open")
        completed_at = now()
        receipt_sha = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        closure_writes = {}
        for prior_path, prior in resolved_ledgers.items():
            ids = {item["id"] for item in closure
                   if (run / item["ledger"]).resolve() == prior_path}
            for item in prior["findings"]:
                if item.get("id") in ids:
                    item["status"] = ("resolved" if item["id"] in resolved_ids
                                      else "invalid")
                    item["resolution"] = (
                        "verified by fresh fix-scope re-review"
                        if item["id"] in resolved_ids
                        else result["invalid_evidence"][item["id"]]
                    )
                    item["evidence"] = str(receipt_path.relative_to(run))
                    item["reviewer"] = receipt["reviewer"]
                    item["resolved_at"] = completed_at
            closure_writes[prior_path] = prior
        ledger["completed_at"] = completed_at
        ledger["completed_by"] = receipt["reviewer"]
        ledger["attestation"] = {
            "reviewer": receipt["reviewer"],
            "session_id": receipt["session_id"],
            "result_sha256": result_sha,
            "execution": expected_execution,
            "receipt": str(receipt_path.relative_to(run)),
            "receipt_sha256": receipt_sha,
            "result": result,
        }
        closure_writes[path] = ledger
        commit_review_transaction(path.parent, closure_writes)
    print("complete")


def cmd_check(args, repo, run):
    state = read(run / "state.json")
    wave_policy = wave_policy_for(state, args.wave)
    require_risk = (args.require_risk or state.get("risk_forced", False)
                    or wave_policy.get("require_risk_review", False))
    axes = ["spec", "standards"] + (["risk"] if require_risk else [])
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
        try:
            validate_source_snapshots(run, packet)
            if ledger.get("completed_at"):
                validate_completed_review(run, path, ledger)
        except SystemExit as exc:
            errors.append(f"{axis} ledger integrity failure: {exc}")
        if not packet_matches_policy(packet, state, wave_policy):
            errors.append(f"{axis} ledger policy is stale or invalid")
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
                or attestation.get("execution") != packet.get("review_execution")
                or result.get("axis") != axis or result.get("verdict") != "pass"):
            errors.append(f"{axis} review has no valid independent attestation")
        else:
            attestations[axis] = attestation
    if all(axis in attestations for axis in ("spec", "standards")):
        if attestations["spec"]["session_id"] == attestations["standards"]["session_id"]:
            errors.append("spec and standards reviews must use distinct sessions")
        if attestations["spec"]["reviewer"] == attestations["standards"]["reviewer"]:
            errors.append("spec and standards reviews must use distinct reviewers")
    if "risk" in attestations:
        for axis in ("spec", "standards"):
            if axis not in attestations:
                continue
            if attestations["risk"]["session_id"] == attestations[axis]["session_id"]:
                errors.append(f"risk and {axis} reviews must use distinct sessions")
            if attestations["risk"]["reviewer"] == attestations[axis]["reviewer"]:
                errors.append(f"risk and {axis} reviews must use distinct reviewers")
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
         "--head", args.head, "--wave", str(args.wave),
         *(["--require-risk"] if args.require_risk else [])],
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
    p.add_argument("--scope", choices=("wave", "fix", "dispute", "final"),
                   default="wave")
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--spec-source", action="append", default=[])
    p.add_argument("--standards-source", action="append", default=[])
    p.add_argument("--digest-source", action="append", default=[])
    p.add_argument(
        "--refresh-runner", action="store_true",
        help="refresh only an unstarted packet whose Codex runner changed",
    )
    p.set_defaults(func=cmd_create)
    p = sub.add_parser("add")
    p.add_argument("--ledger", required=True)
    p.add_argument("--id")
    p.add_argument("--severity", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--body", required=True)
    p.add_argument("--impact", required=True)
    p.add_argument("--fix-direction", required=True)
    p.add_argument("--file")
    p.add_argument("--line", type=int)
    p.add_argument("--owner", required=True)
    p.add_argument("--reviewer", required=True,
                   help="identity of the read-only reviewer whose structured finding is recorded")
    p.set_defaults(func=cmd_add)
    p = sub.add_parser("complete")
    p.add_argument("--ledger", required=True)
    p.add_argument("--receipt", required=True,
                   help="immutable receipt produced by agent-team-review run")
    p.set_defaults(func=cmd_complete)
    p = sub.add_parser("run")
    p.add_argument("--ledger", required=True)
    p.add_argument("--reviewer", required=True)
    p.add_argument("--session-id", required=True)
    p.set_defaults(func=cmd_run)
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
    if run is None and getattr(args, "ledger", None):
        ledger = read(Path(args.ledger).resolve())
        run_id = ledger.get("packet", {}).get("run_id")
        if not run_id:
            raise SystemExit("error: review ledger has no run identity")
        run = run_dir(repo, run_id)
    if run is not None:
        state = read(run / "state.json")
        if state.get("publication_seal"):
            raise SystemExit(
                "error: publication seal freezes review packets and findings"
            )
    args.func(args, repo, run)


if __name__ == "__main__":
    main()
