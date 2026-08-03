#!/usr/bin/env python3
"""Safe finish gate for an agent-team run.

Default is REPORT-ONLY: shows branch, status, diffstat, untracked files,
active-run phase, and what --integrate/--push would do. Nothing is integrated or
pushed without explicit flags.

  agent-team-finish                          # report only
  agent-team-finish --integrate              # fast-forward locally
  agent-team-finish --integrate --push       # and push
  agent-team-finish ... --allow-default-branch   # required on main/master/trunk

Integration behavior:
  - Require exactly one active READY_TO_FINISH run and the recorded base branch.
  - Preflight all state/review/verification invariants before Git mutation.
  - Fast-forward the base branch to the integration branch; no shared-tree or
    manifest-staging fallback exists.
After successful local integration (and optional push), the run is atomically
closed through agent-team-state. Push-only completion is forbidden.
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
    out = git(["status", "--porcelain", "-z", "--untracked-files=all"])
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
    corrupt = []
    for sf in runs:
        try:
            state = json.loads(sf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            corrupt.append(f"{sf}: {exc}")
            continue
        if not state.get("finished", False):
            active.append((sf.parent, state))
    if corrupt:
        raise RuntimeError("cannot safely enumerate runs; corrupt state: " + "; ".join(corrupt))
    return active


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--integrate", action="store_true",
                    help="fast-forward the base branch to the reviewed integration SHA")
    ap.add_argument("--commit", metavar="IGNORED", help=argparse.SUPPRESS)
    ap.add_argument("--push", action="store_true", help="push current branch to origin")
    ap.add_argument("--allow-default-branch", action="store_true",
                    help="permit --integrate/--push on main/master/trunk")
    ap.add_argument("--yes", action="store_true", help="skip interactive confirmation")
    args = ap.parse_args()

    integrate = args.integrate or args.commit is not None
    if args.commit is not None:
        sys.stderr.write(
            "warning: --commit MSG is deprecated; QTeam performs no new commit and "
            "uses --integrate semantics\n"
        )
    if args.push and not integrate:
        sys.stderr.write("error: --push requires --integrate; push-only finish is forbidden\n")
        sys.exit(3)

    repo = Path(git(["rev-parse", "--show-toplevel"]).strip())
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    entries = porcelain_entries()
    untracked = [p for xy, p in entries if xy == "??"]
    junk = [p for p in untracked if any(match(p, g) for g in NEVER_STAGE)]
    material_entries = [(xy, p) for xy, p in entries
                        if not any(match(p, g) for g in NEVER_STAGE)]
    try:
        active = find_active_run(repo)
    except RuntimeError as exc:
        sys.stderr.write(f"error: {exc}\n")
        sys.exit(3)

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

    if not integrate and not args.push:
        print("\nreport-only mode. Use --integrate [--push] to finish; "
              "--allow-default-branch is required on main/master/trunk.")
        return

    if branch in DEFAULT_BRANCHES and not args.allow_default_branch:
        sys.stderr.write(f"error: refusing to --integrate/--push on default branch '{branch}' "
                         f"without --allow-default-branch\n")
        sys.exit(3)
    if len(active) != 1:
        sys.stderr.write(f"error: finish requires exactly one active run; found {len(active)}\n")
        sys.exit(3)
    run_dir, state = active[0]
    if branch != state.get("base_branch"):
        sys.stderr.write(f"error: current branch {branch!r} is not run base_branch "
                         f"{state.get('base_branch')!r}\n")
        sys.exit(3)
    if material_entries:
        sys.stderr.write("error: working tree is dirty; integration finish needs a clean tree\n")
        sys.exit(3)
    integ = state.get("integration_branch")
    if not integ or not git(["rev-parse", "--verify", "--quiet", integ],
                            check=False).strip():
        sys.stderr.write("error: recorded integration branch does not exist\n")
        sys.exit(3)
    state_tool = Path(__file__).with_name("agent-team-state.py")
    if not state_tool.exists():
        state_tool = repo / ".codex/bin/agent-team-state"
    preflight = subprocess.run(
        [sys.executable, str(state_tool), "--run", str(run_dir), "finish", "--check-only"],
        cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if preflight.returncode:
        sys.stderr.write(preflight.stderr)
        sys.stderr.write("error: finish preflight failed; no Git mutation was performed\n")
        sys.exit(3)
    try:
        frozen_head = json.loads(preflight.stdout.splitlines()[-1])["integration_head"]
    except (IndexError, KeyError, json.JSONDecodeError):
        sys.stderr.write("error: finish preflight returned no frozen integration SHA\n")
        sys.exit(3)
    if not frozen_head or git(["rev-parse", "--verify", f"{frozen_head}^{{commit}}"],
                              check=False).strip() != frozen_head:
        sys.stderr.write("error: finish preflight returned an invalid integration SHA\n")
        sys.exit(3)
    if subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", frozen_head],
                      cwd=repo).returncode:
        sys.stderr.write("error: base branch cannot fast-forward to reviewed integration SHA\n")
        sys.exit(3)

    plan = [f"fast-forward {branch} to reviewed {frozen_head}"]
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

    if args.push:
        publish_gate = subprocess.run(
            [sys.executable, str(state_tool), "--run", str(run_dir),
             "decision-check", "--action", "publish", "--seal",
             "--expected-head", frozen_head],
            cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if publish_gate.returncode:
            sys.stderr.write(publish_gate.stderr)
            sys.stderr.write(
                "error: publish authorization could not be sealed; "
                "no Git mutation was performed\n"
            )
            sys.exit(3)
    else:
        integration_gate = subprocess.run(
            [sys.executable, str(state_tool), "--run", str(run_dir), "finish",
             "--check-only", "--seal", "--expected-head", frozen_head],
            cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if integration_gate.returncode:
            sys.stderr.write(integration_gate.stderr)
            sys.stderr.write(
                "error: integration authorization could not be sealed; "
                "no Git mutation was performed\n"
            )
            sys.exit(3)

    git(["merge", "--ff-only", frozen_head], capture=False)
    if args.push:
        git(["push", "origin", "HEAD"], capture=False)
    res = subprocess.run(
        [sys.executable, str(state_tool), "--run", str(run_dir), "finish",
         "--expected-head", frozen_head], cwd=repo,
    )
    if res.returncode:
        sys.stderr.write("error: preflight passed but run state could not be closed\n")
        sys.exit(4)
    print("done")


if __name__ == "__main__":
    main()
