#!/usr/bin/env python3
"""Crash-durably refresh an existing QTeam project runtime."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from qteam_project import (
    atomic_write, atomic_write_json, file_digest, legacy_tree_digest, safe_path,
    tree_digest, validate_manifest,
)


REFRESH_ROOT = ".codex/qteam-refresh"
INTENT = f"{REFRESH_ROOT}/intent.json"


def run(command):
    completed = subprocess.run(command, text=True)
    if completed.returncode:
        raise ValueError("command failed: " + " ".join(map(str, command)))


def verify_plugin_postcondition(expected_version):
    if expected_version is None:
        return
    completed = subprocess.run(
        ["codex", "plugin", "list", "--json"], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
    )
    if completed.returncode or len(completed.stdout.encode("utf-8")) > 1024 * 1024:
        raise ValueError("could not verify the frozen QTeam plugin postcondition")
    try:
        payload = json.loads(completed.stdout)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"invalid plugin postcondition response: {exc}")
    matches = [
        item for item in payload.get("installed", [])
        if isinstance(item, dict) and item.get("pluginId") == "qteam@qteam"
    ] if isinstance(payload, dict) else []
    if (len(matches) != 1
            or matches[0].get("installed") is not True
            or matches[0].get("enabled") is not True
            or matches[0].get("version") != expected_version):
        raise ValueError(
            "QTeam plugin did not reach the frozen installed version "
            + expected_version
        )


def repository_root(project):
    return Path(subprocess.check_output(
        ["git", "-C", project, "rev-parse", "--show-toplevel"], text=True,
    ).strip()).resolve()


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_tree(root):
    for directory, _names, files in os.walk(root, topdown=False,
                                             followlinks=False):
        parent = Path(directory)
        for name in files:
            path = parent / name
            if path.is_symlink():
                continue
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        fsync_directory(parent)


def remove_tree(path):
    parent = path.parent
    shutil.rmtree(path)
    fsync_directory(parent)


def load_manifest(path, label):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is missing or unsafe")
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError(f"{label} is too large")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"corrupt {label}: {exc}")
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def verify_installed_runtime(root, manifest):
    """Verify the complete installed runtime and its uninstall preimage."""
    validate_manifest(root, manifest)
    if manifest.get("phase") != "installed":
        raise ValueError("QTeam refresh requires an installed project runtime")
    for record in manifest["installed_files"] + manifest["mutable_files"]:
        target = safe_path(root, record["path"])
        if (target.is_symlink() or not target.is_file()
                or file_digest(target) != record["sha256"]
                or ("mode" in record
                    and target.stat().st_mode & 0o777 != record["mode"])):
            raise ValueError(
                f"managed QTeam runtime failed integrity check: {record['path']}"
            )
        prior = record.get("prior")
        if prior is not None:
            saved = safe_path(root, prior)
            if (saved.is_symlink() or not saved.is_file()
                    or file_digest(saved) != record["prior_sha256"]
                    or ("prior_mode" in record
                        and saved.stat().st_mode & 0o777
                            != record["prior_mode"])):
                raise ValueError(
                    f"QTeam pre-install backup failed integrity check: {prior}"
                )
    for record in manifest["moved_paths"]:
        saved = safe_path(root, record["backup"])
        digest = (
            legacy_tree_digest(saved)
            if manifest["schema_version"] == 2 else tree_digest(saved)
        ) if saved.exists() and not saved.is_symlink() else None
        if (not saved.exists() or saved.is_symlink()
                or digest != record["backup_sha256"]):
            raise ValueError(
                f"QTeam moved backup failed integrity check: {record['backup']}"
            )


def cleanup_uncommitted_snapshot(root, refresh_root):
    """Remove only a snapshot that provably precedes any destructive phase."""
    manifest = load_manifest(
        safe_path(root, ".codex/qteam-project.json"),
        "QTeam project manifest",
    )
    verify_installed_runtime(root, manifest)
    remove_tree(refresh_root)


def snapshot_previous(root, manifest, backup_root, after_command):
    refresh_root = safe_path(root, REFRESH_ROOT)
    if refresh_root.exists():
        raise ValueError(
            f"unfinished QTeam refresh exists at {refresh_root}; recover it first"
        )
    files = refresh_root / "files"
    files.mkdir(parents=True)
    fsync_directory(files.parent)
    verify_installed_runtime(root, manifest)
    records = manifest["installed_files"] + manifest["mutable_files"]
    metadata = []
    for index, record in enumerate(records):
        target = safe_path(root, record["path"])
        if target.is_symlink() or not target.is_file():
            raise ValueError(
                f"managed QTeam path is not a regular file: {record['path']}"
            )
        saved_relative = f"files/{index}"
        saved = refresh_root / saved_relative
        atomic_write(saved, target.read_bytes(), target.stat().st_mode & 0o777)
        metadata.append({
            "path": record["path"],
            "mode": target.stat().st_mode & 0o777,
            "saved": saved_relative,
            "sha256": file_digest(saved),
        })
    saved_backup = refresh_root / "backup"
    shutil.copytree(backup_root, saved_backup, symlinks=True)
    fsync_tree(saved_backup)
    manifest_path = safe_path(root, ".codex/qteam-project.json")
    atomic_write(
        refresh_root / "previous-manifest.json", manifest_path.read_bytes(),
        manifest_path.stat().st_mode & 0o777,
    )
    previous_manifest = refresh_root / "previous-manifest.json"
    intent = {
        "schema_version": 3,
        "repository": str(root),
        "phase": "snapshot-ready",
        "backup_root": manifest["backup_root"],
        "after_command": list(after_command),
        "plugin_version": os.environ.get("QTEAM_REFRESH_PLUGIN_VERSION"),
        "previous_manifest": {
            "saved": "previous-manifest.json",
            "mode": previous_manifest.stat().st_mode & 0o777,
            "sha256": file_digest(previous_manifest),
        },
        "backup": {
            "saved": "backup",
            "sha256": tree_digest(saved_backup),
        },
        "files": metadata,
    }
    if os.environ.get("QTEAM_TEST_HARD_EXIT_DURING_REFRESH_SNAPSHOT") == "1":
        os._exit(88)
    # The intent is written last: its presence proves every referenced prior
    # byte and backup directory was made durable before uninstall begins.
    atomic_write_json(refresh_root / "intent.json", intent)
    return refresh_root, intent


def read_intent(root):
    refresh_root = safe_path(root, REFRESH_ROOT)
    if not refresh_root.exists():
        return None, None
    if not refresh_root.is_dir():
        raise ValueError("QTeam refresh root is not a directory")
    intent_path = safe_path(root, INTENT)
    if not intent_path.is_file() or intent_path.is_symlink():
        return refresh_root, None
    try:
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"corrupt QTeam refresh intent: {exc}")
    required = {
        "schema_version", "repository", "phase", "backup_root",
        "previous_manifest", "backup", "files", "after_command",
        "plugin_version",
    }
    if (not isinstance(intent, dict) or set(intent) != required
            or intent.get("schema_version") != 3
            or intent.get("repository") != str(root)
            or intent.get("phase") not in {
                "snapshot-ready", "project-installing", "plugin-installing",
            }
            or not isinstance(intent.get("files"), list)
            or not isinstance(intent.get("after_command"), list)
            or len(intent.get("after_command")) > 16
            or any(not isinstance(item, str) or not item or len(item) > 4096
                   for item in intent.get("after_command", []))
            or (intent.get("plugin_version") is not None
                and (not isinstance(intent.get("plugin_version"), str)
                     or re.fullmatch(r"[0-9A-Za-z.+-]{1,128}",
                                     intent.get("plugin_version", "")) is None))):
        raise ValueError("invalid QTeam refresh intent")
    previous = intent.get("previous_manifest")
    backup = intent.get("backup")
    if (not isinstance(previous, dict)
            or set(previous) != {"saved", "mode", "sha256"}
            or previous.get("saved") != "previous-manifest.json"
            or type(previous.get("mode")) is not int
            or re.fullmatch(r"[0-9a-f]{64}", previous.get("sha256", "")) is None
            or not isinstance(backup, dict)
            or set(backup) != {"saved", "sha256"}
            or backup.get("saved") != "backup"
            or re.fullmatch(r"[0-9a-f]{64}", backup.get("sha256", "")) is None):
        raise ValueError("invalid QTeam refresh snapshot metadata")
    for item in intent["files"]:
        if (not isinstance(item, dict)
                or set(item) != {"path", "mode", "saved", "sha256"}
                or type(item.get("mode")) is not int
                or re.fullmatch(r"files/[0-9]+", item.get("saved", "")) is None
                or re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", "")) is None
                or (refresh_root / item.get("saved", "")).is_symlink()
                or not (refresh_root / item.get("saved", "")).is_file()):
            raise ValueError("invalid QTeam refresh file snapshot")
        safe_path(root, item["path"])
    safe_path(root, intent["backup_root"])
    return refresh_root, intent


def verify_snapshot(refresh_root, intent):
    previous = refresh_root / intent["previous_manifest"]["saved"]
    if (previous.is_symlink() or not previous.is_file()
            or previous.stat().st_mode & 0o777
                != intent["previous_manifest"]["mode"]
            or file_digest(previous) != intent["previous_manifest"]["sha256"]):
        raise ValueError("QTeam refresh prior manifest failed integrity check")
    backup = refresh_root / intent["backup"]["saved"]
    if (backup.is_symlink() or not backup.is_dir()
            or tree_digest(backup) != intent["backup"]["sha256"]):
        raise ValueError("QTeam refresh prior backup failed integrity check")
    for item in intent["files"]:
        saved = refresh_root / item["saved"]
        if (saved.is_symlink() or not saved.is_file()
                or saved.stat().st_mode & 0o777 != item["mode"]
                or file_digest(saved) != item["sha256"]):
            raise ValueError(
                f"QTeam refresh file snapshot failed integrity check: {item['path']}"
            )


def set_phase(refresh_root, intent, phase):
    intent = dict(intent)
    intent["phase"] = phase
    atomic_write_json(refresh_root / "intent.json", intent)
    return intent


def recover(project, after_command=()):
    root = repository_root(project)
    refresh_root, intent = read_intent(root)
    if refresh_root is None:
        return False
    if intent is None:
        cleanup_uncommitted_snapshot(root, refresh_root)
        return True
    verify_snapshot(refresh_root, intent)
    if intent["phase"] == "plugin-installing":
        if not after_command:
            raise ValueError(
                "QTeam plugin installation is incomplete; rerun qteam setup"
            )
        if list(after_command) != intent["after_command"]:
            raise ValueError(
                "QTeam refresh continuation differs from the frozen command"
            )
        current = load_manifest(
            safe_path(root, ".codex/qteam-project.json"),
            "QTeam project manifest",
        )
        verify_installed_runtime(root, current)
        run(intent["after_command"])
        verify_plugin_postcondition(intent["plugin_version"])
        remove_tree(refresh_root)
        return True
    scripts = Path(__file__).resolve().parent
    uninstall = scripts / "project-uninstall.py"
    manifest_path = safe_path(root, ".codex/qteam-project.json")
    if manifest_path.exists():
        run([str(uninstall), str(root)])
    previous_manifest_path = refresh_root / "previous-manifest.json"
    previous_manifest = load_manifest(
        previous_manifest_path, "QTeam refresh prior manifest"
    )
    backup_root = safe_path(root, intent["backup_root"])
    for record in previous_manifest["moved_paths"]:
        destination = safe_path(root, record["path"])
        if destination.is_symlink() or destination.is_file():
            destination.unlink()
        elif destination.is_dir():
            shutil.rmtree(destination)
    if backup_root.exists():
        shutil.rmtree(backup_root)
    saved_backup = refresh_root / "backup"
    if saved_backup.exists():
        if saved_backup.is_symlink() or not saved_backup.is_dir():
            raise ValueError("QTeam refresh prior backup is unsafe")
        backup_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(saved_backup, backup_root, symlinks=True)
        fsync_tree(backup_root)
    for item in intent["files"]:
        saved = refresh_root / item["saved"]
        atomic_write(
            safe_path(root, item["path"]), saved.read_bytes(), item["mode"]
        )
    atomic_write(
        manifest_path, previous_manifest_path.read_bytes(),
        intent["previous_manifest"]["mode"],
    )
    # Validate the restored manifest/backups before deleting the only durable
    # refresh intent. A second recovery is safe after interruption anywhere
    # above because the snapshot remains untouched until this point.
    validate_manifest(root, previous_manifest)
    verify_installed_runtime(root, previous_manifest)
    remove_tree(refresh_root)
    return True


def refresh(project, after_command):
    root = repository_root(project)
    refresh_root, existing = read_intent(root)
    if refresh_root is not None:
        if existing is not None and existing["phase"] == "plugin-installing":
            recover(root, after_command)
            return
        recover(root)
    scripts = Path(__file__).resolve().parent
    uninstall = scripts / "project-uninstall.py"
    setup = scripts / "project-setup.py"
    manifest_path = safe_path(root, ".codex/qteam-project.json")
    manifest = load_manifest(manifest_path, "QTeam project manifest")
    backup_root = validate_manifest(root, manifest)
    run([str(uninstall), "--check-only", str(root)])
    if manifest.get("phase") in {"preparing", "restored"}:
        run([str(uninstall), str(root)])
        run([str(setup), str(root)])
        if after_command:
            run(after_command)
        return

    refresh_root, intent = snapshot_previous(
        root, manifest, backup_root, after_command
    )
    try:
        run([str(uninstall), str(root)])
        if os.environ.get("QTEAM_TEST_HARD_EXIT_AFTER_REFRESH_UNINSTALL") == "1":
            os._exit(87)
        intent = set_phase(refresh_root, intent, "project-installing")
        run([str(setup), str(root)])
        if after_command:
            intent = set_phase(refresh_root, intent, "plugin-installing")
            run(after_command)
            verify_plugin_postcondition(intent["plugin_version"])
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError):
        if intent["phase"] == "plugin-installing":
            raise ValueError(
                "QTeam plugin installation is incomplete; rerun qteam setup "
                "to finish the durable refresh"
            )
        recover(root)
        raise ValueError("QTeam refresh failed; the previous runtime was restored")
    remove_tree(refresh_root)


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--recover":
        if len(sys.argv) != 3:
            raise SystemExit("usage: project-refresh.py --recover <project>")
        try:
            if recover(sys.argv[2]):
                print("recovered interrupted QTeam project refresh")
        except (OSError, UnicodeError, ValueError,
                subprocess.SubprocessError) as exc:
            raise SystemExit(f"error: {exc}")
        return
    if len(sys.argv) < 2:
        raise SystemExit(
            "usage: project-refresh.py <project> [after-success-command ...]"
        )
    try:
        refresh(sys.argv[1], sys.argv[2:])
    except (OSError, UnicodeError, ValueError,
            subprocess.SubprocessError) as exc:
        raise SystemExit(f"error: {exc}")


if __name__ == "__main__":
    main()
