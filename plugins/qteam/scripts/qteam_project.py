"""Shared QTeam project-runtime manifest and filesystem safety contract."""

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path, PurePosixPath


AGENTS = (
    "researcher", "architect", "parallel-planner", "test-designer",
    "spec-reviewer", "standards-reviewer", "risk-reviewer",
)
OBSOLETE_AGENTS = (
    "developer", "debugger", "frontend-debugger", "system-debugger", "tester",
    "integration-tester", "code-reviewer", "knowledge-distiller",
)
WORKER_PROMPTS = (
    "debugger", "developer", "fixer", "frontend-debugger", "integration-tester",
    "knowledge-distiller", "system-debugger", "test-writer",
)
SCHEMAS = (
    "artifact-lint", "code-index", "decision-gate", "diagnosis", "epic",
    "eval-case", "experiment", "experiment-verification", "finding", "goal-status", "handoff", "review-receipt", "review-result",
    "run-state", "scenario-coverage", "spec-drift", "task-policy", "task",
    "project-policy", "quality-lane", "queue-item", "tdd-cycle", "trajectory",
    "verification", "worker-result",
)
UI_FILES = ("index.html", "app.js", "styles.css")
PLUGIN_SKILLS = (
    "agent-team-dev", "brainstorming", "domain-modeling",
    "goal-execution-discipline", "grill-me", "grill-with-docs", "grilling",
    "qteam-diagnose", "qteam-explore", "qteam-review", "qteam-router",
    "qteam-goal", "qteam-harden", "qteam-tdd", "to-spec", "to-tickets",
    "verification-before-completion", "wayfinder", "writing-plans",
)
LEGACY_OWNED_PLUGIN_SKILLS = tuple(
    name for name in PLUGIN_SKILLS
    if name not in {"qteam-explore", "qteam-goal", "qteam-harden"}
)
LOCAL_SKILL_CONFLICTS = ("qteam-explore", "qteam-goal", "qteam-harden")
LEGACY_ORCHESTRATION_SKILLS = (
    "using-superpowers", "executing-plans", "subagent-driven-development",
    "requesting-code-review", "receiving-code-review",
    "finishing-a-development-branch", "using-git-worktrees",
    "test-driven-development", "systematic-debugging",
    "dispatching-parallel-agents",
)
BINARIES = (
    "wake-agent-team", "agent-team-artifact", "agent-team-finish",
    "agent-team-check-task", "agent-team-doctor", "agent-team-state",
    "agent-team-goal",
    "agent-team-worker", "agent-team-review", "agent-team-web",
    "agent-team-session", "agent_team_artifact.py", "agent_team_eval.py",
    "agent_team_policy.py", "import-agent-learning", "qteam-project-uninstall",
    "qteam_project.py",
)

