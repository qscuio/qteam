#!/usr/bin/env python3
"""Install the minimal QTeam runtime into one Git repository."""

import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from qteam_project import (
    AGENTS, BINARIES, INSTALLED_PATHS, LEGACY_ORCHESTRATION_SKILLS,
    LEGACY_OWNED_PLUGIN_SKILLS, LOCAL_SKILL_CONFLICTS, MOVED_PATHS,
    MUTABLE_PATHS, OBSOLETE_AGENTS, SCHEMAS, UI_FILES, WORKER_PROMPTS,
    atomic_write, atomic_write_json, bytes_digest, file_digest, safe_path,
    tree_digest, validate_manifest,
)


SECTION_RE = re.compile(
    r"^\s*(?:\[[^\[\]]+\]|\[\[[^\[\]]+\]\])\s*(?:#.*)?$"
)
AGENTS_RE = re.compile(r"^\s*\[\s*agents\s*\]\s*(?:#.*)?$")
AGENTS_SEMANTIC_HEADER_RE = re.compile(
    r'''^\s*\[\[?\s*(?:agents|"agents"|'agents')(?:\s*[.\]])'''
)
ROOT_AGENTS_KEY_RE = re.compile(
    r'''^\s*(?:agents|"agents"|'agents')\s*(?:[.=])'''
)
ESCAPED_BASIC_KEY_RE = re.compile(r'''^\s*"[^"\n]*\\''')
ESCAPED_BASIC_HEADER_RE = re.compile(r'''^\s*\[\[?\s*"[^"\n]*\\''')
REAL_CAPACITY_RE = re.compile(r"^\s*max_concurrent_threads_per_session\s*=")
LEGACY_CAPACITY_RE = re.compile(r"^\s*max_threads\s*=\s*(.+)$")
DEPTH_RE = re.compile(r"^\s*max_depth\s*=")


