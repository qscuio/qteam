#!/usr/bin/env python3
"""Mechanical task gate: a task may merge only if this passes.

Checks, rename/delete aware (git diff --name-status -z):
  - every changed path is inside the declared write set
  - no forbidden path is touched
  - shared surfaces require an explicit allow_shared_surfaces declaration
  - empty diffs fail unless the task explicitly allows them
  - task mode requires successful structured verification evidence
  - the task worktree has no uncommitted changes (all work is in task commits)

Task mode (reads the run's task record):
  agent-team-check-task --run .agents/runs/<run-id> --task T01

Direct mode:
  agent-team-check-task --base <ref> --head <ref> [--worktree <path>]
                        --write-set 'src/auth/**' --write-set 'tests/auth/**'
                        [--forbidden 'migrations/**'] ...

Glob semantics (uniform, documented): Python fnmatch on the full repo-relative
path; '*' and '?' match across '/' as well, and 'dir/**' additionally matches
everything under 'dir/'. Exit 0 pass, 1 violations, 2 usage/setup error.
"""

import argparse
import fnmatch
import json
import subprocess
import sys
from pathlib import Path

SHARED_SURFACES = [
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "pyproject.toml", "setup.cfg", "tox.ini", "go.mod", "go.sum",
    "Cargo.toml", "Cargo.lock", "requirements*.txt", "Makefile",
    "CMakeLists.txt", "configure.ac", "Dockerfile*", "docker-compose*.yml",
    ".gitignore", ".github/**", "migrations/**", "schemas/**", "generated/**",
    "**/*.proto", "**/openapi*", "**/schemas/**", "**/fixtures/**",
    "**/__snapshots__/**", "**/*.snap",
]


def run_git(args, cwd):
    res = subprocess.run(["git", *args], cwd=cwd, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, universal_newlines=True)
    if res.returncode != 0:
        sys.stderr.write(res.stderr)
        sys.exit(2)
    return res.stdout


def ref_exists(ref, cwd):
    if not ref:
        return False
    res = subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref],
                         cwd=cwd, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    return res.returncode == 0


def match(path, glob):
    if fnmatch.fnmatch(path, glob):
        return True
    if glob.startswith("**/") and fnmatch.fnmatch(path, glob[3:]):
        return True
    if glob.endswith("/**") and (path == glob[:-3] or path.startswith(glob[:-3] + "/")):
        return True
    return False


def matches_any(path, globs):
    return any(match(path, g) for g in globs)


