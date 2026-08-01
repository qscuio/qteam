#!/usr/bin/env python3
"""Mechanical task gate: a task may merge only if this passes.

Checks, rename/delete aware (git diff --name-status -z):
  - every changed path is inside the declared write set
  - no forbidden path is touched
  - shared surfaces (lockfiles, build/config files, migrations) are flagged
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

SHARED_SURFACE_WARN = [
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "go.mod", "go.sum", "Cargo.toml", "Cargo.lock", "requirements.txt",
    "Makefile", "CMakeLists.txt", "configure.ac", ".gitignore",
    ".github/**", "migrations/**",
]


def run_git(args, cwd):
    res = subprocess.run(["git", *args], cwd=cwd, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, universal_newlines=True)
    if res.returncode != 0:
        sys.stderr.write(res.stderr)
        sys.exit(2)
    return res.stdout


def match(path, glob):
    if fnmatch.fnmatch(path, glob):
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
    args = ap.parse_args()

    repo = run_git(["rev-parse", "--show-toplevel"], Path.cwd()).strip()

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
                    base = json.loads(state_file.read_text(encoding="utf-8")) \
                        .get("integration_branch")
                except (OSError, json.JSONDecodeError):
                    base = None
        if not base:
            base = rec.get("base_commit")
        head = args.head or rec.get("branch")
        write_set = args.write_set or rec.get("write_set", [])
        forbidden = args.forbidden or rec.get("forbidden_paths", [])
        worktree = args.worktree or rec.get("worktree")
    else:
        base, head = args.base, args.head
        write_set, forbidden, worktree = args.write_set, args.forbidden, args.worktree

    if not base or not head:
        ap.error("need --base and --head (or a task record providing them)")
    if not write_set:
        ap.error("empty write set: declare at least one --write-set glob")

    entries = changed_paths(base, head, repo)
    violations, forbidden_hits, warnings = [], [], []
    for status, path in entries:
        if matches_any(path, forbidden):
            forbidden_hits.append((status, path))
        if not matches_any(path, write_set):
            violations.append((status, path))
        elif matches_any(path, SHARED_SURFACE_WARN):
            warnings.append((status, path))

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

    ok = not violations and not forbidden_hits and not dirty
    print(f"check-task: {len(entries)} changed path(s), base={base} head={head}")
    if forbidden_hits:
        print("FORBIDDEN paths touched:")
        for s, p in forbidden_hits:
            print(f"  {s}  {p}")
    if violations:
        print("write-set VIOLATIONS (changed outside declared write set):")
        for s, p in violations:
            print(f"  {s}  {p}")
    if dirty:
        print("worktree NOT CLEAN (uncommitted changes must be committed or dropped):")
        for line in dirty.splitlines():
            print(f"  {line}")
    if warnings:
        print("warnings (shared surfaces inside write set / setup):")
        for s, p in warnings:
            print(f"  {s}  {p}")
    print("result: PASS" if ok else "result: FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