def git(args, cwd):
    completed = subprocess.run(
        ["git", *args], cwd=cwd, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise ValueError(completed.stderr.strip())
    return completed.stdout.strip()


def repository_root(value):
    raw = git(["-C", value, "rev-parse", "--show-toplevel"], Path.cwd())
    return Path(raw).resolve()


def merge_config(text):
    if text and not text.endswith("\n"):
        text += "\n"
    lines = text.splitlines()
    matches = [index for index, line in enumerate(lines) if AGENTS_RE.match(line)]
    if len(matches) > 1:
        raise ValueError("config.toml contains duplicate [agents] tables")
    if not matches:
        first_section = next(
            (index for index, line in enumerate(lines) if SECTION_RE.match(line)),
            len(lines),
        )
        semantic_header = any(AGENTS_SEMANTIC_HEADER_RE.match(line) for line in lines)
        semantic_root_key = any(
            ROOT_AGENTS_KEY_RE.match(line) for line in lines[:first_section]
        )
        escaped_header = any(ESCAPED_BASIC_HEADER_RE.match(line) for line in lines)
        escaped_root_key = any(
            ESCAPED_BASIC_KEY_RE.match(line) for line in lines[:first_section]
        )
        if semantic_header or semantic_root_key or escaped_header or escaped_root_key:
            raise ValueError(
                "config.toml defines agents with a quoted, dotted, or inline form; "
                "QTeam refuses to rewrite it"
            )
        if text.strip():
            text += "\n"
        return text + (
            "[agents]\n"
            "max_concurrent_threads_per_session = 6\n"
            "max_depth = 1\n"
        )
    start = matches[0]
    end = next(
        (index for index in range(start + 1, len(lines))
         if SECTION_RE.match(lines[index])),
        len(lines),
    )
    block = lines[start + 1:end]
    real_count = sum(bool(REAL_CAPACITY_RE.match(line)) for line in block)
    depth_count = sum(bool(DEPTH_RE.match(line)) for line in block)
    legacy_count = sum(bool(LEGACY_CAPACITY_RE.match(line)) for line in block)
    if any(ESCAPED_BASIC_KEY_RE.match(line) for line in block):
        raise ValueError(
            "config.toml [agents] contains an escaped quoted key QTeam refuses to rewrite"
        )
    for key, count in (
        ("max_concurrent_threads_per_session", real_count),
        ("max_depth", depth_count),
        ("max_threads", legacy_count),
    ):
        unsafe_key = re.compile(
            rf'''^\s*(?:"{re.escape(key)}"|'{re.escape(key)}'|{re.escape(key)}\s*\.)'''
        )
        if count == 0 and any(unsafe_key.match(line) for line in block):
            raise ValueError(
                f"config.toml [agents] key {key!r} uses a form QTeam refuses to rewrite"
            )
    if real_count > 1 or depth_count > 1:
        raise ValueError("config.toml [agents] contains duplicate QTeam capacity keys")
    migrated = []
    capacity_present = real_count == 1
    for line in block:
        legacy = LEGACY_CAPACITY_RE.match(line)
        if not legacy:
            migrated.append(line)
        elif not capacity_present:
            migrated.append(f"max_concurrent_threads_per_session = {legacy.group(1)}")
            capacity_present = True
    if not capacity_present:
        migrated.append("max_concurrent_threads_per_session = 6")
    if depth_count == 0:
        migrated.append("max_depth = 1")
    lines[start + 1:end] = migrated
    return "\n".join(lines) + "\n"


def merge_gitignore(text):
    required = (
        ".agents/runs/", ".agents/tmp/", "*.bak.*",
        ".codex/qteam-backups/", ".codex/qteam-project.json",
        ".codex/qteam-refresh/",
        ".codex/agent-team-template.version",
    )
    present = set(text.splitlines())
    missing = [line for line in required if line not in present]
    if not missing:
        return text
    if text and not text.endswith("\n"):
        text += "\n"
    return text + "\n# QTeam local runtime and recovery data\n" + "\n".join(missing) + "\n"


def source_bytes(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing or unsafe QTeam plugin source: {path}")
    return path.read_bytes()


def installed_payloads(plugin_root, version, source_commit, stamp):
    payloads = {}
    for name in AGENTS:
        payloads[f".codex/agents/{name}.toml"] = (
            source_bytes(plugin_root / f"agents/{name}.toml"), 0o644,
        )
    for name in WORKER_PROMPTS:
        payloads[f".codex/worker-prompts/{name}.md"] = (
            source_bytes(plugin_root / f"worker-prompts/{name}.md"), 0o644,
        )
    payloads[".codex/worker-prompts/wake-prompt.md"] = (
        source_bytes(plugin_root / "skills/agent-team-dev/references/wake-prompt.md"),
        0o644,
    )
    for name in SCHEMAS:
        payloads[f".codex/schemas/{name}.schema.json"] = (
            source_bytes(plugin_root / f"schemas/{name}.schema.json"), 0o644,
        )
    for name in UI_FILES:
        payloads[f".codex/qteam-ui/{name}"] = (
            source_bytes(plugin_root / f"ui/{name}"), 0o644,
        )
    binary_sources = {
        "wake-agent-team": "bin/wake-agent-team.sh",
        "agent-team-artifact": "bin/agent_team_artifact.py",
        "agent-team-finish": "bin/agent-team-finish.py",
        "agent-team-check-task": "bin/agent-team-check-task.py",
        "agent-team-doctor": "bin/agent-team-doctor.sh",
        "agent-team-state": "bin/agent-team-state.py",
        "agent-team-goal": "bin/agent-team-goal.py",
        "agent-team-worker": "bin/agent-team-worker.py",
        "agent-team-review": "bin/agent-team-review.py",
        "agent-team-web": "bin/agent-team-web.py",
        "agent-team-session": "bin/agent-team-session.py",
        "agent_team_artifact.py": "bin/agent_team_artifact.py",
        "agent_team_eval.py": "bin/agent_team_eval.py",
        "agent_team_policy.py": "bin/agent_team_policy.py",
        "import-agent-learning": "bin/import-agent-learning.py",
        "qteam-project-uninstall": "scripts/project-uninstall.py",
        "qteam_project.py": "scripts/qteam_project.py",
    }
    if set(binary_sources) != set(BINARIES):
        raise ValueError("internal QTeam binary source contract is inconsistent")
    for destination, source in binary_sources.items():
        mode = 0o644 if destination.endswith(".py") else 0o755
        payloads[f".codex/bin/{destination}"] = (
            source_bytes(plugin_root / source), mode,
        )
    payloads[".codex/QTEAM-THIRD-PARTY-NOTICES.md"] = (
        source_bytes(plugin_root / "THIRD_PARTY_NOTICES.md"), 0o644,
    )
    for name in (
        "Matt-Pocock-MIT.txt", "Superpowers-MIT.txt", "Autoresearch-MIT.txt",
        "LoopX-MIT.txt", "Smart-Ralph-MIT.txt", "Diagram-Design-MIT.txt",
        "Tabler-Icons-MIT.txt", "Simple-Icons-CC0-1.0.txt",
        "Log-Z-Logos-MIT.txt", "Devicon-MIT.txt",
    ):
        payloads[f".codex/licenses/{name}"] = (
            source_bytes(plugin_root / f"LICENSES/{name}"), 0o644,
        )
    marker = (
        f"qteam-plugin-version: {version}\n"
        f"source-commit: {source_commit}\n"
        f"source-path: {plugin_root}\n"
        f"installed-at: {stamp}\n"
    ).encode("utf-8")
    payloads[".codex/agent-team-template.version"] = (marker, 0o644)
    if set(payloads) != set(INSTALLED_PATHS):
        raise ValueError("internal QTeam installed-path contract is inconsistent")
    return payloads


def inspect_regular(root, backup_root, relative, slot):
    destination = safe_path(root, relative)
    if not destination.exists():
        return None, None, None
    if not destination.is_file():
        raise ValueError(f"managed destination is not a regular file: {relative}")
    prior_relative = f"{backup_root.relative_to(root).as_posix()}/{slot}/{relative}"
    safe_path(root, prior_relative)
    return (
        prior_relative,
        file_digest(destination),
        destination.stat().st_mode & 0o777,
    )


def write_preimage_backups(root, manifest):
    """Materialize verified preimages after the preparing intent is durable."""
    for record in manifest["installed_files"] + manifest["mutable_files"]:
        if record["prior"] is None:
            continue
        source = safe_path(root, record["path"])
        if (
            not source.is_file()
            or file_digest(source) != record["prior_sha256"]
            or source.stat().st_mode & 0o777 != record["prior_mode"]
        ):
            raise ValueError(
                f"managed file changed while QTeam prepared its backup: {record['path']}"
            )
        destination = safe_path(root, record["prior"])
        atomic_write(destination, source.read_bytes(), record["prior_mode"])
        if (
            file_digest(destination) != record["prior_sha256"]
            or destination.stat().st_mode & 0o777 != record["prior_mode"]
        ):
            raise ValueError(f"QTeam could not verify backup: {record['prior']}")


def prepare_manifest(root, plugin_root, backup_root, version, source_commit, stamp):
    payloads = installed_payloads(plugin_root, version, source_commit, stamp)
    installed = []
    for relative, (_data, mode) in sorted(payloads.items()):
        prior, prior_sha, prior_mode = inspect_regular(
            root, backup_root, relative, "files"
        )
        installed.append({
            "path": relative,
            "sha256": bytes_digest(payloads[relative][0]),
            "mode": mode,
            "prior": prior,
            "prior_sha256": prior_sha,
            "prior_mode": prior_mode,
        })

    mutable_payloads = {}
    config = safe_path(root, ".codex/config.toml")
    config_text = config.read_text(encoding="utf-8") if config.exists() else ""
    config_mode = config.stat().st_mode & 0o777 if config.exists() else 0o644
    mutable_payloads[".codex/config.toml"] = (
        merge_config(config_text).encode("utf-8"), config_mode,
    )
    gitignore = safe_path(root, ".gitignore")
    ignore_text = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    ignore_mode = gitignore.stat().st_mode & 0o777 if gitignore.exists() else 0o644
    mutable_payloads[".gitignore"] = (
        merge_gitignore(ignore_text).encode("utf-8"), ignore_mode,
    )
    mutable = []
    prior_names = {".codex/config.toml": "config.toml", ".gitignore": "gitignore"}
    for relative, (data, mode) in sorted(mutable_payloads.items()):
        destination = safe_path(root, relative)
        if destination.exists() and not destination.is_file():
            raise ValueError(f"managed destination is not a regular file: {relative}")
        prior = prior_sha = prior_mode = None
        if destination.exists():
            prior = f"{backup_root.relative_to(root).as_posix()}/pre/{prior_names[relative]}"
            safe_path(root, prior)
            prior_sha = file_digest(destination)
            prior_mode = destination.stat().st_mode & 0o777
        mutable.append({
            "path": relative,
            "sha256": bytes_digest(data),
            "mode": mode,
            "prior": prior,
            "prior_sha256": prior_sha,
            "prior_mode": prior_mode,
        })

    legacy = safe_path(root, ".codex/agent-team-template.version").exists()
    candidates = (
        [f".codex/agents/{name}.toml" for name in OBSOLETE_AGENTS]
        + [".codex/bin/__pycache__"]
    )
    if legacy:
        candidates.extend(
            f".agents/skills/{name}" for name in LEGACY_OWNED_PLUGIN_SKILLS
        )
        candidates.extend(
            f".agents/skills/{name}" for name in LEGACY_ORCHESTRATION_SKILLS
        )
    moved = []
    for relative in sorted(candidates):
        source = safe_path(root, relative)
        if not source.exists():
            continue
        backup = f"{backup_root.relative_to(root).as_posix()}/moved/{relative}"
        safe_path(root, backup)
        moved.append({
            "path": relative,
            "backup": backup,
            "backup_sha256": tree_digest(source),
        })
    manifest = {
        "schema_version": 3,
        "plugin": "qteam",
        "version": version,
        "stamp": stamp,
        "phase": "preparing",
        "backup_root": backup_root.relative_to(root).as_posix(),
        "installed_files": installed,
        "moved_paths": moved,
        "mutable_files": mutable,
    }
    validate_manifest(root, manifest)
    return manifest, payloads, mutable_payloads


def install(project):
    script_dir = Path(__file__).resolve().parent
    plugin_root = script_dir.parent
    root = repository_root(project)
    manifest_path = safe_path(root, ".codex/qteam-project.json")
    if manifest_path.exists():
        raise ValueError(
            "QTeam project runtime is already installed; use 'qteam setup' to update safely"
        )
    conflicts = [
        relative for relative in (
            f".agents/skills/{name}" for name in LOCAL_SKILL_CONFLICTS
        ) if safe_path(root, relative).exists()
    ]
    if conflicts:
        raise ValueError(
            "repository-local skill conflicts with QTeam plugin and is not QTeam-owned: "
            + ", ".join(conflicts)
        )
    for relative in INSTALLED_PATHS | MUTABLE_PATHS | MOVED_PATHS:
        safe_path(root, relative)
    version = (plugin_root / "VERSION").read_text(encoding="utf-8").strip()
    source_commit = git(["rev-parse", "--short", "HEAD"], plugin_root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f") + f"-{os.getpid()}"
    backup_relative = f".codex/qteam-backups/install/{stamp}"
    backup_root = safe_path(root, backup_relative)
    if backup_root.exists():
        raise ValueError(f"QTeam backup collision: {backup_relative}")
    intent_written = False
    try:
        manifest, payloads, mutable_payloads = prepare_manifest(
            root, plugin_root, backup_root, version, source_commit, stamp
        )
        atomic_write_json(manifest_path, manifest)
        intent_written = True
        for relative in ("files", "moved", "pre"):
            safe_path(root, f"{backup_relative}/{relative}").mkdir(
                parents=True, exist_ok=False
            )
        write_preimage_backups(root, manifest)
        if os.environ.get("QTEAM_TEST_HARD_EXIT_AFTER_BACKUPS") == "1":
            os._exit(86)
        manifest["phase"] = "installing"
        atomic_write_json(manifest_path, manifest)
        # Install ignore/config mutations first so recovery data is ignored
        # before role files and moved legacy trees are materialized.
        for relative, (data, mode) in mutable_payloads.items():
            destination = safe_path(root, relative)
            atomic_write(destination, data, mode)
        for record in manifest["moved_paths"]:
            source = safe_path(root, record["path"])
            destination = safe_path(root, record["backup"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
        fail_after_raw = os.environ.get("QTEAM_TEST_FAIL_AFTER_INSTALLS", "0")
        if not fail_after_raw.isdigit():
            raise ValueError("QTEAM_TEST_FAIL_AFTER_INSTALLS must be a non-negative integer")
        fail_after = int(fail_after_raw)
        count = 0
        for relative, (data, mode) in payloads.items():
            atomic_write(safe_path(root, relative), data, mode)
            count += 1
            if fail_after and count == fail_after:
                raise RuntimeError("injected QTeam setup failure")
        manifest["phase"] = "installed"
        atomic_write_json(manifest_path, manifest)
    except Exception:
        if intent_written:
            rollback = subprocess.run(
                [sys.executable, str(script_dir / "project-uninstall.py"), str(root)],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            if rollback.returncode:
                print(rollback.stdout, end="", file=sys.stderr)
                print(rollback.stderr, end="", file=sys.stderr)
                print(
                    f"error: automatic QTeam setup rollback failed; recovery manifest: {manifest_path}",
                    file=sys.stderr,
                )
        else:
            shutil.rmtree(backup_root, ignore_errors=True)
        raise
    print(f"installed QTeam project runtime v{version} ({source_commit})")
    print(f"project: {root}")
    print("skills: supplied by qteam@qteam plugin (not copied into the repository)")
    print("next:")
    print(f"  cd {root}")
    print("  .codex/bin/agent-team-doctor")
    print('  .codex/bin/wake-agent-team "<your goal>"')
    print("project-only uninstall:")
    print(f"  .codex/bin/qteam-project-uninstall {root}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", nargs="?", default=".")
    args = parser.parse_args()
    try:
        install(args.project)
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        raise SystemExit(f"error: {exc}")


if __name__ == "__main__":
    main()
