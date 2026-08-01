#!/usr/bin/env python3
"""Safe finish gate for an agent-team run.

Default is REPORT-ONLY: shows branch, status, diffstat, untracked files,
active-run phase, and what --commit/--push would do. Nothing is committed or
pushed without explicit flags.

  agent-team-finish                          # report only
  agent-team-finish --commit "feat: ..."     # integrate/commit locally
  agent-team-finish --commit "..." --push    # and push
  agent-team-finish ... --allow-default-branch   # required on main/master/trunk

Commit behavior:
  - With an active run in READY_TO_FINISH whose integration branch carries the
    work: fast-forward the current branch to the integration branch.
  - Otherwise (serial/shared-tree work): stage only paths in the run manifest
    (union of task write sets); with no active run, --all is required to stage
    everything. Installer backups (*.bak.*) and .agents/runs/ are never staged.
Quality gates are the workflow's job; this script gates scope and destination.
"""

import argparse
import fnmatch
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_BRANCHES = {"main", "master", "trunk"}
NEVER_STAGE = ["*.bak.*", ".agents/runs/**", ".agents/tmp/**"]


def git(args, check=True, capture=True):
    kwargs = {}
    if capture:
        kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                  "universal_newlines": True}
    res = subprocess.run(["git", *args], **kwargs)
    if check and res.returncode != 0:
        if capture:
            sys.stderr.write(res.stderr)
        sys.exit(2)
    return res.stdout if capture else ""


def match(path, glob):
    if fnmatch.fnmatch(path, glob):
        return True
    return glob.endswith("/**") and (path == glob[:-3] or path.startswith(glob[:-3] + "/"))


def porcelain_entries():
    """[(XY, path)] from `git status --porcelain -z` (NUL-safe: no C-quoting
    of non-ASCII paths); renames yield the new path, the orig field is skipped."""
    out = git(["status", "--porcelain", "-z"])
    fields = out.split("\0")
    entries, i = [], 0
    while i < len(fields):
        f = fields[i]
        if not f:
            i += 1
            continue
        xy, path = f[:2], f[3:]
        entries.append((xy, path))
        i += 2 if xy[0] in "RC" else 1
    return entries


def find_active_run(repo):
    runs = sorted((repo / ".agents" / "runs").glob("*/state.json"))
    active = []
    for sf in runs:
        try:
            state = json.loads(sf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not state.get("finished", False):
            active.append((sf.parent, state))
    return active


def manifest_globs(run_dir):
    globs = []
    for tf in sorted((run_dir / "tasks").glob("*.json")):
        try:
            rec = json.loads(tf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        globs.extend(rec.get("write_set", []))
    return globs


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--commit", metavar="MSG", help="commit/integrate locally with this message")
    ap.add_argument("--push", action="store_true", help="push current branch to origin")
    ap.add_argument("--allow-default-branch", action="store_true",
                    help="permit --commit/--push on main/master/trunk")
    ap.add_argument("--all", action="store_true",
                    help="with no active run: stage all changes (minus junk)")
    ap.add_argument("--yes", action="store_true", help="skip interactive confirmation")
    args = ap.parse_args()

    repo = Path(git(["rev-parse", "--show-toplevel"]).strip())
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    entries = porcelain_entries()
    untracked = [p for xy, p in entries if xy == "??"]
    junk = [p for p in untracked if any(match(p, g) for g in NEVER_STAGE)]
    active = find_active_run(repo)

    print(f"branch: {branch}")
    if entries:
        print("status:")
        print("\n".join(f"  {xy} {p}" for xy, p in entries[:50]))
        print("diffstat:")
        print(git(["diff", "--stat"]).rstrip() or "  (only staged/untracked changes)")
    else:
        print("status: clean")
    if junk:
        print(f"junk that will never be staged: {', '.join(junk[:10])}")
    for run_dir, state in active:
        print(f"active run: {state.get('run_id', run_dir.name)}  phase={state.get('phase')}  "
              f"integration={state.get('integration_branch')}")

    if not args.commit and not args.push:
        print("\nreport-only mode. Use --commit \"<msg>\" [--push] to finish; "
              "--allow-default-branch is required on main/master/trunk.")
        return

    if branch in DEFAULT_BRANCHES and not args.allow_default_branch:
        sys.stderr.write(f"error: refusing to --commit/--push on default branch '{branch}' "
                         f"without --allow-default-branch\n")
        sys.exit(3)
    if len(active) > 1:
        sys.stderr.write("error: multiple active runs; finish is ambiguous. "
                         "Mark stale runs finished in their state.json first.\n")
        sys.exit(3)

    plan = []
    ff_target = None
    stage = []
    if args.commit:
        state = active[0][1] if active else {}
        # the phase gate applies to ANY active run, whichever commit path runs:
        # a run mid-wave must finish its workflow gates before anything lands
        if active and state.get("phase") != "READY_TO_FINISH":
            sys.stderr.write(f"error: run phase is {state.get('phase')}, not READY_TO_FINISH; "
                             f"finish the workflow gates first\n")
            sys.exit(3)
        integ = state.get("integration_branch")
        integ_exists = bool(integ and git(["rev-parse", "--verify", "--quiet", integ],
                                          check=False).strip())
        if integ and not integ_exists:
            sys.stderr.write(f"error: state.json names integration branch '{integ}' but it "
                             f"does not exist — refusing to degrade to tree staging\n")
            sys.exit(3)
        if integ_exists:
            if entries:
                sys.stderr.write("error: working tree is dirty; integration finish needs a clean "
                                 "tree (commit or stash unrelated changes first)\n")
                sys.exit(3)
            ff_target = integ
            plan.append(f"fast-forward {branch} to {integ}")
        else:
            globs = manifest_globs(active[0][0]) if active else []
            changed = [p for _, p in entries]
            if globs:
                stage = [p for p in changed
                         if any(match(p, g) for g in globs)
                         and not any(match(p, g) for g in NEVER_STAGE)]
                plan.append(f"stage {len(stage)} manifest path(s) and commit")
            elif args.all:
                stage = [p for p in changed
                         if not any(match(p, g) for g in NEVER_STAGE)]
                plan.append(f"stage all {len(stage)} path(s) (minus junk) and commit")
            else:
                sys.stderr.write("error: no active run manifest to scope staging; "
                                 "pass --all to stage everything (minus junk)\n")
                sys.exit(3)
            if not stage:
                plan.append("nothing to stage")
    if args.push:
        plan.append(f"push {branch} to origin")

    print("\nplanned actions:")
    for p in plan:
        print(f"  - {p}")
    if not args.yes:
        if not sys.stdin.isatty():
            sys.stderr.write("error: non-interactive session; pass --yes to proceed\n")
            sys.exit(3)
        if input("proceed? [y/N] ").strip().lower() not in ("y", "yes"):
            print("aborted")
            sys.exit(1)

    if ff_target:
        git(["merge", "--ff-only", ff_target], capture=False)
    elif args.commit and stage:
        git(["add", "--", *stage], capture=False)
        git(["commit", "-m", args.commit], capture=False)
    if args.push:
        git(["push", "origin", "HEAD"], capture=False)
    print("done")


if __name__ == "__main__":
    main()