def changed_paths(base, head, cwd):
    """Return [(status, path)] for merge-base(base,head)..head; renames yield old and new.

    Three-dot semantics: only the task's own changes count, not changes the
    task branch picked up by refreshing from an updated integration branch.
    """
    out = run_git(["diff", "--name-status", "-z", "-M", f"{base}...{head}"], cwd)
    fields = out.split("\0")
    entries, i = [], 0
    while i < len(fields):
        status = fields[i]
        if not status:
            i += 1
            continue
        if status[0] in ("R", "C"):
            if i + 2 >= len(fields):
                break
            entries.append((status[0], fields[i + 1]))   # old path
            entries.append((status[0], fields[i + 2]))   # new path
            i += 3
        else:
            if i + 1 >= len(fields):
                break
            entries.append((status[0], fields[i + 1]))
            i += 2
    return entries


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", help="run directory (.agents/runs/<run-id>)")
    ap.add_argument("--task", help="task ID, requires --run")
    ap.add_argument("--base", help="base ref (direct mode)")
    ap.add_argument("--head", help="head ref (direct mode)")
    ap.add_argument("--worktree", help="task worktree to check for cleanliness")
    ap.add_argument("--write-set", action="append", default=[], dest="write_set")
    ap.add_argument("--forbidden", action="append", default=[], dest="forbidden")
    ap.add_argument("--allow-empty-diff", action="store_true")
    ap.add_argument("--allow-shared-surface", action="append", default=[])
    ap.add_argument("--verification-evidence", action="append", default=[],
                    help="JSON evidence file; direct mode only")
    args = ap.parse_args()

    repo = run_git(["rev-parse", "--show-toplevel"], Path.cwd()).strip()
    state_data = {}

    if args.task:
        if not args.run:
            ap.error("--task requires --run")
        task_file = Path(repo) / args.run / "tasks" / f"{args.task}.json"
        if not task_file.is_file():
            sys.stderr.write(f"error: missing task record {task_file}\n")
            sys.exit(2)
        rec = json.loads(task_file.read_text(encoding="utf-8"))
        # prefer the run's integration branch as base: `integration...head`
        # isolates the task's own changes even after the task branch refreshed
        # from (merged in) a moved integration branch, which a frozen
        # base_commit cannot do
        base = args.base
        if not base:
            state_file = Path(repo) / args.run / "state.json"
            if state_file.is_file():
                try:
                    state_data = json.loads(state_file.read_text(encoding="utf-8"))
                    base = state_data.get("integration_branch")
                except (OSError, json.JSONDecodeError):
                    base = None
        if base and not ref_exists(base, repo):
            base = None
        if not base:
            base = rec.get("base_commit")
        head = args.head or rec.get("branch")
        write_set = args.write_set or rec.get("write_set", [])
        forbidden = args.forbidden or rec.get("forbidden_paths", [])
        worktree = args.worktree or rec.get("worktree")
        allow_empty = args.allow_empty_diff or rec.get("allow_empty_diff", False)
        allowed_shared = args.allow_shared_surface or rec.get("allow_shared_surfaces", [])
        shared_surfaces = list(dict.fromkeys([
            *SHARED_SURFACES, *state_data.get("shared_surfaces", [])
        ]))
        evidence = rec.get("verification_evidence", [])
        expected_verification = rec.get("verification")
    else:
        base, head = args.base, args.head
        write_set, forbidden, worktree = args.write_set, args.forbidden, args.worktree
        allow_empty = args.allow_empty_diff
        allowed_shared = args.allow_shared_surface
        shared_surfaces = SHARED_SURFACES
        evidence = []
        expected_verification = None
        for evidence_file in args.verification_evidence:
            try:
                loaded = json.loads(Path(evidence_file).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                sys.stderr.write(f"error: invalid verification evidence {evidence_file}: {exc}\n")
                sys.exit(2)
            evidence.extend(loaded if isinstance(loaded, list) else [loaded])

    if not base or not head:
        ap.error("need --base and --head (or a task record providing them)")
    if not write_set:
        ap.error("empty write set: declare at least one --write-set glob")

    base_sha = run_git(["rev-parse", base], repo).strip()
    head_sha = run_git(["rev-parse", head], repo).strip()
    entries = changed_paths(base_sha, head_sha, repo)
    violations, forbidden_hits, shared_hits = [], [], []
    for status, path in entries:
        if matches_any(path, forbidden):
            forbidden_hits.append((status, path))
        if not matches_any(path, write_set):
            violations.append((status, path))
        elif matches_any(path, shared_surfaces) and not matches_any(path, allowed_shared):
            shared_hits.append((status, path))

    empty_violation = not entries and not allow_empty
    verification_ok = any(
        isinstance(item, dict) and item.get("exit_code") == 0 and item.get("command")
        and (not expected_verification or item.get("command") == expected_verification)
        and (not args.task or item.get("head_sha") == head_sha)
        for item in evidence
    )
    verification_missing = bool(args.task) and not verification_ok

    dirty = ""
    if worktree:
        wt = Path(repo) / worktree if not Path(worktree).is_absolute() else Path(worktree)
        if wt.is_dir():
            dirty = run_git(["status", "--porcelain"], wt).strip()
        else:
            # an unverifiable cleanliness precondition is a setup error, not a pass
            sys.stderr.write(f"error: worktree path not found: {worktree} — "
                             f"cannot verify the task worktree is clean\n")
            sys.exit(2)

    ok = not (violations or forbidden_hits or shared_hits or dirty or
              empty_violation or verification_missing)
    print(f"check-task: {len(entries)} changed path(s), base={base} head={head}")
    if forbidden_hits:
        print("FORBIDDEN paths touched:")
        for s, p in forbidden_hits:
            print(f"  {s}  {p}")
    if violations:
        print("write-set VIOLATIONS (changed outside declared write set):")
        for s, p in violations:
            print(f"  {s}  {p}")
    if shared_hits:
        print("UNDECLARED shared surfaces touched (serialize and declare allow_shared_surfaces):")
        for s, p in shared_hits:
            print(f"  {s}  {p}")
    if empty_violation:
        print("EMPTY diff (task produced no declared change; set allow_empty_diff only for audit tasks)")
    if verification_missing:
        print("VERIFICATION evidence missing (need command + exit_code=0 in task record)")
    if dirty:
        print("worktree NOT CLEAN (uncommitted changes must be committed or dropped):")
        for line in dirty.splitlines():
            print(f"  {line}")
    if args.task:
        state_tool = Path(__file__).with_name("agent-team-state.py")
        if not state_tool.exists():
            state_tool = Path(repo) / ".codex/bin/agent-team-state"
        res = subprocess.run(
            [sys.executable, str(state_tool), "--run", args.run, "task-check",
             args.task, "passed" if ok else "failed", "--base", base_sha, "--head", head_sha,
             "--evidence", (f"{len(entries)} changed paths; "
                            f"violations={len(violations)} forbidden={len(forbidden_hits)} "
                            f"shared={len(shared_hits)} dirty={bool(dirty)} "
                            f"empty={empty_violation} verification_missing={verification_missing}")],
            cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if res.returncode:
            sys.stderr.write(res.stderr)
            sys.stderr.write("error: mechanical check result could not be recorded\n")
            sys.exit(2)
    print("result: PASS" if ok else "result: FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