INSTALLED_PATHS = frozenset(
    [f".codex/agents/{name}.toml" for name in AGENTS]
    + [f".codex/worker-prompts/{name}.md" for name in WORKER_PROMPTS]
    + [".codex/worker-prompts/wake-prompt.md"]
    + [f".codex/schemas/{name}.schema.json" for name in SCHEMAS]
    + [f".codex/bin/{name}" for name in BINARIES]
    + [f".codex/qteam-ui/{name}" for name in UI_FILES]
    + [
        ".codex/QTEAM-THIRD-PARTY-NOTICES.md",
        ".codex/licenses/Matt-Pocock-MIT.txt",
        ".codex/licenses/Superpowers-MIT.txt",
        ".codex/licenses/Autoresearch-MIT.txt",
        ".codex/licenses/LoopX-MIT.txt",
        ".codex/licenses/Smart-Ralph-MIT.txt",
        ".codex/agent-team-template.version",
    ]
)
MUTABLE_PATHS = frozenset((".codex/config.toml", ".gitignore"))
MOVED_PATHS = frozenset(
    [f".codex/agents/{name}.toml" for name in OBSOLETE_AGENTS]
    + [f".agents/skills/{name}" for name in LEGACY_OWNED_PLUGIN_SKILLS]
    + [f".agents/skills/{name}" for name in LEGACY_ORCHESTRATION_SKILLS]
    + [".codex/bin/__pycache__"]
)
V012_INSTALLED_PATHS = frozenset(
    INSTALLED_PATHS - {
        ".codex/bin/agent-team-goal",
        ".codex/schemas/goal-status.schema.json",
    }
)
V011_INSTALLED_PATHS = frozenset(
    V012_INSTALLED_PATHS - {
        ".codex/bin/agent-team-web",
        ".codex/bin/agent-team-session",
        ".codex/schemas/project-policy.schema.json",
        ".codex/schemas/quality-lane.schema.json",
        ".codex/schemas/queue-item.schema.json",
        ".codex/qteam-ui/index.html",
        ".codex/qteam-ui/app.js",
        ".codex/qteam-ui/styles.css",
    }
)
V010_INSTALLED_PATHS = frozenset(
    V011_INSTALLED_PATHS - {
        ".codex/bin/agent_team_eval.py",
        ".codex/schemas/eval-case.schema.json",
        ".codex/schemas/trajectory.schema.json",
    }
)
V09_INSTALLED_PATHS = frozenset(
    V010_INSTALLED_PATHS - {
        ".codex/bin/agent-team-artifact",
        ".codex/bin/agent_team_artifact.py",
        ".codex/licenses/Smart-Ralph-MIT.txt",
        ".codex/schemas/artifact-lint.schema.json",
        ".codex/schemas/code-index.schema.json",
        ".codex/schemas/epic.schema.json",
        ".codex/schemas/spec-drift.schema.json",
    }
)
V07_INSTALLED_PATHS = frozenset(
    V09_INSTALLED_PATHS - {
        ".codex/licenses/LoopX-MIT.txt",
        ".codex/schemas/decision-gate.schema.json",
        ".codex/schemas/handoff.schema.json",
        ".codex/schemas/scenario-coverage.schema.json",
    }
)
V06_INSTALLED_PATHS = frozenset(
    V07_INSTALLED_PATHS - {
        ".codex/licenses/Autoresearch-MIT.txt",
        ".codex/schemas/experiment.schema.json",
    }
)
EARLY_V06_INSTALLED_PATHS = frozenset(
    V06_INSTALLED_PATHS - {".codex/bin/import-agent-learning"}
)
V06_MOVED_PATHS = MOVED_PATHS
SCHEMA2_MOVED_PATHS = frozenset(V06_MOVED_PATHS - {".codex/bin/__pycache__"})
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
STAMP_PATTERN = re.compile(r"^[0-9]{14,20}-[0-9]+$")
VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def normalized_relative(raw):
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise ValueError(f"unsafe QTeam manifest path: {raw!r}")
    value = PurePosixPath(raw)
    if value.is_absolute() or any(part in {"", ".", ".."} for part in value.parts):
        raise ValueError(f"unsafe QTeam manifest path: {raw!r}")
    if value.as_posix() != raw:
        raise ValueError(f"non-canonical QTeam manifest path: {raw!r}")
    return value


def safe_path(root, raw):
    """Return a lexical child path after rejecting every existing symlink parent."""
    relative = normalized_relative(raw)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"managed path contains symlink: {raw}")
        if current.exists() and current != root / relative and not current.is_dir():
            raise ValueError(f"managed path parent is not a directory: {raw}")
    return root.joinpath(*relative.parts)


def file_digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def bytes_digest(value):
    return hashlib.sha256(value).hexdigest()


def _tree_digest(path, include_modes):
    value = hashlib.sha256()

    def visit(candidate, relative):
        info = candidate.lstat()
        mode = info.st_mode
        value.update(relative.as_posix().encode("utf-8") + b"\0")
        if include_modes:
            value.update(f"{stat.S_IMODE(mode):04o}".encode("ascii") + b"\0")
        if stat.S_ISREG(mode):
            value.update(b"file\0")
            with candidate.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    value.update(chunk)
        elif stat.S_ISDIR(mode):
            value.update(b"dir\0")
            for child in sorted(candidate.iterdir(), key=lambda item: item.name):
                visit(child, relative / child.name)
        elif stat.S_ISLNK(mode):
            value.update(b"symlink\0" + os.readlink(candidate).encode("utf-8"))
        else:
            raise ValueError(f"unsupported special file in QTeam backup: {candidate}")
        value.update(b"\0")

    visit(path, Path("."))
    return value.hexdigest()


def tree_digest(path):
    """Hash content, structure, and permission modes without following links."""
    return _tree_digest(path, include_modes=True)


def legacy_tree_digest(path):
    """Read a schema-v2 tree digest, which predates permission integrity."""
    return _tree_digest(path, include_modes=False)


