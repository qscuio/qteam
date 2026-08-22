#!/usr/bin/env python3
"""Safely remove a QTeam project runtime and restore its verified preimage."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from qteam_project import (
    atomic_write, atomic_write_json, file_digest, normalize_legacy_manifest,
    safe_path, tree_digest, validate_manifest,
)


def repository_root(value):
    completed = subprocess.run(
        ["git", "-C", value, "rev-parse", "--show-toplevel"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise ValueError(completed.stderr.strip())
    return Path(completed.stdout.strip()).resolve()


def cleanup_empty_directories(root):
    for relative in (
        ".codex/practices", ".codex/standards",
        ".codex/licenses", ".codex/schemas", ".codex/worker-prompts",
        ".codex/agents", ".codex/bin", ".codex/qteam-ui",
        ".codex/qteam-backups/install",
        ".codex/qteam-backups", ".codex", ".agents/skills", ".agents",
    ):
        path = root / relative
        try:
            path.rmdir()
        except (FileNotFoundError, OSError):
            pass


def verified_prior(root, record):
    if record.get("prior") is None:
        return None
    prior = safe_path(root, record["prior"])
    if (
        not prior.is_file()
        or file_digest(prior) != record.get("prior_sha256")
        or prior.stat().st_mode & 0o777 != record.get("prior_mode")
    ):
        raise ValueError(f"QTeam pre-install backup failed integrity check: {record['prior']}")
    return prior


def current_file_state(root, record, prior):
    destination = safe_path(root, record["path"])
    if destination.exists() and not destination.is_file():
        raise ValueError(f"managed QTeam path is not a regular file: {record['path']}")
    if destination.exists():
        current = file_digest(destination)
        mode = destination.stat().st_mode & 0o777
        if current == record["sha256"] and mode == record["mode"]:
            return "installed", destination
        if (
            prior is not None
            and current == record["prior_sha256"]
            and mode == record["prior_mode"]
        ):
            return "restored", destination
        raise ValueError(f"locally modified QTeam path must be retained: {record['path']}")
    return ("restored" if prior is None else "missing"), destination


def verify_preimage_destinations(root, manifest):
    """Prove managed destinations are exactly in their pre-QTeam state."""
    for record in manifest["installed_files"] + manifest["mutable_files"]:
        destination = safe_path(root, record["path"])
        if record["prior"] is None:
            if destination.exists():
                raise ValueError(
                    f"QTeam {manifest['phase']} phase does not match destination: "
                    f"{record['path']}"
                )
            continue
        if (
            not destination.is_file()
            or file_digest(destination) != record["prior_sha256"]
            or destination.stat().st_mode & 0o777 != record["prior_mode"]
        ):
            raise ValueError(
                f"QTeam {manifest['phase']} phase does not match preimage: "
                f"{record['path']}"
            )
    for record in manifest["moved_paths"]:
        destination = safe_path(root, record["path"])
        if not destination.exists() or tree_digest(destination) != record["backup_sha256"]:
            raise ValueError(
                f"QTeam {manifest['phase']} phase does not match moved preimage: "
                f"{record['path']}"
            )


def uninstall(project, *, check_only=False):
    root = repository_root(project)
    manifest_path = safe_path(root, ".codex/qteam-project.json")
    if not manifest_path.is_file():
        raise ValueError(f"QTeam project runtime is not installed in {root}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"corrupt QTeam project manifest: {exc}")
    backup_root = validate_manifest(root, manifest)
    normalize_legacy_manifest(root, manifest)
    backup_root = validate_manifest(root, manifest)
    if manifest["phase"] in {"preparing", "restored"}:
        # These phases may only discard recovery data after the actual managed
        # destinations prove that no QTeam mutation remains.
        verify_preimage_destinations(root, manifest)
        if check_only:
            print(f"QTeam project runtime preflight passed for {root}")
            return
        if backup_root.exists():
            shutil.rmtree(backup_root)
        manifest_path.unlink()
        cleanup_empty_directories(root)
        print(f"removed QTeam project runtime from {root}")
        return

    file_actions = []
    for record in manifest["installed_files"] + manifest["mutable_files"]:
        prior = verified_prior(root, record)
        state, destination = current_file_state(root, record, prior)
        file_actions.append((record, prior, state, destination))

    moved_actions = []
    for record in manifest["moved_paths"]:
        destination = safe_path(root, record["path"])
        saved = safe_path(root, record["backup"])
        if saved.exists():
            if tree_digest(saved) != record["backup_sha256"]:
                raise ValueError(
                    f"QTeam moved backup failed integrity check: {record['backup']}"
                )
            if destination.exists():
                raise ValueError(
                    f"cannot restore pre-QTeam path because it now exists: {record['path']}"
                )
            moved_actions.append(("restore", saved, destination))
        elif destination.exists() and tree_digest(destination) == record["backup_sha256"]:
            moved_actions.append(("restored", saved, destination))
        else:
            raise ValueError(f"missing QTeam moved backup: {record['backup']}")

    if check_only:
        print(f"QTeam project runtime preflight passed for {root}")
        return

    # All paths, current content, and backup digests are proven before the first
    # mutation. Operations are idempotent if the process is interrupted.
    self_path = ".codex/bin/qteam-project-uninstall"
    regular_actions = [item for item in file_actions if item[0]["path"] != self_path]
    self_actions = [item for item in file_actions if item[0]["path"] == self_path]

    def apply_file_actions(actions):
        for _record, prior, state, destination in actions:
            if state == "installed":
                destination.unlink()
                if prior is not None:
                    atomic_write(
                        destination, prior.read_bytes(), _record["prior_mode"]
                    )
            elif state == "missing":
                atomic_write(
                    destination, prior.read_bytes(), _record["prior_mode"]
                )

    apply_file_actions(regular_actions)
    for state, saved, destination in moved_actions:
        if state == "restore":
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(saved, destination)
    apply_file_actions(self_actions)

    manifest["phase"] = "restored"
    atomic_write_json(manifest_path, manifest)
    shutil.rmtree(backup_root)
    manifest_path.unlink()
    cleanup_empty_directories(root)
    print(f"removed QTeam project runtime from {root}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", nargs="?", default=".")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        uninstall(args.project, check_only=args.check_only)
    except (OSError, UnicodeError, ValueError) as exc:
        raise SystemExit(f"error: {exc}")


if __name__ == "__main__":
    main()
