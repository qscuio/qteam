#!/usr/bin/env python3
"""Optional session/display adapters for QTeam (currently Herdr)."""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PANE_ID = re.compile(r"^[A-Za-z0-9:_-]{1,128}$")
SAFE_ID = re.compile(r"^(?!.*\.\.)(?=.{1,128}$)[A-Za-z0-9][A-Za-z0-9._-]*$")
MAX_HERDR_OUTPUT = 1024 * 1024


def git_root(value=None):
    command = ["git"]
    if value:
        command.extend(["-C", str(value)])
    command.extend(["rev-parse", "--show-toplevel"])
    result = subprocess.run(
        command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=10,
    )
    if result.returncode:
        raise ValueError("not inside a Git repository")
    return Path(result.stdout.strip()).resolve()


def herdr(args, *, timeout=30):
    executable = shutil.which("herdr")
    if executable is None:
        raise ValueError("Herdr is not installed or not on PATH")
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(
            [executable, *args], stdout=stdout_file, stderr=stderr_file,
            start_new_session=True,
        )
        try:
            return_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, 15)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, 9)
                except ProcessLookupError:
                    pass
                process.wait()
            raise ValueError("Herdr command timed out")
        stdout_file.seek(0, os.SEEK_END)
        stdout_size = stdout_file.tell()
        stderr_file.seek(0, os.SEEK_END)
        stderr_size = stderr_file.tell()
        if stdout_size > MAX_HERDR_OUTPUT or stderr_size > MAX_HERDR_OUTPUT:
            raise ValueError("Herdr output exceeded the retained limit")
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read().decode("utf-8", errors="strict")
        stderr = stderr_file.read().decode("utf-8", errors="strict")
    if return_code:
        raise ValueError(stderr.strip() or stdout.strip() or "Herdr command failed")
    return stdout


def web_tool():
    directory = Path(__file__).resolve().parent
    installed = directory / "agent-team-web"
    source = directory / "agent-team-web.py"
    target = installed if installed.is_file() else source
    if not target.is_file() or target.is_symlink():
        raise ValueError("missing agent-team-web")
    return [sys.executable, str(target)] if target.suffix == ".py" else [str(target)]


def validate_run(repo, run_id):
    if not isinstance(run_id, str) or not SAFE_ID.fullmatch(run_id):
        raise ValueError("unsafe run id")
    agents = repo / ".agents"
    root = agents / "runs"
    target = root / run_id
    if (agents.is_symlink() or root.is_symlink() or target.is_symlink()
            or not agents.is_dir() or not root.is_dir() or not target.is_dir()):
        raise ValueError("QTeam run is missing or unsafe")
    if target.resolve().parent != root.resolve():
        raise ValueError("QTeam run escaped the run root")


def cmd_doctor(_args):
    version = herdr(["--version"], timeout=10).strip()
    if not version:
        raise ValueError("Herdr returned an empty version")
    print(json.dumps({
        "backend": "herdr", "available": True, "version": version,
        "inside_herdr": os.environ.get("HERDR_ENV") == "1",
        "authority": "display-only",
    }, sort_keys=True))


def cmd_open(args):
    if os.environ.get("HERDR_ENV") != "1":
        raise ValueError("Herdr open requires running inside a Herdr-managed pane")
    repo = git_root(args.repo)
    validate_run(repo, args.run)
    if not 0 <= args.port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    if not 0.25 <= args.interval <= 60:
        raise ValueError("watch interval must be between 0.25 and 60 seconds")
    split_args = [
        "pane", "split", "--current", "--direction", args.direction,
        "--cwd", str(repo), "--focus" if args.focus else "--no-focus",
    ]
    raw = herdr(split_args)
    try:
        response = json.loads(raw)
        pane_id = response["result"]["pane"]["pane_id"]
    except (KeyError, TypeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Herdr split returned an invalid response") from exc
    if not isinstance(pane_id, str) or not PANE_ID.fullmatch(pane_id):
        raise ValueError("Herdr split returned an unsafe pane id")
    command = [*web_tool(), "--repo", str(repo), "--run", args.run]
    if args.mode == "watch":
        command.extend(["--watch", "--interval", str(args.interval)])
    else:
        command.extend(["--host", "127.0.0.1", "--port", str(args.port)])
        if args.token_file:
            command.extend(["--token-file", str(Path(args.token_file).resolve())])
        if args.allow_raw_logs:
            command.append("--allow-raw-logs")
    try:
        herdr(["pane", "run", pane_id, shlex.join(command)])
        label = f"qteam-{args.mode}-{args.run}"[:64]
        herdr(["pane", "rename", pane_id, label])
        expected = "QTeam Web:" if args.mode == "web" else '"run_id"'
        herdr([
            "pane", "wait-output", pane_id, "--match", expected,
            "--timeout", "10000",
        ], timeout=15)
    except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired):
        try:
            herdr(["pane", "close", pane_id], timeout=10)
        except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired):
            pass
        raise
    print(json.dumps({
        "backend": "herdr", "pane_id": pane_id, "run_id": args.run,
        "mode": args.mode, "authority": "display-only",
    }, sort_keys=True))


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)
    opened = sub.add_parser("open")
    opened.add_argument("--repo")
    opened.add_argument("--run", required=True)
    opened.add_argument("--mode", choices=["web", "watch"], default="web")
    opened.add_argument("--direction", choices=["right", "down"], default="right")
    opened.add_argument("--focus", action="store_true")
    opened.add_argument("--port", type=int, default=8765)
    opened.add_argument("--interval", type=float, default=2.0)
    opened.add_argument("--allow-raw-logs", action="store_true")
    opened.add_argument(
        "--token-file",
        help="mode-0600 Web bearer token; without it the UI is read-only",
    )
    opened.set_defaults(func=cmd_open)
    return ap


def main():
    args = parser().parse_args()
    try:
        args.func(args)
    except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"error: {exc}")


if __name__ == "__main__":
    main()