def atomic_write(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        # chmod mutates inode metadata after the content fsync. Persist that
        # metadata before publishing the file so a power loss cannot leave a
        # content-valid snapshot or restored executable with mkstemp's 0600.
        mode_descriptor = os.open(temporary, os.O_RDONLY)
        try:
            os.fsync(mode_descriptor)
        finally:
            os.close(mode_descriptor)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_json(path, value):
    atomic_write(
        path,
        (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def require_sha(value, label):
    if not isinstance(value, str) or HEX_SHA256.fullmatch(value) is None:
        raise ValueError(f"invalid SHA-256 in QTeam manifest: {label}")


def require_mode(value, label):
    if type(value) is not int or not 0 <= value <= 0o777:
        raise ValueError(f"invalid permission mode in QTeam manifest: {label}")


def validate_manifest(root, manifest):
    if not isinstance(manifest, dict):
        raise ValueError("QTeam project manifest must be an object")
    expected_keys = {
        "schema_version", "plugin", "version", "stamp", "phase", "backup_root",
        "installed_files", "moved_paths", "mutable_files",
    }
    if set(manifest) != expected_keys:
        raise ValueError("QTeam project manifest has unknown or missing fields")
    schema_version = manifest.get("schema_version")
    if schema_version not in {2, 3} or manifest.get("plugin") != "qteam":
        raise ValueError("unsupported QTeam project manifest")
    version = manifest.get("version")
    if not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None:
        raise ValueError("invalid QTeam project manifest version")
    version_tuple = tuple(int(part) for part in version.split("."))
    if schema_version == 2 and version_tuple >= (0, 7, 0):
        raise ValueError("unsupported QTeam schema-version contract")
    allowed_phases = {"installing", "installed", "restored"}
    if schema_version == 3:
        allowed_phases.add("preparing")
    if manifest.get("phase") not in allowed_phases:
        raise ValueError("invalid QTeam project manifest phase")
    stamp = manifest.get("stamp")
    if not isinstance(stamp, str) or STAMP_PATTERN.fullmatch(stamp) is None:
        raise ValueError("invalid QTeam project manifest stamp")
    backup_relative = f".codex/qteam-backups/install/{stamp}"
    if manifest.get("backup_root") != backup_relative:
        raise ValueError("QTeam backup root does not match manifest stamp")
    backup_root = safe_path(root, backup_relative)
    if backup_root.exists() and not backup_root.is_dir():
        raise ValueError("QTeam uninstall backup is not a directory")
    if manifest.get("phase") in {"installing", "installed"} and not backup_root.is_dir():
        raise ValueError("missing QTeam uninstall backup")

    def records(name, allowed, fields):
        values = manifest.get(name)
        if not isinstance(values, list):
            raise ValueError(f"QTeam manifest {name} must be an array")
        seen = set()
        for record in values:
            if not isinstance(record, dict) or set(record) != fields:
                raise ValueError(f"invalid QTeam manifest record in {name}")
            path = record.get("path")
            if path not in allowed or path in seen:
                raise ValueError(f"unexpected or duplicate QTeam managed path: {path!r}")
            safe_path(root, path)
            seen.add(path)
        return values

    file_fields = {"path", "sha256", "prior", "prior_sha256"}
    installed_allowed = (
        EARLY_V06_INSTALLED_PATHS if schema_version == 2 else INSTALLED_PATHS
    )
    if schema_version == 3:
        file_fields |= {"mode", "prior_mode"}
    installed = records("installed_files", installed_allowed, file_fields)
    installed_set = frozenset(record["path"] for record in installed)
    if schema_version == 2:
        supported_sets = (EARLY_V06_INSTALLED_PATHS,)
    elif version_tuple < (0, 7, 0):
        supported_sets = (V06_INSTALLED_PATHS, EARLY_V06_INSTALLED_PATHS)
    elif version_tuple < (0, 8, 0):
        supported_sets = (V07_INSTALLED_PATHS,)
    elif version_tuple < (0, 10, 0):
        supported_sets = (V09_INSTALLED_PATHS,)
    elif version_tuple < (0, 11, 0):
        supported_sets = (V010_INSTALLED_PATHS,)
    elif version_tuple < (0, 12, 0):
        supported_sets = (V011_INSTALLED_PATHS,)
    elif version_tuple < (0, 13, 0):
        supported_sets = (V012_INSTALLED_PATHS,)
    else:
        supported_sets = (INSTALLED_PATHS,)
    if installed_set not in supported_sets:
        raise ValueError("QTeam project manifest omits installed runtime paths")
    for record in installed:
        require_sha(record.get("sha256"), record["path"])
        if schema_version == 3:
            require_mode(record.get("mode"), record["path"])
        expected_prior = f"{backup_relative}/files/{record['path']}"
        if record.get("prior") is None:
            if record.get("prior_sha256") is not None:
                raise ValueError("QTeam prior metadata exists without prior path")
            if schema_version == 3 and record.get("prior_mode") is not None:
                raise ValueError("QTeam prior metadata exists without prior path")
        else:
            if record.get("prior") != expected_prior:
                raise ValueError("QTeam installed-file prior path is outside its backup slot")
            require_sha(record.get("prior_sha256"), expected_prior)
            if schema_version == 3:
                require_mode(record.get("prior_mode"), expected_prior)
            safe_path(root, expected_prior)

    mutable = records("mutable_files", MUTABLE_PATHS, file_fields)
    if {record["path"] for record in mutable} != set(MUTABLE_PATHS):
        raise ValueError("QTeam project manifest omits mutable runtime paths")
    mutable_prior_names = {
        ".codex/config.toml": "config.toml", ".gitignore": "gitignore",
    }
    for record in mutable:
        require_sha(record.get("sha256"), record["path"])
        if schema_version == 3:
            require_mode(record.get("mode"), record["path"])
        expected_prior = f"{backup_relative}/pre/{mutable_prior_names[record['path']]}"
        if record.get("prior") is None:
            if record.get("prior_sha256") is not None:
                raise ValueError("QTeam mutable prior metadata exists without prior path")
            if schema_version == 3 and record.get("prior_mode") is not None:
                raise ValueError("QTeam mutable prior metadata exists without prior path")
        else:
            if record.get("prior") != expected_prior:
                raise ValueError("QTeam mutable prior path is outside its backup slot")
            require_sha(record.get("prior_sha256"), expected_prior)
            if schema_version == 3:
                require_mode(record.get("prior_mode"), expected_prior)
            safe_path(root, expected_prior)

    if schema_version == 2 or installed_set == EARLY_V06_INSTALLED_PATHS:
        moved_allowed = SCHEMA2_MOVED_PATHS
    elif version_tuple < (0, 7, 0):
        moved_allowed = V06_MOVED_PATHS
    else:
        moved_allowed = MOVED_PATHS
    moved = records(
        "moved_paths", moved_allowed,
        {"path", "backup", "backup_sha256"},
    )
    for record in moved:
        expected_backup = f"{backup_relative}/moved/{record['path']}"
        if record.get("backup") != expected_backup:
            raise ValueError("QTeam moved-path backup is outside its backup slot")
        require_sha(record.get("backup_sha256"), expected_backup)
        safe_path(root, expected_backup)
    return backup_root


def _legacy_installed_mode(path):
    name = PurePosixPath(path).name
    if path.startswith(".codex/bin/") and name not in {
        "agent_team_artifact.py", "agent_team_policy.py", "qteam_project.py",
    }:
        return 0o755
    return 0o644


def normalize_legacy_manifest(root, manifest):
    """Upgrade a validated schema-v2 manifest in memory for safe recovery."""
    if manifest.get("schema_version") != 2:
        return

    for record in manifest["installed_files"] + manifest["mutable_files"]:
        prior_mode = None
        if record["prior"] is not None:
            prior = safe_path(root, record["prior"])
            destination = safe_path(root, record["path"])
            if prior.is_file() and file_digest(prior) == record["prior_sha256"]:
                prior_mode = prior.stat().st_mode & 0o777
            elif (
                manifest["phase"] == "restored"
                and destination.is_file()
                and file_digest(destination) == record["prior_sha256"]
            ):
                prior_mode = destination.stat().st_mode & 0o777
            else:
                raise ValueError(
                    f"cannot migrate QTeam v2 backup metadata: {record['path']}"
                )
        record["prior_mode"] = prior_mode
        if record["path"] in MUTABLE_PATHS:
            record["mode"] = prior_mode if prior_mode is not None else 0o644
        else:
            record["mode"] = _legacy_installed_mode(record["path"])

    for record in manifest["moved_paths"]:
        saved = safe_path(root, record["backup"])
        destination = safe_path(root, record["path"])
        candidate = saved if saved.exists() else destination
        if not candidate.exists() or legacy_tree_digest(candidate) != record["backup_sha256"]:
            raise ValueError(
                f"cannot migrate QTeam v2 moved backup: {record['path']}"
            )
        record["backup_sha256"] = tree_digest(candidate)

    manifest["schema_version"] = 3
