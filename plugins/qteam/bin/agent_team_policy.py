#!/usr/bin/env python3
"""Deterministic QTeam task risk, model-tier, and review policy derivation."""

import hashlib
import re
from pathlib import PurePosixPath


POLICY_VERSION = 1
WORK_KINDS = {
    "feature", "bugfix", "debug", "refactor", "test", "integration",
    "docs", "config", "generated", "learning", "experiment",
}
RISK_FLAGS = {
    "concurrency", "security", "migration", "data-loss", "authorization",
    "authentication", "compatibility", "public-api",
}
MODEL_TIERS = ("economy", "standard", "deep")
MODEL_PROFILES = {
    "economy": {"model": "gpt-5.6-terra", "thinking": "low"},
    "standard": {"model": "gpt-5.6-terra", "thinking": "medium"},
    "deep": {"model": "gpt-5.6-sol", "thinking": "high"},
}
TIER_ORDER = {name: index for index, name in enumerate(MODEL_TIERS)}
ROLE_MINIMUM = {
    "developer": "economy",
    "debugger": "standard",
    "frontend-debugger": "standard",
    "system-debugger": "standard",
    "test-writer": "standard",
    "integration-tester": "standard",
    "fixer": "standard",
    "knowledge-distiller": "economy",
}
REVIEW_AXIS_INSTRUCTIONS = {
    "spec": (
        "Check the changed behavior against frozen spec/acceptance sources. Flag "
        "missing, extra, ambiguous, fallback, or untested required behavior."
    ),
    "standards": (
        "Check correctness, error handling, data flow, repository standards, "
        "architecture, maintainability, regression risk, and required tests. Ignore "
        "style-only preferences and optional refactors."
    ),
    "risk": (
        "Check only named risk flags and their ownership, failure, rollback, race, "
        "privilege, compatibility, and irreversible-effect paths."
    ),
}
REVIEW_INTENSITY_INSTRUCTIONS = {
    "compact": (
        "Read only the final diff, affected acceptance/contracts, and focused tests; "
        "do not audit unrelated callers or the repository."
    ),
    "full": (
        "Read the wave diff once, following only necessary callers, error paths, and "
        "integration cases with concrete relevance."
    ),
    "risk": (
        "Apply full scope plus only the named risk and rollback/failure paths; do not "
        "expand into a general audit."
    ),
}
REVIEW_FINDING_INSTRUCTIONS = (
    "Every finding must have a stable id, P0-P3 severity, concise title, concrete "
    "review_evidence, user/system impact, an owned fix_direction, and a non-empty "
    "owner; do not emit style-only preferences or optional refactors."
)
REVIEW_CLOSURE_INSTRUCTIONS = (
    "Partition the frozen closure set between resolved_ids (verified fixed) and "
    "invalid_ids (independently disproved). invalid_evidence must map every and only "
    "invalid_id to a non-empty disproof rationale. A dispute that disproves the "
    "finding returns pass with every closure ID in invalid_ids and a rationale map. "
    "A dispute that confirms the finding, or a fix re-review that finds it still "
    "unfixed, returns needs-fix with findings [], every closure ID in upheld_ids, "
    "and empty resolved/invalid sets."
)


def safe_identifier(value):
    return (isinstance(value, str) and bool(value)
            and value[0].isascii() and value[0].isalnum()
            and value not in {".", ".."} and ".." not in value
            and all(ch.isascii() and (ch.isalnum() or ch in "._-") for ch in value))


def review_contract_digest(axis, intensity):
    contract = (REVIEW_AXIS_INSTRUCTIONS[axis] + "\n"
                + REVIEW_INTENSITY_INSTRUCTIONS[intensity] + "\n"
                + REVIEW_FINDING_INSTRUCTIONS + "\n"
                + REVIEW_CLOSURE_INSTRUCTIONS)
    return hashlib.sha256(contract.encode()).hexdigest()


def _path_risks(patterns):
    inferred = set()
    for raw in patterns:
        value = str(PurePosixPath(str(raw))).lower()
        segments = set(filter(None, re.split(r"[^a-z0-9]+", value)))
        if "migrations" in segments or "migration" in segments:
            inferred.add("migration")
        if segments & {"auth", "authentication", "login", "oauth", "session"}:
            inferred.add("authentication")
        if segments & {"permission", "permissions", "authorization", "acl", "rbac"}:
            inferred.add("authorization")
        if segments & {"security", "crypto", "cryptography", "secrets"}:
            inferred.add("security")
        if segments & {"lock", "locks", "thread", "threads", "concurrency", "race"}:
            inferred.add("concurrency")
        if ("schemas" in segments or "schema" in segments or value.endswith(".proto")
                or "openapi" in value):
            inferred.update({"compatibility", "public-api"})
    return inferred


def derive_task_policy(task):
    work_kind = task.get("work_kind")
    if work_kind not in WORK_KINDS:
        raise ValueError("work_kind must be one of: " + ", ".join(sorted(WORK_KINDS)))
    declared = task.get("risk_flags")
    if (not isinstance(declared, list)
            or any(not isinstance(flag, str) or flag not in RISK_FLAGS
                   for flag in declared)):
        raise ValueError("risk_flags must contain only: " + ", ".join(sorted(RISK_FLAGS)))
    if len(declared) != len(set(declared)):
        raise ValueError("risk_flags must not contain duplicates")
    inferred = _path_risks(task.get("write_set", []))
    effective = set(declared) | inferred
    reasons = []
    if effective:
        tier = "deep"
        reasons.append("high-risk surface: " + ", ".join(sorted(effective)))
    elif work_kind in {"bugfix", "debug", "refactor", "integration", "experiment"}:
        tier = "standard"
        reasons.append(f"judgment-heavy work kind: {work_kind}")
    elif task.get("allow_shared_surfaces"):
        tier = "standard"
        reasons.append("shared surface requires serial judgment")
    elif len(task.get("write_set", [])) >= 4:
        tier = "standard"
        reasons.append("broad write set")
    else:
        tier = "economy"
        reasons.append("bounded low-risk vertical slice")
    return {
        "policy_version": POLICY_VERSION,
        "work_kind": work_kind,
        "declared_risk_flags": sorted(set(declared)),
        "inferred_risk_flags": sorted(inferred),
        "effective_risk_flags": sorted(effective),
        "execution_tier": tier,
        "review_intensity": {
            "economy": "compact", "standard": "full", "deep": "risk"
        }[tier],
        "require_risk_review": tier == "deep",
        "tdd_required": work_kind in {"feature", "bugfix"},
        "diagnosis_required": work_kind in {"bugfix", "debug"},
        "reasons": reasons,
    }


def effective_execution(policy, role, model_profiles=None):
    requested = policy["execution_tier"]
    minimum = ROLE_MINIMUM.get(role, "standard")
    tier = max((requested, minimum), key=TIER_ORDER.__getitem__)
    profiles = MODEL_PROFILES if model_profiles is None else model_profiles
    profile = profiles.get(tier) if isinstance(profiles, dict) else None
    if (not isinstance(profile, dict)
            or not isinstance(profile.get("model"), str) or not profile["model"]
            or profile.get("thinking") not in {"low", "medium", "high", "xhigh"}):
        raise ValueError(f"missing or invalid model profile for tier {tier}")
    return {
        "tier": tier,
        "model": profile["model"],
        "thinking": profile["thinking"],
        "reason": (f"task={requested}; role={role} minimum={minimum}"),
    }
